"""Sondes système réutilisables (processus, ports, HTTP, Tailscale, Hermes).

Règles (voir NOTES.md) :
  * le fil d'affichage ne sonde jamais : les plugins appellent ce module depuis le sondeur ;
  * préférer psutil / une lecture de fichier à un CLI — un CLI lance un interpréteur (2-3 s) ;
  * aucune commande d'arrêt globale : on cible un PID précis.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


def run(cmd: list[str], timeout: int = 15) -> tuple[str, str, int]:
    """Exécute une commande, renvoie (stdout, stderr, code). Ne lève jamais."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return p.stdout.strip(), p.stderr.strip(), p.returncode
    except FileNotFoundError:
        return "", f"introuvable: {cmd[0]}", -1
    except subprocess.TimeoutExpired:
        return "", "délai dépassé", -1
    except Exception as exc:  # pragma: no cover
        return "", str(exc), -1


# ── processus / ports ─────────────────────────────────────────────────────────

#: Instantané partagé des écouteurs TCP {port: pid} (0 = propriétaire illisible).
#: Revue du 16 nov 2026 : chaque tuile énumérait toute la table TCP à chaque cycle (parfois
#: plusieurs fois : port principal + sondes supplémentaires). Une seule énumération par
#: fenêtre de `LISTENERS_TTL` suffit à toutes les tuiles du même cycle de sondage.
LISTENERS_TTL = 1.0
_LISTENERS: tuple[float, dict[int, int] | None] = (0.0, None)
_LISTENERS_LOCK = threading.Lock()


def listeners_snapshot(fresh: bool = False) -> dict[int, int] | None:
    """{port: pid} des sockets TCP en écoute (règle `raddr` vide), ou None si psutil échoue."""
    global _LISTENERS
    if psutil is None:
        return None
    with _LISTENERS_LOCK:
        stamp, cached = _LISTENERS
        if not fresh and cached is not None and time.monotonic() - stamp < LISTENERS_TTL:
            return cached
        try:
            found: dict[int, int] = {}
            for conn in psutil.net_connections(kind="tcp"):
                if not conn.laddr or conn.raddr:
                    continue
                port = conn.laddr.port
                if conn.pid:
                    found[port] = int(conn.pid)  # écouteur identifié : il l'emporte
                else:
                    found.setdefault(port, 0)    # écoute confirmée, propriétaire illisible
        except Exception:
            return None
        _LISTENERS = (time.monotonic(), found)
        return found


def listener_pid(port: int, fresh: bool = False) -> int | None:
    """PID du process qui ÉCOUTE sur `port` (None si personne).

    Lit l'instantané partagé (`listeners_snapshot`, ≤ 1 s) ; `fresh=True` force une relecture
    (après un arrêt, pour ne pas viser un PID qui vient de disparaître).

    **Ne pas filtrer sur `conn.status == "LISTEN"`.** Sous Windows, psutil 7.x rapporte le même
    socket d'écoute avec des états fantaisistes qui changent d'un appel à l'autre (`LISTEN`,
    `NONE`, `CLOSE`, parfois `FIN_WAIT1`) : mesuré 5 appels de suite sur le port 9119 d'un
    dashboard qui répondait 200 à chaque fois. Filtrer sur l'état faisait clignoter l'UI entre
    « en ligne » et « arrêté ». On reconnaît donc un écouteur à ce qu'il n'a **pas de pair**
    distant (`raddr` vide) — les sockets ESTABLISHED/TIME_WAIT en ont un.
    """
    snapshot = listeners_snapshot(fresh)
    if snapshot is not None:
        return snapshot.get(int(port)) if int(port) in snapshot else None
    out, _, _ = run(["netstat", "-ano", "-p", "tcp"], timeout=10)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
            if parts[1].endswith(f":{port}"):
                try:
                    return int(parts[4])
                except ValueError:
                    return None
    return None



