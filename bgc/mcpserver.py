"""Serveur MCP local : donne à un agent IA les moyens de piloter et de configurer Background Check.

Pourquoi dans l'application et pas dans un processus séparé : un `.exe` PyInstaller
`--onefile` n'a pas d'interpréteur utilisable (`sys.executable` = l'exe lui-même), et un
second processus ne verrait ni les contrôleurs vivants (PID lancés par l'app) ni le journal.
Le serveur tourne donc dans un fil démon, comme le pont STT (`sttbridge.py`).

Transport : **Streamable HTTP** de MCP, en JSON-RPC 2.0 sur `POST /mcp`, sans session (le
serveur n'émet pas d'en-tête `Mcp-Session-Id` : le client n'ouvre alors pas de flux GET —
vérifié dans le SDK MCP 2.0 utilisé par Hermes). Aucune dépendance : `http.server` suffit.

Écoute uniquement sur 127.0.0.1. Rien n'est exposé au réseau local.

Sécurité (revue du 16 nov 2026) — « 127.0.0.1 » ne protège pas du **navigateur** de
l'utilisateur : une page web peut envoyer un POST `text/plain` vers 127.0.0.1 sans preflight
CORS (la réponse est illisible, mais l'effet de bord a lieu). Chaque requête doit donc :
  * ne porter **aucun en-tête `Origin`** (un client MCP n'en envoie pas, un navigateur si) ;
  * viser un `Host` local (`127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>`) — parade
    au DNS rebinding ;
  * être en `Content-Type: application/json` (POST) ;
  * présenter `Authorization: Bearer <jeton>` — jeton aléatoire créé au premier lancement,
    copiable dans les Réglages et à reporter dans la config MCP du client (Hermes : `headers`).

Outils exposés :
  * lecture   : list_servers, get_server, list_server_types, get_machine_info, logs, presets ;
  * pilotage  : start_server, stop_server ;
  * écriture  : create_server, update_server, delete_server (sauvegarde de config avant écriture).
"""

from __future__ import annotations

import hmac
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, i18n, presets, service, sysinfo
from . import presentation as pres
from .core import deps as deps_mod

HOST = "127.0.0.1"
DEFAULT_PORT = 8781
#: Taille maximale d'un message JSON-RPC accepté (un appel d'outil fait quelques Ko).
MAX_BODY = 1_000_000
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")
#: Versions du protocole MCP négociées à la main (le client choisit la sienne).
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
FALLBACK_PROTOCOL = "2025-06-18"

#: Clés de journal qui signifient « l'action a échoué » (les contrôleurs renvoient une clé).
FAILURE_KEYS = {
    "log.start_failed", "log.no_cli", "log.no_port", "log.port_busy",
    "log.not_running", "log.command_no_start", "log.command_already",
    "log.command_stop_refused", "log.command_stop_cmd_failed",
    "log.wsl_start_failed", "log.wsl_stop_refused", "log.wsl_missing",
    "log.stt_start_failed", "log.stt_port_busy", "log.stt_not_running",
    "log.desktop_missing",
    "log.item_failed", "log.item_unknown", "log.docker_missing", "log.winsvc_failed",
    "log.winsvc_denied", "log.winsvc_missing",
}
#: Actions qui n'ont de sens que dans la fenêtre (presse-papiers, onglet console).
MCP_UI_ONLY_ACTIONS = {"copy_url", "output"}

INSTRUCTIONS = (
    "Background Check is a Windows desktop app that turns local services into tiles and can start/stop "
    "them. Use these tools to inspect and configure those tiles for the user.\n"
    "Workflow to configure a tile: call list_server_types first (it lists the exact param keys, "
    "toggles and defaults of each server type), then get_machine_info (OS, GPU, listening ports, "
    "WSL distros, installed tools and known folders), then list_servers so you do not duplicate an "
    "existing tile; then create_server or update_server. Unknown param keys are reported and "
    "ignored, and every param set is validated by the server type. Call start_server to test, then "
    "get_server or get_server_log to verify. Use web search / official docs yourself when you do "
    "not know how a service is launched.\n"
    "Some tiles hold several items (one per Docker/Podman container, WSL distribution or "
    "Windows service): call list_items, then item_action to act on one item.\n"
    "Everything listens on 127.0.0.1 only and every request needs the bearer token shown in "
    "Background Check settings. Config writes are preceded by a timestamped backup."
)


