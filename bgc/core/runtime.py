"""Un serveur surveillé à l'exécution (sans Tk) : configuration, contrôleur, journal, instantané.

Partagé par l'interface, le tray et le serveur MCP (découpage core/ui du 16 nov 2026).
"""

from __future__ import annotations

import time
from collections import deque
from datetime import datetime

from bgc import applog, i18n, service
from bgc import presentation as pres
from bgc import sysinfo


class ServerRuntime:
    """Un serveur surveillé : configuration + contrôleur + journal + dernier instantané."""

    def __init__(self, app, server: dict):
        self.app = app
        self.server = server
        self.type = service.get(server.get("type", ""))
        self.log_lines: deque[tuple[str, str]] = deque(maxlen=400)
        self.snapshot_data: dict | None = None
        self.busy = False
        self.last_phase: str | None = None
        # Petites statistiques depuis le lancement de l'app (le « petit historique ») :
        # chutes, démarrages, instant du dernier passage en marche.
        self.stats = {"falls": 0, "starts": 0, "since": 0.0, "fell": False}
        # Date limite pendant laquelle un arrêt est *attendu* (arrêt demandé depuis l'app) :
        # une transition vers « arrêté » dans cette fenêtre ne déclenche aucune alerte.
        self.expect_stop_until = 0.0
        # Boutons sur mesure : leur état réel (l'interface web répond-elle ?). Un bouton qui
        # ouvre une page morte est un piège : il est grisé tant que la cible ne répond pas
        # (10 nov 2026, demande « piloter les interfaces web »).
        self.custom_states: dict[int, bool] = {}
        self._custom_probe: dict[str, tuple[float, bool]] = {}
        self.controller = self.type.controller(server, self.log_key) if self.type else None
        hook = getattr(self.controller, "adopt", None)
        if hook:  # un serveur déjà lancé hors de l'app est adopté (affiché, non tué)
            try:
                hook()
            except Exception:
                pass

    # --- journal (appelable depuis n'importe quel fil) ---------------------
    def log_key(self, key: str, **params) -> None:
        """On garde la CLÉ et les paramètres, pas le texte : le journal suit la langue."""
        self.log_lines.append((datetime.now().strftime("%H:%M:%S"), key, params))
        try:  # copie dans le fichier journal (revue du 16 nov 2026)
            applog.LOGGER.info("[%s] %s", self.server.get("id", "?"), i18n.t(key, **params))
        except Exception:
            pass

    # --- état -------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.server["id"]

    @property
    def label(self) -> str:
        return str(self.server.get("label") or self.server.get("type"))

    @property
    def phase(self) -> str:
        return (self.snapshot_data or {}).get("phase", "unknown")

    @property
    def enabled(self) -> bool:
        """Tuile en pause = ni sondée, ni affichée comme en marche."""
        return bool(self.server.get("enabled", True))

    @property
    def state(self) -> str:
        """État d'affichage (vert/ambre/rouge/gris/pause) — voir `presentation.badge_state`."""
        return pres.badge_state(self.snapshot_data, self.enabled, bool(self.stats.get("fell")))

    def status_text(self) -> str:
        key, params = pres.status_label(self.type, self.snapshot_data, self.enabled,
                                        bool(self.stats.get("fell")))
        return i18n.t(key, **params)

    def refresh(self) -> None:
        if not self.enabled:
            # Tuile en pause : on ne sonde rien (ni port, ni process, ni CLI) et on le dit.
            self.snapshot_data = {"phase": "unknown", "rows": [], "actions": [], "toggles": [],
                                  "url": "", "meta": {}, "extra": {"readonly": True,
                                                                   "paused": True},
                                  "tooltip": {"state_key": "status.paused"}}
            self.custom_states = {}
            self.last_phase = "unknown"
            return
        if not self.controller:
            return
        try:
            self.snapshot_data = self.controller.snapshot()
        except Exception as exc:  # pragma: no cover
            applog.LOGGER.exception("snapshot en échec : %s", self.id)
            self.snapshot_data = {"phase": "unknown", "rows": [], "actions": [], "toggles": [],
                                  "url": "", "meta": {}, "extra": {},
                                  "error": str(exc)}
        self._probe_custom_actions()
        phase = self.phase
        if self.last_phase is not None and phase != self.last_phase:
            keys = getattr(self.type, "phase_keys", None) or {}
            params = self.server.get("params") or {}
            key = keys.get(phase) or {"running": "log.phase_running",
                                      "starting": "log.phase_starting",
                                      "stopped": "log.phase_stopped"}.get(phase,
                                                                          "log.loading_state")
            self.log_key(key, port=params.get("port", "?"),
                         name=params.get("distro") or self.label, label=self.label)
        self.last_phase = phase
        hook = getattr(self.controller, "maybe_auto_desktop", None)
        if hook:
            try:
                hook(phase)
            except Exception:
                applog.LOGGER.exception("auto-desktop en échec : %s", self.id)
        autostart = getattr(self.controller, "maybe_auto_start", None)
        if autostart:
            try:
                autostart(phase)
            except Exception:
                applog.LOGGER.exception("auto-start en échec : %s", self.id)

    def set_option(self, key: str, value: bool) -> None:
        self.server.setdefault("options", {})[key] = bool(value)
        if self.controller:
            self.controller.set_option(key, value)
        self.app.cfg.update_server(self.id, options={key: bool(value)})

    # --- boutons sur mesure : l'interface web répond-elle ? ----------------
    #: Durée de validité d'une sonde de bouton (s). Les interfaces changent d'état avec le
    #: service : inutile de re-sonder à chaque cycle de 2 s, inutile d'attendre 30 s.
    CUSTOM_PROBE_TTL = 5.0

    def _probe_custom_actions(self) -> None:
        """État de chaque bouton sur mesure : actif si sa sonde répond.

        Trois cas, dans cet ordre :
        1. `check` déclaré (`proc:LM Studio.exe`, `port:1234`, `hôte:port`, URL) → c'est lui
           qui décide. Un bouton « Fermer » grisé quand l'application ne tourne pas ;
        2. cible qui est une adresse web → on sonde la cible elle-même ;
        3. cible qui est une commande → toujours actif (rien à sonder).

        Un cache court évite de payer un délai d'attente à chaque cycle quand la cible est
        arrêtée.
        """
        states: dict[int, bool] = {}
        now = time.time()
        for index, item in enumerate(self.server.get("actions") or []):
            target = str(item.get("target") or "").strip()
            if not target:
                continue
            probe = str(item.get("check") or "").strip()
            if probe:
                kind, _, value = probe.partition(":")
                if kind.lower() == "proc":
                    states[index] = sysinfo.process_running(value) is not None
                    continue
                if kind.lower() in ("port", "host", "url"):
                    probe = value.strip()   # « port:1234 » → on sonde 1234
                if not probe:
                    states[index] = True
                    continue
            else:
                if not target.lower().startswith(("http://", "https://")):
                    states[index] = True          # commande : rien à vérifier
                    continue
                probe = target
            hit = self._custom_probe.get(probe)
            if hit is None or (now - hit[0]) > self.CUSTOM_PROBE_TTL:
                hit = (now, self._probe_up(probe))
                self._custom_probe[probe] = hit
            states[index] = hit[1]
        self.custom_states = states

    @staticmethod
    def _probe_up(probe: str) -> bool:
        """La sonde d'un bouton répond-elle ? (URL, `hôte:port` ou port nu)."""
        if probe.lower().startswith(("http://", "https://")):
            return bool(sysinfo.http_probe(probe, timeout=1.2).get("up"))
        host, port = "127.0.0.1", 0
        if ":" in probe:
            left, _, right = probe.rpartition(":")
            host, port = left.strip() or host, right
        else:
            port = probe
        try:
            number = int(port)
        except ValueError:
            return False
        if host in ("127.0.0.1", "localhost", "::1", ""):
            return sysinfo.listener_pid(number) is not None
        return sysinfo.tcp_open(host, number)
