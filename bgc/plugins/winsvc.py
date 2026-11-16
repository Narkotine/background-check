"""Service Windows — démarrer / arrêter un service, avec élévation UAC à la demande.

* état : `psutil.win_service_get()` (nom affiché, état, type de démarrage, PID), sinon
  `sc query` ;
* un seul service → une tuile classique (bouton Démarrer/Arrêter, tray) ; plusieurs noms
  (séparés par des virgules) → un **élément par service** (modèle « liste d'items ») ;
* démarrer/arrêter : `sc start|stop` d'abord **sans** droits d'administrateur ; si Windows
  répond « accès refusé » (erreur 5), la même commande est relancée **élevée** (fenêtre UAC :
  l'utilisateur garde la main, un refus est journalisé et rien d'autre ne se passe).

Les noms de service sont validés (lettres, chiffres, `_ . - $` et espaces) : ils finissent
dans une ligne de commande élevée, rien d'autre n'y entre.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

from .. import sysinfo
from ..service import (Controller, Field, PHASE_RUNNING, PHASE_STARTING, PHASE_STOPPED,
                       PHASE_UNKNOWN, ROW_BAD, ROW_IDLE, ROW_OK, ROW_WARN, ServerType, register)

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NAME_RE = re.compile(r"^[\w.\-$ ]{1,256}$")
#: Codes de `sc` / Win32 utiles.
ERROR_ACCESS_DENIED = 5
ERROR_CANCELLED = 1223
ERROR_SERVICE_DOES_NOT_EXIST = 1060


def split_names(text: str) -> list[str]:
    names = []
    for part in re.split(r"[,;\n]+", str(text or "")):
        name = part.strip()
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    return names


def phase_of(status: str) -> str:
    status = (status or "").lower()
    if status == "running":
        return PHASE_RUNNING
    if status in ("start_pending", "continue_pending", "stop_pending", "pause_pending"):
        return PHASE_STARTING
    if status in ("stopped", "paused"):
        return PHASE_STOPPED
    return PHASE_UNKNOWN


STATES = ("running", "stopped", "start_pending", "stop_pending", "paused", "pause_pending",
          "continue_pending")
START_TYPES = ("automatic", "manual", "disabled", "boot", "system")


def state_text(status: str):
    """État traduit à l'affichage ({"key": …}) ; un état inconnu reste tel quel."""
    status = (status or "").lower()
    return {"key": f"winsvc.state.{status}"} if status in STATES else (status or "?")


def start_text(start_type: str):
    start_type = {"auto": "automatic", "demand": "manual"}.get((start_type or "").lower(),
                                                             (start_type or "").lower())
    return {"key": f"winsvc.start.{start_type}"} if start_type in START_TYPES else start_type


def parse_sc_query(out: str) -> dict:
    """Sortie de `sc query <nom>` → {status, pid?} (« STATE : 4 RUNNING »)."""
    match = re.search(r"STATE\s*:\s*\d+\s+([A-Z_]+)", out or "")
    if not match:
        return {}
    pid = re.search(r"PID\s*:\s*(\d+)", out or "")
    return {"status": match.group(1).lower(), "pid": int(pid.group(1)) if pid else None}


def service_info(name: str) -> dict:
    """{name, display_name, status, start_type, pid, binpath} ; {} si introuvable."""
    psutil = getattr(sysinfo, "psutil", None)
    if psutil is not None and hasattr(psutil, "win_service_get"):
        try:
            data = psutil.win_service_get(name).as_dict()
            return {"name": data.get("name") or name,
                    "display_name": data.get("display_name") or name,
                    "status": str(data.get("status") or ""),
                    "start_type": str(data.get("start_type") or ""),
                    "pid": data.get("pid"),
                    "binpath": str(data.get("binpath") or "")}
        except Exception as exc:
            if "does not exist" in str(exc).lower() or "NoSuchProcess" in type(exc).__name__:
                return {}
    if os.name != "nt":
        return {}
    out, _err, code = run_sc(["query", name])
    if code == ERROR_SERVICE_DOES_NOT_EXIST:
        return {}
    parsed = parse_sc_query(out)
    if not parsed:
        return {}
    return {"name": name, "display_name": name, "start_type": "", "binpath": "", **parsed}


SC_ERRORS = {5: "accès refusé", 1051: "d'autres services dépendent de celui-ci",
             1052: "commande non valide pour ce service", 1053: "le service ne répond pas",
             1058: "service désactivé", 1060: "service introuvable",
             1068: "un service requis n'a pas démarré", 1069: "échec d'ouverture de session"}


def sc_error_text(code: int) -> str:
    return f"{SC_ERRORS[code]} ({code})" if code in SC_ERRORS else f"code {code}"