def tcp_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Connexion TCP brève : pour surveiller un hôte DISTANT (psutil ne voit que ce PC)."""
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def listening_ports() -> list[dict]:
    """Ports TCP en écoute sur la machine : [{port, pid, name}] triés par port.

    Pour suggérer des valeurs au formulaire d'ajout (étape 3 : ajout assisté). Lecture
    seule, une seule passe psutil ; un écouteur est reconnu à l'absence de pair distant
    (même règle que `listener_pid`, pas de filtre sur l'état fantaisiste).
    """
    found: dict[int, dict] = {}
    if psutil is not None:
        try:
            for conn in psutil.net_connections(kind="tcp"):
                if not conn.laddr or conn.raddr:
                    continue
                port = conn.laddr.port
                if port in found:
                    continue
                info = proc_info(conn.pid) if conn.pid else {}
                found[port] = {"port": port, "pid": conn.pid or None,
                               "name": info.get("name", "")}
        except Exception:
            pass
    return sorted(found.values(), key=lambda item: item["port"])


def proc_info(pid: int | None) -> dict:
    if not pid or psutil is None:      # 0 = écouteur sans propriétaire lisible : inconnu
        return {}
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            return {
                "pid": pid,
                "name": proc.name(),
                "cmdline": " ".join(proc.cmdline() or [])[:300],
                "uptime": max(0.0, time.time() - proc.create_time()),
                "rss_mb": proc.memory_info().rss / (1024 * 1024),
                "create_time": proc.create_time(),
            }
    except Exception:
        return {}


CTRL_BREAK_EVENT = 1
_CONSOLE_LOCK = threading.Lock()


def send_ctrl_break(pid: int) -> bool:
    """Envoie CTRL_BREAK au **groupe de processus** `pid` (créé avec CREATE_NEW_PROCESS_GROUP).

    Une app sans console (exe fenêtré, pythonw) ne partage pas la console cachée de ses
    enfants : on s'y attache le temps d'envoyer l'événement (en s'immunisant soi-même), puis
    on s'en détache. Si l'app a sa propre console (lancement de développement), on ne tente
    rien : se détacher de sa console serait pire que le mal. True si l'événement est parti.
    """
    if sys.platform != "win32" or not pid:
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if kernel32.GetConsoleWindow():
        return False
    with _CONSOLE_LOCK:
        if not kernel32.AttachConsole(int(pid)):
            return False  # pas de console (application fenêtrée) : rien à envoyer
        try:
            kernel32.SetConsoleCtrlHandler(None, True)   # ne pas se l'envoyer à soi-même
            return bool(kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, int(pid)))
        finally:
            kernel32.FreeConsole()
            time.sleep(0.05)
            kernel32.SetConsoleCtrlHandler(None, False)


def terminate_tree(pid: int, wait: float = 15.0, graceful: float = 0.0) -> bool:
    """Termine un process et ses enfants. True si le process a disparu.

    `graceful` (secondes, 0 = non) : envoie d'abord CTRL_BREAK au groupe et laisse ce délai
    au programme pour se fermer proprement (vider ses fichiers, libérer son port) avant
    l'arrêt forcé. À réserver à un processus **lancé par l'app** avec
    CREATE_NEW_PROCESS_GROUP — c'est lui le chef de groupe (revue du 16 nov 2026).
    """
    if graceful and psutil is not None:
        try:
            proc = psutil.Process(pid)
            family = [proc] + proc.children(recursive=True)
            if send_ctrl_break(pid):
                _gone, alive = psutil.wait_procs(family, timeout=graceful)
                if not alive:
                    return True
        except Exception:
            pass
    try:
        if psutil is not None:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    child.terminate()
                except Exception:
                    pass
            proc.terminate()
            psutil.wait_procs([proc], timeout=wait)
        else:  # pragma: no cover
            run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=int(wait))
        deadline = time.time() + wait
        while time.time() < deadline:
            if psutil is None or not psutil.pid_exists(pid):
                return True
            time.sleep(0.2)
        return psutil is None or not psutil.pid_exists(pid)
    except Exception:
        return False


# ── HTTP ──────────────────────────────────────────────────────────────────────

def http_probe(url: str, timeout: float = 2.5) -> dict:
    """Renvoie {up, code, data} — `up` = on a obtenu une réponse HTTP (même 401/404)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(4096).decode("utf-8", "replace")
            data = {}
            if raw.strip().startswith("{"):
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}
            return {"up": True, "code": resp.status, "data": data}
    except urllib.error.HTTPError as exc:
        return {"up": True, "code": exc.code, "data": {}}
    except Exception:
        return {"up": False, "code": None, "data": {}}


# ── Tailscale ─────────────────────────────────────────────────────────────────

