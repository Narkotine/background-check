"""Pont STT (GPU) — expose l'API OpenAI et relaie vers whisper.cpp en Vulkan.

Le pendant « utilisable par Hermes » de ce que fait OpenWhispr : le même modèle turbo, mais sur
le GPU (Vulkan) au lieu du CPU. Sur cette machine : 0,55 s pour 8,7 s d'audio, contre 11,2 s avec
faster-whisper en CPU.

Ce que ce type de serveur fait, et ne fait pas
----------------------------------------------
* Il démarre/arrête **un fil dans Background Check** (`hermesserv.sttbridge`) : aucun sous-processus,
  rien qui survive à l'app, aucun port laissé ouvert après un arrêt propre.
* Il ne lance **jamais** OpenWhispr ni son serveur whisper : il s'y connecte (`upstream`). Si
  l'amont est fermé, la ligne passe en ambre et le journal le dit — c'est un état, pas une panne.
* Il refuse d'écraser un autre process qui tiendrait déjà le port : la règle du socle
  (« je n'arrête que ce que j'ai lancé ») vaut aussi pour l'écoute.
"""

from __future__ import annotations

import os
import time

from .. import sttbridge, sysinfo
from ..service import (Controller, Field, PHASE_RUNNING, PHASE_STARTING, PHASE_STOPPED,
                       ROW_IDLE, ROW_OK, ROW_WARN, ServerType, Toggle, register)