class ToolError(Exception):
    """Erreur destinée au modèle (mauvaise valeur, serveur inconnu…), pas une panne de l'app."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


class Tool:
    """Un outil MCP : nom, description, schéma JSON et fonction."""

    def __init__(self, name: str, title: str, description: str, schema: dict, handler,
                 read_only: bool = False):
        self.name = name
        self.title = title
        self.description = description
        self.schema = schema
        self.handler = handler
        self.read_only = read_only

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {"title": self.title, "readOnlyHint": bool(self.read_only)},
        }


def _schema(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {},
            "required": required or [], "additionalProperties": False}


def _text_result(payload, is_error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(
        payload, ensure_ascii=False, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def _rpc_error(message_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": message_id,
            "error": {"code": code, "message": message}}


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "oui")
    return bool(value)


def _limit(value, default: int = 50, maximum: int = 200) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


class McpTools:
    """Implémentation des outils ; ne touche jamais Tk directement.

    Les lectures peuvent lire `app.runtimes` (liste remplacée seulement à l'ajout/suppression,
    protégée par `app.ui_call` pour les écritures). Les opérations longues (démarrer/arrêter)
    tournent dans le fil HTTP, comme le fait déjà `App.run_action` dans un fil de travail.
    """

    def __init__(self, app):
        self.app = app
        self._gpu_cache: tuple[float, list[str]] = (0.0, [])
        self.tools: dict[str, Tool] = {tool.name: tool for tool in self._build_tools()}

    # ── catalogue ──────────────────────────────────────────────────────────
    def _build_tools(self) -> list[Tool]:
        sid = {"type": "string", "description": "Server id from list_servers (e.g. 'hermes-1')."}
        return [
            Tool("list_servers", "List servers",
                 "List every tile configured in Background Check with its id, type, label, enabled "
                 "state, live phase, pid, URL, params and resource usage (metrics: cpu_percent of "
                 "the whole machine, ram_mb, vram_mb over the service process tree, sampled every "
                 "~4 s; null when nothing is measured). Call this before creating a server so "
                 "you do not duplicate an existing tile.",
                 _schema(), self._list_servers, read_only=True),
            Tool("get_server", "Get server",
                 "Return one tile in full: configuration (params, options, icon, custom buttons) "
                 "plus its live status (phase, pid, URL, status rows, available actions and "
                 "metrics: cpu_percent, ram_mb, vram_mb).",
                 _schema({"server_id": sid}, ["server_id"]), self._get_server, read_only=True),
            Tool("list_server_types", "List server types",
                 "Describe every server type (plugin): id, description, param fields with their "
                 "exact keys, kinds, defaults, hints and choices, and the toggle keys. Read this "
                 "before create_server or update_server so the params match the type.",
                 _schema(), self._list_server_types, read_only=True),
            Tool("get_machine_info", "Get machine info",
                 "Return the PC facts needed to configure a tile: OS and build, CPU, RAM, disks, "
                 "GPU names, listening TCP ports with their process, WSL distributions, tools "
                 "found on PATH (python, node, git, ollama, lms, wsl, ffmpeg…), known app folders "
                 "(LM Studio, LlamaGUI, Hermes) and the Background Check config path.",
                 _schema(), self._machine_info, read_only=True),
            Tool("list_presets", "List presets",
                 "Ready-made field presets (watch only, local script, LM Studio, Ollama, LlamaGUI) "
                 "with the exact values they fill, including machine-specific paths when found. "
                 "Use them as a starting point for create_server, then adapt.",
                 _schema(), self._list_presets, read_only=True),
            Tool("get_server_log", "Get server log",
                 "Recent events for one tile (translated to the app's current language) plus the "
                 "captured console output of its last launch command. Use it to diagnose why a "
                 "server did not start.",
                 _schema({"server_id": sid,
                          "limit": {"type": "integer", "minimum": 1, "maximum": 200,
                                    "description": "How many events to return (default 50)."}},
                         ["server_id"]), self._get_server_log, read_only=True),
            Tool("get_app_log", "Get app log",
                 "Recent Background Check application events: config writes, tile imports, MCP actions "
                 "and errors.",
                 _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
                         []), self._get_app_log, read_only=True),
            Tool("start_server", "Start server",
                 "Start a configured server using the same controller as the tile's Start button "
                 "(runs its command, starts the WSL distro, opens remote access…). The server must "
                 "be enabled (not paused). Returns the action result and the resulting phase; "
                 "call get_server afterwards to confirm.",
                 _schema({"server_id": sid}, ["server_id"]), self._start_server),
            Tool("stop_server", "Stop server",
                 "Stop a running server using the same targeted stop as the tile's Stop button. "
                 "Background Check refuses to stop processes it did not launch unless the tile allows "
                 "it; the refusal is returned as the result. Returns the resulting phase.",
                 _schema({"server_id": sid}, ["server_id"]), self._stop_server),
            Tool("list_items", "List items",
                 "List the items of a tile: one per Docker/Podman container, WSL distribution, "
                 "Windows service… with id, label, phase, status rows and the actions available "
                 "on each item (ids to pass to item_action). A tile type without items is "
                 "returned as one implicit item with id \"\" whose actions are the tile's. Each "
                 "item carries metrics (cpu_percent, ram_mb, vram_mb) when its processes are "
                 "known, else null. `dependencies` lists the related servers the tile relies on "
                 "(e.g. the whisper server behind the STT bridge): label, target (port, "
                 "host:port or process), up, pids, uptime_s and metrics. They are watched only "
                 "and are not valid item ids.",
                 _schema({"server_id": sid}, ["server_id"]), self._list_items, read_only=True),
            Tool("item_action", "Run item action",
                 "Run one action on one item of a tile, exactly like its button in the window "
                 "(start/stop/restart a container, start/terminate a WSL distribution…). "
                 "`action` must be one of that item's enabled action ids from list_items. "
                 "With item_id \"\" the action runs on the tile itself. Returns the result and "
                 "the refreshed item.",
                 _schema({"server_id": sid,
                          "item_id": {"type": "string",
                                      "description": "Item id from list_items (\"\" = the tile)."},
                          "action": {"type": "string", "description": "Action id of that item."}},
                         ["server_id", "item_id", "action"]), self._item_action),
            Tool("create_server", "Create server",
                 "Create a new tile. `type` must be an id from list_server_types; `params` must use "
                 "that type's exact field keys (call list_server_types first). `options` uses its "
                 "toggle keys. `actions` is up to 3 custom buttons {label, target}; a target that "
                 "starts with http(s) opens a browser, anything else runs as a command. `icon` is "
                 "an existing image or .exe/.dll path (omit for the type pictogram). Returns the "
                 "new server id and a timestamped config backup path.",
                 _schema({
                     "type": {"type": "string", "description": "Server type id."},
                     "label": {"type": "string", "description": "Tile name shown in the sidebar."},
                     "params": {"type": "object", "description": "Type-specific param values."},
                     "options": {"type": "object", "description": "Toggle values (booleans)."},
                     "actions": {"type": "array", "description": "Up to 3 {label, target} buttons.",
                                 "items": {"type": "object"}},
                     "icon": {"type": "string", "description": "Existing image/.exe/.dll path."},
                     "enabled": {"type": "boolean", "description": "Monitor it (default true)."},
                     "select": {"type": "boolean",
                                "description": "Show the new tile in the window (default false: "
                                               "the user's current view is left alone)."},
                 }, ["type", "label", "params"]), self._create_server),
            Tool("update_server", "Update server",
                 "Change an existing tile. Only the fields you pass are modified; `params` is "
                 "merged into the current params (pass an empty string to clear a field). The "
                 "merged params are validated by the server type; unknown keys are reported and "
                 "ignored, and nothing is written if validation fails. Use `enabled` to pause or "
                 "resume monitoring. `deps` declares extra related servers to watch (not start), as "
                 "\"name = target; …\" where target is a port, host:port, URL or process name "
                 "(ollama.exe); empty string clears them. Returns the updated tile and a config "
                 "backup path.",
                 _schema({
                     "server_id": sid,
                     "label": {"type": "string"},
                     "params": {"type": "object"},
                     "options": {"type": "object"},
                     "actions": {"type": "array", "items": {"type": "object"}},
                     "icon": {"type": "string",
                              "description": "Existing path, or empty string to clear the icon."},
                     "enabled": {"type": "boolean"},
                     "deps": {"type": "string",
                              "description": "Related servers: \"name = target; …\"."},
                 }, ["server_id"]), self._update_server),
            Tool("delete_server", "Delete server",
                 "Delete a tile. `confirm` must repeat the server id. This only removes the tile "
                 "from Background Check: a process it launched keeps running. A config backup is written "
                 "first.",
                 _schema({"server_id": sid,
                          "confirm": {"type": "string",
                                      "description": "Must equal server_id."}},
                         ["server_id", "confirm"]), self._delete_server),
        ]

    # ── JSON-RPC ───────────────────────────────────────────────────────────
    def handle_message(self, message):
        """Traite un message JSON-RPC (dict) ou un lot ; None = rien à répondre."""
        if isinstance(message, list):
            replies = [reply for reply in (self._handle_one(item) for item in message)
                       if reply is not None]
            return replies or None
        return self._handle_one(message)

    def _handle_one(self, message):
        if not isinstance(message, dict):
            return _rpc_error(None, -32600, "Invalid Request")
        method = message.get("method")
        if method is None:
            return None  # réponse ou notification inconnue : rien à envoyer
        message_id = message.get("id")
        if message_id is None:
            return None  # notification (notifications/initialized, cancelled…)
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [tool.describe() for tool in self.tools.values()]}
            elif method == "tools/call":
                result = self.call(str(params.get("name") or ""),
                                   params.get("arguments") or {})
            elif method in ("resources/list", "resources/templates/list"):
                result = {"resources": []}
            elif method == "prompts/list":
                result = {"prompts": []}
            elif method == "logging/setLevel":
                result = {}
            else:
                return _rpc_error(message_id, -32601, f"Method not found: {method}")
            return {"jsonrpc": "2.0", "id": message_id, "result": result}
        except Exception as exc:  # le serveur ne doit jamais mourir sur une requête
            return _rpc_error(message_id, -32603, f"{type(exc).__name__}: {exc}")

    def _initialize(self, params: dict) -> dict:
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in PROTOCOL_VERSIONS else FALLBACK_PROTOCOL
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "background-check", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def call(self, name: str, arguments) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return _text_result({"ok": False, "error": f"Unknown tool {name!r}. Available: "
                                 + ", ".join(sorted(self.tools))}, True)
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            payload = tool.handler(arguments)
        except ToolError as exc:
            data = {"ok": False, "error": str(exc)}
            if exc.details:
                data["details"] = exc.details
            return _text_result(data, True)
        except Exception as exc:  # panne de l'app : le modèle doit voir le message, pas un 500
            return _text_result({"ok": False,
                                 "error": f"{type(exc).__name__}: {exc}"}, True)
        is_error = isinstance(payload, dict) and payload.get("ok") is False
        return _text_result(payload, is_error)

    # ── aides ──────────────────────────────────────────────────────────────
    def _runtime(self, server_id: str):
        runtime = next((item for item in self.app.runtimes
                        if item.id == str(server_id or "")), None)
        if runtime is None:
            raise ToolError(f"Unknown server id {server_id!r}. Call list_servers to get the ids.")
        return runtime

    @staticmethod
    def _require(args: dict, key: str) -> str:
        value = str(args.get(key) or "").strip()
        if not value:
            raise ToolError(f"Missing required argument {key!r}.")
        return value

    @staticmethod
    def _redact_params(params: dict) -> tuple[dict, list[str]]:
        """Ne jamais renvoyer au modèle les valeurs de `env` ni d'un champ « secret ».

        Les outils MCP sont lus par un LLM qui peut être distant : une variable
        `OPENAI_API_KEY=sk-…` dans `env` n'a rien à y faire. Les noms restent visibles
        (le modèle sait quelles variables existent) ; les valeurs sont remplacées.
        """
        clean = dict(params)
        redacted: list[str] = []
        env = clean.get("env")
        if isinstance(env, str) and env.strip():
            lines = []
            for line in env.splitlines():
                name, sep, _value = line.partition("=")
                lines.append(f"{name.strip()}=***" if sep else line)
            clean["env"] = "\n".join(lines)
            redacted.append("env")
        for key, value in list(clean.items()):
            lowered = key.lower()
            if isinstance(value, str) and value and any(
                    token in lowered for token in ("key", "token", "secret", "password",
                                                   "passwd", "credential")):
                clean[key] = "***"
                redacted.append(key)
        return clean, redacted

    def _metrics(self, runtime, item_id: str = "") -> dict | None:
        """CPU % (part de la machine), RAM et VRAM (Mo) de l'arbre de processus ; None si
        rien n'est mesuré (service arrêté, conteneur sans PID côté Windows…)."""
        getter = getattr(self.app, "metrics_of", None)
        value = getter(runtime, item_id) if getter else None
        if not value:
            return None
        return {"cpu_percent": value.get("cpu"), "ram_mb": value.get("ram_mb"),
                "vram_mb": value.get("vram_mb"), "processes": value.get("procs"),
                "sampled_at": value.get("at")}

    def _summary(self, runtime) -> dict:
        snapshot = runtime.snapshot_data or {}
        meta = snapshot.get("meta") or {}
        params, redacted = McpTools._redact_params(dict(runtime.server.get("params") or {}))
        options = dict(runtime.server.get("options") or {})
        return {
            "id": runtime.id,
            "type": str(runtime.server.get("type") or ""),
            "deps": str(runtime.server.get("deps") or ""),
            "label": runtime.label,
            "enabled": runtime.enabled,
            "phase": runtime.phase,
            "paused": not runtime.enabled,
            "url": str(snapshot.get("url") or ""),
            "pid": meta.get("pid"),
            "params": params,
            "redacted": sorted(set(redacted)),
            "options": options,
            "icon": str(runtime.server.get("icon") or ""),
            "custom_actions": [dict(item) for item in (runtime.server.get("actions") or [])],
            "probe_error": str(snapshot.get("error") or ""),
            "metrics": self._metrics(runtime) if runtime.enabled else None,
            "ports": [{"port": port, "role": i18n.t(role) if role else ""}
                      for port, role in pres.port_entries(runtime.server, snapshot)],
            "dependencies": self._dependencies(runtime),
        }

    def _dependencies(self, runtime) -> list[dict]:
        """Serveurs liés (surveillés seulement), avec leurs ressources mesurées à part : la
        mesure de la tuile les exclut, rien n'est compté deux fois."""
        if not runtime.enabled:
            return []
        out = []
        now = time.time()
        for dep in pres.deps_of(runtime.snapshot_data):
            started = dep.get("started")
            out.append({
                "id": dep.get("id"), "label": pres.dep_label(dep),
                "source": dep.get("source") or "",
                "target": pres.dep_target(dep), "host": dep.get("host") or "",
                "port": dep.get("port"), "process": dep.get("process") or "",
                "up": bool(dep.get("up")), "pids": list(dep.get("pids") or []),
                "process_name": dep.get("name") or "",
                "uptime_s": round(now - started) if dep.get("up") and started else None,
                "metrics": self._metrics(runtime, deps_mod.dep_key(str(dep.get("id")))),
            })
        return out

    def _status(self, runtime) -> dict:
        snapshot = runtime.snapshot_data or {}
        rows = []
        for row in snapshot.get("rows") or []:
            rows.append({
                "status": row.get("status"),
                "label": i18n.t(row.get("label_key", ""), **(row.get("label_params") or {})),
                "value": i18n.t(row.get("value_key", ""), **(row.get("value_params") or {})),
            })
        actions = []
        for action in snapshot.get("actions") or []:
            actions.append({"id": action.get("id"),
                            "label": i18n.t(action.get("label_key", "")),
                            "enabled": bool(action.get("enabled", True))})
        result = {"phase": runtime.phase, "paused": not runtime.enabled,
                  "url": snapshot.get("url") or "",
                  "pid": (snapshot.get("meta") or {}).get("pid"), "rows": rows,
                  "actions": actions, "rows_raw": snapshot.get("rows") or []}
        if pres.has_items(snapshot):
            result["items"] = [self._item(item, self._metrics(runtime, item["id"]))
                               for item in pres.items_of(snapshot)]
        result["metrics"] = self._metrics(runtime) if runtime.enabled else None
        result["dependencies"] = self._dependencies(runtime)
        return result

    @staticmethod
    def _item(item: dict, metrics: dict | None = None) -> dict:
        """Un élément lisible par le modèle (textes traduits, ids d'action)."""
        return {
            "id": item["id"], "label": item["label"], "phase": item["phase"],
            "implicit": bool(item.get("implicit")),
            "detail": (i18n.t(item["detail_key"], **item["detail_params"])
                       if item.get("detail_key") else ""),
            "rows": [{"status": row.get("status"),
                      "label": i18n.t(row.get("label_key", ""), **(row.get("label_params") or {})),
                      "value": i18n.t(row.get("value_key", ""), **(row.get("value_params") or {}))}
                     for row in item["rows"]],
            "actions": [{"id": a.get("id"), "label": i18n.t(str(a.get("label_key") or "")),
                         "enabled": bool(a.get("enabled", True)), "main": bool(a.get("main"))}
                        for a in item["actions"] if a.get("id") not in MCP_UI_ONLY_ACTIONS],
            "metrics": metrics,
        }

    def _backup_config(self) -> str:
        """Copie horodatée avant une écriture faite par l'agent (jamais écraser une sauvegarde)."""
        path = self.app.cfg.path
        try:
            if not os.path.isfile(path):
                return ""
            stamp = time.strftime("%Y%m%dT%H%M%S")
            target = f"{path}.bak-{stamp}"
            index = 1
            while os.path.exists(target):
                target = f"{path}.bak-{stamp}-{index}"
                index += 1
            shutil.copy2(path, target)
            return target
        except OSError:
            return ""

    def _log_mcp(self, key: str, **params) -> None:
        try:
            self.app.log_app(key, **params)
        except Exception:
            pass

    @staticmethod
    def _action_message(runtime, key: str) -> str:
        """Traduit la clé d'action avec les paramètres réellement journalisés (pid, commande…).

        Les contrôleurs renvoient une clé seule ; sans cette relecture, le modèle reçoit
        « Commande lancée (pid {pid}) » avec les accolades intactes (constaté au harnais).
        """
        for _stamp, log_key, params in reversed(list(runtime.log_lines)):
            if log_key == key:
                return i18n.t(key, **params)
        return i18n.t(key)

    # ── outils de lecture ──────────────────────────────────────────────────
    def _list_servers(self, _args: dict) -> dict:
        return {"count": len(self.app.runtimes),
                "servers": [self._summary(runtime) for runtime in self.app.runtimes]}

    def _get_server(self, args: dict) -> dict:
        runtime = self._runtime(self._require(args, "server_id"))
        return {"server": self._summary(runtime), "status": self._status(runtime)}

    def _list_server_types(self, _args: dict) -> dict:
        types = []
        for server_type in service.all_types():
            fields = []
            for field in server_type.fields:
                fields.append({
                    "key": field.key,
                    "label": i18n.t(field.label_key),
                    "kind": field.kind,
                    "required": bool(field.required),
                    "default": field.default,
                    "hint": i18n.t(field.hint_key) if field.hint_key else "",
                    "choices": [{"value": value, "label": i18n.t(key)}
                                for value, key in (field.choices or [])],
                    "section": i18n.t(field.section_key) if field.section_key else "",
                    "browse": field.browse or "",
                })
            toggles = [{"key": toggle.key, "label": i18n.t(toggle.label_key),
                        "default": bool(toggle.default)} for toggle in server_type.toggles]
            types.append({
                "id": server_type.id,
                "label": i18n.t(server_type.label_key),
                "description": i18n.t(server_type.desc_key),
                "supports_start_stop": bool(server_type.supports_start_stop),
                "params": fields,
                "toggles": toggles,
                "target_fields": list(getattr(server_type, "TARGET_FIELDS", None) or []),
            })
        return {"count": len(types), "types": types,
                "note": "params must use these exact keys; the config stores them as declared."}

    def _list_presets(self, _args: dict) -> dict:
        items = []
        for key in presets.KEYS:
            try:
                data = presets.values(key)
            except Exception as exc:
                items.append({"key": key, "label": i18n.t(key), "error": str(exc)})
                continue
            icon = str(data.get("icon") or "")
            items.append({
                "key": key,
                "label": i18n.t(key),
                "fields": data.get("fields") or {},
                "name": data.get("name") or "",
                "icon": icon,
                "icon_exists": bool(icon and os.path.isfile(icon)),
                "actions": data.get("actions") or [],
                "focus": data.get("focus"),
            })
        return {"presets": items}

    def _get_server_log(self, args: dict) -> dict:
        runtime = self._runtime(self._require(args, "server_id"))
        limit = _limit(args.get("limit"), 50)
        events = [{"time": stamp, "key": key, "text": i18n.t(key, **params)}
                  for stamp, key, params in list(runtime.log_lines)[-limit:]]
        output = []
        getter = getattr(runtime.controller, "output_lines", None)
        if callable(getter):
            try:
                output = list(getter())[-limit:]
            except Exception:
                output = []
        return {"server_id": runtime.id, "label": runtime.label, "phase": runtime.phase,
                "events": events, "console_output": output}

    def _get_app_log(self, args: dict) -> dict:
        limit = _limit(args.get("limit"), 50)
        events = [{"time": stamp, "key": key, "text": i18n.t(key, **params)}
                  for stamp, key, params in list(self.app.app_log)[-limit:]]
        return {"events": events}

    # ── machine ────────────────────────────────────────────────────────────
    def _gpu_names(self) -> list[str]:
        now = time.time()
        cached_at, cached = self._gpu_cache
        if cached and now - cached_at < 300:
            return list(cached)
        names: list[str] = []
        if os.name == "nt":
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                     "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                     "(Get-CimInstance Win32_VideoController).Name"],
                    capture_output=True, text=True, timeout=8, encoding="utf-8",
                    errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                names = [line.strip() for line in (result.stdout or "").splitlines()
                         if line.strip()]
            except Exception:
                names = []
        self._gpu_cache = (now, names)
        return names

    def _machine_info(self, _args: dict) -> dict:
        psutil = sysinfo.psutil
        disks = []
        if psutil is not None:
            try:
                for part in psutil.disk_partitions(all=False):
                    try:
                        usage = psutil.disk_usage(part.mountpoint)
                    except Exception:
                        continue
                    disks.append({"mount": part.mountpoint, "fs": part.fstype,
                                  "total_gb": round(usage.total / 1e9, 1),
                                  "free_gb": round(usage.free / 1e9, 1)})
            except Exception:
                disks = []
        tools = {}
        for name in ("python", "py", "node", "npm", "npx", "git", "wsl", "docker", "ffmpeg",
                     "ollama", "lms", "uv", "powershell", "code"):
            path = shutil.which(name)
            if path:
                tools[name] = path
        ports = [entry for entry in sysinfo.listening_ports() if entry["port"] >= 1024][:80]
        wsl_distros, wsl_error = [], ""
        try:
            from .plugins import wsl as wsl_mod
            wsl_distros, wsl_error = wsl_mod.list_distros()
        except Exception as exc:
            wsl_error = str(exc)
        memory_gb = None
        if psutil is not None:
            try:
                memory_gb = round(psutil.virtual_memory().total / 1e9, 1)
            except Exception:
                memory_gb = None
        return {
            "app": {"name": "Background Check", "version": __version__,
                    "config_path": self.app.cfg.path},
            "os": {"platform": platform.platform(), "version": platform.version(),
                   "release": platform.release(), "machine": platform.machine(),
                   "hostname": socket.gethostname(),
                   "user": os.environ.get("USERNAME") or ""},
            "python": {"version": sys.version.split()[0], "executable": sys.executable},
            "cpu": {"model": platform.processor(),
                    "logical": psutil.cpu_count() if psutil else None,
                    "physical": psutil.cpu_count(logical=False) if psutil else None},
            "memory_gb": memory_gb,
            "disks": disks,
            "gpus": self._gpu_names(),
            "gpu_memory_mb": (self.app.metrics.gpu_total()
                              if getattr(self.app, "metrics", None) else None),
            "tools": tools,
            "listening_ports": ports,
            "wsl": {"distros": wsl_distros, "error": wsl_error},
            "folders": {"userprofile": os.environ.get("USERPROFILE") or "",
                        "localappdata": os.environ.get("LOCALAPPDATA") or "",
                        "hermes_home": os.path.join(os.environ.get("LOCALAPPDATA") or "",
                                                    "hermes")},
            "known_apps": {"lm_studio": presets.lmstudio_exe(),
                           "llamagui_root": presets.llamagui_root(),
                           "ollama": presets.ollama_exe()},
            "note": "listening_ports shows ports >= 1024 (services mostly listen below).",
        }

    # ── pilotage ───────────────────────────────────────────────────────────
    def _start_server(self, args: dict) -> dict:
        runtime = self._runtime(self._require(args, "server_id"))
        if not runtime.enabled:
            raise ToolError("This server is paused. Call update_server with enabled=true first.")
        if runtime.controller is None:
            raise ToolError("No controller for this server: its type/plugin is not loaded.")
        if runtime.phase == "running":
            return {"ok": True, "message": "Server is already running.",
                    "server": self._summary(runtime)}
        if runtime.phase == "starting":
            return {"ok": True, "message": "Server is already starting; poll get_server.",
                    "server": self._summary(runtime)}
        # Même chemin que le bouton « Démarrer » et le tray (bgc/core/actions.py).
        key = self.app.actions.perform(runtime, "start")
        message = self._action_message(runtime, key)
        ok = key not in FAILURE_KEYS
        self._log_mcp("log.mcp_agent_start" if ok else "log.mcp_agent_start_failed",
                      label=runtime.label, result=message)
        result = {"ok": ok, "result_key": key, "message": message,
                  "server": self._summary(runtime)}
        if not ok:
            getter = getattr(runtime.controller, "output_lines", None)
            if callable(getter):
                try:
                    result["console_output"] = list(getter())[-15:]
                except Exception:
                    pass
        return result

    def _stop_server(self, args: dict) -> dict:
        runtime = self._runtime(self._require(args, "server_id"))
        if runtime.controller is None:
            raise ToolError("No controller for this server: its type/plugin is not loaded.")
        if runtime.phase == "stopped":
            return {"ok": True, "message": "Server is already stopped.",
                    "server": self._summary(runtime)}
        # Même chemin que le bouton « Arrêter » : arrêt attendu, donc jamais d'alerte de chute.
        key = self.app.actions.perform(runtime, "stop")
        message = self._action_message(runtime, key)
        ok = key not in FAILURE_KEYS
        self._log_mcp("log.mcp_agent_stop" if ok else "log.mcp_agent_stop_failed",
                      label=runtime.label, result=message)
        return {"ok": ok, "result_key": key, "message": message,
                "server": self._summary(runtime)}

    def _list_items(self, args: dict) -> dict:
        runtime = self._runtime(self._require(args, "server_id"))
        items = pres.items_of(runtime.snapshot_data, runtime.label)
        return {"server_id": runtime.id, "label": runtime.label, "phase": runtime.phase,
                "has_items": pres.has_items(runtime.snapshot_data), "count": len(items),
                "items": [self._item(item, self._item_metrics(runtime, item))
                          for item in items],
                "dependencies": self._dependencies(runtime)}

    def _item_metrics(self, runtime, item: dict) -> dict | None:
        """Élément implicite (type sans `items`) = la tuile elle-même."""
        if not runtime.enabled:
            return None
        return self._metrics(runtime, "" if item.get("implicit") else item["id"])

    def _item_action(self, args: dict) -> dict:
        runtime = self._runtime(self._require(args, "server_id"))
        item_id = str(args.get("item_id") or "").strip()
        action_id = self._require(args, "action")
        if not runtime.enabled:
            raise ToolError("This server is paused. Call update_server with enabled=true first.")
        if runtime.controller is None:
            raise ToolError("No controller for this server: its type/plugin is not loaded.")
        if runtime.busy:
            raise ToolError("This tile is busy with another action; retry in a few seconds.")
        items = pres.items_of(runtime.snapshot_data, runtime.label)
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            raise ToolError(f"Unknown item id {item_id!r}. Call list_items to get the ids.",
                            {"items": [i["id"] for i in items]})
        action = next((a for a in item["actions"] if a.get("id") == action_id
                       and action_id not in MCP_UI_ONLY_ACTIONS), None)
        if action is None:
            raise ToolError(f"Action {action_id!r} is not available on this item.",
                            {"actions": [a.get("id") for a in item["actions"]
                                         if a.get("id") not in MCP_UI_ONLY_ACTIONS]})
        if not action.get("enabled", True):
            raise ToolError(f"Action {action_id!r} is disabled right now (item phase: "
                            f"{item['phase']}).")
        key = self.app.actions.perform(runtime, action_id, item_id=item_id or None)
        message = self._action_message(runtime, key)
        ok = key not in FAILURE_KEYS
        self._log_mcp("log.mcp_agent_item", label=runtime.label, item=item["label"],
                      action=action_id, result=message)
        fresh = next((i for i in pres.items_of(runtime.snapshot_data, runtime.label)
                      if i["id"] == item_id), None)
        return {"ok": ok, "result_key": key, "message": message,
                "item": self._item(fresh, self._item_metrics(runtime, fresh)) if fresh else None}

    # ── configuration ──────────────────────────────────────────────────────
    @staticmethod
    def _true_keys(server_type) -> set[str]:
        return {field.key for field in server_type.fields}

    @staticmethod
    def _toggle_keys(server_type) -> set[str]:
        return {toggle.key for toggle in server_type.toggles}

    @staticmethod
    def _invalid_message(server_type, exc: Exception) -> str:
        """« invalid value for param 'port' (Port) » plutôt que le seul libellé « Port »."""
        key = str(exc)
        for field in server_type.fields:
            if field.label_key == key:
                return f"invalid value for param {field.key!r} ({i18n.t(field.label_key)})"
        return i18n.t(key)

    @staticmethod
    def _env_is_redacted(value) -> bool:
        if not isinstance(value, str):
            return False
        return any(line.partition("=")[2].strip() == "***" for line in value.splitlines())

    def _create_server(self, args: dict) -> dict:
        type_id = self._require(args, "type")
        label = self._require(args, "label")
        server_type = service.get(type_id)
        if server_type is None:
            raise ToolError(f"Unknown server type {type_id!r}. Call list_server_types.")
        raw_params = args.get("params") if isinstance(args.get("params"), dict) else {}
        if self._env_is_redacted(raw_params.get("env")):
            raise ToolError("env values cannot be '***': pass the real values or omit env.")
        warnings = []
        unknown = sorted(set(raw_params) - self._true_keys(server_type))
        if unknown:
            warnings.append("Unknown param keys ignored: " + ", ".join(unknown))
        try:
            params = server_type.validate(raw_params)
        except ValueError as exc:
            raise ToolError(f"Invalid params for type {type_id!r}: "
                            f"{self._invalid_message(server_type, exc)}",
                            {"type": type_id, "fields": sorted(self._true_keys(server_type))})
        options = dict(server_type.default_options())
        raw_options = args.get("options") if isinstance(args.get("options"), dict) else {}
        unknown_options = sorted(set(raw_options) - self._toggle_keys(server_type))
        if unknown_options:
            warnings.append("Unknown option keys ignored: " + ", ".join(unknown_options))
        for key in self._toggle_keys(server_type):
            if key in raw_options:
                options[key] = _as_bool(raw_options[key])
        actions = args.get("actions") if isinstance(args.get("actions"), list) else []
        if len(actions) > 3:
            warnings.append("Only the first 3 custom actions are kept.")
        icon = str(args.get("icon") or "").strip()
        if icon:
            icon = os.path.normpath(os.path.expandvars(os.path.expanduser(icon)))
            if not os.path.isfile(icon):
                raise ToolError(f"Icon file not found: {icon}")
        enabled = _as_bool(args.get("enabled", True))
        select = _as_bool(args.get("select", False))

        def job():
            backup = self._backup_config()
            server = self.app.create_server(type_id, label, params, icon=icon,
                                            actions=actions, options=options,
                                            enabled=enabled, select=select)
            return backup, server

        backup, server = self.app.ui_call(job)
        self._log_mcp("log.mcp_agent_create", label=server.get("label", label))
        runtime = next((item for item in self.app.runtimes
                        if item.id == server.get("id")), None)
        return {"ok": True, "id": server.get("id"),
                "server": self._summary(runtime) if runtime else server,
                "warnings": warnings, "backup": backup}

    def _update_server(self, args: dict) -> dict:
        server_id = self._require(args, "server_id")
        runtime = self._runtime(server_id)
        server_type = runtime.type
        if server_type is None:
            raise ToolError(f"Server {server_id!r} has no loaded type/plugin.")
        current = dict(runtime.server.get("params") or {})
        params = None
        warnings = []
        if isinstance(args.get("params"), dict):
            if self._env_is_redacted(args["params"].get("env")):
                raise ToolError("env values are hidden in read results; omit env to keep the "
                                "current value, or pass the full env to replace it.")
            unknown = sorted(set(args["params"]) - self._true_keys(server_type))
            if unknown:
                warnings.append("Unknown param keys ignored: " + ", ".join(unknown))
            merged = dict(current)
            merged.update(args["params"])
            try:
                params = server_type.validate(merged)
            except ValueError as exc:
                raise ToolError(f"Invalid params for type {server_type.id!r}: "
                                f"{self._invalid_message(server_type, exc)}",
                                {"fields": sorted(self._true_keys(server_type))})
        options = None
        if isinstance(args.get("options"), dict):
            unknown_options = sorted(set(args["options"]) - self._toggle_keys(server_type))
            if unknown_options:
                warnings.append("Unknown option keys ignored: " + ", ".join(unknown_options))
            options = dict(runtime.server.get("options") or {})
            for key in self._toggle_keys(server_type):
                if key in args["options"]:
                    options[key] = _as_bool(args["options"][key])
        icon = None
        if "icon" in args:
            icon = str(args.get("icon") or "").strip()
            if icon:
                icon = os.path.normpath(os.path.expandvars(os.path.expanduser(icon)))
                if not os.path.isfile(icon):
                    raise ToolError(f"Icon file not found: {icon}")
        label = None
        if "label" in args:
            label = str(args.get("label") or "").strip()
            if not label:
                raise ToolError("label cannot be empty.")
        actions = None
        if isinstance(args.get("actions"), list):
            actions = args["actions"]
            if len(actions) > 3:
                warnings.append("Only the first 3 custom actions are kept.")
        enabled = None
        if "enabled" in args:
            enabled = _as_bool(args["enabled"])
        deps = None
        if "deps" in args:
            deps = str(args.get("deps") or "")
            dropped = len([p for p in deps.replace("\n", ";").split(";") if p.strip()]) - len(
                deps_mod.parse_spec(deps))
            if dropped > 0:
                warnings.append(f"{dropped} related-server entr{'y' if dropped == 1 else 'ies'} "
                                "ignored (expected \"name = target\", at most 8).")

        def job():
            backup = self._backup_config()
            self.app.apply_server_update(server_id, label=label, params=params,
                                         options=options, icon=icon, actions=actions,
                                         enabled=enabled, deps=deps)
            return backup

        backup = self.app.ui_call(job)
        updated = next((item for item in self.app.runtimes if item.id == server_id), None)
        self._log_mcp("log.mcp_agent_update", label=updated.label if updated else server_id)
        return {"ok": True, "server": self._summary(updated) if updated else {},
                "warnings": warnings, "backup": backup}

    def _delete_server(self, args: dict) -> dict:
        server_id = self._require(args, "server_id")
        confirm = self._require(args, "confirm")
        if confirm != server_id:
            raise ToolError("confirm must repeat the server id exactly.")
        runtime = self._runtime(server_id)
        label, phase = runtime.label, runtime.phase

        def job():
            backup = self._backup_config()
            self.app.delete_server(server_id)
            return backup

        backup = self.app.ui_call(job)
        self._log_mcp("log.mcp_agent_delete", label=label)
        note = "Tile deleted."
        if phase == "running":
            note += " A process it launched may still be running; delete does not stop it."
        return {"ok": True, "id": server_id, "label": label, "note": note,
                "backup": backup}


