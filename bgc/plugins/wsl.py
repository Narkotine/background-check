"""WSL — distributions Linux. Le cas « ce n'est pas un process » de l'abstraction.

Une distro WSL est une machine virtuelle gérée par Windows : pas de PID à surveiller, pas de
port à sonder. L'état réel vient de `wsl.exe -l -v` (36 ms mesuré ici, sortie UTF-16LE), mis
en cache court ; quand une distro tourne, une sonde invitée (`/proc`) donne uptime, charge et
nombre de processus (95 ms, cache 10 s).

Deux usages, un seul champ :
  * `distro` **vide** → toutes les distributions, **un élément par distribution** (modèle
    « liste d'items », 16 nov 2026) : démarrer / arrêter / shell / fichiers pour chacune.
    Avant, ce mode était en lecture seule ;
  * `distro` nommé → une distribution pilotable (démarrer / arrêter / shell / fichiers) —
    inchangé : les tuiles existantes n'ont aucune migration à subir.

Règle de sûreté (celle du socle) : une distribution déjà démarrée hors de Background Check est
**adoptée** et l'arrêt est **refusé** — sauf si l'utilisateur active explicitement l'option
« autoriser l'arrêt d'une distribution lancée hors de l'app ». Arrêter une distro tue les
processus qui tournent dedans : ce n'est jamais un geste anodin.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

from .. import sysinfo
from ..service import (Controller, Field, PHASE_RUNNING, PHASE_STOPPED, PHASE_UNKNOWN,
                       ROW_BAD, ROW_IDLE, ROW_OK, ROW_WARN, ServerType, Toggle, register)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WSL_EXE_MISSING = "wsl.exe introuvable"
WSL_TIMEOUT = "délai dépassé"


def probe_problem(error: str) -> tuple[str, str]:
    """(état de ligne, clé de valeur) pour une sonde WSL sans distribution.

    La sortie brute de wsl.exe (plusieurs lignes, dans la langue de Windows) n'est jamais
    affichée telle quelle : elle va dans l'infobulle et le journal. « Aucune distribution
    installée » n'est pas une erreur (pastille neutre).
    """
    if error == WSL_EXE_MISSING:
        return ROW_BAD, "value.wsl_exe_missing"
    if error == WSL_TIMEOUT:
        return ROW_BAD, "value.wsl_timeout"
    return ROW_IDLE, "value.wsl_none_installed"
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
    subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

KNOWN_STATES = {"running", "stopped", "installing", "uninstalling", "converting", "starting",
                "paused", "unknown"}
GUEST_CMD = "cat /proc/uptime; cat /proc/loadavg; ls -d /proc/[0-9]* 2>/dev/null | wc -l"


# ── accès à wsl.exe (sortie UTF-16LE, jamais d'exception) ─────────────────────
def _decode(raw: bytes) -> str:
    if not raw:
        return ""
    if raw.count(b"\x00") > max(2, len(raw) // 4):
        return raw.decode("utf-16-le", "replace")
    return raw.decode("utf-8", "replace")


def _wsl(args: list[str], timeout: int = 15) -> tuple[str, str, int]:
    """Renvoie (stdout, stderr, code). N'échoue jamais, même sans wsl.exe."""
    try:
        # WSL_UTF8=1 : sortie UTF-8 au lieu d'UTF-16LE (WSL récent) ; _decode gère les deux.
        proc = subprocess.run(["wsl.exe", *args], capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
                              env={**os.environ, "WSL_UTF8": "1"})
    except FileNotFoundError:
        return "", WSL_EXE_MISSING, -1
    except subprocess.TimeoutExpired:
        return "", WSL_TIMEOUT, -1
    except Exception as exc:  # pragma: no cover
        return "", str(exc), -1
    return (_decode(proc.stdout).replace("\x00", "").strip(),
            _decode(proc.stderr).replace("\x00", "").strip(), proc.returncode)


WSL_STATES = ("running", "stopped", "installing", "converting")