def tailscale_serve_target() -> str:
    out, _, _ = run(["tailscale", "serve", "status"], timeout=10)
    match = re.search(r"proxy\s+(http://\S+)", out)
    return match.group(1) if match else ""


def tailscale_peers() -> dict:
    """{nom-court-dns: {'label': nom lisible, 'online': bool}}."""
    out, _, _ = run(["tailscale", "status", "--json"], timeout=10)
    if not out:
        return {}
    try:
        data = json.loads(out)
    except Exception:
        return {}
    peers = {}
    for peer in (data.get("Peer") or {}).values():
        dns = (peer.get("DNSName") or "").rstrip(".")
        short = dns.split(".")[0] if dns else (peer.get("HostName") or "?")
        peers[short] = {"label": peer.get("HostName") or short, "online": bool(peer.get("Online"))}
    return peers


# ── Hermes ────────────────────────────────────────────────────────────────────

def find_hermes_cli(root: str) -> str | None:
    for rel in (("hermes-agent", "venv", "Scripts", "hermes.exe"),
                ("hermes-agent", ".hermes", "bin", "hermes.exe")):
        path = os.path.join(root, *rel)
        if os.path.isfile(path):
            return path
    from shutil import which
    return which("hermes")


def gateway_command(root: str, profile: str = "default") -> tuple[list[str], str | None] | None:
    """Commande directe du gateway local (`python -m hermes_cli.main --profile … gateway run --quiet`).

    Préfère l'interpréteur embarqué sous `<root>/tools/python-*/python.exe` avec `cwd` sur
    `<root>/hermes-agent` (la forme exacte que lance l'app desktop) ; sinon retombe sur
    `hermes.exe gateway run --quiet`.
    """
    agent_dir = os.path.join(root, "hermes-agent")
    tools_dir = os.path.join(root, "tools")
    if os.path.isdir(agent_dir) and os.path.isdir(tools_dir):
        candidates = []
        try:
            for entry in os.listdir(tools_dir):
                if entry.lower().startswith("python-"):
                    exe = os.path.join(tools_dir, entry, "python.exe")
                    if os.path.isfile(exe):
                        candidates.append(exe)
        except OSError:
            candidates = []
        if candidates:
            python_exe = sorted(candidates)[-1]
            return ([python_exe, "-m", "hermes_cli.main", "--profile", profile,
                     "gateway", "run", "--quiet"], agent_dir)
    cli = find_hermes_cli(root)
    if cli:
        return ([cli, "--profile", profile, "gateway", "run", "--quiet"],
                agent_dir if os.path.isdir(agent_dir) else (root or None))
    return None


def pid_is_hermes_gateway(pid: int | None) -> bool:
    """Ce PID est-il bien un processus `hermes … gateway` ? Garde-fou avant un arrêt."""
    if not pid or psutil is None:
        return False
    try:
        argv = list(psutil.Process(int(pid)).cmdline() or [])
    except Exception:
        return False
    return _argv_is_hermes_backend(argv) == "gateway"


def pid_is_hermes_dashboard(pid: int | None) -> bool:
    """Ce PID est-il bien un processus `hermes … dashboard|serve` ?"""
    if not pid or psutil is None:
        return False
    try:
        argv = list(psutil.Process(int(pid)).cmdline() or [])
    except Exception:
        return False
    return _argv_is_hermes_backend(argv) in ("dashboard", "serve")


def find_desktop_app(root: str) -> str | None:
    for name in ("Hermes.exe", "hermes.exe"):
        path = os.path.join(root, "hermes-agent", "apps", "desktop", "release", "win-unpacked", name)
        if os.path.isfile(path):
            return path
    return None


def desktop_command(root: str) -> list[str] | None:
    exe = find_desktop_app(root)
    if exe:
        return [exe]
    cli = find_hermes_cli(root)
    return [cli, "desktop", "--skip-build"] if cli else None


def desktop_running(root: str) -> int | None:
    """PID d'un process de l'app desktop Electron, s'il tourne."""
    if psutil is None:
        return None
    known = (find_desktop_app(root) or "").lower().replace("/", "\\")
    for proc in psutil.process_iter(["name", "exe"]):
        name = (proc.info.get("name") or "").lower()
        exe = (proc.info.get("exe") or "").lower().replace("/", "\\")
        if name != "hermes.exe":
            continue
        if (known and exe == known) or "win-unpacked" in exe or "hermes-desktop" in exe:
            return proc.pid
    return None


