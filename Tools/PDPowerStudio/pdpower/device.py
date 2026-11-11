"""Driver de alto nível do PDPowerMini: E/S serial serializada, polling e estado."""
from __future__ import annotations

import logging
import threading
import time
from copy import deepcopy
from typing import Callable

import serial
import serial.tools.list_ports

from .protocol import (
    Cmd, FactoryData, FrameError, INPUT_STATE_LABEL, LCD_PANEL, MODE_CC, MODE_CV, MODE_OC,
    USB_PID, USB_VID, build_frame, le16, model_from_serial, try_parse, u16, version_ge,
)
from .i18n import tr
from .simulator import SimulatedPort

log = logging.getLogger("pdpower.device")


class DeviceError(Exception):
    pass


def list_ports() -> list[dict]:
    out = []
    for p in serial.tools.list_ports.comports():
        is_pd = p.vid == USB_VID and p.pid == USB_PID
        sn = p.serial_number or ""
        out.append({
            "port": p.device,
            "description": p.description,
            "serial": sn,
            "pdpower": is_pd and sn[:2].upper() in ("AC", "B3"),
            "vid": p.vid, "pid": p.pid,
        })
    out.sort(key=lambda d: (not d["pdpower"], d["port"]))
    return out


def find_device_port() -> str | None:
    for p in list_ports():
        if p["pdpower"]:
            return p["port"]
    return None


def _empty_state() -> dict:
    return {
        "connected": False, "port": None, "link": "usb", "baud": 115200, "simulated": False,
        "info": {"name": None, "version": None, "serial": None, "model": None, "lcd": None},
        "spec": None,
        "output": {"on": False, "mode": MODE_CV, "raw": 0},
        "preset_id": 0,
        "presets": [{"v": None, "i": None} for _ in range(5)],
        "meas": {"v": 0.0, "i": 0.0, "p": 0.0, "t": 0.0},
        "settings": {"ocp": None, "offset": None, "brightness": None, "discharge": None},
        "input": {"supported": False, "state": None, "label": None, "v": None, "pd_req": None},
        "stats": {"polls": 0, "errors": 0, "rate_hz": 0.0},
        "last_error": None,
    }


