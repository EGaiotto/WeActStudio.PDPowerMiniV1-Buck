"""Servidor MCP (Streamable HTTP em /mcp) — permite que um agente controle a fonte."""
from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .automation import apply_setting
from .device import DeviceError, list_ports
from .protocol import Cmd, READ_FLAG
from .service import get_service

INSTRUCTIONS = """\
Controle da fonte WeAct PDPowerMini V1 (buck USB-PD, 0,5–20 V / 0,005–3 A; V1.1 até 24 V).
A mesma instância é usada pela interface gráfica PDPower Studio — mudanças aparecem ao vivo nela.
Boas práticas: consulte pdpower_status antes de agir; ajuste tensão/corrente ANTES de ligar a saída;
respeite os limites de software configurados pelo usuário (não podem ser alterados via MCP);
para rotinas longas use pdpower_run_sequence e acompanhe com pdpower_automation_status.
"""

mcp = MCPServer("pdpower", title="PDPower Studio", instructions=INSTRUCTIONS, version="1.0.0")


def tool(fn):
    """Registra o tool convertendo erros esperados em ToolError (mensagem chega ao agente)."""
    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except (DeviceError, ValueError, TimeoutError) as e:
            raise ToolError(str(e)) from e
    return mcp.tool()(wrapper)


def _svc():
    return get_service()


def _need_write():
    s = _svc()
    if not s.cfg.get("mcp", {}).get("allow_write", True):
        raise DeviceError("Escrita via MCP está desabilitada pelo usuário na interface (Config → MCP).")
    if not s.dev.connected:
        raise DeviceError("Dispositivo não conectado. Use pdpower_connect.")


async def _run(fn, *a, **kw):
    return await asyncio.to_thread(fn, *a, **kw)


def _brief(st: dict) -> dict:
    pr = st["presets"][st["preset_id"]] if st["connected"] else None
    return {
        "connected": st["connected"], "port": st["port"], "link": st["link"], "simulated": st["simulated"],
        "device": st["info"], "output_on": st["output"]["on"], "regulation_mode": st["output"]["mode"],
        "measured": {"voltage_V": st["meas"]["v"], "current_A": st["meas"]["i"], "power_W": st["meas"]["p"]},
        "active_preset": st["preset_id"], "setpoint": pr,
        "presets": st["presets"], "settings": st["settings"], "input": st["input"],
        "limits": st["limits"], "counters": st.get("counters"),
        "automation": {k: st["automation"].get(k) for k in ("running", "name", "step", "done", "total", "result")},
        "csv_logging": st.get("csv"), "poll_rate_hz": st["stats"]["rate_hz"],
    }


# ============================================================= leitura
@tool
async def pdpower_status() -> dict:
    """Estado completo: conexão, medições (V, A, W), modo CV/CC/OC, saída, presets, ajustes, entrada PD, limites, energia acumulada e automação."""
    return _brief(_svc().full_state())


@tool
async def pdpower_list_ports() -> list[dict]:
    """Lista portas seriais; 'pdpower': true indica uma PDPowerMini (USB 1A86:FE0C)."""
    return await _run(list_ports)


@tool
async def pdpower_measure(seconds: float = 1.0) -> dict:
    """Mede a saída pela média das amostras durante `seconds` (0,1–30 s). Retorna V, A, W, mín/máx e nº de amostras."""
    s = _svc()
    if not s.dev.connected:
        raise DeviceError("Dispositivo não conectado")
    seconds = min(max(seconds, 0.1), 30)
    seq0 = s.rec.seq
    await asyncio.sleep(seconds)
    _, rows = s.rec.since_seq(seq0)
    if not rows:
        m = s.dev.snapshot()["meas"]
        return {"voltage_V": m["v"], "current_A": m["i"], "power_W": m["p"], "samples": 0}
    v = [r[1] for r in rows]
    i = [r[2] for r in rows]
    av, ai = sum(v) / len(v), sum(i) / len(i)
    return {"voltage_V": round(av, 4), "current_A": round(ai, 4), "power_W": round(av * ai, 4),
            "v_min": min(v), "v_max": max(v), "i_min": min(i), "i_max": max(i), "samples": len(rows),
            "mode": s.dev.snapshot()["output"]["mode"]}


@tool
async def pdpower_history(seconds: float = 60, max_points: int = 120) -> dict:
    """Histórico recente (média por janelas) de tensão, corrente e potência, com estatísticas e marcadores de eventos."""
    s = _svc()
    data = s.rec.downsampled(seconds, max_points)
    t_now = time.time()
    data["t_rel_s"] = [round(t - t_now, 2) for t in data.pop("t")]
    return {"series": data, "stats": s.rec.stats(seconds), "counters": s.rec.counters(),
            "markers": [m for m in s.rec.markers if m["t"] >= t_now - seconds]}


