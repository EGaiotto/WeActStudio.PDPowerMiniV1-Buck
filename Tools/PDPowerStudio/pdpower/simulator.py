"""Dispositivo PDPowerMini simulado, no nível de bytes (substitui serial.Serial).

Modela uma carga resistiva (com bateria opcional) para testar GUI, automação
e MCP sem hardware. Implementa o mesmo protocolo que a placa real.
"""
from __future__ import annotations

import random
import threading
import time

from .protocol import Cmd, FactoryData, READ_FLAG, TERMINATOR, crc8, le16, u16


class SimulatedPort:
    def __init__(self, crc: bool = False, load_ohm: float = 10.0):
        self.crc = crc
        self.is_open = True
        self.port = "SIM"
        self._rx = bytearray()          # bytes que o host vai ler
        self._tx = bytearray()          # bytes que o host escreveu
        self._lock = threading.Lock()
        # estado
        self.out_en = False
        self.out_id = 0
        self.presets = [(5000, 200), (3300, 500), (5000, 1000), (9000, 2000), (12000, 3000)]
        self.ocp = False
        self.offset = True
        self.brightness = 20
        self.discharge = False
        self.pd_req = 200            # 0.1 V
        self.lcd = 0
        self.factory = FactoryData(
            d_v=[47, 1044, 3030, 4999, 9954, 14921, 18888],
            s_v=[0, 1005, 2997, 5002, 10002, 15002, 18994],
            d_a=[7, 56, 355, 751, 1499, 2496, 2995],
            s_a=[0, 49, 348, 744, 1488, 2480, 2977],
            s_0a_s1=1)
        self.load_ohm = load_ohm
        self.oc_tripped = False
        self._cc_since: float | None = None
        self._vout = 0.0
        self._last = time.monotonic()

    # ------------------------------------------------------------ física
    def _physics(self) -> tuple[float, float, int]:
        now = time.monotonic()
        dt = min(now - self._last, 0.5)
        self._last = now
        vset, iset = self.presets[self.out_id][0] / 1000, self.presets[self.out_id][1] / 1000
        mode = 0
        if self.out_en:
            r = max(self.load_ohm, 0.05)
            v_cv = vset
            if v_cv / r > iset:
                target, mode = iset * r, 1
            else:
                target = v_cv
            # dinâmica de 1ª ordem (~30 ms)
            self._vout += (target - self._vout) * min(1.0, dt / 0.03)
            if mode == 1 and self.ocp:
                self._cc_since = self._cc_since or now
                if now - self._cc_since > 0.2:
                    self.out_en, self.oc_tripped = False, True
            else:
                self._cc_since = None
        else:
            tau = 0.05 if self.discharge else 2.0
            self._vout -= self._vout * min(1.0, dt / tau)
        v = max(0.0, self._vout + random.gauss(0, 0.004))
        i = v / max(self.load_ohm, 0.05) if self.out_en else 0.0
        i = max(0.0, i + random.gauss(0, 0.0015)) if self.out_en else 0.0
        if self.oc_tripped:
            mode = 2
        return v, i, mode

    # ------------------------------------------------------------ API tipo pyserial
    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._rx)

    def reset_input_buffer(self):
        with self._lock:
            self._rx.clear()

    def read(self, n: int = 1) -> bytes:
        deadline = time.monotonic() + 0.05
        while True:
            with self._lock:
                if self._rx:
                    out = bytes(self._rx[:n])
                    del self._rx[:n]
                    return out
            if time.monotonic() > deadline:
                return b""
            time.sleep(0.002)

    def write(self, data: bytes) -> int:
        with self._lock:
            self._tx.extend(data)
        self._process()
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    # ------------------------------------------------------------ protocolo
    def _frame_len(self, cmd: int, read: bool) -> int | None:
        if read:
            return 3 if cmd == Cmd.OUTPUT_DATA else 2
        return {Cmd.OUTPUT_EN: 3, Cmd.OUTPUT_ID: 3, Cmd.OUTPUT_DATA: 7, Cmd.OUTPUT_OCP_EN: 3,
                Cmd.OUTPUT_OFFSET_EN: 3, Cmd.BRIGHTNESS: 3, Cmd.OUTPUT_DISCHARGE_EN: 3,
                Cmd.INPUT_STATE: 4, Cmd.SYSTEM_RESET: 2, Cmd.SYSTEM_CONFIG_SAVE: 2,
                Cmd.SYSTEM_FACTORY_RESET: 2, Cmd.SYSTEM_LCD_PANEL_TYPE: 3,
                Cmd.SYSTEM_FACTORY_DATA: 66}.get(cmd)

    def _process(self):
        while True:
            with self._lock:
                if not self._tx:
                    return
                head = self._tx[0]
                read = bool(head & READ_FLAG)
                cmd = head & 0x7F
                n = self._frame_len(cmd, read)
                if n is None:
                    del self._tx[0]
                    continue
                if len(self._tx) < n:
                    return
                frame = bytes(self._tx[:n])
                del self._tx[:n]
            tail = frame[-1]
            if self.crc and tail != crc8(frame[:-1]):
                continue
            if not self.crc and tail != TERMINATOR:
                continue
            body = frame[1:-1]
            resp = self._handle(cmd, read, body)
            if resp is not None:
                with self._lock:
                    self._rx.extend(resp)

    def _reply(self, cmd: int, data: list[int] | bytes) -> bytes:
        b = bytearray([cmd | READ_FLAG]) + bytes(data)
        b.append(crc8(b) if self.crc else TERMINATOR)
        return bytes(b)

    def _reply_str(self, cmd: int, s: str) -> bytes:
        raw = s.encode()
        if self.crc:
            b = bytearray([cmd | READ_FLAG, len(raw)]) + raw
            b.append(crc8(b))
            return bytes(b)
        return bytes([cmd | READ_FLAG]) + raw + bytes([TERMINATOR])

    def _handle(self, cmd: int, read: bool, body: bytes) -> bytes | None:
        if read:
            if cmd == Cmd.WHO_AM_I:
                return self._reply_str(cmd, "WeAct Studio PD Power Mini V1 BUCK (SIM)")
            if cmd == Cmd.SYSTEM_VERSION:
                return self._reply_str(cmd, "V1.0.2.0_simulado")
            if cmd == Cmd.SYSTEM_SERIAL_NUM:
                return self._reply_str(cmd, "ac0000514d00")
            if cmd == Cmd.OUTPUT_EN:
                _, _, mode = self._physics()
                return self._reply(cmd, [int(self.out_en) | (mode << 1)])
            if cmd == Cmd.OUTPUT_ID:
                return self._reply(cmd, [self.out_id])
            if cmd == Cmd.OUTPUT_DATA:
                pid = body[0] if body else 0
                if pid > 4:
                    return None
                v, i = self.presets[pid]
                return self._reply(cmd, [pid, *le16(v), *le16(i)])
            if cmd == Cmd.OUTPUT_DISPLAY:
                v, i, _ = self._physics()
                return self._reply(cmd, [*le16(round(v * 1000)), *le16(round(i * 1000))])
            if cmd == Cmd.OUTPUT_OCP_EN:
                return self._reply(cmd, [int(self.ocp)])
            if cmd == Cmd.OUTPUT_OFFSET_EN:
                return self._reply(cmd, [int(self.offset)])
            if cmd == Cmd.BRIGHTNESS:
                return self._reply(cmd, [self.brightness])
            if cmd == Cmd.INPUT_STATE:
                vin = 20000 + random.randint(-80, 80)
                return self._reply(cmd, [5, *le16(vin), *le16(self.pd_req)])
            if cmd == Cmd.SYSTEM_LCD_PANEL_TYPE:
                return self._reply(cmd, [self.lcd])
            if cmd == Cmd.SYSTEM_FACTORY_DATA:
                return self._reply(cmd, self.factory.to_bytes())
            return None
        # escritas (sem resposta)
        if cmd == Cmd.OUTPUT_EN:
            self.out_en = bool(body[0])
            if self.out_en:
                self.oc_tripped = False
        elif cmd == Cmd.OUTPUT_ID:
            if body[0] <= 4:
                self.out_id = body[0]
        elif cmd == Cmd.OUTPUT_DATA:
            pid = body[0]
            if pid <= 4:
                self.presets[pid] = (u16(body[1], body[2]), u16(body[3], body[4]))
        elif cmd == Cmd.OUTPUT_OCP_EN:
            self.ocp = bool(body[0])
        elif cmd == Cmd.OUTPUT_OFFSET_EN:
            self.offset = bool(body[0])
        elif cmd == Cmd.BRIGHTNESS:
            self.brightness = body[0]
        elif cmd == Cmd.OUTPUT_DISCHARGE_EN:
            self.discharge = bool(body[0])
        elif cmd == Cmd.INPUT_STATE:
            v = u16(body[0], body[1])
            if v >= 80 and not self.out_en:
                self.pd_req = v
        elif cmd == Cmd.SYSTEM_RESET:
            self.out_en = False
        elif cmd == Cmd.SYSTEM_FACTORY_RESET:
            self.__init__(crc=self.crc, load_ohm=self.load_ohm)
        elif cmd == Cmd.SYSTEM_LCD_PANEL_TYPE:
            self.lcd = body[0]
        elif cmd == Cmd.SYSTEM_FACTORY_DATA:
            self.factory = FactoryData.from_bytes(body)
        return None
