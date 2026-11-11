"""Motor de automação: sequências de passos, varreduras e guardas (watchdog).

Formato da sequência (JSON):
{
  "name": "Minha rotina",
  "on_end": "off" | "keep",          # o que fazer com a saída ao terminar/abortar (padrão off)
  "guards": [ {"cond": "p > 15", "action": "off|stop", "msg": "potência alta"} ],
  "steps": [
    {"op": "set", "v": 5.0, "i": 1.0},
    {"op": "output", "on": true},
    {"op": "wait", "s": 2},
    {"op": "ramp", "param": "v", "from": 1, "to": 12, "step": 0.5, "dwell": 1.0, "settle": 0.65, "record": true},
    {"op": "wait_until", "cond": "i < 0.05", "timeout": 3600, "hold": 5},
    {"op": "loop", "count": 3, "steps": [ ... ]},
    {"op": "preset", "id": 2},
    {"op": "setting", "name": "ocp|offset|discharge|brightness|pd_voltage", "value": 1},
    {"op": "measure", "s": 1, "label": "ponto A"},
    {"op": "log", "msg": "texto"},
    {"op": "mark", "label": "evento no gráfico"},
    {"op": "reset_counters"},
    {"op": "save_config"}
  ]
}
Variáveis em condições: v, i, p, vset, iset, on, cv, cc, oc, t (s desde início),
t_on (s de saída ligada no contador), wh, mah, vin.
"""
from __future__ import annotations

import ast
import math
import threading
import time
from collections import deque
from typing import Any

from .device import Device, DeviceError
from .i18n import tr
from .recorder import Recorder

_ALLOWED = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
            ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq, ast.BinOp, ast.Add,
            ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.Name, ast.Load, ast.Constant, ast.Call)
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "sqrt": math.sqrt}


def compile_cond(expr: str):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(tr("expressão não permitida: {name}", name=type(node).__name__))
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise ValueError(tr("só abs/min/max/round/sqrt são permitidas"))
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, bool)):
            raise ValueError(tr("só constantes numéricas"))
    code = compile(tree, "<cond>", "eval")

    def ev(vars_: dict) -> bool:
        return bool(eval(code, {"__builtins__": {}}, {**_FUNCS, **vars_}))
    ev.expr = expr
    return ev


class AbortSequence(Exception):
    pass