@tool
async def pdpower_calibration_data() -> dict:
    """Lê a tabela de calibração de fábrica (somente leitura via MCP): 7 pontos de tensão e 7 de corrente (mV/mA)."""
    s = _svc()
    fd = await _run(s.dev.read_factory)
    return fd.to_dict()


# ============================================================= conexão
@tool
async def pdpower_connect(port: str | None = None, link: Literal["usb", "uart"] = "usb", baud: int = 115200,
                          simulate: bool = False) -> dict:
    """Conecta à fonte. Sem `port` detecta automaticamente pela USB. link='uart' usa CRC8 (pinos DP/DM, baud configurável).
    simulate=true usa um dispositivo virtual (carga de 10 Ω) para testes."""
    s = _svc()
    st = await _run(s.connect, port, link, baud, simulate)
    return _brief({**st, "counters": s.rec.counters(), "automation": s.auto.status, "csv": s.rec.csv_path})


@tool
async def pdpower_disconnect() -> str:
    """Desconecta da fonte (libera a porta serial)."""
    await _run(_svc().dev.disconnect)
    return "desconectado"


# ============================================================= controle
@tool
async def pdpower_set_output(on: bool) -> dict:
    """Liga (true) ou desliga (false) a saída principal."""
    _need_write()
    return await _run(_svc().dev.set_output, on)


@tool
async def pdpower_set(voltage: float | None = None, current: float | None = None) -> dict:
    """Ajusta tensão (V) e/ou limite de corrente (A) do preset ativo — tem efeito imediato na saída se ligada.
    Resolução 10 mV / 1 mA. Faixa V1: 0,5–20 V e 0,005–3 A (ou menos, se o usuário limitou)."""
    _need_write()
    if voltage is None and current is None:
        raise DeviceError("informe voltage e/ou current")
    return await _run(_svc().dev.set_vi, voltage, current)


@tool
async def pdpower_set_preset(preset: int, voltage: float | None = None, current: float | None = None) -> dict:
    """Grava V/I num dos 5 presets (0–4, M0–M4 no display) sem trocar o preset ativo."""
    _need_write()
    return await _run(_svc().dev.set_preset, preset, voltage, current)


@tool
async def pdpower_select_preset(preset: int) -> dict:
    """Ativa o preset 0–4 (a saída precisa estar desligada)."""
    _need_write()
    s = _svc()
    await _run(s.dev.select_preset, preset)
    st = s.dev.snapshot()
    return {"active_preset": st["preset_id"], "setpoint": st["presets"][st["preset_id"]]}


@tool
async def pdpower_configure(ocp: bool | None = None, calibration: bool | None = None, discharge: bool | None = None,
                            brightness: int | None = None, pd_voltage: float | None = None) -> dict:
    """Altera ajustes do dispositivo (só os informados):
    ocp = proteção de sobrecorrente (desliga após ~200 ms em limite de corrente);
    calibration = usar tabela de calibração de fábrica; discharge = descarga ativa da saída;
    brightness = brilho do display 0–100 %; pd_voltage = tensão pedida ao carregador PD (8–24 V, só com saída desligada)."""
    _need_write()
    d = _svc().dev
    for name, val in (("ocp", ocp), ("offset", calibration), ("discharge", discharge),
                      ("brightness", brightness), ("pd_voltage", pd_voltage)):
        if val is not None:
            await _run(apply_setting, d, name, val)
    st = d.snapshot()
    return {"settings": st["settings"], "input": st["input"]}


@tool
async def pdpower_save_config() -> str:
    """Salva na flash do dispositivo: preset ativo, presets, OCP, calibração, brilho e tensão PD."""
    _need_write()
    await _run(_svc().dev.save_config)
    return "configuração salva na flash"


@tool
async def pdpower_system(action: Literal["reset", "factory_reset"], confirm: str = "") -> str:
    """Reinicia o dispositivo ('reset') ou restaura padrões de fábrica ('factory_reset', exige confirm='FACTORY_RESET';
    não apaga calibração). A conexão é restabelecida automaticamente."""
    _need_write()
    d = _svc().dev
    if action == "reset":
        await _run(d.system_reset)
        return "reset enviado; reconectando"
    if confirm != "FACTORY_RESET":
        raise DeviceError("Para restaurar padrões de fábrica passe confirm='FACTORY_RESET'")
    await _run(d.factory_reset)
    return "restauração de fábrica enviada; reconectando"


@tool
async def pdpower_counters(reset: bool = False) -> dict:
    """Energia (Wh), carga (mAh) e tempo de saída ligada acumulados. reset=true zera os contadores."""
    s = _svc()
    if reset:
        s.rec.reset_counters()
    return s.rec.counters()