def run_sc(args: list[str], timeout: int = 20) -> tuple[str, str, int]:
    try:
        proc = subprocess.run(["sc.exe", *args], capture_output=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, text=True,
                              encoding="oem" if os.name == "nt" else "utf-8", errors="replace")
    except FileNotFoundError:
        return "", "sc.exe introuvable", -1
    except subprocess.TimeoutExpired:
        return "", "délai dépassé", -1
    except Exception as exc:  # pragma: no cover
        return "", str(exc), -1
    out = (proc.stdout or "").strip()
    code = proc.returncode
    failed = re.search(r"(?:FAILED|ÉCHEC|ECHEC)\s+(\d+)", out, re.IGNORECASE)
    if failed:
        code = int(failed.group(1))
    return out, (proc.stderr or "").strip(), code


def run_elevated(args: list[str], timeout: float = 60.0) -> int:
    """`sc.exe <args>` élevé (fenêtre UAC si Windows en affiche une) et **attendu**.

    Renvoie le code de sortie de sc.exe (0, 1056, 1062… comme sans élévation),
    ERROR_CANCELLED si l'élévation est refusée, ou -1 si l'attente dépasse `timeout`
    (fenêtre UAC laissée sans réponse). Avant : `ShellExecuteW` ne disait que « lancé » —
    un échec de sc.exe élevé se voyait seulement 20 s plus tard en « délai dépassé ».
    """
    if os.name != "nt":
        return -1
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    see_mask_nocloseprocess, see_mask_noasync, see_mask_flag_no_ui = 0x40, 0x100, 0x400
    params = " ".join(f'"{a}"' if " " in a else a for a in args)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_nocloseprocess | see_mask_noasync | see_mask_flag_no_ui
    info.lpVerb, info.lpFile, info.lpParameters, info.nShow = "runas", "sc.exe", params, 0
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        return ERROR_CANCELLED if error in (ERROR_CANCELLED, ERROR_ACCESS_DENIED) else error or -1
    if not info.hProcess:
        return 0
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) != 0:  # WAIT_OBJECT_0
            return -1
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


