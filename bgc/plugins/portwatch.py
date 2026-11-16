"""Port / site à surveiller — type « par défaut », en LECTURE SEULE.

**FUSIONNÉ DANS `command.py` le 7 novembre 2026** : la commande de lancement étant devenue
facultative, « command » couvre ce type (surveiller un port/URL sans rien piloter). Ce module
n'est plus enregistré ; il reste comme référence, et les configurations existantes sont
migrées au chargement (`config._normalise`).

C'est le type à proposer quand on ne sait pas quoi choisir : il surveille un port local (et
éventuellement un chemin HTTP) et n'agit sur rien. Il ne démarre ni n'arrête aucun process —
donc aucun risque d'arrêter quelque chose qui ne nous appartient pas.
"""

from __future__ import annotations

from .. import sysinfo
from ..service import (Controller, Field, PHASE_RUNNING, PHASE_STARTING, PHASE_STOPPED,
                       ROW_IDLE, ROW_OK, ROW_WARN, ServerType, register)


class PortWatchController(Controller):
    @property
    def host(self) -> str:
        return str(self.params.get("host") or "127.0.0.1")
    @property
    def port(self) -> int:
        try:
            return int(self.params.get("port") or 0)
        except (TypeError, ValueError):
            return 0
    @property
    def check_path(self) -> str:
        return str(self.params.get("check_url") or "")

    def snapshot(self) -> dict:
        pid = sysinfo.listener_pid(self.port) if self.port else None
        info = sysinfo.proc_info(pid)
        http = {"up": False, "code": None}
        if pid and self.check_path:
            path = self.check_path if self.check_path.startswith("/") else "/" + self.check_path
            http = sysinfo.http_probe(f"http://{self.host}:{self.port}{path}")
        if pid is None:
            phase = PHASE_STOPPED
        elif self.check_path and not http["up"]:
            phase = PHASE_STARTING
        else:
            phase = PHASE_RUNNING

        rows = [self.row(ROW_OK if pid else ROW_IDLE, "row.port",
                         "value.listening" if pid else "value.free",
                         {"port": self.port}, {"pid": pid})]
        if pid and self.check_path:
            path = self.check_path if self.check_path.startswith("/") else "/" + self.check_path
            rows.append(self.row(ROW_OK if http["up"] else ROW_WARN, "row.api",
                                 "value.watch_ok" if http["up"] else "value.watch_slow",
                                 value_params={"code": http.get("code")}))
        return {
            "phase": phase,
            "url": str(self.params.get("url") or ""),
            "rows": rows,
            "actions": [],
            "toggles": [],
            "meta": {"pid": pid, "info": info},
            "extra": {"readonly": True},
            "tooltip": {"state_key": {PHASE_RUNNING: "status.online", PHASE_STARTING: "status.starting",
                                      PHASE_STOPPED: "status.stopped"}[phase], "pid": pid},
        }

    # lecture seule : aucune action, aucun arrêt
    def start(self) -> str:
        return "log.not_running"

    def stop(self) -> str:
        return "log.not_running"


class PortWatchType(ServerType):
    id = "portwatch"
    label_key = "type.portwatch.label"
    desc_key = "type.portwatch.desc"
    fields = [
        Field("host", "field.host", "text", "127.0.0.1"),
        Field("port", "field.port", "port", 8080, required=True),
        Field("check_url", "field.check_url", "text", "", hint_key="field.check_url_hint"),
        Field("url", "field.url", "text", ""),
    ]
    toggles = []
    supports_start_stop = False

    def controller(self, server: dict, log) -> Controller:
        return PortWatchController(server, log)


PORTWATCH = register(PortWatchType())