def parse_list(out: str, names: set[str] | None = None,
               running: set[str] | None = None) -> list[dict]:
    """Lignes de `wsl -l -v` → [{name, state, version, default}].

    Sans `names` : seuls les états anglais connus sont acceptés (filtre les messages et
    l'en-tête). Avec `names` (sortie de `wsl -l -q`) : toute ligne dont le premier mot est
    une distribution connue, quel que soit l'état affiché — `wsl.exe` traduit les états
    (« En cours d'exécution », « Arrêté », « Wird ausgeführt »…), en plusieurs mots.
    """
    distros: list[dict] = []
    for line in out.splitlines():
        text = line.strip()
        if not text:
            continue
        default = text.startswith("*")
        if default:
            text = text[1:].strip()
        parts = text.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        version = parts[-1] if parts[-1].isdigit() else ""
        state_text = " ".join(parts[1:-1] if version else parts[1:]).lower()
        if names is None:
            if state_text not in KNOWN_STATES:
                continue  # en-tête, ligne de message (« aucune distribution… »)
            state = state_text
        else:
            if name not in names:
                continue
            if state_text in KNOWN_STATES:
                state = state_text
            else:
                state = "running" if name in (running or set()) else "stopped"
        distros.append({"name": name, "state": state, "version": version, "default": default})
    return distros


def _names(out: str) -> set[str]:
    """Sortie de `wsl -l -q` : un nom par ligne (un nom WSL n'a pas d'espace)."""
    return {line.strip() for line in out.splitlines()
            if line.strip() and " " not in line.strip()}


def list_distros() -> tuple[list[dict], str]:
    """[{name, state, version, default}] + message d'erreur éventuel."""
    out, err, code = _wsl(["-l", "-v"])
    distros = parse_list(out)
    if not distros and code == 0 and out:
        # Windows non anglais : états traduits. Les noms (`-l -q`) et les distributions en
        # marche (`-l --running -q`) ne dépendent pas de la langue.
        names_out, _e, names_code = _wsl(["-l", "-q"])
        names = _names(names_out) if names_code == 0 else set()
        if names:
            running_out, _e, running_code = _wsl(["-l", "--running", "-q"])
            running = _names(running_out) if running_code == 0 else set()
            distros = parse_list(out, names, running)
    if not distros:
        return [], (err or out or "aucune distribution WSL")
    return distros, ""


def wsl_version() -> str:
    out, _err, _code = _wsl(["--version"])
    match = re.search(r"(\d+\.\d+\.\d+(?:\.\d+)?)", out)
    return match.group(1) if match else ("2" if out else "")


def guest_info(name: str) -> dict:
    """Uptime / charge / nombre de processus vus de l'intérieur (distro démarrée seulement)."""
    out, _err, code = _wsl(["-d", name, "-e", "/bin/sh", "-c", GUEST_CMD], timeout=8)
    if code != 0 or not out:
        return {}
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    info: dict = {}
    try:
        info["uptime"] = float(lines[0].split()[0])
    except (IndexError, ValueError):
        pass
    try:
        info["load"] = " ".join(lines[1].split()[:3])
    except (IndexError, ValueError):
        pass
    try:
        info["procs"] = int(lines[2])
    except (IndexError, ValueError):
        pass
    return info


def vm_memory_mb() -> float:
    """Mémoire du processus de la VM (vmmemWSL) — une seule par machine, toutes distros."""
    psutil = getattr(sysinfo, "psutil", None)
    if psutil is None:
        return 0.0
    total = 0.0
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            if (proc.info.get("name") or "").lower() in ("vmmemwsl", "vmmem"):
                total += proc.info["memory_info"].rss / (1024 * 1024)
        except Exception:
            continue
    return total


def shell_command(name: str) -> list[str]:
    """Commande qui ouvre un shell dans la distro (Windows Terminal si présent)."""
    if shutil.which("wt.exe"):
        return ["wt.exe", "-w", "new", "wsl.exe", "-d", name]
    return ["cmd.exe", "/c", "start", "Background Check", "wsl.exe", "-d", name]