class WinServiceController(Controller):
    WAIT = 20.0

    def __init__(self, server: dict, log):
        super().__init__(server, log)
        self._elevated_once = False

    @property
    def names(self) -> list[str]:
        return [n for n in split_names(self.params.get("services")) if NAME_RE.match(n)]

    # --- sondage ----------------------------------------------------------
    def snapshot(self) -> dict:
        names = self.names
        infos = {name: service_info(name) for name in names}
        if len(names) == 1:
            return self._single(names[0], infos[names[0]])
        items = [self._item(name, infos[name]) for name in names]
        running = sum(1 for i in items if i["phase"] == PHASE_RUNNING)
        phase = (PHASE_UNKNOWN if not items or all(not infos[n] for n in names)
                 else PHASE_RUNNING if running == len(items)
                 else PHASE_STARTING if any(i["phase"] == PHASE_STARTING for i in items)
                 else PHASE_STOPPED if not running else PHASE_RUNNING)
        return {"phase": phase, "url": "", "actions": [], "toggles": [],
                "rows": [self.row(ROW_OK if running else ROW_IDLE, "row.winsvc_services",
                                  "items.count", value_params={"running": running,
                                                               "total": len(items)})],
                "items": items, "items_title_key": "items.services",
                "items_empty_key": "items.winsvc_none",
                "meta": {"pid": None, "pids": [i["pid"] for i in items if i.get("pid")],
                         "line": {"key": "status.winsvc_line",
                                  "params": {"running": running, "total": len(items)}}},
                "extra": {"readonly": False},
                "tooltip": {"state_key": "status.online" if running else "status.stopped",
                            "pid": None}}

    def _rows(self, name: str, info: dict) -> list[dict]:
        if not info:
            return [self.row(ROW_BAD, "row.winsvc_name", "value.winsvc_missing",
                             value_params={"name": name})]
        phase = phase_of(info["status"])
        state = ROW_OK if phase == PHASE_RUNNING else (ROW_WARN if phase == PHASE_STARTING
                                                        else ROW_IDLE)
        rows = [self.row(state, "row.winsvc_name", "value.wsl_text",
                         value_params={"text": f"{info['display_name']} ({info['name']})"}),
                self.row(state, "row.winsvc_state", "value.wsl_text",
                         value_params={"text": state_text(info["status"])})]
        if info.get("start_type"):
            rows.append(self.row(ROW_IDLE, "row.winsvc_start_type", "value.wsl_text",
                                 value_params={"text": start_text(info["start_type"])}))
        if info.get("pid"):
            rows.append(self.row(ROW_OK, "row.winsvc_pid", "value.wsl_text",
                                 value_params={"text": str(info["pid"])}))
        return rows

    def _actions(self, phase: str, info: dict, main_id: str) -> list[dict]:
        if not info:
            return []
        if phase == PHASE_RUNNING:
            return [{"id": main_id, "label_key": "btn.winsvc_stop", "style": "danger",
                     "main": True, "stops": True}]
        if phase == PHASE_STARTING:
            return [{"id": main_id, "label_key": "btn.starting", "style": "ghost",
                     "main": True, "enabled": False, "stops": False}]
        return [{"id": main_id, "label_key": "btn.winsvc_start", "style": "primary",
                 "main": True, "stops": False}]

    def _single(self, name: str, info: dict) -> dict:
        phase = phase_of(info.get("status", "")) if info else PHASE_UNKNOWN
        return {"phase": phase, "url": "", "toggles": [], "rows": self._rows(name, info),
                "actions": self._actions(phase, info, "toggle"),
                "meta": {"pid": info.get("pid") if info else None,
                         "line": {"key": "status.winsvc_one",
                                  "params": {"name": (info or {}).get("display_name") or name}}},
                "extra": {"readonly": False},
                "tooltip": {"state_key": {PHASE_RUNNING: "status.online",
                                          PHASE_STOPPED: "status.stopped"}.get(phase,
                                                                               "status.loading"),
                            "pid": None}}

    def _item(self, name: str, info: dict) -> dict:
        phase = phase_of(info.get("status", "")) if info else PHASE_UNKNOWN
        return {"id": name, "label": (info or {}).get("display_name") or name, "phase": phase,
                "detail_key": "value.winsvc_detail" if info else "value.winsvc_missing",
                "detail_params": {"name": name,
                                  "status": state_text((info or {}).get("status", "")),
                                  "start_type": start_text((info or {}).get("start_type", ""))},
                "rows": self._rows(name, info), "actions": self._actions(phase, info, "toggle"),
                "pid": (info or {}).get("pid")}

    # --- actions ----------------------------------------------------------
    def start(self) -> str:
        names = self.names
        return self._switch(names[0], "start") if len(names) == 1 else "log.not_running"

    def stop(self) -> str:
        names = self.names
        return self._switch(names[0], "stop") if len(names) == 1 else "log.not_running"

    def action(self, action_id: str) -> str:
        names = self.names
        if action_id == "toggle" and len(names) == 1:
            return self.item_action(names[0], "toggle")
        return "log.not_running"

    def item_action(self, item_id: str, action_id: str) -> str:
        name = next((n for n in self.names if n.lower() == str(item_id).lower()), None)
        if name is None:
            self._log("log.item_unknown", item=item_id)
            return "log.item_unknown"
        if action_id == "toggle":
            info = service_info(name)
            action_id = "stop" if info and phase_of(info["status"]) == PHASE_RUNNING else "start"
        if action_id not in ("start", "stop"):
            return "log.not_running"
        return self._switch(name, action_id)

    def _switch(self, name: str, verb: str) -> str:
        info = service_info(name)
        if not info:
            self._log("log.winsvc_missing", name=name)
            return "log.winsvc_missing"
        want = PHASE_RUNNING if verb == "start" else PHASE_STOPPED
        if phase_of(info["status"]) == want:
            return "log.winsvc_already"
        out, _err, code = run_sc([verb, name])
        if code == ERROR_ACCESS_DENIED:
            # Droits insuffisants : on demande l'élévation (fenêtre UAC), jamais en silence.
            self._log("log.winsvc_uac", name=name, action=verb)
            code = run_elevated([verb, name])
            if code == ERROR_CANCELLED:
                self._log("log.winsvc_denied", name=name)
                return "log.winsvc_denied"
            if code == -1:
                self._log("log.winsvc_failed", name=name, action=verb,
                          error="fenêtre UAC sans réponse")
                return "log.winsvc_failed"
            if code not in (0, 1056, 1062):
                self._log("log.winsvc_failed", name=name, action=verb,
                          error=sc_error_text(code))
                return "log.winsvc_failed"
        elif code not in (0, 1056, 1062):   # 1056 déjà démarré, 1062 pas démarré
            self._log("log.winsvc_failed", name=name, action=verb,
                      error=(out.splitlines()[-1] if out else f"code {code}")[:120])
            return "log.winsvc_failed"
        deadline = time.time() + self.WAIT
        while time.time() < deadline:
            info = service_info(name)
            if info and phase_of(info["status"]) == want:
                key = "log.winsvc_started" if verb == "start" else "log.winsvc_stopped"
                self._log(key, name=info.get("display_name") or name)
                return key
            time.sleep(0.5)
        self._log("log.winsvc_failed", name=name, action=verb, error="délai dépassé")
        return "log.winsvc_failed"


class WinServiceType(ServerType):
    id = "winsvc"
    label_key = "type.winsvc.label"
    desc_key = "type.winsvc.desc"
    fields = [Field("services", "field.winsvc_services", "text", "", required=True,
                    hint_key="field.winsvc_services_hint")]
    toggles = []
    phase_labels = {PHASE_RUNNING: "status.winsvc_running", PHASE_STOPPED: "status.winsvc_stopped"}

    def validate(self, raw: dict) -> dict:
        cleaned = super().validate(raw)
        names = split_names(cleaned.get("services"))
        if not names or any(not NAME_RE.match(n) for n in names):
            raise ValueError("field.winsvc_services")
        cleaned["services"] = ", ".join(names)
        return cleaned

    def summary(self, params: dict) -> tuple[str, dict] | None:
        names = split_names(params.get("services"))
        if len(names) == 1:
            return ("summary.winsvc_one", {"name": names[0]})
        return ("summary.winsvc_many", {"count": len(names)})

    def controller(self, server: dict, log) -> Controller:
        return WinServiceController(server, log)


WINSVC = register(WinServiceType())
