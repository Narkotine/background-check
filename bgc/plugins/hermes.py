"""Hermes — pilote séparément le Gateway local (8642) et l'Accès distant (Dashboard 9119).

Deux moteurs distincts coexistent :
  * le **Gateway local** (`python -m hermes_cli.main --profile default gateway run --quiet`,
    port 8642 par défaut, lu dans `config.yaml`) : sert l'application Desktop en mode local ;
  * le **Dashboard distant** (`hermes dashboard --no-open --skip-build`, port 9119) : sert
    l'interface web et l'accès Tailscale (téléphone / extérieur).

Les deux ont leur bouton Démarrer/Arrêter côte à côte sur la même tuile, leurs lignes d'état
dédiées, et un arrêt ciblé qui ne coupe jamais l'autre moteur.
"""

from __future__ import annotations

import os
import subprocess
import time
import webbrowser

from .. import sysinfo
from ..service import (Controller, Field, PHASE_RUNNING, PHASE_STARTING, PHASE_STOPPED,
                       ROW_BAD, ROW_IDLE, ROW_OK, ROW_WARN, ServerType, Toggle, register)


class HermesController(Controller):
    # --- accès aux paramètres --------------------------------------------
    @property
    def root(self) -> str:
        return str(self.params.get("hermes_root") or "")
    @property
    def host(self) -> str:
        return str(self.params.get("host") or "127.0.0.1")
    @property
    def port(self) -> int:
        try:
            return int(self.params.get("port") or 9119)
        except (TypeError, ValueError):
            return 9119
    @property
    def phone_node(self) -> str:
        return str(self.params.get("phone_node") or "")
    @property
    def dashboard_args(self) -> str:
        return str(self.params.get("dashboard_args") or "dashboard --no-open --skip-build")

    #: Plafond d'attente de l'URL du desktop avant de l'ouvrir quand même (s).
    WAIT_REMOTE_MAX = 120.0

    # --- état interne -----------------------------------------------------
    def __init__(self, server: dict, log):
        super().__init__(server, log)
        self.proc: subprocess.Popen | None = None
        self.owned_pid: int | None = None
        self.gateway_proc: subprocess.Popen | None = None
        self.owned_gateway_pid: int | None = None
        self._cache: dict[str, tuple[float, object]] = {}
        self._ttl = {"url": 300.0, "registry": 5.0, "tunnel": 15.0, "peers": 10.0,
                     "backends": 5.0, "desktop": 3.0, "remote": 1.0, "gateway": 2.5}
        self._desktop_auto_done = False
        self._desktop_wait_since = 0.0

    def _cached(self, key: str, fn):
        now = time.time()
        hit = self._cache.get(key)
        if hit and (now - hit[0]) < self._ttl.get(key, 5.0):
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def invalidate(self) -> None:
        self._cache.clear()

    # --- sondage ----------------------------------------------------------
    def dependencies(self) -> list[dict]:
        """L'application Hermes Desktop : liée (elle parle au gateway), lancée à part."""
        pid = self._cached("desktop", lambda: sysinfo.desktop_running(self.root))
        return [{"id": "desktop", "label_key": "dep.hermes_desktop",
                 "pids": [pid] if pid else [], "up": bool(pid)}]

    def snapshot(self) -> dict:
        pid = sysinfo.listener_pid(self.port)
        http = sysinfo.http_probe(f"http://{self.host}:{self.port}/api/status")
        url = self.desktop_url()

        if http["up"]:
            remote_phase = PHASE_RUNNING
        elif pid is not None:
            remote_phase = PHASE_STARTING
        else:
            remote_phase = PHASE_STOPPED

        gateway = self._cached("gateway", lambda: sysinfo.hermes_gateway_state(self.root))
        gw_port = gateway["port"]
        gw_pid = gateway["pid"]
        gw_health = (gateway.get("health") or {}).get("up", False)
        if gw_health or (gateway["listening"] and not gateway["local"]):
            local_phase = PHASE_RUNNING
        elif gw_pid is not None:
            local_phase = PHASE_STARTING
        else:
            local_phase = PHASE_STOPPED

        # Phase globale de la tuile : en ligne dès que le gateway local OU l'accès distant
        # tourne, avec un libellé de bandeau qui précise lequel (ou les deux).
        if remote_phase == PHASE_RUNNING and local_phase == PHASE_RUNNING:
            phase = PHASE_RUNNING
            status_label_key = "status.hermes_both"
            status_label_params = {"local_port": gw_port, "remote_port": self.port}
        elif remote_phase == PHASE_RUNNING:
            phase = PHASE_RUNNING
            status_label_key = "status.hermes_remote_only"
            status_label_params = {"port": self.port}
        elif local_phase == PHASE_RUNNING:
            phase = PHASE_RUNNING
            status_label_key = "status.hermes_local_only"
            status_label_params = {"port": gw_port}
        elif remote_phase == PHASE_STARTING or local_phase == PHASE_STARTING:
            phase = PHASE_STARTING
            status_label_key = "status.starting"
            status_label_params = {}
        else:
            phase = PHASE_STOPPED
            status_label_key = "status.stopped"
            status_label_params = {}

        active_pid = pid if pid is not None else gw_pid
        info = sysinfo.proc_info(active_pid)

        # --- Lignes de diagnostic structurées (Local / Distant / Desktop) -----
        rows = []
        # 1. Gateway local (8642)
        if gateway["enabled"] is False:
            rows.append(self.row(ROW_IDLE, "row.hermes_gateway", "value.hermes_gateway_disabled",
                                 label_params={"port": gw_port},
                                 value_params={"port": gw_port}))
        elif gateway["listening"]:
            key = ("value.hermes_gateway_on_version" if gateway["version"]
                   else "value.hermes_gateway_on")
            rows.append(self.row(ROW_OK if gw_health else ROW_WARN, "row.hermes_gateway", key,
                                 label_params={"port": gw_port},
                                 value_params={"host": gateway["host"],
                                               "port": gw_port,
                                               "version": gateway["version"],
                                               "pid": gw_pid or "?"}))
        else:
            rows.append(self.row(ROW_IDLE, "row.hermes_gateway", "value.hermes_gateway_off",
                                 label_params={"port": gw_port},
                                 value_params={"host": gateway["host"],
                                               "port": gw_port}))

        # 2. Accès distant / Dashboard (9119)
        data = http.get("data") or {}
        rows.append(self.row(ROW_OK if pid else ROW_IDLE, "row.port",
                             "value.listening" if pid else "value.free",
                             {"port": self.port}, {"pid": pid}))
        rows.append(self.row(ROW_OK if http["up"] else (ROW_WARN if pid else ROW_IDLE), "row.api",
                             "value.api_ok" if http["up"] else "value.api_down",
                             value_params={"path": "/api/status", "code": http.get("code")}))
        if pid:
            providers = data.get("auth_providers") or []
            required = bool(data.get("auth_required"))
            rows.append(self.row(ROW_OK, "row.auth",
                                 "value.auth_required" if required else "value.auth_none",
                                 value_params={"providers": ", ".join(providers) or "basic"}))
        # Tunnel : vert seulement si sa CIBLE répond. Une route vers un port où rien
        # n'écoute mène dans le vide — c'était affiché « OK » en vert (revue du 16 nov, b-3).
        target = self._cached("tunnel", sysinfo.tailscale_serve_target)
        target_alive = bool(target) and self._target_alive(target, pid)
        if target and target_alive:
            rows.append(self.row(ROW_OK, "row.tunnel", "value.tunnel_ok",
                                 value_params={"target": target}))
        elif target:
            rows.append(self.row(ROW_WARN, "row.tunnel", "value.tunnel_dead",
                                 value_params={"target": target}))
        else:
            rows.append(self.row(ROW_IDLE, "row.tunnel", "value.tunnel_none",
                                 value_params={"target": target}))
        node = self.phone_node
        phone_state = None  # None = non configuré, True/False = en ligne ou non
        phone_label = node
        if node:
            peers = self._cached("peers", sysinfo.tailscale_peers)
            peer = peers.get(node)
            if peer:
                phone_state, phone_label = bool(peer["online"]), peer["label"]
                rows.append(self.row(ROW_OK if peer["online"] else ROW_WARN, "row.phone",
                                     "value.phone_online" if peer["online"] else "value.phone_offline",
                                     value_params={"label": peer["label"]}))
            else:
                phone_state = False
                rows.append(self.row(ROW_WARN, "row.phone", "value.phone_offline",
                                     value_params={"label": node}))
        else:
            rows.append(self.row(ROW_IDLE, "row.phone", "value.phone_unknown"))

        # 3. Application Desktop & cible
        desktop_pid = self._cached("desktop", lambda: sysinfo.desktop_running(self.root))
        registry = self._cached("registry", sysinfo.desktop_target_url)
        if registry == "":
            rows.append(self.row(ROW_OK, "row.hermes_desktop_target",
                                 "value.hermes_target_local"))
        elif registry:
            rows.append(self.row(ROW_IDLE, "row.hermes_desktop_target",
                                 "value.hermes_target_remote", value_params={"url": registry}))
        else:
            rows.append(self.row(ROW_IDLE, "row.hermes_desktop_target",
                                 "value.hermes_target_unknown"))
        # L'état de l'application Desktop a sa propre ligne : le bouton « Desktop ✓ » mêlait
        # une action et un état (revue b-4).
        rows.append(self.row(ROW_OK if desktop_pid else ROW_IDLE, "row.hermes_desktop_app",
                             "value.hermes_desktop_running" if desktop_pid
                             else "value.hermes_desktop_closed",
                             value_params={"pid": desktop_pid or "?"}))

        can_stop_remote = (pid is not None and (
            pid == self.owned_pid or sysinfo.pid_is_hermes_dashboard(pid)))
        can_stop_local = (gw_pid is not None and (
            gw_pid == self.owned_gateway_pid or sysinfo.pid_is_hermes_gateway(gw_pid)))

        # Les deux boutons de contrôle côte à côte sur la même tuile :
        # 1) Gateway local (8642)  2) Accès distant (9119)
        actions = [
            # `main` : bascules principales (tray, vue d'ensemble) — le cœur de l'app ne
            # connaît plus les identifiants propres à Hermes (16 nov 2026).
            {"id": "toggle_gateway", "main": True,
             "style": "danger" if local_phase != PHASE_STOPPED else "primary",
             "label_key": ("btn.hermes_gw_stop" if local_phase == PHASE_RUNNING else
                           ("btn.starting" if local_phase == PHASE_STARTING
                            else "btn.hermes_gw_start")),
             "enabled": (False if (local_phase == PHASE_STARTING or gateway["enabled"] is False)
                         else True if gw_pid is None else can_stop_local)},
            {"id": "toggle", "main": True,
             "style": "danger" if remote_phase != PHASE_STOPPED else "primary",
             "label_key": ("btn.hermes_stop" if remote_phase == PHASE_RUNNING else
                           ("btn.starting" if remote_phase == PHASE_STARTING
                            else "btn.hermes_start")),
             "enabled": (False if remote_phase == PHASE_STARTING
                         else True if pid is None else can_stop_remote)},
            {"id": "desktop", "style": "ghost", "label_key": "btn.desktop",
             "enabled": True, "accent": bool(desktop_pid)},
            {"id": "browser", "style": "ghost", "label_key": "btn.hermes_webui",
             "enabled": remote_phase == PHASE_RUNNING},
            # L'URL (publique) ne sert que si l'accès distant tourne : grisé sinon.
            {"id": "copy_url", "style": "ghost", "label_key": "btn.copy_url",
             "enabled": bool(url) and remote_phase == PHASE_RUNNING},
        ]

        ours = True
        if pid is not None and not can_stop_remote:
            ours = False
        if gw_pid is not None and not can_stop_local:
            ours = False

        chain = self.chain_status(
            phase=phase, registry=registry, url=url, remote_phase=remote_phase,
            api_up=bool(http["up"]), pid=pid, local_phase=local_phase, gw_health=gw_health,
            gw_port=gw_port, gateway_enabled=gateway["enabled"], target=target,
            target_alive=target_alive, phone_state=phone_state, phone_label=phone_label,
            desktop_pid=desktop_pid)

        return {
            "phase": phase,
            "url": url,
            "health": chain["health"],
            "rows": rows,
            "actions": actions,
            "toggles": [
                {"key": "auto_launch_desktop", "label_key": "toggle.auto_desktop",
                 "value": bool(self.options.get("auto_launch_desktop"))},
                {"key": "stop_on_quit", "label_key": "toggle.stop_on_quit",
                 "value": bool(self.options.get("stop_on_quit"))},
            ],
            "meta": {"pid": active_pid, "info": info,
                     # deux process possibles (gateway local + dashboard) : tous mesurés
                     "pids": [p for p in (pid, gw_pid) if p],
                     "ports": [{"port": p, "role_key": k} for p, k in
                               ((self.port, "port.dashboard"), (gw_port, "port.gateway")) if p]},
            "extra": {
                "desktop_pid": desktop_pid,
                "ours": ours,
                "remote_phase": remote_phase,
                "local_phase": local_phase,
                "status_label_key": status_label_key,
                "status_label_params": status_label_params,
                # L'URL publique n'est « active » que si l'accès distant répond (revue b-3).
                "url_active": remote_phase == PHASE_RUNNING,
                # Format générique (service.py) : l'UI n'a plus de libellés Hermes en dur.
                "chain": {
                    "lines": [
                        {"id": "remote", "title_key": "chain.title_remote",
                         "links": chain["links"]["remote"],
                         "used": chain["mode"] == "remote", "note_key": "chain.used_by_desktop"},
                        {"id": "local", "title_key": "chain.title_local",
                         "links": chain["links"]["local"],
                         "used": chain["mode"] == "local", "note_key": "chain.used_by_desktop"},
                    ],
                    "missing": chain["missing"],
                },
                "chain_missing": chain["missing"],
            },
            "tooltip": {"state_key": _phase_key(phase), "pid": active_pid},
        }

    def _target_alive(self, target: str, dashboard_pid: int | None) -> bool:
        """La cible du tunnel Tailscale (`http://127.0.0.1:9119`) a-t-elle un écouteur ?"""
        rest = target.split("://", 1)[-1].split("/", 1)[0]
        tail = rest.rpartition(":")[2] if ":" in rest else ""
        try:
            port = int(tail) if tail else 80
        except ValueError:
            return False
        if port == self.port:
            return dashboard_pid is not None
        return sysinfo.listener_pid(port) is not None

    def chain_status(self, *, phase, registry, url, remote_phase, api_up, pid, local_phase,
                     gw_health, gw_port, gateway_enabled, target, target_alive, phone_state,
                     phone_label, desktop_pid) -> dict:
        """Statut **honnête** de la chaîne réellement utilisée (revue du 16 nov 2026, e-5).

        La `phase` de la tuile ne change pas (le reste de l'app et l'auto-desktop s'y fient) ;
        on ajoute un verdict de couleur `health` et deux chaînes lisibles :
        * accès distant : Téléphone → Tunnel Tailscale → Dashboard :9119 → API ;
        * local : Application Desktop → Gateway local :8642.

        Vert seulement si la chaîne que le **desktop utilise** répond en entier (mode local :
        le gateway ; mode distant : dashboard + API + tunnel vers une cible vivante). Une
        chaîne partielle est ambre, avec ce qui manque, en clair.
        """
        public = bool(url) and not _url_is_local(url)
        remote_full = api_up and (target_alive or not public)
        local_ok = bool(gw_health) or local_phase == PHASE_RUNNING

        def link(key, state, value_key, label_params=None, value_params=None):
            return {"label_key": key, "label_params": label_params or {}, "state": state,
                    "value_key": value_key, "value_params": value_params or {}}

        remote_links = []
        if phone_state is not None:
            remote_links.append(link("chain.phone", "ok" if phone_state else "warn",
                                     "chain.online" if phone_state else "chain.offline",
                                     value_params={"label": phone_label}))
        if target:
            remote_links.append(link("chain.tunnel", "ok" if target_alive else "warn",
                                     "chain.ok" if target_alive else "chain.tunnel_dead"))
        elif public:
            remote_links.append(link("chain.tunnel", "warn", "chain.none"))
        remote_links.append(link("chain.dashboard",
                                 {PHASE_RUNNING: "ok", PHASE_STARTING: "warn"}.get(remote_phase,
                                                                                  "idle"),
                                 "chain.listening" if pid else "chain.stopped",
                                 label_params={"port": self.port}))
        remote_links.append(link("chain.api", "ok" if api_up else ("warn" if pid else "idle"),
                                 "chain.ok" if api_up else "chain.no_answer"))
        local_links = [
            link("chain.desktop", "ok" if desktop_pid else "idle",
                 "chain.open" if desktop_pid else "chain.closed"),
            link("chain.gateway",
                 "idle" if gateway_enabled is False else
                 ("ok" if gw_health else ("warn" if local_phase == PHASE_STARTING else "idle")),
                 "chain.disabled" if gateway_enabled is False else
                 ("chain.ok" if gw_health else "chain.stopped"),
                 label_params={"port": gw_port}),
        ]

        mode = "local" if registry == "" else ("remote" if registry else "unknown")
        missing: list[tuple[str, dict]] = []
        if target and not target_alive:
            missing.append(("chain.missing_target", {"port": self.port}))
        if api_up and public and not target:
            missing.append(("chain.missing_tunnel", {"port": self.port}))
        if mode in ("remote", "unknown") and public and remote_phase != PHASE_RUNNING \
                and not (mode == "unknown" and local_ok):
            missing.append(("chain.missing_remote", {"url": url}))
        if mode == "local" and not local_ok:
            missing.append(("chain.missing_gateway", {"port": gw_port}))
        if phone_state is False:
            missing.append(("chain.phone_offline", {"label": phone_label}))

        health = None
        if phase == PHASE_RUNNING:
            if mode == "local":
                health = "ok" if local_ok else "warn"
            elif mode == "remote":
                health = "ok" if remote_full else "warn"
            else:
                health = "ok" if (local_ok or remote_full) else "warn"
        return {"health": health, "mode": mode, "missing": missing,
                "links": {"mode": mode, "remote": remote_links, "local": local_links}}

    # --- cycle de vie -----------------------------------------------------
    def adopt(self) -> None:
        pid = sysinfo.listener_pid(self.port)
        if pid is not None and self.owned_pid is None:
            self.owned_pid = pid
            self._log("log.adopted", port=self.port, pid=pid)
        gw = sysinfo.hermes_gateway_state(self.root)
        gw_pid = gw.get("pid")
        if gw_pid is not None and self.owned_gateway_pid is None and sysinfo.pid_is_hermes_gateway(gw_pid):
            self.owned_gateway_pid = gw_pid
            self._log("log.hermes_gw_adopted", port=gw.get("port", 8642), pid=gw_pid)

    def start(self, wait: float = 75.0) -> str:
        """Démarre l'accès distant (dashboard sur le port 9119)."""
        if sysinfo.listener_pid(self.port) is not None:
            return "log.already_listening"
        cli = sysinfo.find_hermes_cli(self.root)
        if not cli:
            self._log("log.no_cli", what="hermes.exe")
            return "log.no_cli"
        args = [cli] + self.dashboard_args.split() + ["--host", self.host, "--port", str(self.port)]
        self._log("log.intro_launch", command=" ".join(args))
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self.proc = subprocess.Popen(
                args, cwd=self.root or None, creationflags=creation,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except Exception as exc:
            self._log("log.start_failed", error=str(exc))
            return "log.start_failed"
        self.invalidate()
        deadline = time.time() + wait
        announced = False
        while time.time() < deadline:
            time.sleep(0.4)
            pid = sysinfo.listener_pid(self.port)
            if pid is not None and not announced:
                self.owned_pid = pid
                announced = True
                self._log("log.port_open", port=self.port, pid=pid)
            if announced and sysinfo.http_probe(
                    f"http://{self.host}:{self.port}/api/status", timeout=1.5)["up"]:
                self.invalidate()
                self._log("log.ready", seconds=round(wait - (deadline - time.time()), 1))
                return "log.ready"
        self.invalidate()
        return "log.api_slow" if announced else "log.no_port"

    def stop(self, wait: float = 15.0) -> str:
        """Arrête l'accès distant (port 9119) — ne touche jamais au gateway local 8642."""
        pid = sysinfo.listener_pid(self.port)
        if pid is None:
            self._reset()
            return "log.not_running"
        if self.owned_pid != pid and not sysinfo.pid_is_hermes_dashboard(pid):
            info = sysinfo.proc_info(pid)
            self._log("log.port_busy", port=self.port, pid=pid, name=info.get("name", "?"))
            return "log.port_busy"
        sysinfo.terminate_tree(pid, wait)
        if sysinfo.listener_pid(self.port) is None:
            self._reset()
            self._log("log.stopped", port=self.port)
            return "log.stopped"
        return "log.no_port"

    def start_gateway(self, wait: float = 45.0) -> str:
        """Démarre le Gateway local (port 8642) sans lancer le dashboard distant."""
        cfg = sysinfo.read_gateway_config(self.root)
        gw_host, gw_port = cfg["host"], cfg["port"]
        if sysinfo.listener_pid(gw_port) is not None:
            self._log("log.already_listening", port=gw_port)
            return "log.already_listening"
        spec = sysinfo.gateway_command(self.root)
        if not spec:
            self._log("log.no_cli", what="hermes gateway")
            return "log.no_cli"
        args, cwd = spec
        self._log("log.intro_launch", command=" ".join(args))
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self.gateway_proc = subprocess.Popen(
                args, cwd=cwd, creationflags=creation,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        except Exception as exc:
            self._log("log.start_failed", error=str(exc))
            return "log.start_failed"
        self.invalidate()
        deadline = time.time() + wait
        announced = False
        while time.time() < deadline:
            time.sleep(0.4)
            pid = sysinfo.listener_pid(gw_port)
            if pid is not None and not announced:
                self.owned_gateway_pid = pid
                announced = True
                self._log("log.port_open", port=gw_port, pid=pid)
            if announced and sysinfo.http_probe(
                    f"http://{gw_host}:{gw_port}/health", timeout=1.5)["up"]:
                self.invalidate()
                self._log("log.hermes_gw_ready", port=gw_port,
                          seconds=round(wait - (deadline - time.time()), 1))
                return "log.hermes_gw_ready"
        self.invalidate()
        return "log.api_slow" if announced else "log.no_port"

    def stop_gateway(self, wait: float = 15.0) -> str:
        """Arrête uniquement le Gateway local (port 8642)."""
        cfg = sysinfo.read_gateway_config(self.root)
        gw_port = cfg["port"]
        pid = sysinfo.listener_pid(gw_port)
        if pid is None:
            self._reset_gateway()
            return "log.not_running"
        if self.owned_gateway_pid != pid and not sysinfo.pid_is_hermes_gateway(pid):
            info = sysinfo.proc_info(pid)
            self._log("log.port_busy", port=gw_port, pid=pid, name=info.get("name", "?"))
            return "log.port_busy"
        sysinfo.terminate_tree(pid, wait)
        if sysinfo.listener_pid(gw_port) is None:
            self._reset_gateway()
            self._log("log.hermes_gw_stopped", port=gw_port)
            return "log.hermes_gw_stopped"
        return "log.no_port"

    def _reset(self) -> None:
        self.proc = None
        self.owned_pid = None
        self.invalidate()

    def _reset_gateway(self) -> None:
        self.gateway_proc = None
        self.owned_gateway_pid = None
        self.invalidate()

    # --- actions ----------------------------------------------------------
    def action(self, action_id: str) -> str:
        if action_id == "toggle":
            pid = sysinfo.listener_pid(self.port)
            return self.stop() if pid is not None else self.start()
        if action_id == "toggle_gateway":
            gw_port = sysinfo.read_gateway_config(self.root)["port"]
            pid = sysinfo.listener_pid(gw_port)
            return self.stop_gateway() if pid is not None else self.start_gateway()
        if action_id == "desktop":
            return self.launch_desktop("à la demande")
        if action_id == "browser":
            url = str(self.params.get("url") or "") or sysinfo.public_url_from_config(self.root) \
                or f"http://{self.host}:{self.port}"
            webbrowser.open(url)
            self._log("log.browser_open", url=url)
            return "log.browser_open"
        if action_id == "copy_url":
            return "log.url_copied"
        return "log.not_running"

    def launch_desktop(self, source: str = "à la demande") -> str:
        cmd = sysinfo.desktop_command(self.root)
        if not cmd:
            self._log("log.desktop_missing")
            return "log.desktop_missing"
        running = sysinfo.desktop_running(self.root)
        if running:
            self._log("log.desktop_already", pid=running)
            return "log.desktop_already"
        try:
            subprocess.Popen(
                cmd, cwd=os.path.dirname(cmd[0]) or None,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)
        except Exception as exc:
            self._log("log.start_failed", error=str(exc))
            return "log.start_failed"
        self._log("log.desktop_launched", source=source, exe=os.path.basename(cmd[0]))
        self.invalidate()
        return "log.desktop_launched"

    def desktop_url(self) -> str:
        """URL que l'app desktop utilisera : son registre d'abord, sinon la config Hermes."""
        registry = self._cached("registry", sysinfo.desktop_target_url)
        if registry:
            return registry
        return str(self._cached("url", lambda: str(self.params.get("url") or "")
                                or sysinfo.public_url_from_config(self.root)) or "")

    def _desktop_uses_local(self) -> bool | None:
        """True si l'app desktop est réglée sur « This computer » (aucune URL à attendre)."""
        registry = self._cached("registry", sysinfo.desktop_target_url)
        return None if registry is None else registry == ""

    def _remote_ready(self, url: str) -> bool:
        """Vrai quand l'URL du desktop répond vraiment (sonde cachée 1 s)."""
        target = url.rstrip("/") + "/api/status"
        return bool(self._cached("remote", lambda: sysinfo.http_probe(target, timeout=1.5)["up"]))

    def maybe_auto_desktop(self, phase: str) -> None:
        """Lancement automatique, une seule fois par montée du serveur.

        L'UI n'est ouverte que lorsque la cible qu'elle utilisera elle-même répond :
        * en mode local (« This device ») : dès que le gateway local (8642) ou le dashboard
          tourne ;
        * en mode distant (URL Tailscale) : uniquement quand le dashboard distant (9119)
          tourne et que son URL publique répond.
        """
        if phase == PHASE_STOPPED:
            self._desktop_auto_done = False
            self._desktop_wait_since = 0.0
            self._cache.pop("remote", None)  # une URL muette ne doit pas rester « prête »
            return
        if self._desktop_auto_done or not self.options.get("auto_launch_desktop"):
            return
        if phase != PHASE_RUNNING:
            return
        if self._desktop_uses_local() is True:
            self._log("log.desktop_local_mode")
        else:
            # En mode distant, ne pas déclencher tant que le port 9119 n'est pas en marche.
            if sysinfo.listener_pid(self.port) is None:
                return
            url = self.desktop_url()
            if url:
                if not self._desktop_wait_since:
                    self._desktop_wait_since = time.time()
                    self._log("log.desktop_wait_remote", url=url)
                if not self._remote_ready(url):
                    waited = time.time() - self._desktop_wait_since
                    if waited < self.WAIT_REMOTE_MAX:
                        return  # on repassera au prochain cycle du sondeur
                    self._log("log.desktop_wait_timeout", url=url, seconds=round(waited))
        self._desktop_auto_done = True
        if self._cached("desktop", lambda: sysinfo.desktop_running(self.root)):
            self._log("log.desktop_auto_skip")
            return
        self._log("log.desktop_auto")
        self.launch_desktop("automatique")


def _url_is_local(url: str) -> bool:
    from ..presentation import url_is_local
    return url_is_local(url)


def _phase_key(phase: str) -> str:
    return {PHASE_RUNNING: "status.online", PHASE_STARTING: "status.starting",
            PHASE_STOPPED: "status.stopped"}.get(phase, "status.loading")


class HermesType(ServerType):
    id = "hermes"
    label_key = "type.hermes.label"
    desc_key = "type.hermes.desc"
    fields = [
        Field("hermes_root", "field.hermes_root", "path", "", required=True,
              hint_key="field.hermes_root_hint", browse="dir"),
        Field("host", "field.host", "text", "127.0.0.1", required=True),
        Field("port", "field.port", "port", 9119, required=True),
        Field("url", "field.url", "text", "", hint_key="field.url_hint"),
        Field("phone_node", "field.phone_node", "text", "",
              hint_key="field.phone_node_hint"),
        Field("dashboard_args", "field.dashboard_args", "text", "dashboard --no-open --skip-build",
              hint_key="field.dashboard_args_hint"),
    ]
    toggles = [
        Toggle("auto_launch_desktop", "toggle.auto_desktop", False),
        Toggle("stop_on_quit", "toggle.stop_on_quit", False),
    ]

    def default_params(self) -> dict:
        """Pré-remplit le dossier Hermes s'il existe sur cette machine (ajout plus rapide)."""
        base = {f.key: f.default for f in self.fields}
        try:
            from ..config import hermes_home
            root = hermes_home()
            if os.path.isdir(root):
                base["hermes_root"] = root
        except Exception:
            pass
        return base

    def summary(self, params: dict) -> tuple[str, dict] | None:
        """Cet onglet pilote séparément le gateway local et l'accès web distant."""
        root = str(params.get("hermes_root") or "")
        gw_port = sysinfo.read_gateway_config(root)["port"] if root else 8642
        return ("summary.hermes", {"port": params.get("port") or 9119, "local_port": gw_port})

    def controller(self, server: dict, log) -> Controller:
        return HermesController(server, log)


HERMES = register(HermesType())
