"""Ressources consommées par service : CPU %, RAM, VRAM (arbre de processus).

Un fil dédié échantillonne toutes les ~4 s (jamais le fil Tk) et publie un instantané
partagé : `latest(clé)` et `history(clé)`. Clé = id de tuile, ou « id/élément » pour un
élément (service Windows, conteneur…). Les PID viennent de ce que les sondes trouvent déjà
(`meta.pid` / `meta.pids` de l'instantané, `pid` / `pids` d'un élément).

* CPU : `psutil.Process.cpu_percent` — le premier appel rend 0, on garde donc les objets
  Process d'un passage à l'autre ; normalisé sur le nombre de cœurs (100 % = machine pleine).
* RAM : somme des working sets (rss) de l'arbre.
* VRAM : compteurs Windows « GPU Process Memory » (voir `bgc/gpu.py`), lus à cadence plus
  lente ; None (« — ») s'ils sont indisponibles.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Callable, Iterable

import psutil

INTERVAL = 4.0
GPU_EVERY = 2               # VRAM tous les 2 passages (~8 s)
HISTORY = 75                # 5 min à 4 s
MB = 1024 * 1024


def _pids(value) -> list[int]:
    out = []
    for pid in value if isinstance(value, (list, tuple)) else [value]:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in out:
            out.append(pid)
    return out


def snapshot_pids(snapshot: dict | None) -> list[int]:
    meta = (snapshot or {}).get("meta") or {}
    return _pids(list(_pids(meta.get("pids") or [])) + _pids(meta.get("pid")))


def item_pids(item: dict | None) -> list[int]:
    item = item or {}
    return _pids(list(_pids(item.get("pids") or [])) + _pids(item.get("pid")))


class ProcessCache:
    """Objets psutil.Process conservés (cpu_percent a besoin du passage précédent)."""

    def __init__(self):
        self._procs: dict[int, psutil.Process] = {}
        self.seen: set[int] = set()
        self._usage: dict[int, tuple[float, int]] = {}

    def get(self, pid: int) -> psutil.Process | None:
        self.seen.add(pid)
        proc = self._procs.get(pid)
        try:
            if proc is not None and proc.is_running():   # compare aussi l'heure de création
                return proc
            proc = psutil.Process(pid)
            proc.cpu_percent(None)                       # amorce : le prochain appel compte
        except psutil.Error:
            self._procs.pop(pid, None)
            return None
        self._procs[pid] = proc
        return proc

    def usage(self, proc: psutil.Process) -> tuple[float, int] | None:
        """(cpu %, rss) d'un processus, mesuré **une fois par passage** : un même PID compté
        par la tuile et par son élément ne doit pas relancer cpu_percent (qui rendrait ~0)."""
        if proc.pid not in self._usage:
            try:
                self._usage[proc.pid] = (proc.cpu_percent(None), proc.memory_info().rss)
            except psutil.Error:
                return None
        return self._usage[proc.pid]

    def prune(self) -> None:
        """Fin de passage : oublie les processus disparus et les mesures du passage."""
        for pid in [p for p in self._procs if p not in self.seen]:
            del self._procs[pid]
        self.seen = set()
        self._usage = {}


def tree(cache: ProcessCache, root: int) -> list[psutil.Process]:
    proc = cache.get(root)
    if proc is None:
        return []
    if root == os.getpid():
        # Pont intégré à BGC : ne pas compter les enfants (ce sont les autres services).
        return [proc]
    try:
        children = proc.children(recursive=True)
    except psutil.Error:
        children = []
    out = [proc]
    for child in children:
        cached = cache.get(child.pid)
        if cached is not None:
            out.append(cached)
    return out


def measure(cache: ProcessCache, roots: Iterable[int], vram: dict[int, int] | None,
            cpus: int) -> dict | None:
    procs: dict[int, psutil.Process] = {}
    for root in roots:
        for proc in tree(cache, root):
            procs.setdefault(proc.pid, proc)
    if not procs:
        return None
    cpu, rss = 0.0, 0
    for proc in procs.values():
        usage = cache.usage(proc)
        if usage is not None:
            cpu += usage[0]
            rss += usage[1]
    return {
        "cpu": round(cpu / max(1, cpus), 1),
        "ram_mb": round(rss / MB, 1),
        "vram_mb": (round(sum(vram.get(pid, 0) for pid in procs) / MB, 1)
                    if vram is not None else None),
        "procs": len(procs),
    }


class MetricsSampler:
    """`targets()` (appelé depuis le fil d'échantillonnage) → [(clé, [pid…]), …]."""

    def __init__(self, targets: Callable[[], list[tuple[str, list[int]]]],
                 interval: float = INTERVAL, gpu=None):
        self._targets = targets
        self.interval = interval
        self._gpu = gpu
        self._gpu_wanted = gpu is None
        self._cache = ProcessCache()
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        self._history: dict[str, deque] = {}
        self._gpu_total: dict | None = None
        self._vram: dict[int, int] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._passes = 0
        self.cpus = psutil.cpu_count() or 1

    # --- fil ---------------------------------------------------------------
    def start(self) -> "MetricsSampler":
        self._thread = threading.Thread(target=self._run, name="bgc-metrics", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._gpu is not None:
            try:
                self._gpu.close()
            except Exception:
                pass

    def _run(self) -> None:
        if self._gpu_wanted:
            from bgc.gpu import GpuCounters, total_vram_bytes
            self._gpu = GpuCounters()
            self._capacity = total_vram_bytes() if self._gpu.available else None
        else:
            self._capacity = None
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                pass                       # une mesure ratée ne doit jamais tuer le fil
            self._stop.wait(self.interval)

    # --- un passage --------------------------------------------------------
    def sample_once(self) -> None:
        if self._gpu is not None and self._passes % GPU_EVERY == 0:
            sample = self._gpu.sample()
            if sample is None:
                self._vram, total = None, None
            else:
                self._vram, used = sample
                capacity = getattr(self, "_capacity", None)
                total = {"used_mb": round(used / MB, 1),
                         "total_mb": round(capacity / MB, 1) if capacity else None}
            with self._lock:
                self._gpu_total = total
        self._passes += 1
        now = time.time()
        latest: dict[str, dict] = {}
        for key, pids in self._targets():
            if not pids:
                continue
            value = measure(self._cache, pids, self._vram, self.cpus)
            if value is not None:
                value["at"] = now
                latest[key] = value
        self._cache.prune()
        with self._lock:
            for key, value in latest.items():
                self._history.setdefault(key, deque(maxlen=HISTORY)).append(
                    (now, value["cpu"], value["ram_mb"], value["vram_mb"]))
            for key in [k for k in self._history if k not in latest]:
                del self._history[key]
            self._latest = latest

    # --- lecture (n'importe quel fil) ---------------------------------------
    def latest(self, key: str) -> dict | None:
        with self._lock:
            value = self._latest.get(key)
            return dict(value) if value else None

    def history(self, key: str) -> list[tuple]:
        with self._lock:
            return list(self._history.get(key) or ())

    def gpu_total(self) -> dict | None:
        with self._lock:
            return dict(self._gpu_total) if self._gpu_total else None


def item_key(runtime_id: str, item_id: str) -> str:
    return f"{runtime_id}/{item_id}"
