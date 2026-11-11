"""Une dispositivo, gravador, automação e configuração persistente (instância única)."""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from .automation import Automation
from .device import Device
from .i18n import normalize, set_lang, system_lang, tr
from .recorder import Recorder

log = logging.getLogger("pdpower")

CONFIG_DIR = Path(os.environ.get("PDPOWER_CONFIG_DIR") or Path(os.environ.get("APPDATA", Path.home())) / "PDPowerStudio")
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_DIR = Path(os.environ.get("PDPOWER_LOG_DIR") or Path.home() / "Documents" / "PDPowerStudio")

DEFAULT_CONFIG = {
    "connection": {"port": None, "link": "usb", "baud": 115200, "simulate": False, "autoconnect": True},
    "limits": {"v_max": None, "i_max": None},
    "poll_interval": 0.1,
    "guards_enabled": True,
    "guards": [
        {"cond": "p > 45", "action": "off", "msg": "Potência acima de 45 W", "enabled": False},
    ],
    "sequences": {},
    "mcp": {"allow_write": True},
    "ui": {"lang": None},   # None = segue o idioma do sistema (detectado pela interface)
}


class Service:
    def __init__(self):
        self.lock = threading.Lock()
        self.cfg = self._load()
        self.dev = Device()
        self.rec = Recorder()
        self.auto = Automation(self.dev, self.rec)
        self.dev.sample_listeners.append(self.rec.add)
        self.dev.event_listeners.append(lambda lvl, msg: self.auto.log(lvl, msg))
        self.apply_cfg()
        if not CONFIG_FILE.exists():   # 1ª execução: guarda de exemplo no idioma do sistema
            for g in self.cfg.get("guards", []):
                g["msg"] = tr(g.get("msg", ""))

    # ------------------------------------------------------------------ config
    def _load(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            if CONFIG_FILE.exists():
                user = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for k, v in user.items():
                    if isinstance(v, dict) and isinstance(cfg.get(k), dict) and k != "sequences":
                        cfg[k].update(v)
                    else:
                        cfg[k] = v
        except Exception as e:
            log.warning("config inválida: %s", e)
        return cfg

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_FILE)

    def apply_cfg(self):
        self.dev.limits = dict(self.cfg["limits"])
        self.dev.poll_interval = float(self.cfg.get("poll_interval", 0.1))
        self.auto.set_global_guards(self.cfg.get("guards", []), self.cfg.get("guards_enabled", True))
        set_lang(self.cfg.get("ui", {}).get("lang") or system_lang())

    def update_cfg(self, patch: dict) -> dict:
        allowed = {"limits", "poll_interval", "guards", "guards_enabled", "connection", "mcp", "ui"}
        for k, v in patch.items():
            if k not in allowed:
                raise ValueError(tr("chave de config não permitida: {k}", k=k))
            if isinstance(v, dict) and isinstance(self.cfg.get(k), dict):
                self.cfg[k].update(v)
            else:
                self.cfg[k] = v
        lang = (self.cfg.get("ui") or {}).get("lang")
        if lang and not normalize(lang):
            raise ValueError(tr("idioma não suportado: {lang}", lang=lang))
        if lang:
            self.cfg["ui"]["lang"] = normalize(lang)
        lim = self.cfg["limits"]
        for key in ("v_max", "i_max"):
            if lim.get(key) in ("", 0):
                lim[key] = None
        pi = float(self.cfg.get("poll_interval", 0.1))
        self.cfg["poll_interval"] = min(max(pi, 0.05), 2.0)
        self.apply_cfg()
        self.save()
        return self.cfg

    # ------------------------------------------------------------------ conexão
    def connect(self, port=None, link="usb", baud=115200, simulate=False):
        st = self.dev.connect(port=port, link=link, baud=baud, simulate=simulate)
        self.cfg["connection"].update(port=None if simulate else st["port"], link=link, baud=baud, simulate=simulate)
        self.save()
        return st

    def autoconnect(self):
        c = self.cfg["connection"]
        if not c.get("autoconnect", True):
            return
        try:
            self.dev.connect(port=None if c.get("simulate") else c.get("port"), link=c.get("link", "usb"),
                             baud=c.get("baud", 115200), simulate=c.get("simulate", False))
        except Exception as e:
            if c.get("port") and not c.get("simulate"):
                try:   # porta pode ter mudado de número
                    self.dev.connect(port=None, link=c.get("link", "usb"), baud=c.get("baud", 115200))
                    return
                except Exception:
                    pass
            log.info("autoconexão falhou: %s", e)
            self.auto.log("warn", tr("Autoconexão falhou: {e}", e=e))

    def full_state(self) -> dict:
        s = self.dev.snapshot()
        s["counters"] = self.rec.counters()
        s["automation"] = dict(self.auto.status)
        s["guards"] = {"enabled": self.cfg.get("guards_enabled", True), "list": self.cfg.get("guards", [])}
        s["csv"] = self.rec.csv_path
        return s


_service: Service | None = None


def get_service() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service