class Automation:
    def __init__(self, dev: Device, rec: Recorder):
        self.dev, self.rec = dev, rec
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.logs: deque[dict] = deque(maxlen=1000)
        self.log_seq = 0
        self.status: dict[str, Any] = {"running": False}
        self.results: dict[str, Any] = {"sweep": [], "measures": []}
        # guardas globais (sempre ativas enquanto habilitadas)
        self.global_guards: list[dict] = []
        self.global_guards_enabled = True
        self._seq_guards: list[tuple[Any, dict]] = []
        self._t_start = time.time()
        self._wd = threading.Thread(target=self._watchdog, name="pdpower-watchdog", daemon=True)
        self._wd.start()
        self._last_trip: dict[str, float] = {}

    # ------------------------------------------------------------- log
    def log(self, level: str, msg: str):
        self.log_seq += 1
        self.logs.append({"id": self.log_seq, "t": time.time(), "level": level, "msg": msg})

    def logs_since(self, last_id: int) -> list[dict]:
        return [e for e in list(self.logs) if e["id"] > last_id]

    # ------------------------------------------------------------- variáveis
    def vars(self) -> dict:
        s = self.dev.snapshot()
        m, out = s["meas"], s["output"]
        pr = s["presets"][s["preset_id"]] if s["presets"] else {"v": 0, "i": 0}
        c = self.rec.counters()
        return {
            "v": m["v"], "i": m["i"], "p": m["v"] * m["i"],
            "vset": pr["v"] or 0.0, "iset": pr["i"] or 0.0,
            "on": 1 if out["on"] else 0, "cv": out["mode"] == "CV" and out["on"],
            "cc": out["mode"] == "CC", "oc": out["mode"] == "OC",
            "t": time.time() - self._t_start, "t_on": c["on_time"], "wh": c["wh"], "mah": c["mah"],
            "vin": (s["input"].get("v") or 0.0),
        }

    # ------------------------------------------------------------- guardas
    def set_global_guards(self, guards: list[dict], enabled: bool = True):
        for g in guards:
            compile_cond(g["cond"])
        self.global_guards = guards
        self.global_guards_enabled = enabled

    def _watchdog(self):
        while True:
            time.sleep(0.1)
            if not self.dev.connected:
                continue
            guards = []
            if self.global_guards_enabled:
                guards += [(None, g) for g in self.global_guards if g.get("enabled", True)]
            guards += self._seq_guards
            if not guards:
                continue
            try:
                vv = self.vars()
            except Exception:
                continue
            if not vv["on"]:
                continue
            for comp, g in guards:
                try:
                    fn = comp or compile_cond(g["cond"])
                    if fn(vv):
                        self._trip(g, vv)
                        break
                except Exception as e:
                    self.log("error", tr("guarda inválida '{cond}': {e}", cond=g.get('cond'), e=e))

    def _trip(self, g: dict, vv: dict):
        key = g["cond"]
        if time.time() - self._last_trip.get(key, 0) < 1.0:
            return
        self._last_trip[key] = time.time()
        msg = g.get("msg") or g["cond"]
        try:
            self.dev.set_output(False)
        except DeviceError as e:
            self.log("error", tr("falha ao desligar saída: {e}", e=e))
        self.log("error", tr("⚠ GUARDA ACIONADA: {msg}  (v={v:.3f} V, i={i:.3f} A, p={p:.2f} W) → saída desligada", msg=msg, v=vv['v'], i=vv['i'], p=vv['p']))
        self.rec.mark(tr("Guarda: {msg}", msg=msg))
        if g.get("action", "off") == "stop" or self.status.get("running"):
            self._stop.set()

    # ------------------------------------------------------------- execução
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def validate(self, seq: dict) -> int:
        steps = seq.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError(tr("sequência sem passos"))
        for g in seq.get("guards", []):
            compile_cond(g["cond"])

        def count(st):
            n = 0
            for s in st:
                op = s.get("op")
                if op not in ("set", "output", "wait", "ramp", "wait_until", "loop", "preset", "setting",
                              "measure", "log", "mark", "reset_counters", "save_config"):
                    raise ValueError(tr("op desconhecida: {op}", op=op))
                if op == "wait_until":
                    compile_cond(s["cond"])
                if op == "loop":
                    n += int(s.get("count", 1)) * count(s.get("steps", []))
                elif op == "ramp":
                    step = abs(float(s.get("step", 0.1))) or 0.1
                    n += int(abs(float(s["to"]) - float(s["from"])) / step + 1e-9) + 2
                else:
                    n += 1
            return n
        return count(steps)

    def start(self, seq: dict) -> dict:
        if self.running:
            raise DeviceError(tr("Já existe uma automação em execução"))
        if not self.dev.connected:
            raise DeviceError(tr("Dispositivo não conectado"))
        total = self.validate(seq)
        self._stop.clear()
        self.results = {"sweep": [], "measures": [], "name": seq.get("name")}
        self.status = {"running": True, "name": seq.get("name") or tr("Sequência"), "total": total, "done": 0,
                       "step": None, "started": time.time(), "ended": None, "result": None}
        self._seq_guards = [(compile_cond(g["cond"]), g) for g in seq.get("guards", [])]
        self._thread = threading.Thread(target=self._run, args=(seq,), name="pdpower-seq", daemon=True)
        self._thread.start()
        return self.status

    def stop(self):
        if self.running:
            self._stop.set()
            self.log("warn", tr("Parada solicitada"))

    def wait(self, timeout: float | None = None) -> bool:
        if self._thread:
            self._thread.join(timeout)
        return not self.running

    def _run(self, seq: dict):
        self._t_start = time.time()
        name = seq.get("name") or tr("Sequência")
        self.log("info", tr("▶ Iniciando '{name}'", name=name))
        self.rec.mark(f"▶ {name}")
        result = "ok"
        try:
            self._exec(seq["steps"])
        except AbortSequence:
            result = tr("abortada")
        except Exception as e:
            result = tr("erro: {e}", e=e)
            self.log("error", tr("Erro: {e}", e=e))
        finally:
            if seq.get("on_end", "off") == "off" or result != "ok":
                try:
                    if self.dev.connected:
                        self.dev.set_output(False)
                except Exception:
                    pass
            self._seq_guards = []
            self.status.update(running=False, ended=time.time(), result=result, step=None)
            self.log("ok" if result == "ok" else "warn", tr("■ '{name}' finalizada ({result})", name=name, result=result))
            self.rec.mark(f"■ {name} ({result})")

    def _sleep(self, s: float):
        end = time.monotonic() + s
        while True:
            if self._stop.is_set():
                raise AbortSequence()
            left = end - time.monotonic()
            if left <= 0:
                return
            self._stop.wait(min(left, 0.1))

    def _tick(self, desc: str):
        if self._stop.is_set():
            raise AbortSequence()
        self.status["step"] = desc
        self.status["done"] = self.status.get("done", 0) + 1

    def _measure(self, seconds: float) -> dict:
        seq0 = self.rec.seq
        self._sleep(seconds)
        _, rows = self.rec.since_seq(seq0)
        if not rows:
            vv = self.vars()
            return {"v": vv["v"], "i": vv["i"], "p": vv["p"], "n": 0}
        v = sum(r[1] for r in rows) / len(rows)
        i = sum(r[2] for r in rows) / len(rows)
        return {"v": round(v, 4), "i": round(i, 4), "p": round(v * i, 4), "n": len(rows)}

    def _exec(self, steps: list[dict]):
        d = self.dev
        for s in steps:
            op = s["op"]
            if op == "set":
                self._tick(tr("Ajustar {v} V / {i} A", v=s.get('v', '–'), i=s.get('i', '–')))
                d.set_vi(s.get("v"), s.get("i"))
            elif op == "output":
                self._tick(tr("Saída {s}", s='ON' if s.get('on') else 'OFF'))
                d.set_output(bool(s.get("on")))
            elif op == "wait":
                self._tick(tr("Aguardar {s} s", s=s['s']))
                self._sleep(float(s["s"]))
            elif op == "preset":
                self._tick(f"Preset M{s['id']}")
                d.select_preset(int(s["id"]))
            elif op == "setting":
                self._tick(f"{s['name']} = {s['value']}")
                apply_setting(d, s["name"], s["value"])
            elif op == "log":
                self._tick("Log")
                self.log("info", str(s.get("msg", "")))
            elif op == "mark":
                self._tick(tr("Marcador"))
                self.rec.mark(str(s.get("label") or tr("marcador")))
            elif op == "reset_counters":
                self._tick(tr("Zerar contadores"))
                self.rec.reset_counters()
            elif op == "save_config":
                self._tick(tr("Salvar config"))
                d.save_config()
            elif op == "measure":
                self._tick(tr("Medir {s} s", s=s.get('s', 1)))
                m = self._measure(float(s.get("s", 1)))
                m["label"] = s.get("label", "")
                m["t"] = time.time()
                self.results["measures"].append(m)
                self.log("info", tr("Medida {label}: {v:.3f} V  {i:.4f} A  {p:.3f} W", label=m['label'], v=m['v'], i=m['i'], p=m['p']))
            elif op == "wait_until":
                cond = compile_cond(s["cond"])
                timeout = float(s.get("timeout", 0)) or None
                hold = float(s.get("hold", 0))
                self._tick(tr("Aguardar até {cond}", cond=s['cond']))
                t0, since = time.monotonic(), None
                while True:
                    if cond(self.vars()):
                        since = since or time.monotonic()
                        if time.monotonic() - since >= hold:
                            break
                    else:
                        since = None
                    if timeout and time.monotonic() - t0 > timeout:
                        if s.get("on_timeout", "fail") == "continue":
                            self.log("warn", tr("timeout em wait_until({cond}) — continuando", cond=s['cond']))
                            break
                        raise DeviceError(tr("timeout aguardando {cond}", cond=s['cond']))
                    self._sleep(0.1)
                self.log("info", tr("Condição atingida: {cond}", cond=s['cond']))
            elif op == "ramp":
                self._ramp(s)
            elif op == "loop":
                cnt = int(s.get("count", 1))
                for k in range(cnt):
                    self.status["loop"] = f"{k + 1}/{cnt}"
                    self.log("info", tr("Ciclo {k}/{n}", k=k + 1, n=cnt))
                    self._exec(s.get("steps", []))
            else:
                raise ValueError(tr("op desconhecida: {op}", op=op))

    def _ramp(self, s: dict):
        param = s.get("param", "v")
        a, b = float(s["from"]), float(s["to"])
        step = abs(float(s.get("step", 0.1))) or 0.1
        dwell = float(s.get("dwell", 0.5))
        record = bool(s.get("record", True))
        n = int(math.floor(abs(b - a) / step + 1e-9)) + 1
        sign = 1 if b >= a else -1
        pts = [round(a + sign * k * step, 4) for k in range(n)]
        if pts[-1] != b:
            pts.append(b)
        self._tick(tr("Rampa {p} {a}→{b} ({n} pts)", p=param.upper(), a=a, b=b, n=len(pts)))
        for k, x in enumerate(pts):
            if self._stop.is_set():
                raise AbortSequence()
            self.status["step"] = tr("Rampa {p} = {x:g} ({k}/{n})", p=param.upper(), x=x, k=k + 1, n=len(pts))
            if k:
                self.status["done"] = self.status.get("done", 0) + 1
            if param == "v":
                self.dev.set_vi(v=x)
            else:
                self.dev.set_vi(i=x)
            if record:
                # a placa filtra a leitura (Filter.Win) — espera acomodar antes de medir
                settle = float(s["settle"]) if "settle" in s else dwell * 0.65
                settle = min(max(settle, 0.0), dwell - 0.05) if dwell > 0.1 else 0.0
                self._sleep(settle)
                m = self._measure(max(dwell - settle, 0.05))
                snap = self.dev.snapshot()
                self.results["sweep"].append({"set": x, "param": param, **m, "mode": snap["output"]["mode"]})
            else:
                self._sleep(dwell)


def apply_setting(d: Device, name: str, value):
    name = name.lower()
    if name == "ocp":
        return d.set_ocp(bool(value))
    if name in ("offset", "calibration"):
        return d.set_offset(bool(value))
    if name == "discharge":
        return d.set_discharge(bool(value))
    if name == "brightness":
        return d.set_brightness(int(value))
    if name in ("pd_voltage", "pd"):
        return d.set_pd_voltage(float(value))
    raise DeviceError(tr("configuração desconhecida: {name}", name=name))