def process_running(name: str) -> int | None:
    """PID d'un process dont le nom d'exécutable correspond (insensible à la casse).

    Sert aux boutons sur mesure : « Fermer LM Studio » ne doit pas avoir l'air disponible
    quand l'application ne tourne pas (10 nov 2026). Analyse par nom exact, jamais par
    sous-chaîne : `LM Studio.exe` ne doit pas ramasser `LM Studio Helper.exe`.
    """
    if psutil is None or not str(name or "").strip():
        return None
    wanted = os.path.basename(str(name).strip().strip('"')).lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == wanted:
                return proc.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def public_url_from_config(root: str) -> str:
    """Lit `dashboard.public_url` dans config.yaml (quelques ms, sans CLI)."""
    try:
        with open(os.path.join(root, "config.yaml"), "r", encoding="utf-8", errors="replace") as fh:
            in_dashboard = False
            for line in fh:
                if line.startswith("dashboard:"):
                    in_dashboard = True
                    continue
                if not in_dashboard:
                    continue
                if line.strip() and not line[0].isspace():
                    break
                if line.strip().startswith("public_url:"):
                    value = line.split(":", 1)[1].strip().strip('"').strip("'")
                    return value if value.startswith("http") else ""
    except OSError:
        pass
    return ""


def desktop_target_url() -> str | None:
    """URL que l'app desktop utilisera au prochain lancement, lue dans SON registre.

    L'app desktop garde son mode de connexion dans `%APPDATA%\\Hermes\\connections.json`
    (v2 : `primary` / `launchMode` / `connections[]`) — ou, à défaut, dans
    `connection.json` (v1 : `mode` + `remote.url`). On lit, on n'écrit jamais :
    ce fichier appartient à l'app, qui le réécrit à chaque connexion.

    Retours : une URL => le desktop passera par là ; `""` => mode local
    (« This computer »), rien à attendre ; `None` => registre illisible, à
    l'appelant de retomber sur `dashboard.public_url`.
    """
    appdata = os.environ.get("APPDATA") or ""
    if not appdata:
        return None
    for folder in ("Hermes", "hermes"):
        path = os.path.join(appdata, folder, "connections.json")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                registry = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(registry, dict):
            continue
        entries = [c for c in (registry.get("connections") or []) if isinstance(c, dict)]
        if not entries:
            continue
        wanted = (registry.get("lastUsed") if registry.get("launchMode") == "last-used"
                  else registry.get("primary"))
        chosen = next((c for c in entries if c.get("id") == wanted), None)
        if chosen is None:
            chosen = next((c for c in entries if c.get("kind") != "local"), entries[0])
        if chosen.get("kind") == "local":
            return ""
        return str(chosen.get("url") or "") or None
    for folder in ("Hermes", "hermes"):
        try:
            with open(os.path.join(appdata, folder, "connection.json"),
                      "r", encoding="utf-8", errors="replace") as fh:
                legacy = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(legacy, dict):
            continue
        if str(legacy.get("mode") or "").lower() == "local":
            return ""
        url = str((legacy.get("remote") or {}).get("url") or "")
        return url or None
    return None


def _argv_is_hermes_backend(argv: list[str]) -> str | None:
    """'serve'/'dashboard'/'gateway' si ces arguments sont ceux d'un backend Hermes.

    Le **gateway local du desktop** ne s'appelle ni `hermes.exe serve` ni `hermes.exe
    dashboard` : il tourne en `python -m hermes_cli.main --profile … gateway run --quiet`
    (constaté le 10 nov 2026 : port 8642 tenu par ce process, et la ligne « processus
    serve/dashboard » affichait « aucun » devant un gateway bien vivant). On le reconnaît
    donc au module `-m hermes_cli…` + le jeton `gateway`, sans jamais compter l'app Electron.
    """
    if not argv:
        return None
    exe = os.path.basename(argv[0]).lower()
    launches = any(os.path.basename(tok).lower() == "hermes.exe" for tok in argv[1:3])
    module = ""
    if exe.startswith("python") and "-m" in argv:
        try:
            module = argv[argv.index("-m") + 1].lower()
        except IndexError:
            module = ""
    if not (exe == "hermes.exe" or launches or module.startswith("hermes_cli")):
        return None
    if any(tok.startswith("--type=") for tok in argv) or "win-unpacked" in argv[0]:
        return None  # l'app Electron n'est pas un backend
    if "serve" in argv:
        return "serve"
    if "dashboard" in argv:
        return "dashboard"
    if "gateway" in argv:
        return "gateway"
    return None


