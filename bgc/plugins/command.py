"""Commande personnalisée — le type « généraliste » : lance, surveille et arrête n'importe
quelle ligne de commande (serveur de dev, script, application), sans écrire de plugin.

Règles du socle respectées :
  * l'arrêt est **ciblé** (PID + enfants récursifs via `sysinfo.terminate_tree`) et n'est
    accordé que pour un process lancé par Background Check — un jumeau démarré hors de l'app est
    adopté (affiché, jamais tué), le bouton d'arrêt reste cliquable et écrit le refus ;
  * la détection d'un process externe se fait par **jetons d'argv** (nom du binaire + chacun
    de nos arguments), jamais par sous-chaîne — et les helpers Electron (`--type=…`) sont
    écartés pour éviter les faux positifs ;
  * aucune commande d'arrêt globale.

La commande est lancée via le shell (`shell=True`) : c'est une ligne de commande au sens
console, `.bat` et raccourcis compris. Le PID suivi est celui du vrai process quand le shell
n'en a qu'un seul enfant.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
import webbrowser
from collections import deque

import psutil

from .. import sysinfo
from ..service import (Controller, Field, PHASE_RUNNING, PHASE_STARTING, PHASE_STOPPED,
                       ROW_IDLE, ROW_OK, ROW_WARN, ServerType, Toggle, register)

_DETECTION_TTL = 5.0


class CommandController(Controller):
    def __init__(self, server: dict, log):
        super().__init__(server, log)
        self.proc: subprocess.Popen | None = None
        self._started_here = False
        self._started_at = 0.0
        self._adopted_logged = False
        self._auto_done = False
        self._stop_requested = False   # un arrêt demandé ici désarme la relance auto
        self._restart_count = 0        # tentatives de relance consécutives (max 3)
        self._last_restart = 0.0
        self._cache: tuple[float, int | None] = (0.0, None)
        # Sortie console du dernier lancement (80 dernières lignes) — chantier 4, 9 nov 2026.
        self._output: deque[str] = deque(maxlen=80)
        self._reader: threading.Thread | None = None
        self._exit_reported = False
        # Sondes supplémentaires (ports/URL en plus de la sonde principale) — cache 5 s.
        self._checks_cache: tuple[float, list[dict], list] = (0.0, [], [])

    # --- paramètres -------------------------------------------------------
    @property
    def host(self) -> str:
        return str(self.params.get("host") or "127.0.0.1")

    @property
    def local(self) -> bool:
        """Ce PC ? psutil ne voit que la machine locale — un hôte distant se sonde en TCP."""
        return self.host.strip().lower() in ("", "127.0.0.1", "localhost", "::1")

    def _listener(self) -> int | None:
        return sysinfo.listener_pid(self.port) if (self.port and self.local) else None

    def _remote_open(self) -> bool:
        return bool(self.port) and not self.local and sysinfo.tcp_open(self.host, self.port)

    @property
    def command(self) -> str:
        return str(self.params.get("command") or "").strip()

    @property
    def stop_command(self) -> str:
        """Commande d'arrêt personnalisée (ex. « lms server stop ») — facultative.

        Quand elle existe, c'est elle qui arrête le service (utile quand le
        démarrage rend la main immédiatement et que le vrai process n'est pas
        notre enfant) ; sinon l'arrêt tue le process lancé et ses enfants.
        """
        return str(self.params.get("stop_command") or "").strip()

    @property
    def workdir(self) -> str:
        return str(self.params.get("workdir") or "").strip()

    @property
    def port(self) -> int:
        try:
            return int(self.params.get("port") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def check_path(self) -> str:
        return str(self.params.get("check_url") or "")

    @property
    def can_control(self) -> bool:
        """Y a-t-il quelque chose à piloter ? Sans commande ni process lancé par nous,
        le serveur est en **observation pure** : pas d'interrupteur trompeur."""
        if self.command or self.stop_command:
            return True
        return self.proc is not None and self.proc.poll() is None

    def effective_url(self) -> str:
        """Adresse à ouvrir/copier : l'URL déclarée, sinon `http://hôte:port`."""
        url = str(self.params.get("url") or "").strip()
        if url:
            return url
        return f"http://{self.host}:{self.port}" if self.port else ""

    def _extra_env(self) -> dict:
        env = {}
        for line in str(self.params.get("env") or "").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
        return env

    # --- sondes supplémentaires (facultatives) -----------------------------
    @staticmethod
    def _norm_url(url: str) -> str:
        text = str(url or "").strip().lower().rstrip("/")
        for prefix in ("http://", "https://"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if text.startswith("localhost:"):
            text = "127.0.0.1:" + text[len("localhost:"):]
        return text

    def _has_matching_custom_web_action(self, url: str) -> bool:
        """Un bouton sur mesure ouvre-t-il déjà cette même URL ? Évite le doublon « Browser »."""
        wanted = self._norm_url(url)
        if not wanted:
            return False
        for item in self.server.get("actions") or []:
            if not isinstance(item, dict):
                continue
            target = str(item.get("target") or "").strip()
            if target.lower().startswith(("http://", "https://")) and self._norm_url(target) == wanted:
                return True
        return False

    def extra_checks(self) -> list[tuple[str, str]]:
        """Lignes « libellé = cible » du champ `extra_checks` (6 au maximum).

        Cible : un port (`8642`), un hôte et son port (`192.168.1.50:8123`) ou une URL
        (`http://127.0.0.1:8642/health`). Sans « = », la cible sert de libellé.
        """
        items: list[tuple[str, str]] = []
        for line in str(self.params.get("extra_checks") or "").splitlines():
            line = line.strip()
            if not line:
                continue
            name, sep, target = line.partition("=")
            if sep:
                name, target = name.strip(), target.strip()
            else:
                name, target = line, line
            if target:
                items.append((name or target, target))
        return items[:6]

    def _split_target(self, target: str) -> tuple[str, int]:
        if ":" in target:
            host, _, port = target.rpartition(":")
            try:
                return (host.strip() or self.host), int(port)
            except ValueError:
                return self.host, 0
        try:
            return self.host, int(target)
        except ValueError:
            return self.host, 0

    @staticmethod
    def _extra_label_with_port(name: str, port: int) -> str:
        if not port or str(port) in str(name):
            return name
        return f"{name} ({port})"

    def _extra_rows(self) -> list[dict]:
        """Une ligne par sonde supplémentaire — cache 5 s (6 appels HTTP à chaque cycle
        coûteraient plus que la sonde principale qu'ils complètent)."""
        checks = self.extra_checks()
        if not checks:
            return []
        now = time.time()
        cached_at, cached_rows, cached_checks = self._checks_cache
        if cached_checks == checks and (now - cached_at) < 5.0:
            return cached_rows
        rows: list[dict] = []
        for name, target in checks:
            if target.lower().startswith(("http://", "https://")):
                rest = target.split("://", 1)[1].split("/", 1)[0]
                _host, port = self._split_target(rest)
                label = self._extra_label_with_port(name, port)
                pid = (sysinfo.listener_pid(port)
                       if port and _host.strip().lower() in ("", "127.0.0.1", "localhost", "::1")
                       else None)
                if port and _host.strip().lower() in ("", "127.0.0.1", "localhost", "::1") and not pid:
                    rows.append(self.row(ROW_IDLE, label, "value.extra_closed",
                                         value_params={"port": port, "pid": None}))
                    continue
                probe = sysinfo.http_probe(target, timeout=1.5)
                code = probe.get("code")
                if probe["up"] and code == 503:
                    rows.append(self.row(ROW_WARN, label, "value.extra_loading",
                                         value_params={"code": code, "port": port, "pid": pid}))
                elif probe["up"] and (code is None or code < 400):
                    rows.append(self.row(ROW_OK, label, "value.extra_http",
                                         value_params={"code": code, "port": port, "pid": pid}))
                elif probe["up"]:
                    rows.append(self.row(ROW_WARN, label, "value.extra_http",
                                         value_params={"code": code, "port": port, "pid": pid}))
                else:
                    rows.append(self.row(ROW_WARN, label, "value.extra_down",
                                         value_params={"code": code, "port": port}))
                continue
            host, port = self._split_target(target)
            if not port:
                rows.append(self.row(ROW_WARN, name, "value.extra_invalid"))
                continue
            label = self._extra_label_with_port(name, port)
            if host.strip().lower() in ("", "127.0.0.1", "localhost", "::1"):
                pid = sysinfo.listener_pid(port)
                if not pid:
                    rows.append(self.row(ROW_IDLE, label, "value.extra_closed",
                                         value_params={"port": port, "pid": None}))
                elif "llama" in name.lower():
                    # Un llama-server dont le port écoute peut être encore en train de charger
                    # le modèle en VRAM (503 sur /health) : le dire franchement.
                    probe = sysinfo.http_probe(f"http://127.0.0.1:{port}/health", timeout=1.2)
                    code = probe.get("code")
                    if probe["up"] and code == 503:
                        rows.append(self.row(ROW_WARN, label, "value.extra_loading",
                                             value_params={"code": code, "port": port, "pid": pid}))
                    elif probe["up"] and code == 200:
                        rows.append(self.row(ROW_OK, label, "value.extra_ready",
                                             value_params={"port": port, "pid": pid}))
                    else:
                        rows.append(self.row(ROW_OK, label, "value.extra_open",
                                             value_params={"port": port, "pid": pid}))
                else:
                    rows.append(self.row(ROW_OK, label, "value.extra_open",
                                         value_params={"port": port, "pid": pid}))
            else:
                reachable = sysinfo.tcp_open(host, port)
                rows.append(self.row(ROW_OK if reachable else ROW_IDLE, label,
                                     "value.extra_remote_open" if reachable
                                     else "value.extra_closed",
                                     value_params={"host": host, "port": port}))
        self._checks_cache = (now, rows, checks)
        return rows

    # --- détection de process --------------------------------------------
    def _tokens(self) -> list[str]:
        try:
            parts = shlex.split(self.command, posix=False)
        except ValueError:
            parts = self.command.split()
        return [p.strip('"') for p in parts if p.strip('"')]

    @staticmethod
    def _norm_exe(name: str) -> str:
        base = os.path.basename(str(name)).lower()
        return base[:-4] if base.endswith(".exe") else base

    def _find_external(self) -> int | None:
        """PID d'un process qui correspond exactement à notre commande, lancé hors de l'app.

        Jetons d'argv : le nom du binaire (sans extension) + chacun de nos arguments qui ne
        sont pas des options. TTL de 5 s : cette recherche parcourt tous les process.
        """
        now = time.time()
        if now - self._cache[0] < _DETECTION_TTL:
            return self._cache[1]
        found = self._scan()
        self._cache = (now, found)
        return found

    def _scan(self) -> int | None:
        tokens = self._tokens()
        if not tokens:
            return None
        exe = self._norm_exe(tokens[0])
        args = [t.lower() for t in tokens[1:] if not t.startswith("-")]
        try:
            for proc in psutil.process_iter(["pid", "cmdline"]):
                argv = proc.info.get("cmdline") or []
                if not argv or self._norm_exe(argv[0]) != exe:
                    continue
                if any(a.startswith("--type=") for a in argv[1:]):
                    continue  # helper Electron/Chromium du même binaire
                low = [a.lower() for a in argv[1:]]
                if all(a in low for a in args):
                    return proc.info["pid"]
        except Exception:
            return None
        return None

    def _real_pid(self) -> int | None:
        """Le vrai process derrière notre lancement (le shell ou le lanceur .venv peut n'être
        qu'un intermédiaire)."""
        if self.proc is None or self.proc.poll() is not None:
            return None
        try:
            children = psutil.Process(self.proc.pid).children(recursive=True)
            if not children:
                return self.proc.pid
            listener = self._listener()
            if listener and any(c.pid == listener for c in children):
                return listener
            return children[-1].pid
        except Exception:
            pass
        return self.proc.pid

    def _pid_state(self) -> tuple[int | None, bool]:
        """(pid, est-ce-nous) : notre process d'abord, sinon un jumeau externe (adopté).

        `self._started_here` (« la montée courante vient de nous ») survit à la mort
        du shell de lancement : une commande du type « lms server start » rend la main
        tout de suite et laisse un serveur qui n'est pas notre enfant.
        """
        if self.proc is not None and self.proc.poll() is None:
            return self._real_pid() or self.proc.pid, True
        ext = self._find_external()
        return (ext, bool(self._started_here)) if ext else (None, False)

    # --- sortie console ----------------------------------------------------
    @staticmethod
    def _decode(raw: bytes) -> str:
        """cmd.exe écrit dans la page de code de la console : essayer utf-8 puis cp1252/850."""
        for encoding in ("utf-8", "cp1252", "cp850"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", "replace")

    def _start_reader(self) -> None:
        """Vide le tube du process dans un tampon circulaire (thread démon, jamais bloquant)."""
        proc = self.proc
        if proc is None or proc.stdout is None:
            return

        def pump():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line = self._decode(raw).rstrip("\r\n")
                    if line.strip():
                        self._output.append(line)
            except Exception:
                pass
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

        self._reader = threading.Thread(target=pump, name="cmd-output", daemon=True)
        self._reader.start()

    def output_lines(self) -> list[str]:
        """Dernières lignes écrites par la commande (pour la vue « Sortie console »)."""
        return list(self._output)

    def _last_output(self) -> str:
        for line in reversed(self._output):
            if line.strip():
                return line.strip()[:160]
        return "—"

    def _check_exit(self) -> None:
        """Commande morte ? Alors dire **pourquoi** : code de sortie + dernière ligne émise.

        Pas d'alerte quand la commande rend la main avec le code 0 et que le service tourne
        vraiment (cas `lms server start`, qui lance un démon puis se termine).
        """
        if self.proc is None or self._exit_reported:
            return
        code = self.proc.poll()
        if code is None:
            return
        self._exit_reported = True
        if self._stop_requested:  # arrêt demandé ici : ce n'est pas une chute
            return
        alive = (self._listener() is not None or self._remote_open()
                 or self._find_external() is not None)
        if code != 0 or not alive:
            elapsed = round(time.time() - self._started_at, 1) if self._started_at else 0.0
            self._log("log.command_exit_output", code=code, seconds=elapsed,
                      output=self._last_output())

    # --- sondage ----------------------------------------------------------
    def snapshot(self) -> dict:
        self._check_exit()
        pid, ours = self._pid_state()
        listener = self._listener()
        remote = self._remote_open()
        # « Vivant » = notre process, un jumeau détecté, le port local en écoute, ou le port
        # distant qui répond — le cas « lms server start » : la commande rend la main et le
        # serveur vit ailleurs.
        alive = pid is not None or listener is not None or remote
        owned = ours or self._started_here
        info = sysinfo.proc_info(pid or listener)
        http = {"up": False, "code": None}
        if alive and (listener or remote) and self.check_path:
            path = self.check_path if self.check_path.startswith("/") else "/" + self.check_path
            http = sysinfo.http_probe(f"http://{self.host}:{self.port}{path}")

        if not alive:
            phase = PHASE_STOPPED
        elif self.port and ((listener is None and not remote) or (self.check_path and not http["up"])):
            phase = PHASE_STARTING
        else:
            phase = PHASE_RUNNING

        command = self.command or "—"
        if len(command) > 48:
            command = command[:47] + "…"
        rows = [self.row(ROW_IDLE, "row.command", "value.command_text",
                         value_params={"command": command})]
        if alive and pid and owned:
            rows.append(self.row(ROW_OK, "row.command_proc", "value.command_running",
                                 value_params={"pid": pid}))
        elif alive and pid:
            rows.append(self.row(ROW_WARN, "row.command_proc", "value.command_external",
                                 value_params={"pid": pid}))
        elif alive:
            rows.append(self.row(ROW_OK, "row.command_proc", "value.command_running",
                                 value_params={"pid": listener}))
        else:
            rows.append(self.row(ROW_IDLE, "row.command_proc", "value.command_stopped"))
        if owned and alive and self._started_at:
            elapsed = sysinfo.fmt_uptime(time.time() - self._started_at)
            rows.append(self.row(ROW_IDLE, "row.command_uptime", "value.command_since",
                                 value_params={"uptime": elapsed}))
        if self.port:
            if self.local:
                rows.append(self.row(ROW_OK if listener else ROW_IDLE, "row.port",
                                     "value.listening" if listener else "value.free",
                                     {"port": self.port}, {"pid": listener}))
            else:
                rows.append(self.row(ROW_OK if remote else ROW_IDLE, "row.port",
                                     "value.remote_open" if remote else "value.remote_closed",
                                     {"port": self.port}, {"host": self.host}))
            if self.check_path and alive:
                path = self.check_path if self.check_path.startswith("/") else "/" + self.check_path
                rows.append(self.row(ROW_OK if http["up"] else ROW_WARN, "row.api",
                                     "value.watch_ok" if http["up"] else "value.watch_slow",
                                     value_params={"code": http.get("code")}))
        rows.extend(self._extra_rows())  # sondes supplémentaires (une ligne chacune)

        # Un bouton d'interrupteur n'a de sens que s'il y a quelque chose à lancer ou à
        # arrêter : en observation pure (port/URL seul), l'onglet n'affiche aucun Start/Stop.
        actions = []
        if alive:
            actions.append({"id": "toggle", "style": "danger", "label_key": "btn.stop",
                            "enabled": True})
        elif self.command:
            actions.append({"id": "toggle", "style": "primary", "label_key": "btn.start",
                            "enabled": True})
        url = self.effective_url()
        if url:
            if not self._has_matching_custom_web_action(url):
                actions.append({"id": "browser", "style": "ghost", "label_key": "btn.browser",
                                "enabled": True})
            actions.append({"id": "copy_url", "style": "ghost", "label_key": "btn.copy_url",
                            "enabled": True})
        if self.workdir and os.path.isdir(self.workdir):
            actions.append({"id": "folder", "style": "ghost", "label_key": "btn.open_folder",
                            "enabled": True})
        controllable = self.can_control
        # Le bouton « sortie console » n'apparaît que s'il y a quelque chose à lire : un bouton
        # qui ouvre une fenêtre vide est un piège (constaté avec le lanceur LlamaGUI, muet).
        if controllable and self._output:
            actions.append({"id": "output", "style": "ghost", "label_key": "btn.output",
                            "enabled": True})
        state_key = {"running": "status.command_running", "starting": "status.starting",
                     "stopped": "status.command_stopped"}.get(phase, "status.loading")
        if alive and not owned:
            state_key = "status.command_external"
        return {
            "phase": phase,
            "url": url,
            "rows": rows,
            "actions": actions,
            "toggles": [
                {"key": "start_on_app_start", "label_key": "toggle.command_autostart",
                 "value": bool(self.options.get("start_on_app_start"))},
                {"key": "auto_restart", "label_key": "toggle.command_auto_restart",
                 "value": bool(self.options.get("auto_restart"))},
                {"key": "stop_on_quit", "label_key": "toggle.stop_on_quit",
                 "value": bool(self.options.get("stop_on_quit"))},
                {"key": "stop_external", "label_key": "toggle.command_stop_external",
                 "value": bool(self.options.get("stop_external"))},
            ] if controllable else [],
            "meta": {"pid": pid or listener, "info": info},
            "extra": {"ours": owned, "readonly": not controllable},
            "tooltip": {"state_key": state_key, "pid": pid or listener},
        }

    # --- cycle de vie -----------------------------------------------------
    def adopt(self) -> None:
        """Un jumeau qui tourne déjà n'est pas le nôtre : on l'affiche, on ne l'arrête pas."""
        if self._started_here:
            return
        pid = self._find_external()
        if pid and not self._adopted_logged:
            self._adopted_logged = True
            info = sysinfo.proc_info(pid)
            self._log("log.command_adopted", pid=pid, name=info.get("name", "?"))

    def start(self) -> str:
        if not self.command:
            self._log("log.command_no_start")
            return "log.command_no_start"
        pid, ours = self._pid_state()
        listener = self._listener()
        if pid is not None or listener is not None or self._remote_open():
            self._log("log.command_already", pid=pid or listener)
            return "log.command_already"
        env = dict(os.environ)
        env.update(self._extra_env())
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self._output.clear()
        self._exit_reported = False
        try:
            self.proc = subprocess.Popen(
                self.command, shell=True, cwd=self.workdir or None, env=env,
                creationflags=creation,
                # Sortie capturée (stderr fondu dans stdout) : c'est ce qui permet de dire
                # POURQUOI une commande refuse de démarrer (port pris, mauvais argument…).
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        except Exception as exc:
            self._log("log.start_failed", error=str(exc))
            return "log.start_failed"
        self._start_reader()
        self._started_here = True
        self._started_at = time.time()
        self._adopted_logged = False
        self._stop_requested = False  # un démarrage (ré)arme la relance automatique
        self._cache = (0.0, None)
        time.sleep(0.3)  # laisser le shell élire son enfant : le pid journalisé = celui affiché
        pid = self._real_pid() or self.proc.pid
        self._log("log.command_started", pid=pid, command=self.command[:120])
        return "log.command_started"

    def stop(self) -> str:
        self._stop_requested = True  # un arrêt demandé ici ne doit jamais être suivi d'une relance
        pid, ours = self._pid_state()
        listener = self._listener()
        if pid is None and listener is None and not self._remote_open():
            self._reset()
            return "log.not_running"
        if self.stop_command:
            # Arrêt par commande personnalisée (ex. « lms server stop ») : réservé à ce
            # que nous avons lancé, sauf option explicite pour un service externe.
            if not (self._started_here or ours or self.options.get("stop_external")):
                info = sysinfo.proc_info(pid)
                self._log("log.command_stop_refused", pid=pid, name=info.get("name", "?"))
                return "log.command_stop_refused"
            return self._run_stop_command()
        # Arrêt ciblé : le process lancé par nous (et ses enfants récursifs), ou un service
        # externe si `stop_external` l'autorise explicitement.
        if self.proc is not None and self.proc.poll() is None:
            target = self.proc.pid  # tuer la racine (ex. cmd.exe) emporte tout l'arbre
        elif ours or self.options.get("stop_external"):
            target = pid or listener
        else:
            target = None
        if target is None:
            info = sysinfo.proc_info(pid or listener)
            self._log("log.command_stop_refused", pid=pid or listener, name=info.get("name", "?"))
            return "log.command_stop_refused"
        # Arrêt propre d'abord (CTRL_BREAK, 5 s) quand c'est NOTRE processus : il est chef de
        # son groupe (CREATE_NEW_PROCESS_GROUP). Un service adopté est arrêté comme avant.
        launched_here = self.proc is not None and target == self.proc.pid
        sysinfo.terminate_tree(target, 15.0, graceful=5.0 if launched_here else 0.0)
        if listener and listener != target and \
                sysinfo.listener_pid(self.port, fresh=True) is not None:
            sysinfo.terminate_tree(listener, 5.0)
        self._reset()
        self._log("log.command_stopped", pid=target)
        return "log.command_stopped"

    def _run_stop_command(self) -> str:
        env = dict(os.environ)
        env.update(self._extra_env())
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            done = subprocess.run(self.stop_command, shell=True, cwd=self.workdir or None,
                                  env=env, creationflags=creation, timeout=25,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except Exception as exc:
            self._log("log.command_stop_cmd_failed", error=str(exc)[:120])
            return "log.command_stop_cmd_failed"
        if done.returncode != 0:
            err = (done.stderr or b"").decode("utf-8", "replace").strip()[:120] \
                or f"code {done.returncode}"
            self._log("log.command_stop_cmd_failed", error=err)
            return "log.command_stop_cmd_failed"
        self._reset()
        self._log("log.command_stop_cmd_ok", command=self.stop_command[:120])
        return "log.command_stop_cmd_ok"

    def _reset(self) -> None:
        self.proc = None
        self._started_here = False
        self._started_at = 0.0
        self._cache = (0.0, None)

    # --- actions ----------------------------------------------------------
    def action(self, action_id: str) -> str:
        if action_id == "browser":
            url = self.effective_url()
            if not url:
                self._log("log.intro_no_url")
                return "log.intro_no_url"
            webbrowser.open(url)
            self._log("log.browser_open", url=url)
            return "log.browser_open"
        if action_id == "folder":
            path = self.workdir
            if not path or not os.path.isdir(path):
                self._log("log.folder_missing", path=path or "—")
                return "log.folder_missing"
            try:
                os.startfile(path)  # Windows : ouvre l'explorateur sur le dossier de travail
            except Exception as exc:
                self._log("log.folder_missing", path=f"{path} ({exc})")
                return "log.folder_missing"
            self._log("log.folder_open", path=path)
            return "log.folder_open"
        if action_id == "toggle":
            pid, _ours = self._pid_state()
            # Décider avec LA MÊME notion de « vivant » que l'affichage (process, port local
            # ou port distant) : sinon un service à la LM Studio (pas notre enfant) recevait
            # « démarrer » au clic sur « Arrêter » — constaté : le bouton relançait le serveur.
            alive = pid is not None or self._listener() is not None or self._remote_open()
            return self.stop() if alive else self.start()
        return "log.not_running"

    def maybe_auto_start(self, phase: str) -> None:
        """Deux comportements automatiques, tous deux **choisis** par l'utilisateur :

        1. « Démarrer la commande avec Background Check » : une seule tentative par session
           (pas de boucle si la commande échoue ou si le port est pris) ;
        2. « Relancer la commande si le service retombe » (`auto_restart`) : jamais après
           un arrêt demandé ici, 3 tentatives maximum consécutives (compteur remis à zéro
           dès que le service tourne de nouveau), 10 s minimum entre deux tentatives.
        """
        if not self._auto_done:
            self._auto_done = True
            if self.options.get("start_on_app_start") and phase == PHASE_STOPPED:
                self._log("log.intro_launch", command=self.command[:120])
                self.start()
                return
        if phase == PHASE_RUNNING:
            self._restart_count = 0
            return
        if phase != PHASE_STOPPED:
            return
        if not self.options.get("auto_restart") or self._stop_requested:
            return
        now = time.time()
        if self._restart_count >= 3:
            if self._restart_count == 3:
                self._restart_count += 1  # n'annoncer l'abandon qu'une seule fois
                self._log("log.command_restart_giveup")
            return
        if now - self._last_restart < 10.0:
            return
        self._last_restart = now
        self._restart_count += 1
        self._log("log.command_restart", count=self._restart_count)
        self.start()


class CommandType(ServerType):
    id = "command"
    label_key = "type.command.label"
    desc_key = "type.command.desc"
    fields = [
        # 1. Quoi observer ? — la sonde (un port suffit pour la surveillance seule)
        Field("host", "field.host", "text", "127.0.0.1", hint_key="field.host_hint",
              section_key="section.command_probe"),
        Field("port", "field.port", "int", "", hint_key="field.port_hint",
              section_key="section.command_probe"),
        Field("check_url", "field.check_url", "text", "", hint_key="field.check_url_hint",
              section_key="section.command_probe"),
        Field("extra_checks", "field.extra_checks", "text", "",
              hint_key="field.extra_checks_hint", section_key="section.command_probe"),
        # 2. Comment le piloter ? — facultatif par conception
        Field("command", "field.command", "text", "",   # facultatif : un serveur peut être
              hint_key="field.command_hint",            # seulement surveillé
              section_key="section.command_control", browse="exe"),
        Field("stop_command", "field.command_stop", "text", "",  # piloté à l'arrêt seulement
              hint_key="field.command_stop_hint",
              section_key="section.command_control"),
        Field("workdir", "field.command_workdir", "text", "",
              hint_key="field.command_workdir_hint",
              section_key="section.command_control", browse="dir"),
        Field("env", "field.command_env", "text", "", hint_key="field.command_env_hint",
              section_key="section.command_control"),
        # 3. Accès & affichage
        Field("url", "field.url", "text", "", hint_key="field.url_hint",
              section_key="section.command_access"),
    ]
    toggles = [
        Toggle("start_on_app_start", "toggle.command_autostart", False),
        Toggle("auto_restart", "toggle.command_auto_restart", False),
        Toggle("stop_on_quit", "toggle.stop_on_quit", False),
        Toggle("stop_external", "toggle.command_stop_external", False),
    ]
    phase_labels = {"running": "status.command_running", "starting": "status.starting",
                    "stopped": "status.command_stopped"}
    external_label_key = "status.command_external"
    phase_keys = {"running": "log.command_phase_running",
                  "stopped": "log.command_phase_stopped"}

    #: Dans les formulaires, host + port + chemin de vérification tiennent sur **une ligne** :
    #: « 127.0.0.1:5240 » ou « http://127.0.0.1:9931/v1/models ». Le config garde les trois.
    TARGET_FIELDS = ("host", "port", "check_url")

    def target_text(self, params: dict) -> str:
        host = str(params.get("host") or "127.0.0.1")
        port = str(params.get("port") or "").strip()
        check = str(params.get("check_url") or "").strip()
        if not port:
            return "" if host in ("127.0.0.1", "localhost") else host
        target = f"{host}:{port}"
        if check:
            target += check if check.startswith("/") else "/" + check
        return target

    def parse_target(self, text: str, base: dict) -> dict:
        """`5451`, `127.0.0.1:5451`, `:5451`, `hôte` ou une URL → host / port / check_url."""
        params = dict(base)
        value = (text or "").strip()
        if not value:
            params.update({"host": "127.0.0.1", "port": "", "check_url": ""})
            return params
        if "://" in value:
            value = value.split("://", 1)[1]
        path = ""
        if "/" in value:
            value, _, path = value.partition("/")
            path = "/" + path
        elif value.endswith("/"):
            value = value.rstrip("/")
        if value.isdigit():  # un port seul regarde cet ordinateur
            params.update({"host": "127.0.0.1", "port": value, "check_url": path})
            return params
        host, sep, port = value.rpartition(":")
        if not sep:
            host, port = value, ""
        elif not host:
            host = "127.0.0.1"
        params["host"] = host or "127.0.0.1"
        params["port"] = port if port.isdigit() else ""
        params["check_url"] = path
        return params

    def summary(self, params: dict) -> tuple[str, dict] | None:
        """Résumé honnête du mode réel : surveillance seule, pilotage complet, ou arrêt seul."""
        host = str(params.get("host") or "127.0.0.1")
        try:
            port = int(params.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        check = str(params.get("check_url") or "").strip()
        target = f"{host}:{port}" if port else "—"
        if port and check:
            target = f"http://{host}:{port}{check if check.startswith('/') else '/' + check}"
        start = str(params.get("command") or "").strip()
        stop = str(params.get("stop_command") or "").strip()
        if not start and not stop:
            return ("summary.command_watch", {"target": target})
        if not start:
            return ("summary.command_stop_only", {"target": target, "stop": stop})
        if not port:  # sans port, la « sonde » est le processus lui-même : le dire clairement
            if stop:
                return ("summary.command_control_noport", {"start": start, "stop": stop})
            return ("summary.command_kill_noport", {"start": start})
        if stop:
            return ("summary.command_control", {"target": target, "start": start, "stop": stop})
        return ("summary.command_kill", {"target": target, "start": start})

    def validate(self, raw: dict) -> dict:
        cleaned = super().validate(raw)
        # Observation pure acceptée : un port suffit (serveur tiers, machine du réseau).
        if not cleaned.get("command") and not cleaned.get("stop_command") \
                and not cleaned.get("port"):
            raise ValueError("add.error.command_or_port")
        return cleaned

    def controller(self, server: dict, log) -> Controller:
        return CommandController(server, log)


COMMAND = register(CommandType())
