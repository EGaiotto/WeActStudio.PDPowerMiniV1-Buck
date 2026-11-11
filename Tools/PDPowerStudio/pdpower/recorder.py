"""Histórico de amostras, contadores de energia e log CSV."""
from __future__ import annotations

import csv
import threading
import time
from bisect import bisect_left
from collections import deque
from datetime import datetime
from pathlib import Path


class Recorder:
    def __init__(self, maxlen: int = 400_000):
        self._lock = threading.Lock()
        self._t: deque[float] = deque(maxlen=maxlen)
        self._v: deque[float] = deque(maxlen=maxlen)
        self._i: deque[float] = deque(maxlen=maxlen)
        self._on: deque[int] = deque(maxlen=maxlen)
        self._mode: deque[str] = deque(maxlen=maxlen)
        self.seq = 0                    # nº total de amostras já recebidas
        self.wh = 0.0
        self.ah = 0.0
        self.on_time = 0.0
        self.counters_since = time.time()
        self._last_t: float | None = None
        self._csv = None
        self._csv_writer = None
        self.csv_path: str | None = None
        self.markers: deque[dict] = deque(maxlen=500)

    # ------------------------------------------------------------- entrada
    def add(self, t: float, v: float, i: float, on: bool, mode: str):
        with self._lock:
            if self._last_t is not None and on:
                dt = t - self._last_t
                if 0 < dt < 2.0:
                    self.wh += v * i * dt / 3600
                    self.ah += i * dt / 3600
                    self.on_time += dt
            self._last_t = t
            self._t.append(t)
            self._v.append(v)
            self._i.append(i)
            self._on.append(1 if on else 0)
            self._mode.append(mode)
            self.seq += 1
            if self._csv_writer:
                self._csv_writer.writerow([f"{t:.3f}", datetime.fromtimestamp(t).isoformat(timespec="milliseconds"),
                                           f"{v:.3f}", f"{i:.3f}", f"{v * i:.4f}", int(on), mode,
                                           f"{self.wh:.6f}", f"{self.ah * 1000:.3f}"])

    def mark(self, label: str):
        with self._lock:
            m = {"t": time.time(), "label": label}
            self.markers.append(m)
            return m

    def reset_counters(self):
        with self._lock:
            self.wh = self.ah = self.on_time = 0.0
            self.counters_since = time.time()

    def clear(self):
        with self._lock:
            for d in (self._t, self._v, self._i, self._on, self._mode):
                d.clear()
            self.markers.clear()

    def counters(self) -> dict:
        return {"wh": round(self.wh, 6), "mah": round(self.ah * 1000, 3), "on_time": round(self.on_time, 1),
                "since": self.counters_since}

    # ------------------------------------------------------------- consulta
    def since_seq(self, seq: int, limit: int = 5000) -> tuple[int, list[list]]:
        """Amostras com número de sequência > seq (para streaming)."""
        with self._lock:
            n = len(self._t)
            first_seq = self.seq - n
            start = max(seq - first_seq, 0, n - limit)
            rows = [[self._t[k], self._v[k], self._i[k], self._on[k], self._mode[k]] for k in range(start, n)]
            return self.seq, rows

    def window(self, seconds: float | None = None, since: float | None = None) -> dict:
        with self._lock:
            t = list(self._t)
            if not t:
                return {"t": [], "v": [], "i": [], "p": [], "on": [], "mode": []}
            t0 = since if since is not None else (t[-1] - seconds if seconds else t[0])
            k = bisect_left(t, t0)
            v, i = list(self._v)[k:], list(self._i)[k:]
            return {"t": t[k:], "v": v, "i": i, "p": [round(a * b, 4) for a, b in zip(v, i)],
                    "on": list(self._on)[k:], "mode": list(self._mode)[k:]}

    def downsampled(self, seconds: float, max_points: int = 200) -> dict:
        w = self.window(seconds)
        n = len(w["t"])
        if n <= max_points or max_points <= 0:
            return {k: w[k] for k in ("t", "v", "i", "p")}
        step = n / max_points
        out = {"t": [], "v": [], "i": [], "p": []}
        for b in range(max_points):
            a, z = int(b * step), max(int((b + 1) * step), int(b * step) + 1)
            for key in out:
                seg = w[key][a:z]
                out[key].append(round(sum(seg) / len(seg), 4))
        return out

    def stats(self, seconds: float | None = 60) -> dict:
        w = self.window(seconds)
        res = {"n": len(w["t"]), "seconds": seconds}
        for key in ("v", "i", "p"):
            arr = w[key]
            if arr:
                avg = sum(arr) / len(arr)
                rms_dev = (sum((x - avg) ** 2 for x in arr) / len(arr)) ** 0.5
                res[key] = {"min": round(min(arr), 4), "max": round(max(arr), 4),
                            "avg": round(avg, 4), "std": round(rms_dev, 5),
                            "pp": round(max(arr) - min(arr), 4)}
            else:
                res[key] = None
        return res

    # ------------------------------------------------------------- CSV
    def start_csv(self, folder: Path) -> str:
        self.stop_csv()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"pdpower_{datetime.now():%Y%m%d_%H%M%S}.csv"
        f = open(path, "w", newline="", encoding="utf-8")
        w = csv.writer(f)
        w.writerow(["epoch_s", "timestamp", "voltage_V", "current_A", "power_W", "output_on", "mode",
                    "energy_Wh", "charge_mAh"])
        with self._lock:
            self._csv, self._csv_writer, self.csv_path = f, w, str(path)
        return str(path)

    def stop_csv(self) -> str | None:
        with self._lock:
            f, path = self._csv, self.csv_path
            self._csv = self._csv_writer = None
            self.csv_path = None
        if f:
            f.close()
        return path

    def export_csv(self, seconds: float | None = None) -> str:
        w = self.window(seconds)
        lines = ["epoch_s,timestamp,voltage_V,current_A,power_W,output_on,mode"]
        for k in range(len(w["t"])):
            t = w["t"][k]
            lines.append(f"{t:.3f},{datetime.fromtimestamp(t).isoformat(timespec='milliseconds')},"
                         f"{w['v'][k]:.3f},{w['i'][k]:.3f},{w['p'][k]:.4f},{w['on'][k]},{w['mode'][k]}")
        return "\n".join(lines) + "\n"