class Device:
    """Dono exclusivo da porta serial. Thread-safe."""

    QUERY_TIMEOUT = 0.25
    WRITE_SETTLE = 0.02

    def __init__(self):
        self._ser = None
        self._crc = False
        self._io = threading.RLock()
        self._st_lock = threading.Lock()
        self._state = _empty_state()
        self._stop = threading.Event()
        self._poller: threading.Thread | None = None
        self.poll_interval = 0.1
        self.auto_reconnect = True
        self._want = None                      # parâmetros da última conexão pedida
        self.limits = {"v_max": None, "i_max": None}   # limites por software (None = do modelo)
        self.sample_listeners: list[Callable[[float, float, float, bool, str], None]] = []
        self.event_listeners: list[Callable[[str, str], None]] = []

    # ================================================================ util
    def _emit(self, level: str, msg: str):
        log.info("[%s] %s", level, msg)
        for fn in list(self.event_listeners):
            try:
                fn(level, msg)
            except Exception:  # pragma: no cover
                log.exception("listener")

    def _upd(self, **kw):
        with self._st_lock:
            for k, v in kw.items():
                if isinstance(v, dict) and isinstance(self._state.get(k), dict):
                    self._state[k].update(v)
                else:
                    self._state[k] = v

    def snapshot(self) -> dict:
        with self._st_lock:
            s = deepcopy(self._state)
        s["limits"] = self.effective_limits()
        s["poll_interval"] = self.poll_interval
        return s

    @property
    def connected(self) -> bool:
        return self._ser is not None

    def spec(self):
        return model_from_serial(self._state["info"].get("serial"))

    def effective_limits(self) -> dict:
        sp = self.spec()
        v_max = sp.v_max if self.limits.get("v_max") is None else min(sp.v_max, float(self.limits["v_max"]))
        i_max = sp.i_max if self.limits.get("i_max") is None else min(sp.i_max, float(self.limits["i_max"]))
        return {"v_min": sp.v_min, "v_max": v_max, "i_min": sp.i_min, "i_max": i_max,
                "pd_min": sp.pd_min, "pd_max": sp.pd_max,
                "model_v_max": sp.v_max, "model_i_max": sp.i_max,
                "user_v_max": self.limits.get("v_max"), "user_i_max": self.limits.get("i_max")}

    # ================================================================ conexão
    def connect(self, port: str | None = None, link: str = "usb", baud: int = 115200,
                simulate: bool = False) -> dict:
        self.disconnect(user=False)
        link = link.lower()
        if link not in ("usb", "uart"):
            raise DeviceError(tr("link deve ser 'usb' ou 'uart'"))
        if link == "usb":
            baud = 115200
        self._want = {"port": port, "link": link, "baud": baud, "simulate": simulate}
        self._open(**self._want)
        self._stop.clear()
        self._poller = threading.Thread(target=self._poll_loop, name="pdpower-poll", daemon=True)
        self._poller.start()
        return self.snapshot()

    def _open(self, port, link, baud, simulate):
        crc = link == "uart"
        if simulate:
            ser = SimulatedPort(crc=crc)
            port = "SIM"
        else:
            port = port or find_device_port()
            if not port:
                raise DeviceError(tr("PDPowerMini não encontrado (USB 1A86:FE0C). Conecte a placa ou informe a porta."))
            try:
                ser = serial.Serial(port, baud, timeout=0.02, write_timeout=0.5)
            except serial.SerialException as e:
                raise DeviceError(tr("Não foi possível abrir {port}: {e}", port=port, e=e)) from e
        with self._io:
            self._ser, self._crc = ser, crc
        st = _empty_state()
        st.update(connected=True, port=port, link=link, baud=baud, simulated=simulate)
        with self._st_lock:
            self._state = st
        try:
            time.sleep(0.05)
            self.refresh_all()
        except Exception as e:
            self._close_port()
            with self._st_lock:
                self._state = _empty_state()
            raise DeviceError(tr("Dispositivo não respondeu em {port}: {e}", port=port, e=e)) from e
        self._emit("ok", tr("Conectado a {name} em {port} ({link})", name=self._state['info']['name'], port=port, link=link.upper()))

    def _close_port(self):
        with self._io:
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
            self._ser = None

    def disconnect(self, user: bool = True):
        if user:
            self._want = None
        self._stop.set()
        if self._poller and self._poller.is_alive() and threading.current_thread() is not self._poller:
            self._poller.join(timeout=2)
        was = self._ser is not None
        self._close_port()
        with self._st_lock:
            self._state = _empty_state()
        if was and user:
            self._emit("info", tr("Desconectado"))

    # ================================================================ E/S
    def _require(self):
        if self._ser is None:
            raise DeviceError(tr("Dispositivo não conectado"))

    def query(self, cmd: Cmd, payload=(), retries: int = 2) -> bytes:
        self._require()
        frame = build_frame(cmd, payload, read=True, crc=self._crc)
        last = None
        with self._io:
            for _ in range(retries + 1):
                ser = self._ser
                if ser is None:
                    raise DeviceError(tr("Dispositivo não conectado"))
                try:
                    ser.reset_input_buffer()
                    ser.write(frame)
                    buf = bytearray()
                    deadline = time.monotonic() + self.QUERY_TIMEOUT
                    while time.monotonic() < deadline:
                        chunk = ser.read(max(1, ser.in_waiting))
                        if chunk:
                            buf += chunk
                            res = try_parse(buf, cmd, crc=self._crc)
                            if res is not None:
                                return res[0]
                    last = TimeoutError(tr("sem resposta para {cmd}", cmd=cmd.name))
                except FrameError as e:
                    last = e
                    time.sleep(0.02)
                except (serial.SerialException, OSError) as e:
                    self._on_link_lost(e)
                    raise DeviceError(tr("Conexão perdida: {e}", e=e)) from e
        raise DeviceError(str(last))

    def write(self, cmd: Cmd, payload=()):
        self._require()
        frame = build_frame(cmd, payload, read=False, crc=self._crc)
        with self._io:
            try:
                self._ser.write(frame)
                time.sleep(self.WRITE_SETTLE)
            except (serial.SerialException, OSError) as e:
                self._on_link_lost(e)
                raise DeviceError(tr("Conexão perdida: {e}", e=e)) from e

    def raw(self, data: bytes, wait: float = 0.25) -> bytes:
        """Envia bytes crus e devolve tudo que chegar em `wait` s (console avançado)."""
        self._require()
        with self._io:
            self._ser.reset_input_buffer()
            self._ser.write(data)
            buf = bytearray()
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                buf += self._ser.read(max(1, self._ser.in_waiting))
            return bytes(buf)

    def _on_link_lost(self, e):
        log.warning("link perdido: %s", e)
        self._close_port()
        with self._st_lock:
            self._state["connected"] = False
            self._state["last_error"] = str(e)
        self._emit("error", tr("Conexão com o dispositivo perdida: {e}", e=e))

    # ================================================================ leituras
    def read_info(self) -> dict:
        name = self.query(Cmd.WHO_AM_I).decode(errors="replace").strip()
        ver = self.query(Cmd.SYSTEM_VERSION).decode(errors="replace").strip()
        sn = self.query(Cmd.SYSTEM_SERIAL_NUM).decode(errors="replace").strip().upper()
        sp = model_from_serial(sn)
        info = {"name": name, "version": ver, "serial": sn, "model": sp.name}
        try:
            lcd = self.query(Cmd.SYSTEM_LCD_PANEL_TYPE)[0]
            info["lcd"] = LCD_PANEL.get(lcd, str(lcd))
            info["lcd_raw"] = lcd
        except DeviceError:
            info["lcd"] = None
        self._upd(info=info, spec=sp.__dict__,
                  input={"supported": version_ge(ver, sp.input_state_fw)})
        return info

    def read_output_state(self) -> dict:
        x = self.query(Cmd.OUTPUT_EN)[0]
        m = (x >> 1) & 0x03
        out = {"on": bool(x & 1), "mode": {0: MODE_CV, 1: MODE_CC}.get(m, MODE_OC), "raw": x}
        self._upd(output=out)
        return out

    def read_display(self) -> tuple[float, float]:
        d = self.query(Cmd.OUTPUT_DISPLAY)
        v, i = u16(d[0], d[1]) / 1000, u16(d[2], d[3]) / 1000
        return v, i

    def read_preset_id(self) -> int:
        pid = self.query(Cmd.OUTPUT_ID)[0]
        self._upd(preset_id=pid)
        return pid

    def read_preset(self, pid: int) -> dict:
        d = self.query(Cmd.OUTPUT_DATA, [pid])
        p = {"v": u16(d[1], d[2]) / 1000, "i": u16(d[3], d[4]) / 1000}
        with self._st_lock:
            self._state["presets"][d[0] if d[0] < 5 else pid] = p
        return p

    def read_presets(self) -> list[dict]:
        return [self.read_preset(n) for n in range(5)]

    def read_settings(self) -> dict:
        s = {
            "ocp": bool(self.query(Cmd.OUTPUT_OCP_EN)[0]),
            "offset": bool(self.query(Cmd.OUTPUT_OFFSET_EN)[0]),
            "brightness": int(self.query(Cmd.BRIGHTNESS)[0]),
        }
        self._upd(settings=s)
        return s

    def read_input(self) -> dict | None:
        if not self._state["input"].get("supported"):
            return None
        d = self.query(Cmd.INPUT_STATE)
        inp = {"state": d[0], "label": tr(INPUT_STATE_LABEL.get(d[0], str(d[0]))),
               "v": u16(d[1], d[2]) / 1000, "pd_req": u16(d[3], d[4]) / 10}
        self._upd(input=inp)
        return inp

    def read_factory(self) -> FactoryData:
        return FactoryData.from_bytes(self.query(Cmd.SYSTEM_FACTORY_DATA))

    def refresh_all(self) -> dict:
        self.read_info()
        self.read_output_state()
        self.read_preset_id()
        self.read_presets()
        self.read_settings()
        self.read_input()
        v, i = self.read_display()
        self._upd(meas={"v": v, "i": i, "p": round(v * i, 4), "t": time.time()})
        return self.snapshot()

    # ================================================================ escritas
    def _check_vi(self, v: float | None, i: float | None):
        lim = self.effective_limits()
        if v is not None and not (lim["v_min"] - 1e-9 <= v <= lim["v_max"] + 1e-9):
            raise DeviceError(tr("Tensão {v:.3f} V fora do permitido ({lo}–{hi} V)", v=v, lo=lim['v_min'], hi=lim['v_max']))
        if i is not None and not (lim["i_min"] - 1e-9 <= i <= lim["i_max"] + 1e-9):
            raise DeviceError(tr("Corrente {i:.3f} A fora do permitido ({lo}–{hi} A)", i=i, lo=lim['i_min'], hi=lim['i_max']))

    def set_output(self, on: bool) -> dict:
        self.write(Cmd.OUTPUT_EN, [1 if on else 0])
        out = self.read_output_state()
        if out["on"] != bool(on) and not (on and out["mode"] == MODE_OC):
            time.sleep(0.05)
            out = self.read_output_state()
        self._emit("info", tr("Saída LIGADA") if out['on'] else tr("Saída DESLIGADA"))
        return out

    def set_preset(self, pid: int, v: float | None = None, i: float | None = None) -> dict:
        if not 0 <= int(pid) <= 4:
            raise DeviceError(tr("preset deve ser 0–4"))
        cur = self._state["presets"][pid]
        if cur["v"] is None or cur["i"] is None:
            cur = self.read_preset(pid)
        v = cur["v"] if v is None else float(v)
        i = cur["i"] if i is None else float(i)
        self._check_vi(v, i)
        self.write(Cmd.OUTPUT_DATA, [pid, *le16(round(v * 1000)), *le16(round(i * 1000))])
        return self.read_preset(pid)

    def set_vi(self, v: float | None = None, i: float | None = None) -> dict:
        """Ajusta tensão/corrente do preset ativo (o que está na saída)."""
        return self.set_preset(self._state["preset_id"], v, i)

    def select_preset(self, pid: int, force: bool = False) -> int:
        if not 0 <= int(pid) <= 4:
            raise DeviceError(tr("preset deve ser 0–4"))
        p = self._state["presets"][pid]
        if p["v"] is not None:
            self._check_vi(p["v"], p["i"])
        if self._state["output"]["on"] and not force:
            raise DeviceError(tr("Desligue a saída antes de trocar de preset"))
        self.write(Cmd.OUTPUT_ID, [int(pid)])
        return self.read_preset_id()

    def set_ocp(self, on: bool):
        self.write(Cmd.OUTPUT_OCP_EN, [1 if on else 0])
        return self.read_settings()

    def set_offset(self, on: bool):
        self.write(Cmd.OUTPUT_OFFSET_EN, [1 if on else 0])
        return self.read_settings()

    def set_brightness(self, pct: int):
        pct = int(pct)
        if not 0 <= pct <= 100:
            raise DeviceError(tr("brilho deve ser 0–100"))
        self.write(Cmd.BRIGHTNESS, [pct])
        return self.read_settings()

    def set_discharge(self, on: bool):
        # Não há leitura deste parâmetro no protocolo; mantemos o último valor enviado.
        self.write(Cmd.OUTPUT_DISCHARGE_EN, [1 if on else 0])
        self._upd(settings={"discharge": bool(on)})
        return self.snapshot()["settings"]

    def set_pd_voltage(self, volts: float):
        sp = self.spec()
        if not sp.pd_min <= volts <= sp.pd_max:
            raise DeviceError(tr("Tensão PD deve ficar entre {lo} e {hi} V", lo=sp.pd_min, hi=sp.pd_max))
        if self._state["output"]["on"]:
            raise DeviceError(tr("A tensão PD só é aplicada com a saída desligada"))
        self.write(Cmd.INPUT_STATE, le16(round(volts * 10)))
        time.sleep(0.1)
        return self.read_input()

    def save_config(self):
        self.write(Cmd.SYSTEM_CONFIG_SAVE)
        self._emit("ok", tr("Configuração salva na flash do dispositivo"))

    def system_reset(self):
        self.write(Cmd.SYSTEM_RESET)
        self._emit("warn", tr("Reset do sistema enviado"))
        self._schedule_reconnect()

    def factory_reset(self):
        self.write(Cmd.SYSTEM_FACTORY_RESET)
        self._emit("warn", tr("Restauração de fábrica enviada"))
        self._schedule_reconnect()

    def set_lcd_type(self, t: int):
        if t not in (0, 1):
            raise DeviceError(tr("tipo de LCD deve ser 0 (HAN) ou 1 (BOE)"))
        self.write(Cmd.SYSTEM_LCD_PANEL_TYPE, [t])
        self._emit("warn", tr("Tipo de painel LCD gravado: {p}", p=LCD_PANEL[t]))
        return self.read_info()

    def write_factory(self, fd: FactoryData):
        self.write(Cmd.SYSTEM_FACTORY_DATA, fd.to_bytes())
        time.sleep(0.2)
        back = self.read_factory()
        if back.to_bytes() != fd.to_bytes():
            raise DeviceError(tr("Verificação falhou: dados lidos diferem dos gravados"))
        self._emit("warn", tr("Dados de calibração gravados e verificados"))
        return back

    def _schedule_reconnect(self):
        def later():
            time.sleep(0.3)
            self._close_port()
            with self._st_lock:
                self._state["connected"] = False
        threading.Thread(target=later, daemon=True).start()

    # ================================================================ polling
    def _poll_loop(self):
        n = 0
        t_rate, n_rate = time.monotonic(), 0
        while not self._stop.is_set():
            t0 = time.monotonic()
            if self._ser is None:
                if self._want and self.auto_reconnect:
                    try:
                        self._open(**self._want)
                    except Exception:
                        self._stop.wait(2.0)
                        continue
                else:
                    self._stop.wait(0.5)
                    continue
            try:
                v, i = self.read_display()
                now = time.time()
                if n % 3 == 0:
                    self.read_output_state()
                if n % 10 == 5:
                    self.read_input()
                if n % 10 == 7:
                    self.read_preset_id()
                    self.read_preset(self._state["preset_id"])
                if n % 40 == 20:
                    self.read_settings()
                out = self._state["output"]
                self._upd(meas={"v": v, "i": i, "p": round(v * i, 4), "t": now})
                for fn in list(self.sample_listeners):
                    try:
                        fn(now, v, i, out["on"], out["mode"])
                    except Exception:
                        log.exception("sample listener")
                n += 1
                n_rate += 1
                with self._st_lock:
                    self._state["stats"]["polls"] += 1
                if time.monotonic() - t_rate >= 2:
                    with self._st_lock:
                        self._state["stats"]["rate_hz"] = round(n_rate / (time.monotonic() - t_rate), 1)
                    t_rate, n_rate = time.monotonic(), 0
            except DeviceError as e:
                with self._st_lock:
                    self._state["stats"]["errors"] += 1
                    self._state["last_error"] = str(e)
                self._stop.wait(0.2)
            except Exception as e:  # pragma: no cover
                log.exception("poll")
                self._stop.wait(0.5)
            dt = time.monotonic() - t0
            self._stop.wait(max(0.0, self.poll_interval - dt))