@tool
async def pdpower_mark(label: str) -> dict:
    """Adiciona um marcador de evento no gráfico da interface (útil para anotar testes)."""
    return _svc().rec.mark(label)


@tool
async def pdpower_raw_read(command_hex: str) -> dict:
    """Envia um comando de LEITURA cru (ex.: '85' ou '84 02') e devolve a resposta em hex. Somente comandos de leitura (bit 0x80)."""
    s = _svc()
    data = bytes.fromhex(command_hex.replace("0x", ""))
    if not data or not data[0] & READ_FLAG:
        raise DeviceError("Via MCP só são permitidos comandos de leitura (primeiro byte com 0x80)")
    term = bytes([0x0A]) if s.dev.snapshot()["link"] == "usb" else b""
    if s.dev.snapshot()["link"] == "uart":
        from .protocol import crc8
        term = bytes([crc8(data)])
    resp = await _run(s.dev.raw, data + term)
    return {"sent": (data + term).hex(" "), "received": resp.hex(" "), "ascii": resp.decode("latin-1")}


# ============================================================= automação
@tool
async def pdpower_run_sequence(steps: list[dict[str, Any]], name: str = "Sequência MCP",
                               guards: list[dict[str, Any]] | None = None, on_end: Literal["off", "keep"] = "off",
                               wait: bool = False, wait_timeout: float = 120) -> dict:
    """Executa uma sequência automatizada (visível na aba Automação da interface).
    Passos (campo 'op'):
      set{v,i} | output{on} | wait{s} | preset{id} | setting{name,value} (ocp/offset/discharge/brightness/pd_voltage)
      ramp{param:'v'|'i', from, to, step, dwell (≥1 s recomendado), settle (padrão 65% do dwell), record=true} → results.sweep
      wait_until{cond, timeout, hold, on_timeout:'fail'|'continue'} | measure{s,label} → results.measures
      loop{count, steps} | log{msg} | mark{label} | reset_counters | save_config
    Condições usam v, i, p, vset, iset, on, cv, cc, oc, t, t_on, wh, mah, vin (ex.: 'i < 0.05 and t > 60').
    guards: [{cond, action:'off'|'stop', msg}] avaliadas a cada 100 ms; ao disparar desligam a saída.
    on_end='off' desliga a saída no fim. wait=true bloqueia até terminar (máx wait_timeout s) e devolve os resultados."""
    _need_write()
    s = _svc()
    seq = {"name": name, "steps": steps, "guards": guards or [], "on_end": on_end}
    await _run(s.auto.start, seq)
    if wait:
        await _run(s.auto.wait, min(max(wait_timeout, 1), 3600))
    return {"status": s.auto.status, "results": s.auto.results if not s.auto.running else None}


@tool
async def pdpower_automation_status(include_log: int = 20) -> dict:
    """Status da automação em execução, resultados (sweep/measures) e últimas `include_log` linhas do log."""
    s = _svc()
    return {"status": s.auto.status, "results": s.auto.results, "log": list(s.auto.logs)[-include_log:]}


@tool
async def pdpower_stop() -> dict:
    """PARADA: interrompe qualquer automação e desliga a saída."""
    s = _svc()
    s.auto.stop()
    out = None
    if s.dev.connected:
        out = await _run(s.dev.set_output, False)
    return {"automation_stopped": True, "output": out}


@tool
async def pdpower_sweep(param: Literal["v", "i"], start: float, stop: float, step: float, dwell: float = 1.0,
                        fixed: float | None = None, settle_off: bool = True) -> dict:
    """Varredura rápida: varia tensão (param='v', com corrente fixa=`fixed` A) ou corrente (param='i', com tensão
    fixa=`fixed` V) de start a stop, mede cada ponto e devolve a tabela set→(V, A, W, modo). Desliga a saída no fim."""
    _need_write()
    s = _svc()
    pre = [{"op": "set", "i": fixed}] if (param == "v" and fixed is not None) else \
          [{"op": "set", "v": fixed}] if (param == "i" and fixed is not None) else []
    first = [{"op": "set", param: start}]
    steps = pre + first + [{"op": "output", "on": True},
                           {"op": "ramp", "param": param, "from": start, "to": stop, "step": step, "dwell": dwell}]
    n = abs(stop - start) / max(abs(step), 1e-6) + 1
    await _run(s.auto.start, {"name": f"Varredura {param.upper()}", "steps": steps,
                              "on_end": "off" if settle_off else "keep"})
    await _run(s.auto.wait, n * dwell + 30)
    return {"status": s.auto.status, "points": s.auto.results.get("sweep")}