# ── contrôle d'accès ──────────────────────────────────────────────────────────
def check_request(headers, method: str, port: int, token: str) -> tuple[int, str] | None:
    """Refus éventuel d'une requête HTTP : (statut, raison) — ou None si elle est admise.

    Fonction pure (testable sans socket). `headers` : tout objet avec `.get(nom)`.
    """
    if headers.get("Origin"):
        # Un client MCP (SDK Python, Hermes) n'envoie jamais d'Origin ; un navigateur, si.
        return 403, "browser requests (Origin header) are not allowed"
    host = str(headers.get("Host") or "").strip().lower()
    allowed = {f"{name}:{port}" for name in _LOCAL_HOSTS}
    if host not in allowed:
        return 403, "Host header must be 127.0.0.1:%d or localhost:%d" % (port, port)
    if method == "POST":
        ctype = str(headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return 415, "Content-Type must be application/json"
    if method in ("POST", "GET", "DELETE"):
        if not token:
            return 503, "MCP token not configured"
        auth = str(headers.get("Authorization") or "")
        scheme, _, given = auth.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
                given.strip().encode("utf-8"), token.encode("utf-8")):
            return 401, "missing or invalid bearer token (see Background Check settings)"
    return None


# ── transport HTTP ────────────────────────────────────────────────────────────
class _ThreadingHTTPServer(ThreadingHTTPServer):
    #: Sous Windows, SO_REUSEADDR autorise DEUX sockets à écouter le même port : refus net.
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, handler, tools: McpTools, owner: "McpServer"):
        self.tools = tools
        self.owner = owner
        super().__init__(address, handler)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"BackgroundCheck/{__version__}"
    sys_version = ""

    def log_message(self, fmt, *args):  # pas de bruit dans la console de l'app
        return

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _refused(self, method: str) -> bool:
        """Applique `check_request` ; True si la requête a été refusée (réponse envoyée)."""
        owner = self.server.owner
        refusal = check_request(self.headers, method, owner.port, owner.token)
        if refusal is None:
            return False
        status, reason = refusal
        owner.note_refusal(status, reason)
        body = json.dumps(_rpc_error(None, -32001, reason)).encode("utf-8")
        self.send_response(status)
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="bgc"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True
        return True

    def do_POST(self):  # noqa: N802 (nom imposé par BaseHTTPRequestHandler)
        if self._refused("POST"):
            return
        if self.path.split("?", 1)[0].rstrip("/") != "/mcp":
            self._send_empty(404)
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send_json(413, _rpc_error(None, -32600, "Request too large"))
                return
            raw = self.rfile.read(length) if length else b""
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json(400, _rpc_error(None, -32700, "Parse error"))
            return
        try:
            reply = self.server.tools.handle_message(message)
        except Exception as exc:  # pragma: no cover
            self._send_json(500, _rpc_error(None, -32603, f"{type(exc).__name__}: {exc}"))
            return
        if reply is None:  # notification : 202 sans corps (exigé par le protocole)
            self._send_empty(202)
        else:
            self._send_json(200, reply)

    def do_GET(self):  # noqa: N802
        if self._refused("GET"):
            return
        # Sans en-tête de session, le client n'ouvre pas ce flux. 405 = « pas de flux serveur ».
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):  # noqa: N802
        if self._refused("DELETE"):
            return
        self._send_empty(200)

    def do_OPTIONS(self):  # noqa: N802
        # Jamais d'en-têtes CORS : un preflight venu d'une page web reste sans autorisation.
        if self._refused("OPTIONS"):
            return
        self.send_response(204)
        self.send_header("Allow", "POST, GET, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


class McpServer:
    """Serveur MCP embarqué : démarrage/arrêt explicites, état lisible par les réglages."""

    def __init__(self, app, port: int = DEFAULT_PORT, token: str = ""):
        self.app = app
        self.port = int(port or DEFAULT_PORT)
        #: Jeton exigé dans `Authorization: Bearer …` ; modifiable à chaud (régénération).
        self.token = str(token or "")
        self.error = ""
        self._last_refusal = 0.0
        self._httpd: _ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._httpd is not None and self._thread and self._thread.is_alive())

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}/mcp"

    def start(self) -> bool:
        if self.running:
            return True
        try:
            tools = McpTools(self.app)
            self._httpd = _ThreadingHTTPServer((HOST, self.port), _Handler, tools, self)
        except OSError as exc:
            self.error = f"{exc}"
            self._httpd = None
            return False
        self.error = ""
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name="bgc-mcp", daemon=True)
        self._thread.start()
        return True

    def note_refusal(self, status: int, reason: str) -> None:
        """Journalise un refus (au plus un toutes les 30 s : un client mal réglé insiste)."""
        now = time.time()
        if now - self._last_refusal < 30.0:
            return
        self._last_refusal = now
        log = getattr(self.app, "log_app", None)
        if callable(log):
            try:
                log("log.mcp_refused", status=status, reason=reason)
            except Exception:
                pass

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=3.0)
