"""Découverte de services sur cette machine et essais « à blanc » (sans Tk).

Sert au parcours d'ajout guidé et à l'écran de premier lancement (refonte UX du 16 nov 2026).
Rien ici ne lance un service : on lit la table des ports, on regarde si des programmes
connus sont installés, et l'essai « Tester maintenant » ne fait que sonder.
"""

from __future__ import annotations

import os
import shlex
import shutil

from . import presentation as pres
from . import presets, service, sysinfo


def own_pids() -> set[int]:
    """Ce processus et ses enfants (le lanceur PyInstaller/venv est le parent : inclus aussi)."""
    pids = {os.getpid()}
    try:
        import psutil
        me = psutil.Process(os.getpid())
        pids.update(child.pid for child in me.children(recursive=True))
        parent = me.parent()
        if parent is not None and parent.name().lower().startswith(("backgroundcheck", "hermesserv", "python")):
            pids.add(parent.pid)
    except Exception:
        pass
    return pids


def _exe_of(pid) -> str:
    if not pid:
        return ""
    try:
        import psutil
        return psutil.Process(pid).exe() or ""
    except Exception:
        return ""


def detected_services(servers: list[dict], own_ports: set[int] | None = None) -> list[dict]:
    """Services en écoute, filtrés et regroupés (voir `presentation.group_detected`)."""
    entries = sysinfo.listening_ports()
    groups = pres.group_detected(entries, tracked_ports=pres.tracked_ports(servers),
                                 own_ports=own_ports, own_pids=own_pids())
    for group in groups:
        group["exe"] = _exe_of(group.get("pid"))
    return groups


#: Programmes connus dont la présence sur le disque suffit à proposer une tuile.
_INSTALLED = (
    ("preset.lmstudio", presets.lmstudio_exe),
    ("preset.ollama", presets.ollama_exe),
    ("preset.llamagui", presets.llamagui_root),
    ("preset.llamacpp", presets.llama_server_exe),
    ("preset.koboldcpp", presets.koboldcpp_exe),
    ("preset.textgen", presets.textgen_root),
    ("preset.comfyui", presets.comfyui_root),
    ("preset.sillytavern", presets.sillytavern_root),
)


def installed_presets() -> list[str]:
    found = []
    for key, probe in _INSTALLED:
        try:
            if probe():
                found.append(key)
        except Exception:
            continue
    return found


def preset_server(key: str, name: str = "", port: int | None = None) -> dict | None:
    """Une tuile « commande » prête à créer depuis un modèle (ou None si invalide)."""
    server_type = service.get("command")
    if server_type is None:
        return None
    data = presets.values(key)
    raw = server_type.default_params()
    raw.update(data.get("fields") or {})
    if port:
        raw["port"] = str(port)
    try:
        params = server_type.validate(raw)
    except ValueError:
        return None
    return {"type": "command", "label": name or data.get("name") or "", "params": params,
            "icon": data.get("icon") or "", "actions": data.get("actions") or [],
            "preset": key}


def watch_server(group: dict) -> dict | None:
    """Une tuile « surveillance du port » pour un service détecté non reconnu."""
    server_type = service.get("command")
    if server_type is None:
        return None
    raw = server_type.default_params()
    raw.update({"host": "127.0.0.1", "port": str(group["port"])})
    try:
        params = server_type.validate(raw)
    except ValueError:
        return None
    name = pres.display_name(group.get("name")) or f"Port {group['port']}"
    icon = group.get("exe") if str(group.get("exe") or "").lower().endswith(".exe") else ""
    return {"type": "command", "label": name, "params": params, "icon": icon, "actions": []}


def command_exe(command: str, workdir: str = "") -> str:
    """Le programme réel d'une ligne de commande, s'il existe (pour l'icône automatique)."""
    try:
        parts = shlex.split(command or "", posix=False)
    except ValueError:
        parts = (command or "").split()
    if not parts:
        return ""
    first = parts[0].strip('"')
    candidates = [first]
    if workdir and not os.path.isabs(first):
        candidates.insert(0, os.path.join(workdir, first))
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    found = shutil.which(first)
    return found or ""


def auto_icon(command: str, workdir: str = "") -> str:
    """Icône proposée : l'exécutable de la commande s'il porte une icône (.exe)."""
    exe = command_exe(command, workdir)
    if not exe.lower().endswith(".exe"):
        return ""
    # Les interpréteurs (python, node, cmd…) ont une icône générique : pas utile.
    if os.path.basename(exe).lower() in ("python.exe", "pythonw.exe", "node.exe", "cmd.exe",
                                         "powershell.exe", "pwsh.exe", "py.exe"):
        return ""
    return exe


def dry_run(server_type, params: dict) -> list[tuple]:
    """« Tester maintenant » : sonde à blanc, **rien n'est lancé**.

    Renvoie [(statut, clé, paramètres)] avec statut ∈ ok / warn / bad / info.
    """
    results: list[tuple] = []
    port = 0
    try:
        port = int(params.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    host = str(params.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    local = host.lower() in ("127.0.0.1", "localhost", "::1", "")
    if port:
        if local:
            pid = sysinfo.listener_pid(port, fresh=True)
            if pid:
                name = (sysinfo.proc_info(pid) or {}).get("name", "?")
                results.append(("ok", "test.port_listening", {"port": port, "name": name}))
            else:
                results.append(("warn", "test.port_free", {"port": port}))
        else:
            reachable = sysinfo.tcp_open(host, port)
            results.append(("ok" if reachable else "warn",
                            "test.remote_open" if reachable else "test.remote_closed",
                            {"host": host, "port": port}))
        check = str(params.get("check_url") or "").strip()
        if check:
            path = check if check.startswith("/") else "/" + check
            url = f"http://{host}:{port}{path}"
            probe = sysinfo.http_probe(url, timeout=2.0)
            code = probe.get("code")
            if probe.get("up") and (code is None or code < 400):
                results.append(("ok", "test.http_ok", {"url": url, "code": code}))
            else:
                results.append(("warn", "test.http_down", {"url": url, "code": code or "—"}))
    for field in getattr(server_type, "fields", []):
        value = str(params.get(field.key) or "").strip()
        if not value:
            continue
        if getattr(field, "browse", None) == "dir" or field.kind == "path":
            ok = os.path.isdir(value)
            results.append(("ok" if ok else "bad", "test.dir_ok" if ok else "test.dir_missing",
                            {"path": value}))
    command = str(params.get("command") or "").strip()
    if command:
        exe = command_exe(command, str(params.get("workdir") or ""))
        if exe:
            results.append(("ok", "test.exe_ok", {"exe": exe}))
        else:
            results.append(("warn", "test.exe_unknown", {"command": pres.truncate(command, 60)}))
    if not results:
        results.append(("info", "test.nothing", {}))
    return results