class WSLController(Controller):
    def __init__(self, server: dict, log):
        super().__init__(server, log)
        self._cache: dict[str, tuple[float, object]] = {}
        self._ttl = {"list": 2.0, "version": 300.0, "guest": 10.0, "vm": 5.0}
        # Distributions démarrées par CETTE tuile : seul cas où l'arrêt est un droit acquis.
        self._started: set[str] = set()
        self._adopted_logged = False

    @property
    def _started_here(self) -> bool:
        return bool(self.distro) and self.distro.lower() in self._started

    # --- paramètres -------------------------------------------------------
    @property
    def distro(self) -> str:
        name = str(self.params.get("distro") or "").strip()
        return "" if name in ("", "*") else name

    # --- cache ------------------------------------------------------------
    def _cached(self, key: str, fn, ttl: float | None = None):
        now = time.time()
        hit = self._cache.get(key)
        limit = ttl if ttl is not None else self._ttl.get(key, 5.0)
        if hit and (now - hit[0]) < limit:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def invalidate(self) -> None:
        self._cache.clear()

    def _distros(self) -> tuple[list[dict], str]:
        return self._cached("list", list_distros)  # type: ignore[return-value]

    def find(self, distros: list[dict], name: str) -> dict | None:
        return next((d for d in distros if d["name"].lower() == name.lower()), None)

    # --- adoption ---------------------------------------------------------
    def adopt(self) -> None:
        """Une distro qui tourne déjà n'est pas la nôtre : on l'affiche, on ne l'arrête pas."""
        if not self.distro:
            return
        distros, error = self._distros()
        entry = self.find(distros, self.distro)
        if entry and entry["state"] == "running" and not self._adopted_logged:
            self._adopted_logged = True
            self._log("log.wsl_adopted", name=entry["name"])

    # --- sondage ----------------------------------------------------------
    def snapshot(self) -> dict:
        distros, error = self._distros()
        version = self._cached("version", wsl_version)
        vm = self._cached("vm", vm_memory_mb)
        if self.distro:
            return self._one(distros, error, str(version), vm)
        return self._all(distros, error, str(version), vm)

    def _probe_row(self, error: str) -> dict:
        """Ligne « Sonde WSL » quand aucune distribution n'est lue : message court traduit,
        texte brut de wsl.exe en infobulle et une fois dans le journal de la tuile."""
        status, key = probe_problem(error)
        raw = "\n".join(line.strip() for line in str(error).splitlines() if line.strip())
        row = self.row(status, "row.wsl_probe", key)
        row["tooltip"] = raw
        if raw and raw != getattr(self, "_raw_logged", None):
            self._raw_logged = raw
            self._log("log.wsl_probe_raw", text=" ".join(raw.split())[:300])
        return row

    def _line(self, version: str, running_count: int) -> dict:
        """Ligne du bandeau : deux clés pour éviter le « distribution(s) »."""
        key = "status.wsl_line_one" if running_count == 1 else "status.wsl_line_many"
        return {"key": key, "params": {"version": version or "?", "count": running_count}}

    def _one(self, distros: list[dict], error: str, version: str, vm: float) -> dict:
        target = self.distro
        entry = self.find(distros, target)
        running_count = sum(1 for d in distros if d["state"] == "running")

        if entry is None:
            # Distribution configurée absente : à signaler (ambre), pas une panne de l'app.
            rows = [self.row(ROW_WARN, "row.wsl_distro", "value.wsl_missing",
                             value_params={"name": target})]
            if error:
                rows.append(self._probe_row(error))
            else:
                rows.append(self.row(ROW_OK, "row.wsl_probe", "value.wsl_text",
                                     value_params={"text": version or "?"}))
            return {
                "phase": PHASE_UNKNOWN,
                "url": "",
                "rows": rows,
                "actions": [],
                "toggles": [],   # rien à arrêter : l'option serait sans objet
                "meta": {"pid": None, "line": self._line(version, running_count)},
                # Distribution introuvable : le dire, plutôt que « Lecture de l'état… » sans fin
                # (la phase reste « unknown », sa sémantique ne change pas).
                "extra": {"ours": True, "readonly": False,
                          "status_label_key": "value.wsl_missing",
                          "status_label_params": {"name": target}},
                "tooltip": {"state_key": "status.loading", "pid": None},
            }

        running = entry["state"] == "running"
        phase = PHASE_RUNNING if running else PHASE_STOPPED
        rows = [
            self.row(ROW_OK if running else ROW_IDLE, "row.wsl_distro", "value.wsl_distro",
                     value_params={"name": entry["name"], "version": entry["version"] or "?"}),
            self.row(ROW_OK if running else ROW_IDLE, "row.wsl_state",
                     "value.wsl_state_running" if running else "value.wsl_state_stopped"),
            self.row(ROW_OK if entry["default"] else ROW_IDLE, "row.wsl_default",
                     "value.yes" if entry["default"] else "value.no"),
            self.row(ROW_OK if running else ROW_IDLE, "row.wsl_origin",
                     "value.wsl_ours" if (running and self._started_here)
                     else "value.wsl_external" if running else "value.wsl_none"),
            self.row(ROW_OK if version else ROW_WARN, "row.wsl_probe", "value.wsl_text",
                     value_params={"text": version or "?"}),
        ]
        if running and vm:
            rows.append(self.row(ROW_OK, "row.wsl_vm", "value.wsl_mb",
                                 value_params={"mb": f"{vm:.0f}"}))
        if running:
            guest = self._cached("guest:" + entry["name"],
                                 lambda: guest_info(entry["name"]), ttl=10.0)
            if guest.get("uptime"):
                rows.append(self.row(ROW_OK, "row.wsl_uptime", "value.wsl_since",
                                     value_params={"uptime": sysinfo.fmt_uptime(guest["uptime"])}))
            if guest.get("load"):
                rows.append(self.row(ROW_OK, "row.wsl_load", "value.wsl_text",
                                     value_params={"text": str(guest["load"])}))
            if guest.get("procs"):
                rows.append(self.row(ROW_OK, "row.wsl_procs", "value.wsl_text",
                                     value_params={"text": str(guest["procs"])}))

        actions = []
        if running:
            actions.append({"id": "toggle", "style": "danger", "label_key": "btn.wsl_stop",
                            "enabled": True})   # refus explicite si distro externe (journal)
        else:
            actions.append({"id": "toggle", "style": "primary", "label_key": "btn.wsl_start",
                            "enabled": True})
        actions.append({"id": "shell", "style": "ghost", "label_key": "btn.wsl_shell",
                        "enabled": running})
        actions.append({"id": "files", "style": "ghost", "label_key": "btn.wsl_files",
                        "enabled": running})

        return {
            "phase": phase,
            "url": "",
            "rows": rows,
            "actions": actions,
            "toggles": [self._toggle_external()],
            "meta": {"pid": None, "line": self._line(version, running_count)},
            "extra": {"ours": (not running) or self._started_here, "readonly": False},
            "tooltip": {"state_key": ("status.wsl_running" if running else "status.wsl_stopped"),
                        "pid": None},
        }

    def _all(self, distros: list[dict], error: str, version: str, vm: float) -> dict:
        running_count = 0
        rows = []
        for entry in distros:
            running = entry["state"] == "running"
            running_count += running
            rows.append(self.row(ROW_OK if running else ROW_IDLE, "value.wsl_name",
                                 "value.wsl_item_running" if running else "value.wsl_item_stopped",
                                 label_params={"name": entry["name"]},
                                 value_params={"version": entry["version"] or "?"}))
        if error and not distros:
            rows = [self._probe_row(error)]
        else:
            default = next((d["name"] for d in distros if d["default"]), "")
            rows.append(self.row(ROW_IDLE, "row.wsl_mode", "value.wsl_mode_all"))
            if default:
                rows.append(self.row(ROW_OK, "row.wsl_default", "value.wsl_name",
                                     value_params={"name": default}))
            rows.append(self.row(ROW_OK if version else ROW_WARN, "row.wsl_probe",
                                 "value.wsl_text", value_params={"text": version or "?"}))
            if vm:
                rows.append(self.row(ROW_OK, "row.wsl_vm", "value.wsl_mb",
                                     value_params={"mb": f"{vm:.0f}"}))

        if not distros:
            phase = PHASE_UNKNOWN
        else:
            phase = PHASE_RUNNING if running_count else PHASE_STOPPED
        # Un élément par distribution, pilotable (même règle de sûreté qu'une tuile nommée).
        items = [self._item(entry) for entry in distros]
        # Les lignes « nom → état » sont désormais les éléments : on garde le résumé.
        rows = [row for row in rows if row.get("label_key") != "value.wsl_name"]
        return {
            "phase": phase,
            "url": "",
            "rows": rows,
            "actions": [],
            "toggles": [self._toggle_external()] if distros else [],
            "items": items,
            "items_title_key": "items.distros",
            "items_empty_key": "items.wsl_none",
            "meta": {"pid": None, "line": self._line(version, running_count)},
            "extra": {"ours": True, "readonly": False,
                      **({} if distros else {"status_label_key": "status.wsl_none"})},
            "tooltip": {"state_key": ("status.wsl_running" if running_count
                                      else "status.wsl_stopped"), "pid": None},
        }

    def _item(self, entry: dict) -> dict:
        running = entry["state"] == "running"
        name = entry["name"]
        if running:
            actions = [{"id": "stop", "label_key": "btn.item_terminate", "style": "danger",
                        "main": True, "stops": True}]
        else:
            actions = [{"id": "start", "label_key": "btn.item_start", "style": "primary",
                        "main": True}]
        actions += [{"id": "shell", "label_key": "btn.wsl_shell", "enabled": running},
                    {"id": "files", "label_key": "btn.wsl_files", "enabled": running}]
        origin = ("value.wsl_ours" if running and name.lower() in self._started
                  else "value.wsl_external" if running else "value.wsl_none")
        return {"id": name, "label": name + (" ★" if entry["default"] else ""),
                "phase": PHASE_RUNNING if running else PHASE_STOPPED,
                "detail_key": "value.wsl_item_detail",
                "detail_params": {"version": entry["version"] or "?",
                                  "state": ({"key": f"wsl.state.{entry['state']}"}
                                            if entry["state"] in WSL_STATES else entry["state"])},
                "rows": [self.row(ROW_OK if running else ROW_IDLE, "row.wsl_origin", origin)],
                "actions": actions}

    def item_action(self, item_id: str, action_id: str) -> str:
        distros, _error = self._distros()
        entry = self.find(distros, str(item_id))
        if entry is None:
            self._log("log.wsl_missing", name=item_id)
            return "log.wsl_missing"
        name = entry["name"]
        if action_id == "start":
            return self._start_name(name)
        if action_id == "stop":
            return self._stop_name(name)
        if action_id == "shell":
            return self._launch(shell_command(name), "log.wsl_shell", name=name)
        if action_id == "files":
            return self._launch(["explorer.exe", rf"\\wsl.localhost\{name}"],
                                "log.wsl_files", name=name)
        return "log.not_running"

    def _toggle_external(self) -> dict:
        return {"key": "stop_external", "label_key": "toggle.wsl_stop_external",
                "value": bool(self.options.get("stop_external"))}

    # --- actions ----------------------------------------------------------
    def start(self, wait: float = 90.0) -> str:
        if not self.distro:
            return "log.not_running"
        return self._start_name(self.distro, wait)

    def _start_name(self, name: str, wait: float = 90.0) -> str:
        self.invalidate()
        distros, _error = self._distros()
        entry = self.find(distros, name)
        if entry is None:
            self._log("log.wsl_missing", name=name)
            return "log.wsl_missing"
        if entry["state"] == "running":
            return "log.wsl_already"
        started = time.time()
        _out, err, code = _wsl(["-d", name, "-e", "/bin/true"], timeout=int(wait))
        self.invalidate()
        distros, _error = self._distros()
        entry = self.find(distros, name)
        if code != 0 or not entry or entry["state"] != "running":
            self._log("log.wsl_start_failed", name=name, error=(err or "état inchangé")[:80])
            return "log.wsl_start_failed"
        self._started.add(name.lower())
        self._adopted_logged = False
        self._log("log.wsl_started", name=name, seconds=round(time.time() - started, 1))
        return "log.wsl_started"

    def stop(self, wait: float = 20.0) -> str:
        if not self.distro:
            return "log.not_running"
        return self._stop_name(self.distro, wait)

    def _stop_name(self, name: str, wait: float = 20.0) -> str:
        self.invalidate()
        distros, _error = self._distros()
        entry = self.find(distros, name)
        if entry is None:
            self._log("log.wsl_missing", name=name)
            return "log.wsl_missing"
        if entry["state"] != "running":
            return "log.wsl_stopped_already"
        if name.lower() not in self._started and not self.options.get("stop_external"):
            self._log("log.wsl_stop_refused", name=name)
            return "log.wsl_stop_refused"
        _out, err, code = _wsl(["--terminate", name], timeout=int(wait))
        self.invalidate()
        self._started.discard(name.lower())
        distros, _error = self._distros()
        entry = self.find(distros, name)
        if code != 0 and entry and entry["state"] == "running":
            self._log("log.wsl_start_failed", name=name, error=(err or "état inchangé")[:80])
            return "log.wsl_start_failed"
        self._log("log.wsl_stopped", name=name)
        return "log.wsl_stopped"

    def action(self, action_id: str) -> str:
        name = self.distro
        if not name:
            return "log.not_running"
        if action_id == "toggle":
            distros, _error = self._distros()
            entry = self.find(distros, name)
            if entry and entry["state"] == "running":
                return self.stop()
            return self.start()
        if action_id == "shell":
            return self._launch(shell_command(name), "log.wsl_shell", name=name)
        if action_id == "files":
            return self._launch(["explorer.exe", rf"\\wsl.localhost\{name}"],
                                "log.wsl_files", name=name)
        return "log.not_running"

    def _launch(self, command: list[str], log_key: str, **params) -> str:
        try:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=DETACHED, close_fds=True)
        except Exception as exc:
            self._log("log.start_failed", error=str(exc))
            return "log.start_failed"
        self._log(log_key, **params)
        return log_key