class SttBridgeController(Controller):

    def __init__(self, server: dict, log) -> None:
        super().__init__(server, log)
        self._bridge = sttbridge.SttBridge()
        self._started = False          # ce pont a-t-il été démarré par nous ?
        self._auto_tried = False       # une seule tentative d'auto-démarrage par session
        self._cache: dict[str, tuple[float, object]] = {}

    # --- cache de sonde (l'amont ne change pas d'une seconde à l'autre) ---
    def _cached(self, key: str, fn, ttl: float = 5.0):
        now = time.time()
        hit = self._cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def invalidate(self) -> None:
        self._cache.clear()

    # --- réglages ---------------------------------------------------------
    @property
    def host(self) -> str:
        return str(self.params.get("host") or sttbridge.DEFAULT_HOST)

    @property
    def port(self) -> int:
        try:
            return int(self.params.get("port") or sttbridge.DEFAULT_PORT)
        except (TypeError, ValueError):
            return sttbridge.DEFAULT_PORT

    @property
    def upstream(self) -> str:
        return str(self.params.get("upstream") or sttbridge.DEFAULT_UPSTREAM)

    # --- sondage ----------------------------------------------------------
    def dependencies(self) -> list[dict]:
        """Le serveur whisper (OpenWhispr) vers lequel le pont relaie : surveillé, jamais
        lancé par nous."""
        return [{"id": "upstream", "label_key": "dep.stt_upstream", "target": self.upstream}]

    def snapshot(self) -> dict:
        ours = self._bridge.running
        pid = sysinfo.listener_pid(self.port) if self.port else None
        external = bool(pid) and not ours
        upstream = self._cached(f"upstream:{self.upstream}",
                                lambda: sttbridge.probe_upstream(self.upstream))

        # Un process tiers (pid 0 = propriétaire inconnu côté noyau) ne compte pas comme
        # « notre » pont : on affiche l'avertissement, on ne prétend pas servir.
        if ours:
            phase = PHASE_RUNNING
        else:
            phase = PHASE_STOPPED
        if ours and not upstream["up"]:
            phase = PHASE_STARTING      # le pont écoute, mais il ne peut rien transcrire

        stats = self._bridge.stats.snapshot()
        rows = [
            self.row(ROW_OK if ours else (ROW_WARN if external else ROW_IDLE), "row.stt_bridge",
                     "value.stt_listening" if ours else (
                         "value.stt_external" if external else "value.stt_not_listening"),
                     {"host": self.host, "port": self.port},
                     {"host": self.host, "port": self.port,
                      "pid": os.getpid() if ours else (pid if pid else "—")}),
            self.row(ROW_OK if upstream["up"] else ROW_WARN, "row.stt_upstream",
                     "value.stt_upstream_ok" if upstream["up"] else "value.stt_upstream_down",
                     value_params={"url": self.upstream, "code": upstream.get("code")}),
            self.row(ROW_OK if stats["requests"] else ROW_IDLE, "row.stt_last",
                     "value.stt_last" if stats["requests"] else "value.stt_last_none",
                     value_params={"seconds": f"{stats['last_seconds']:.2f}",
                                   "chars": stats["last_chars"],
                                   "count": stats["requests"],
                                   "conv": stats.get("conversions", 0)}),
        ]
        if stats["errors"]:
            row = self.row(ROW_WARN, "row.stt_errors", "value.stt_errors",
                           value_params={"count": stats["errors"],
                                         "error": " ".join(str(stats["last_error"]).split())[:120]})
            row["tooltip"] = str(stats["last_error"]).strip()   # texte complet au survol
            rows.append(row)

        actions = [{"id": "toggle",
                    "style": "danger" if ours else "primary",
                    "label_key": "btn.stt_stop" if ours else "btn.stt_start",
                    "enabled": not external,
                    "accent": ours}]
        actions.append({"id": "check", "style": "ghost", "label_key": "btn.stt_check",
                        "enabled": True})
        actions.append({"id": "copy_url", "style": "ghost", "label_key": "btn.stt_copy_url",
                        "enabled": True})

        return {
            "phase": phase,
            "url": f"http://{self.host}:{self.port}/v1",
            "rows": rows,
            "actions": actions,
            "toggles": [{"key": "start_on_app_start", "label_key": "toggle.stt_autostart",
                         "value": bool(self.options.get("start_on_app_start", True))}],
            "meta": {"pid": os.getpid() if ours else pid,
                     "ports": [{"port": self.port, "role_key": "port.stt_bridge"}],
                     "info": sysinfo.proc_info(os.getpid() if ours else pid)},
            "extra": {"ours": ours, "external": external, "stats": stats},
            "tooltip": {"state_key": ("status.online" if ours else
                                      "status.external" if external else "status.stopped"),
                        "pid": os.getpid() if ours else pid},
        }

    # --- cycle de vie -----------------------------------------------------
    def start(self) -> str:
        if self._bridge.running:
            return "log.stt_already"
        if sysinfo.listener_pid(self.port) is not None:
            # Un autre process tient le port : on ne l'arrête pas, on ne le remplace pas.
            self._log("log.stt_port_busy", port=self.port)
            return "log.stt_port_busy"

        ok, message = self._bridge.start(self.host, self.port, self.upstream)
        if not ok:
            # « port indisponible » = un autre process tient l'adresse : c'est un refus, pas
            # une panne. Sous Windows le noyau le dit par WinError 10048 (cf. allow_reuse_address).
            if "indisponible" in message:
                self._log("log.stt_port_busy", port=self.port)
                return "log.stt_port_busy"
            self._log("log.stt_start_failed", error=message)
            return "log.stt_start_failed"
        self._started = True
        self.invalidate()
        self._log("log.stt_started", port=self.port, upstream=self.upstream)
        if not sttbridge.probe_upstream(self.upstream)["up"]:
            self._log("log.stt_upstream_missing", upstream=self.upstream)
        return "log.stt_started"

    def stop(self) -> str:
        if not self._bridge.running:
            return "log.stt_not_running"
        self._bridge.stop()
        self._started = False
        self._log("log.stt_stopped", port=self.port)
        return "log.stt_stopped"

    def action(self, action_id: str) -> str:
        if action_id == "toggle":
            return self.stop() if self._bridge.running else self.start()
        if action_id == "check":
            # GET / ne prouve rien : le serveur répond 200 même quand /inference refuse tout
            # (400 « Invalid request »). On teste donc une vraie transcription (silence 0,5 s).
            probe = sttbridge.probe_inference(self.upstream)
            self.invalidate()
            if probe["up"]:
                self._log("log.stt_inference_ok")
                return "log.stt_inference_ok"
            detail = f"HTTP {probe['code']}" if probe.get("code") else (probe.get("body") or "?")
            if probe.get("body") and probe.get("code"):
                detail += f" — {probe['body']}"
            self._log("log.stt_inference_bad", detail=detail[:160])
            return "log.stt_inference_bad"
        if action_id == "copy_url":
            return "log.stt_url_copied"
        return "log.not_running"

    def dispose(self) -> None:
        """À la suppression du serveur : ne laisse pas le pont écouter dans le vide."""
        if self._bridge.running:
            self._bridge.stop()

    # --- démarrage automatique -------------------------------------------
    def maybe_auto_start(self, phase: str) -> None:
        """Appelé à chaque cycle par l'UI : démarre le pont une fois, si l'option est cochée.

        Une seule tentative par montée de l'app (drapeau `_auto_tried`) : si le port est pris ou
        ffmpeg absent, on ne réessaie pas toutes les 1,6 s — l'utilisateur a le bouton.
        """
        if self._auto_tried or self._bridge.running:
            return
        if not self.options.get("start_on_app_start", True):
            return
        self._auto_tried = True
        if sysinfo.listener_pid(self.port) is not None:
            self._log("log.stt_port_busy", port=self.port)
            return
        result = self.start()
        if result == "log.stt_started":
            self._log("log.stt_auto_started", port=self.port)


class SttBridgeType(ServerType):
    id = "stt"
    label_key = "type.stt.label"
    desc_key = "type.stt.desc"
    fields = [
        Field("host", "field.host", "text", sttbridge.DEFAULT_HOST),
        Field("port", "field.stt_port", "port", sttbridge.DEFAULT_PORT, required=True),
        Field("upstream", "field.stt_upstream", "url", sttbridge.DEFAULT_UPSTREAM,
              hint_key="field.stt_upstream_hint"),
    ]
    toggles = [Toggle("start_on_app_start", "toggle.stt_autostart", True)]
    supports_start_stop = True
    phase_labels = {"running": "status.stt_running", "stopped": "status.stt_stopped"}
    phase_keys = {"running": "log.stt_phase_running", "stopped": "log.stt_phase_stopped"}

    def summary(self, params: dict) -> tuple[str, dict] | None:
        return ("summary.stt", {"host": params.get("host") or "127.0.0.1",
                                "port": params.get("port") or "?",
                                "upstream": params.get("upstream") or "?"})

    def controller(self, server: dict, log) -> Controller:
        return SttBridgeController(server, log)


STT_BRIDGE = register(SttBridgeType())
