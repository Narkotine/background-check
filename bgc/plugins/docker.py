"""Docker / Podman — un élément par conteneur (modèle « liste d'items », 16 nov 2026).

L'état vient de la CLI (`docker ps -a --format "{{json .}}"`, idem Podman), en cache court.
Chaque conteneur est un élément avec ses actions : démarrer, arrêter, redémarrer, journaux
(les dernières lignes arrivent dans l'onglet « Sortie console » de la tuile) et ouvrir le
premier port publié dans le navigateur.

Sans moteur installé ou avec un démon arrêté, la tuile le dit (aucune erreur, aucun bouton
trompeur). Les identifiants passés à la CLI viennent toujours de la liste lue juste avant :
rien de ce qu'un agent envoie n'est collé tel quel dans une commande.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import webbrowser
from collections import deque

from ..service import (Controller, Field, PHASE_RUNNING, PHASE_STARTING, PHASE_STOPPED,
                       PHASE_UNKNOWN, ROW_BAD, ROW_IDLE, ROW_OK, ServerType, Toggle,
                       register)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ENGINES = ("docker", "podman")
_PORT_RE = re.compile(r"(?:^|[\s,])(?:0\.0\.0\.0|\[::\]|::|127\.0\.0\.1|localhost)?:(\d+)->")


def find_engine(preference: str = "auto") -> tuple[str, str]:
    """(nom, chemin) du moteur à utiliser, ou ("", "") s'il n'y en a aucun."""
    order = ENGINES if preference not in ENGINES else (preference,)
    for name in order:
        path = shutil.which(name)
        if path:
            return name, path
    return "", ""


def run_cli(cli: str, args: list[str], timeout: int = 15) -> tuple[str, str, int]:
    """(stdout, stderr, code) ; n'échoue jamais."""
    try:
        proc = subprocess.run([cli, *args], capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                              text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "", "introuvable", -1
    except subprocess.TimeoutExpired:
        return "", "délai dépassé", -1
    except Exception as exc:  # pragma: no cover
        return "", str(exc), -1
    return (proc.stdout or "").strip(), (proc.stderr or "").strip(), proc.returncode


def parse_ps(out: str) -> list[dict]:
    """Sortie de `ps --format "{{json .}}"` (Docker : un objet par ligne ; Podman : un
    tableau ou des objets) → [{id, name, image, state, status, ports}]."""
    text = (out or "").strip()
    if not text:
        return []
    raw: list = []
    if text.startswith("["):
        try:
            raw = json.loads(text)
        except ValueError:
            raw = []
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except ValueError:
                continue
    containers = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        ident = str(entry.get("ID") or entry.get("Id") or "")[:12]
        names = entry.get("Names") or entry.get("Name") or ident
        if isinstance(names, list):
            names = names[0] if names else ident
        ports = entry.get("Ports") or ""
        if isinstance(ports, list):   # Podman : [{host_port, container_port, …}]
            ports = ", ".join(f"{p.get('host_ip') or '0.0.0.0'}:{p.get('host_port')}->"
                              f"{p.get('container_port')}/{p.get('protocol') or 'tcp'}"
                              for p in ports if isinstance(p, dict) and p.get("host_port"))
        if not ident:
            continue
        containers.append({
            "id": ident,
            "name": str(names).lstrip("/"),
            "image": str(entry.get("Image") or ""),
            "state": str(entry.get("State") or "").lower(),
            "status": str(entry.get("Status") or entry.get("State") or ""),
            "ports": str(ports),
        })
    return containers


def published_port(ports: str) -> int | None:
    match = _PORT_RE.search(" " + (ports or ""))
    return int(match.group(1)) if match else None


def container_phase(state: str) -> str:
    if state == "running":
        return PHASE_RUNNING
    if state in ("restarting", "created", "removing"):
        return PHASE_STARTING
    if state in ("exited", "dead", "paused", "stopped", "configured"):
        return PHASE_STOPPED
    return PHASE_UNKNOWN


class DockerController(Controller):
    LIST_TTL = 3.0

    def __init__(self, server: dict, log):
        super().__init__(server, log)
        self._cache: dict[str, tuple[float, object]] = {}
        self._output: deque[str] = deque(maxlen=400)

    # --- paramètres -------------------------------------------------------
    @property
    def engine(self) -> tuple[str, str]:
        return self._cached("engine", lambda: find_engine(str(self.params.get("engine")
                                                              or "auto")), 30.0)

    @property
    def name_filter(self) -> str:
        return str(self.params.get("filter") or "").strip().lower()

    def _cached(self, key, fn, ttl):
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def invalidate(self) -> None:
        for key in ("list",):
            self._cache.pop(key, None)

    def output_lines(self) -> list[str]:
        return list(self._output)

    def _list(self) -> tuple[list[dict], str]:
        name, cli = self.engine
        if not cli:
            return [], "missing"

        def read():
            out, err, code = run_cli(cli, ["ps", "-a", "--format", "{{json .}}"], timeout=10)
            if code != 0:
                return [], (err or out or f"code {code}")[:160]
            return parse_ps(out), ""
        return self._cached("list", read, self.LIST_TTL)  # type: ignore[return-value]

    def _visible(self, containers: list[dict]) -> list[dict]:
        result = []
        for c in containers:
            if self.name_filter and self.name_filter not in c["name"].lower() \
                    and self.name_filter not in c["image"].lower():
                continue
            if not self.options.get("show_stopped", True) and c["state"] != "running":
                continue
            result.append(c)
        return result

    # --- sondage ----------------------------------------------------------
    def snapshot(self) -> dict:
        engine, cli = self.engine
        containers, error = self._list()
        base = {"url": "", "toggles": [{"key": "show_stopped", "label_key": "toggle.docker_stopped",
                                        "value": bool(self.options.get("show_stopped", True))}],
                "meta": {"pid": None}, "extra": {"readonly": False},
                "items_title_key": "items.containers"}
        if not cli:
            return {**base, "phase": PHASE_UNKNOWN, "actions": [], "items": [], "toggles": [],
                    "extra": {"readonly": False, "status_label_key": "status.docker_missing"},
                    "items_empty_key": "items.docker_missing",
                    "rows": [self.row(ROW_BAD, "row.docker_engine", "value.docker_missing")],
                    "meta": {"pid": None, "line": {"key": "status.docker_missing", "params": {}}},
                    "tooltip": {"state_key": "status.docker_missing", "pid": None}}
        if error:
            return {**base, "phase": PHASE_STOPPED, "actions": [], "items": [],
                    "items_empty_key": "items.docker_down",
                    "rows": [self.row(ROW_OK, "row.docker_engine", "value.docker_engine",
                                      value_params={"engine": engine}),
                             {**self.row(ROW_BAD, "row.docker_daemon", "value.docker_unreachable"),
                              "tooltip": error.strip()}],
                    "meta": {"pid": None, "line": {"key": "status.docker_down",
                                                   "params": {"engine": engine}}},
                    "tooltip": {"state_key": "status.docker_down", "pid": None}}
        visible = self._visible(containers)
        running = sum(1 for c in containers if c["state"] == "running")
        items = [self._item(c) for c in visible]
        rows = [self.row(ROW_OK, "row.docker_engine", "value.docker_engine",
                         value_params={"engine": engine}),
                self.row(ROW_OK if running else ROW_IDLE, "row.docker_containers",
                         "items.count", value_params={"running": running,
                                                      "total": len(containers)})]
        if self.name_filter:
            rows.append(self.row(ROW_IDLE, "row.docker_filter", "value.wsl_text",
                                 value_params={"text": self.name_filter}))
        return {**base, "phase": PHASE_RUNNING, "actions": [], "items": items,
                "items_empty_key": "items.docker_none", "rows": rows,
                "meta": {"pid": None, "line": {"key": "status.docker_line",
                                               "params": {"engine": engine, "running": running,
                                                          "total": len(containers)}}},
                "tooltip": {"state_key": "status.online", "pid": None}}

    def _item(self, c: dict) -> dict:
        phase = container_phase(c["state"])
        port = published_port(c["ports"])
        running = phase == PHASE_RUNNING
        if running:
            actions = [{"id": "stop", "label_key": "btn.item_stop", "style": "danger",
                        "main": True, "stops": True},
                       {"id": "restart", "label_key": "btn.item_restart"}]
        else:
            actions = [{"id": "start", "label_key": "btn.item_start", "style": "primary",
                        "main": True}]
        actions.append({"id": "logs", "label_key": "btn.item_logs", "then": "console"})
        if port:
            actions.append({"id": "open", "label_key": "btn.item_open", "enabled": running})
        detail_params = {"image": c["image"][:40], "status": c["status"][:40],
                         "ports": f" · :{port}" if port else ""}
        return {"id": c["id"], "label": c["name"], "phase": phase,
                "detail_key": "value.docker_detail", "detail_params": detail_params,
                "rows": [self.row(ROW_OK if running else ROW_IDLE, "row.docker_image",
                                  "value.wsl_text", value_params={"text": c["image"]}),
                         self.row(ROW_OK if running else ROW_IDLE, "row.docker_status",
                                  "value.wsl_text", value_params={"text": c["status"]}),
                         self.row(ROW_OK if port else ROW_IDLE, "row.docker_ports",
                                  "value.wsl_text", value_params={"text": c["ports"] or "—"})],
                "actions": actions}

    # --- actions ----------------------------------------------------------
    def item_action(self, item_id: str, action_id: str) -> str:
        engine, cli = self.engine
        if not cli:
            self._log("log.docker_missing")
            return "log.docker_missing"
        self.invalidate()
        containers, _error = self._list()
        target = next((c for c in containers if c["id"] == item_id), None)
        if target is None:
            self._log("log.item_unknown", item=item_id)
            return "log.item_unknown"
        name = target["name"]
        if action_id in ("start", "stop", "restart"):
            out, err, code = run_cli(cli, [action_id, target["id"]], timeout=90)
            self.invalidate()
            if code != 0:
                self._log("log.item_failed", item=name, action=action_id,
                          error=(err or out or f"code {code}")[:120])
                return "log.item_failed"
            key = {"start": "log.docker_started", "stop": "log.docker_stopped",
                   "restart": "log.docker_restarted"}[action_id]
            self._log(key, name=name)
            return key
        if action_id == "logs":
            out, err, code = run_cli(cli, ["logs", "--tail", "200", target["id"]], timeout=20)
            text = "\n".join(part for part in (out, err) if part)
            self._output.append(f"── {name} ({engine} logs --tail 200) ──")
            self._output.extend(text.splitlines()[-200:] or ["(vide)"])
            self._log("log.docker_logs", name=name)
            return "log.docker_logs"
        if action_id == "open":
            port = published_port(target["ports"])
            if not port:
                return "log.item_failed"
            url = f"http://127.0.0.1:{port}"
            webbrowser.open(url)
            self._log("log.custom_url", label=name, target=url)
            return "log.custom_url"
        return "log.not_running"


class DockerType(ServerType):
    id = "docker"
    label_key = "type.docker.label"
    desc_key = "type.docker.desc"
    fields = [
        Field("engine", "field.docker_engine", "choice", "auto",
              hint_key="field.docker_engine_hint",
              choices=[("auto", "choice.docker_auto"), ("docker", "choice.docker_docker"),
                       ("podman", "choice.docker_podman")]),
        Field("filter", "field.docker_filter", "text", "", hint_key="field.docker_filter_hint"),
    ]
    toggles = [Toggle("show_stopped", "toggle.docker_stopped", True)]
    supports_start_stop = False   # on pilote les conteneurs, pas la tuile
    phase_labels = {PHASE_RUNNING: "status.docker_up", PHASE_STOPPED: "status.docker_down_short"}

    def summary(self, params: dict) -> tuple[str, dict] | None:
        text = str(params.get("filter") or "").strip()
        if text:
            return ("summary.docker_filter", {"filter": text})
        return ("summary.docker_all", {})

    def validate(self, raw: dict) -> dict:
        cleaned = super().validate(raw)
        if cleaned.get("engine") not in ("auto", *ENGINES):
            raise ValueError("field.docker_engine")
        return cleaned

    def controller(self, server: dict, log) -> Controller:
        return DockerController(server, log)


DOCKER = register(DockerType())
