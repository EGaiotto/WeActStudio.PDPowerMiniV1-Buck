"""Ponto de entrada do PDPower Studio.

  python -m pdpower.app              # janela nativa + servidor (MCP em http://127.0.0.1:8765/mcp)
  python -m pdpower.app --headless   # só servidor/MCP (abra a interface no navegador)
  python -m pdpower.app --browser    # servidor + navegador padrão
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn


def _port_busy(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _wait_up(url: str, timeout: float = 15) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url + "/api/state", timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def _ensure_streams():
    """Com pythonw não há console: stdout/stderr são None e qualquer print derruba o app."""
    if sys.stdout is None or sys.stderr is None:
        from .service import CONFIG_DIR
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        f = open(CONFIG_DIR / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def main(argv=None):
    _ensure_streams()
    ap = argparse.ArgumentParser(description="PDPower Studio — controle da WeAct PDPowerMini")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=8765)
    ap.add_argument("--headless", action="store_true", help="sem janela; só servidor HTTP/MCP")
    ap.add_argument("--browser", action="store_true", help="abrir no navegador padrão em vez da janela nativa")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = f"http://{a.host}:{a.http_port}"

    server = None
    if _port_busy(a.host, a.http_port):
        print(f"PDPower Studio já está rodando em {url} — abrindo a interface.")
        if a.headless:
            return 0
    else:
        from .server import create_app
        cfg = uvicorn.Config(create_app(), host=a.host, port=a.http_port, log_level="warning", ws_ping_interval=20)
        server = uvicorn.Server(cfg)
        if a.headless:
            print(f"PDPower Studio (headless) em {url}  •  MCP: {url}/mcp")
            server.run()
            return 0
        threading.Thread(target=server.run, daemon=True, name="uvicorn").start()
        if not _wait_up(url):
            print("Servidor não subiu", file=sys.stderr)
            return 1
        print(f"PDPower Studio em {url}  •  MCP: {url}/mcp")

    if a.browser:
        webbrowser.open(url)
        try:
            while server is None or not server.should_exit:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    else:
        try:
            import webview
            w, h, maximized = 1500, 940, False
            try:   # cabe na tela (considerando escala de DPI e barra de tarefas)
                sc = webview.screens[0]
                if sc.width < w + 60 or sc.height < h + 100:
                    w, h, maximized = int(sc.width * 0.9), int(sc.height * 0.88), True
            except Exception:
                pass
            webview.create_window("PDPower Studio", url, width=w, height=h, min_size=(1000, 640),
                                  background_color="#0b0f17", maximized=maximized)
            webview.start(gui="edgechromium" if sys.platform == "win32" else None)
        except Exception as e:
            print(f"Janela nativa indisponível ({e}); abrindo navegador.")
            webbrowser.open(url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
    if server:
        server.should_exit = True
        time.sleep(0.5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