class WSLType(ServerType):
    id = "wsl"
    label_key = "type.wsl.label"
    desc_key = "type.wsl.desc"
    fields = [
        Field("distro", "field.wsl_distro", "text", "", hint_key="field.wsl_distro_hint"),
    ]
    toggles = [
        Toggle("stop_external", "toggle.wsl_stop_external", False),
    ]
    phase_labels = {PHASE_RUNNING: "status.wsl_running", PHASE_STOPPED: "status.wsl_stopped"}
    external_label_key = "status.wsl_external"
    phase_keys = {PHASE_RUNNING: "log.wsl_phase_running",
                  PHASE_STOPPED: "log.wsl_phase_stopped"}

    def default_params(self) -> dict:
        """Pré-remplit la distribution par défaut détectée (l'utilisateur n'a qu'un nom à valider)."""
        base = {field.key: field.default for field in self.fields}
        try:
            distros, _error = list_distros()
            default = next((d["name"] for d in distros if d["default"]),
                           distros[0]["name"] if distros else "")
            base["distro"] = default
        except Exception:
            pass
        return base

    def summary(self, params: dict) -> tuple[str, dict] | None:
        distro = str(params.get("distro") or "").strip()
        if distro:
            return ("summary.wsl_distro", {"distro": distro})
        return ("summary.wsl_all", {})

    def controller(self, server: dict, log) -> Controller:
        return WSLController(server, log)


WSL = register(WSLType())