def hermes_backends() -> list[dict]:
    """Backends Hermes en cours (regroupés lanceur + enfant python)."""
    if psutil is None:  # pragma: no cover
        return []
    me = os.getpid()
    grouped: dict[tuple, dict] = {}
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            argv = list(proc.info.get("cmdline") or [])
            kind = _argv_is_hermes_backend(argv)
            if not kind:
                continue
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        flags = tuple(sorted(tok for tok in argv if tok.startswith("--") or tok in ("serve", "dashboard")))
        entry = grouped.setdefault((kind, flags), {"kind": kind, "pids": [],
                                                   "cmd": " ".join(argv)[:300]})
        entry["pids"].append(proc.info["pid"])
    return list(grouped.values())


def read_gateway_config(root: str) -> dict:
    """Port, hôte et activation du **gateway local**, lus dans `config.yaml` (sans PyYAML).

    Plusieurs blocs `api_server:` coexistent dans le fichier (un sous `gateway:`, un sous
    `platforms:`) : on retient celui qui porte vraiment un `port` ou un `enabled`, pas le
    premier venu. Retour : {"found", "enabled", "host", "port"} — défauts 127.0.0.1:8642.
    """
    result = {"found": False, "enabled": None, "host": "127.0.0.1", "port": 8642}
    path = os.path.join(root, "config.yaml")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw_lines = fh.readlines()
    except OSError:
        return result

    items: list[tuple[int, str]] = []
    for raw in raw_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        items.append((len(raw) - len(raw.lstrip(" ")), raw.strip()))

    best = None  # (score, bloc)
    for index, (indent, text) in enumerate(items):
        if text.partition(":")[0].strip() != "api_server":
            continue
        block: list[tuple[int, str]] = []
        for inner_indent, inner in items[index + 1:]:
            if inner_indent <= indent:
                break
            block.append((inner_indent, inner))
        score = 0
        parsed = {"host": None, "port": None, "enabled": None}
        for _indent, inner in block:
            key, _, value = inner.partition(":")
            key, value = key.strip(), value.strip().strip("'\"")
            if key == "enabled":
                parsed["enabled"] = value.lower() in ("true", "yes", "on", "1")
                score += 1
            elif key == "port":
                try:
                    parsed["port"] = int(value)
                    score += 2
                except ValueError:
                    pass
            elif key == "host" and value:
                parsed["host"] = value
        if score and (best is None or score > best[0]):
            best = (score, parsed)
    if best is None:
        return result
    parsed = best[1]
    result.update({"found": True,
                   "enabled": parsed["enabled"],
                   "host": parsed["host"] or result["host"],
                   "port": parsed["port"] or result["port"]})
    return result


def hermes_gateway_state(root: str) -> dict:
    """État **réel** du gateway local : écoute-t-il, et que répond son `/health` ?

    C'est ce que l'onglet Hermes appelle « gateway local » : le processus que l'app desktop
    lance pour elle-même (`python -m hermes_cli.main … gateway run`), distinct du dashboard
    du port 9119. `enabled` vient de la config ; `listening`/`health`, de la machine.
    """
    config = read_gateway_config(root)
    host, port = config["host"], config["port"]
    local = host.strip().lower() in ("", "127.0.0.1", "localhost", "::1")
    pid = listener_pid(port) if (port and local) else None
    reachable = bool(pid) if local else (bool(port) and tcp_open(host, port))
    health = {"up": False, "code": None}
    version = ""
    if reachable:
        probe = http_probe(f"http://{host}:{port}/health", timeout=1.5)
        health = {"up": probe["up"], "code": probe["code"]}
        version = str((probe.get("data") or {}).get("version") or "")
    return {"enabled": config["enabled"], "found": config["found"], "host": host,
            "port": port, "local": local, "pid": pid, "listening": bool(pid) or (reachable and not local),
            "health": health, "version": version}


def fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h:
        return f"{h} h {m:02d}"
    if m:
        return f"{m} min {s:02d}"
    return f"{s} s"
