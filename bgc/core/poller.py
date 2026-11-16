"""Sondage en tâche de fond : un fil par tuile, au plus une sonde en vol par tuile.

Sorti de `background_check.App` (découpage core/ui du 16 nov 2026) : le sondeur ne connaît
ni Tk ni le tray. Il appelle `runtime.refresh()` puis `on_probed(runtime, before)` ; l'hôte
décide quoi faire d'une transition (journal, bulle « tombé »…), voir `transition()`.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from bgc import applog


class Poller:
    """Un fil de sondage **par tuile** : une sonde lente ne retarde plus les autres.

    Le cycle était séquentiel : un `wsl.exe` bloqué ou une sonde HTTP qui expire retardait
    la peinture de toutes les tuiles. Un pool fixe (au lieu d'un fil neuf par tuile et par
    cycle) ; une tuile dont la sonde précédente traîne encore est sautée, pas empilée.
    """

    def __init__(self, get_runtimes, on_probed=None, interval: float = 1.6, workers: int = 12):
        self._get_runtimes = get_runtimes
        self._on_probed = on_probed
        self.interval = interval
        self._stop = threading.Event()
        self._in_flight: dict[str, bool] = {}
        self._guard = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="probe")
        self._thread = threading.Thread(target=self._loop, name="poller", daemon=True)

    def start(self) -> "Poller":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._pool.shutdown(wait=False, cancel_futures=True)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def probe_now(self, runtime) -> None:
        """Sonde une tuile tout de suite, dans le fil appelant (tests, actions)."""
        self._probe(runtime, runtime.last_phase, track=False)

    def _probe(self, runtime, before, track: bool = True) -> None:
        try:
            runtime.refresh()
            if self._on_probed is not None:
                self._on_probed(runtime, before)
        except Exception:  # pragma: no cover — une sonde fautive ne tue pas le cycle
            applog.LOGGER.exception("sonde en échec : %s", getattr(runtime, "id", "?"))
        finally:
            if track:
                with self._guard:
                    self._in_flight.pop(runtime.id, None)

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            for runtime in list(self._get_runtimes()):
                if self._stop.is_set():
                    break
                with self._guard:
                    if runtime.id in self._in_flight:
                        continue  # la sonde précédente traîne : on ne l'empile pas
                    self._in_flight[runtime.id] = True
                # refresh() renvoie l'état « en pause » sans rien sonder : on l'appelle
                # toujours, sinon une tuile mise en pause garderait son ancien panneau.
                try:
                    self._pool.submit(self._probe, runtime, runtime.last_phase)
                except Exception:  # pragma: no cover (pool fermé à la sortie)
                    with self._guard:
                        self._in_flight.pop(runtime.id, None)
            self._stop.wait(max(0.5, self.interval - (time.time() - started)))


def transition(runtime, before: str | None, after: str, now: float | None = None) -> str | None:
    """Petit historique par tuile ; renvoie "fall" quand un service en marche tombe tout seul.

    Une chute dans la fenêtre `expect_stop_until` (arrêt demandé depuis l'app, le tray ou le
    MCP) n'est pas une chute.
    """
    if before == after:
        return None
    now = time.time() if now is None else now
    if after == "running":
        runtime.stats["starts"] += 1
        runtime.stats["since"] = now
        runtime.stats["fell"] = False
        return None
    if before != "running":
        return None  # jamais tourné : pas une chute
    runtime.stats["falls"] += 1
    if now < runtime.expect_stop_until:
        return None
    runtime.stats["fell"] = True  # pastille rouge « tombé » jusqu'au prochain démarrage
    return "fall"
