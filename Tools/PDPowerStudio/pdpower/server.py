"""Servidor HTTP: interface web, API REST, WebSocket ao vivo e MCP em /mcp."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .automation import apply_setting
from .device import DeviceError, list_ports
from .i18n import get_lang, tr
from .mcp_server import mcp
from .protocol import FactoryData, LCD_PANEL
from .service import LOG_DIR, CONFIG_DIR, get_service

log = logging.getLogger("pdpower.server")
WEB = Path(__file__).parent / "web"


def create_app() -> FastAPI:
    svc = get_service()
    mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True)

    @asynccontextmanager
    async def lifespan(app):
        asyncio.get_running_loop().run_in_executor(None, svc.autoconnect)
        async with mcp.session_manager.run():
            yield
        svc.auto.stop()
        svc.rec.stop_csv()
        svc.dev.disconnect(user=False)

    app = FastAPI(title="PDPower Studio", lifespan=lifespan)

    @app.exception_handler(DeviceError)
    async def _dev_err(_, e: DeviceError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _val_err(_, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    run = lambda fn, *a, **kw: asyncio.to_thread(fn, *a, **kw)  # noqa: E731

    # ---------------------------------------------------------------- páginas
    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/lang.js")
    async def lang_js():
        # carregado antes do i18n.js: a interface já nasce no idioma salvo (sem piscar)
        return PlainTextResponse(f"window.PD_LANG = {json.dumps(get_lang())};", media_type="application/javascript",
                                 headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")

    # ---------------------------------------------------------------- estado/conexão
    @app.get("/api/state")
    async def state():
        return svc.full_state()

    @app.get("/api/ports")
    async def ports():
        return await run(list_ports)

    @app.post("/api/connect")
    async def connect(body: dict):
        return await run(svc.connect, body.get("port") or None, body.get("link", "usb"),
                         int(body.get("baud", 115200)), bool(body.get("simulate", False)))

    @app.post("/api/disconnect")
    async def disconnect():
        await run(svc.dev.disconnect)
        return {"ok": True}

    @app.post("/api/refresh")
    async def refresh():
        return await run(svc.dev.refresh_all)

    # ---------------------------------------------------------------- controle
    @app.post("/api/output")
    async def output(body: dict):
        return await run(svc.dev.set_output, bool(body["on"]))

    @app.post("/api/setpoint")
    async def setpoint(body: dict):
        v = body.get("v")
        i = body.get("i")
        v = None if v in (None, "") else float(v)
        i = None if i in (None, "") else float(i)
        if "preset" in body and body["preset"] is not None:
            return await run(svc.dev.set_preset, int(body["preset"]), v, i)
        return await run(svc.dev.set_vi, v, i)

    @app.post("/api/preset/select")
    async def preset_select(body: dict):
        return {"preset_id": await run(svc.dev.select_preset, int(body["id"]))}

    @app.post("/api/setting")
    async def setting(body: dict):
        await run(apply_setting, svc.dev, body["name"], body["value"])
        return svc.full_state()

    @app.post("/api/system/{action}")
    async def system(action: str, body: dict | None = None):
        if action == "save":
            await run(svc.dev.save_config)
        elif action == "reset":
            await run(svc.dev.system_reset)
        elif action == "factory_reset":
            if (body or {}).get("confirm") != "FACTORY_RESET":
                raise DeviceError(tr("confirmação necessária"))
            await run(svc.dev.factory_reset)
        else:
            raise HTTPException(404)
        return {"ok": True}

    # ---------------------------------------------------------------- avançado
    @app.get("/api/factory")
    async def factory_get():
        fd = await run(svc.dev.read_factory)
        return fd.to_dict()

    @app.post("/api/factory")
    async def factory_set(body: dict):
        if body.get("confirm") != "WRITE_CALIBRATION":
            raise DeviceError(tr("confirmação necessária"))
        cur = await run(svc.dev.read_factory)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        bk = CONFIG_DIR / f"calibration_backup_{time.strftime('%Y%m%d_%H%M%S')}.json"
        bk.write_text(json.dumps({"serial": svc.dev.snapshot()["info"]["serial"], **cur.to_dict()}, indent=1))
        fd = FactoryData.from_dict(body["data"])
        back = await run(svc.dev.write_factory, fd)
        return {"ok": True, "backup": str(bk), "data": back.to_dict()}

    @app.post("/api/lcd")
    async def lcd(body: dict):
        if body.get("confirm") != "LCD":
            raise DeviceError(tr("confirmação necessária"))
        return await run(svc.dev.set_lcd_type, int(body["type"]))

    @app.post("/api/raw")
    async def raw(body: dict):
        data = bytes.fromhex(body["hex"].replace("0x", "").replace(",", " "))
        resp = await run(svc.dev.raw, data, float(body.get("wait", 0.25)))
        return {"sent": data.hex(" "), "received": resp.hex(" "), "ascii": resp.decode("latin-1")}

    # ---------------------------------------------------------------- dados
    @app.get("/api/history")
    async def history(seconds: float = 600):
        return svc.rec.window(seconds)

    @app.get("/api/stats")
    async def stats(seconds: float = 60):
        return svc.rec.stats(seconds)

    @app.post("/api/counters/reset")
    async def counters_reset():
        svc.rec.reset_counters()
        return svc.rec.counters()

    @app.post("/api/history/clear")
    async def history_clear():
        svc.rec.clear()
        return {"ok": True}

    @app.post("/api/mark")
    async def mark(body: dict):
        return svc.rec.mark(body.get("label") or tr("marcador"))

    @app.post("/api/csv/{action}")
    async def csv_log(action: str):
        if action == "start":
            return {"path": svc.rec.start_csv(LOG_DIR)}
        return {"path": svc.rec.stop_csv()}

    @app.get("/api/export.csv")
    async def export_csv(seconds: float | None = None):
        return PlainTextResponse(svc.rec.export_csv(seconds), media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="pdpower_{time.strftime("%Y%m%d_%H%M%S")}.csv"'})

    @app.post("/api/export")
    async def export_file(body: dict):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"export_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path.write_text(svc.rec.export_csv(body.get("seconds")), encoding="utf-8")
        return {"path": str(path)}

    @app.post("/api/save_file")
    async def save_file(body: dict):
        name = re.sub(r"[^\w.\-]", "_", Path(str(body["name"])).name)[:120] or "arquivo.txt"
        if not name.lower().endswith((".csv", ".json", ".txt")):
            name += ".txt"
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / name
        path.write_text(str(body["content"]), encoding="utf-8")
        return {"path": str(path)}

    # ---------------------------------------------------------------- automação
    @app.post("/api/automation/run")
    async def auto_run(body: dict):
        return await run(svc.auto.start, body["sequence"])

    @app.post("/api/automation/stop")
    async def auto_stop():
        svc.auto.stop()
        return {"ok": True}

    @app.get("/api/automation")
    async def auto_status():
        return {"status": svc.auto.status, "results": svc.auto.results}

    @app.post("/api/automation/validate")
    async def auto_validate(body: dict):
        return {"steps": svc.auto.validate(body["sequence"])}

    @app.get("/api/sequences")
    async def seq_list():
        return svc.cfg.get("sequences", {})

    @app.post("/api/sequences")
    async def seq_save(body: dict):
        name = body["name"].strip()
        if not name:
            raise ValueError(tr("nome vazio"))
        if body.get("delete"):
            svc.cfg["sequences"].pop(name, None)
        else:
            svc.auto.validate(body["sequence"])
            svc.cfg["sequences"][name] = body["sequence"]
        svc.save()
        return svc.cfg["sequences"]

    # ---------------------------------------------------------------- config
    @app.get("/api/config")
    async def cfg_get():
        return {**svc.cfg, "paths": {"config": str(CONFIG_DIR), "logs": str(LOG_DIR)}}

    @app.post("/api/config")
    async def cfg_set(body: dict):
        return svc.update_cfg(body)

    # ---------------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        seq = svc.rec.seq
        log_id = max(0, svc.auto.log_seq - 50)
        mk_seen = svc.rec.markers[-1]["t"] if svc.rec.markers else 0.0
        try:
            while True:
                seq, rows = svc.rec.since_seq(seq)
                logs = svc.auto.logs_since(log_id)
                if logs:
                    log_id = logs[-1]["id"]
                new_marks = [m for m in list(svc.rec.markers) if m["t"] > mk_seen]
                if new_marks:
                    mk_seen = new_marks[-1]["t"]
                await sock.send_text(json.dumps({"state": svc.full_state(), "samples": rows, "logs": logs,
                                                 "markers": new_marks}))
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, RuntimeError):
            pass
        except Exception:
            log.exception("ws")

    app.router.routes.extend(mcp_app.routes)
    return app
