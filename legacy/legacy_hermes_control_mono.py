#!/usr/bin/env python3
"""
HermesServ — petite appli Windows pour lancer / surveiller / arrêter le serveur
Hermes qui rend l'agent joignable depuis le téléphone (Tailscale).

Principes de conception (voir NOTES.md) :
  * On lance un process SÉPARÉ sur un port fixe (9119) : `hermes dashboard --no-open`.
    On ne touche jamais au backend du desktop (`hermes serve --port 0`).
  * On n'utilise JAMAIS `hermes serve --stop` : cette commande tue *tous* les serveurs
    Hermes, backend du desktop inclus. On arrête uniquement le process qu'on a lancé.
  * Le statut se vérifie sur ce qui compte : le port 9119 écoute-t-il, et répond-il ?
  * Le bouton « Desktop » lance l'app Electron (Hermes.exe) telle quelle, sans rebuild ;
    l'interrupteur correspondant peut la lancer automatiquement quand le serveur est prêt.

Usage :
    python hermes_control.py            # interface graphique
    python hermes_control.py --selftest # test du cycle démarrage/arrêt en ligne de commande
"""

from __future__ import annotations

import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections import deque
from datetime import datetime

import tkinter as tk

try:  # icône de notification Win32 (l'app est Windows-only)
    import ctypes
    from ctypes import wintypes
except ImportError:  # pragma: no cover
    ctypes = None
    wintypes = None

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

# ─────────────────────────────────────────────────────────────────────────────
#  Constantes
# ─────────────────────────────────────────────────────────────────────────────

HOST = "127.0.0.1"
PORT = 9119
APP_TITLE = "HermesServ"

HERMES_HOME = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes")
HERMES_CONFIG = os.path.join(HERMES_HOME, "config.yaml")
CANDIDATE_HERMES = [
    os.path.join(HERMES_HOME, "hermes-agent", "venv", "Scripts", "hermes.exe"),
    os.path.join(HERMES_HOME, "hermes-agent", ".hermes", "bin", "hermes.exe"),
]
# App desktop (Electron) : le binaire est lancé tel quel, sans rebuild.
CANDIDATE_DESKTOP = [
    os.path.join(HERMES_HOME, "hermes-agent", "apps", "desktop", "release",
                 "win-unpacked", "Hermes.exe"),
    os.path.join(HERMES_HOME, "hermes-agent", "apps", "desktop", "release",
                 "win-unpacked", "hermes.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "hermes-desktop",
                 "Hermes.exe"),
]
SETTINGS_PATH = os.path.join(os.environ.get("LOCALAPPDATA", ""), "HermesServ", "settings.json")

# Palette
BG = "#0f1116"
CARD = "#171a22"
CARD_HI = "#1d2130"
BORDER = "#262b3a"
TEXT = "#e9ebf2"
MUTED = "#8b93a7"
FAINT = "#5d6579"
ACCENT = "#5b8cff"
ACCENT_HI = "#7aa2ff"
GREEN = "#34d399"
AMBER = "#fbbf24"
RED = "#f87171"

SC = 1.0  # facteur d'échelle DPI, calculé au démarrage


def sc(v: float) -> int:
    return max(1, int(round(v * SC)))


def resource_path(name: str) -> str:
    """Chemin d'une ressource, y compris quand l'app est empaquetée en .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ─────────────────────────────────────────────────────────────────────────────
#  Résolution de l'exécutable hermes + helpers système
# ─────────────────────────────────────────────────────────────────────────────


def find_hermes() -> str | None:
    for path in CANDIDATE_HERMES:
        if os.path.isfile(path):
            return path
    from shutil import which

    return which("hermes")


def find_desktop() -> str | None:
    """Chemin de l'app desktop (Hermes.exe), ou None si elle n'est pas construite."""
    for path in CANDIDATE_DESKTOP:
        if os.path.isfile(path):
            return path
    return None


def desktop_command() -> list[str] | None:
    """Commande de lancement du desktop : le .exe si présent, sinon le CLI `hermes desktop`."""
    exe = find_desktop()
    if exe:
        return [exe]
    hermes = find_hermes()
    if hermes:
        return [hermes, "desktop", "--skip-build"]
    return None


def desktop_running() -> int | None:
    """PID d'un process de l'app desktop s'il tourne (Hermes.exe d'Electron)."""
    if psutil is None:
        return None
    known = (find_desktop() or "").lower().replace("/", "\\")
    for proc in psutil.process_iter(["name", "exe"]):
        name = (proc.info.get("name") or "").lower()
        exe = (proc.info.get("exe") or "").lower().replace("/", "\\")
        if name != "hermes.exe":
            continue
        if (known and exe == known) or "win-unpacked" in exe or "hermes-desktop" in exe:
            return proc.pid
    return None


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass


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


def listener_pid(port: int = PORT) -> int | None:
    """PID du process qui ÉCOUTE sur `port` (None si personne)."""
    if psutil is not None:
        try:
            for c in psutil.net_connections(kind="tcp"):
                if c.status == "LISTEN" and c.laddr and c.laddr.port == port:
                    return c.pid
        except Exception:
            pass
    # repli sans psutil
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


def proc_info(pid: int | None) -> dict:
    if pid is None or psutil is None:
        return {}
    try:
        p = psutil.Process(pid)
        with p.oneshot():
            info = {
                "pid": pid,
                "name": p.name(),
                "cmdline": " ".join(p.cmdline() or [])[:300],
                "uptime": max(0.0, time.time() - p.create_time()),
                "rss_mb": p.memory_info().rss / (1024 * 1024),
                "create_time": p.create_time(),
            }
        return info
    except Exception:
        return {}


def http_status(timeout: float = 2.5) -> dict:
    """Interroge /api/status : c'est LA preuve que le serveur répond vraiment."""
    url = f"http://{HOST}:{PORT}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(4096).decode("utf-8", "replace")
            data = json.loads(raw) if raw.strip().startswith("{") else {}
            return {"up": True, "code": resp.status, "data": data}
    except urllib.error.HTTPError as exc:
        return {"up": True, "code": exc.code, "data": {}}
    except Exception:
        return {"up": False, "code": None, "data": {}}


def public_url_from_config() -> str:
    """Lit `dashboard.public_url` directement dans config.yaml (quelques ms).

    L'appel `hermes config get` lançait un interpréteur Python : ~3 s ici, tout ça pour
    deux lignes de YAML. C'était la dernière lenteur du démarrage.
    """
    try:
        with open(HERMES_CONFIG, "r", encoding="utf-8", errors="replace") as fh:
            in_dashboard = False
            for line in fh:
                if line.startswith("dashboard:"):
                    in_dashboard = True
                    continue
                if not in_dashboard:
                    continue
                if line.strip() and not line[0].isspace():
                    break  # clé racine suivante
                if line.strip().startswith("public_url:"):
                    value = line.split(":", 1)[1].strip().strip('"').strip("'")
                    return value if value.startswith("http") else ""
    except OSError:
        pass
    return ""


def public_url(use_cli_fallback: bool = True) -> str:
    url = public_url_from_config()
    if url or not use_cli_fallback:
        return url
    hermes = find_hermes()
    if not hermes:
        return ""
    out, _, _ = run([hermes, "config", "get", "dashboard.public_url"])
    out = out.strip().strip('"')
    return out if out.startswith("http") else ""


def tailscale_serve_target() -> str:
    out, _, _ = run(["tailscale", "serve", "status"], timeout=10)
    m = re.search(r"proxy\s+(http://\S+)", out)
    return m.group(1) if m else ""


def tailscale_peers() -> dict:
    """{nom-court-dns: {"label": nom lisible, "online": bool}} — ex. 'mon-telephone'."""
    out, _, _ = run(["tailscale", "status", "--json"], timeout=10)
    if not out:
        return {}
    try:
        data = json.loads(out)
    except Exception:
        return {}
    peers = {}
    for _, peer in (data.get("Peer") or {}).items():
        dns = (peer.get("DNSName") or "").rstrip(".")
        short = dns.split(".")[0] if dns else (peer.get("HostName") or "?")
        peers[short] = {
            "label": peer.get("HostName") or short,
            "online": bool(peer.get("Online")),
        }
    return peers


def _argv_is_hermes_backend(argv: list[str]) -> str | None:
    """Renvoie 'serve' / 'dashboard' si ces arguments sont ceux d'un backend Hermes.

    Analyse par JETONS (pas par sous-chaîne) : un script python dont le *code* contient
    « hermes serve » ne doit pas être pris pour un backend, et le lanceur
    `hermes.exe --profile default serve …` doit l'être.
    """
    if not argv:
        return None
    exe = os.path.basename(argv[0]).lower()
    is_hermes_exe = exe == "hermes.exe"
    launches_hermes = any(os.path.basename(tok).lower() == "hermes.exe" for tok in argv[1:3])
    if not (is_hermes_exe or (exe.startswith("python") and launches_hermes)):
        return None
    # Écarter l'application Electron (Hermes.exe du desktop), qui n'est pas un backend.
    if any(tok.startswith("--type=") for tok in argv) or "win-unpacked" in argv[0]:
        return None
    if "serve" in argv:
        return "serve"
    if "dashboard" in argv:
        return "dashboard"
    return None


def hermes_backends() -> list[dict]:
    """Process serveur/dashboard Hermes en cours — scan psutil, sans sous-processus.

    `hermes serve --status` lançait un interpréteur Python à CHAQUE cycle : c'est lent
    (1-3 s) et ça gelait l'interface. psutil donne la même information en millisecondes.
    """
    if psutil is None:
        hermes = find_hermes()
        if not hermes:
            return []
        out, _, _ = run([hermes, "serve", "--status"])
        found = []
        for line in out.splitlines():
            m = re.search(r"PID\s+(\d+)\s+\[(\w+)\]:\s*(.*)", line)
            if m:
                found.append({"pid": int(m.group(1)), "kind": m.group(2), "cmd": m.group(3).strip()})
        return found

    me = os.getpid()
    raw = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.info["pid"] == me:
                continue
            argv = list(proc.info.get("cmdline") or [])
            kind = _argv_is_hermes_backend(argv)
            if kind:
                raw.append({"pid": proc.info["pid"], "kind": kind, "argv": argv})
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # Un backend = un lanceur `hermes.exe` + son enfant python, avec les mêmes arguments.
    # On regroupe pour ne pas compter deux fois le même backend (sinon « 2 process » pour un).
    grouped: dict[tuple, dict] = {}
    for item in raw:
        flags = tuple(sorted(
            tok for tok in item["argv"]
            if tok.startswith("--") or tok in ("serve", "dashboard")
        ))
        key = (item["kind"], flags)
        entry = grouped.setdefault(key, {"kind": item["kind"], "pids": [], "cmd": " ".join(item["argv"])[:300]})
        entry["pids"].append(item["pid"])
    return list(grouped.values())


def fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    if h:
        return f"{h} h {m:02d}"
    if m:
        return f"{m} min {s:02d}"
    return f"{s} s"


# ─────────────────────────────────────────────────────────────────────────────
#  Cœur : contrôle du serveur
# ─────────────────────────────────────────────────────────────────────────────


class ServerController:
    """Lance et arrête UNIQUEMENT le dashboard qu'il a lui-même démarré."""

    def __init__(self, log=None):
        self.proc: subprocess.Popen | None = None
        self.owned_pid: int | None = None
        self.ready_at: float | None = None
        self._log = log or (lambda *_: None)
        # Sondes lentes mises en cache (secondes) : le fil d'affichage ne doit JAMAIS
        # attendre un sous-processus.
        self._cache: dict[str, tuple[float, object]] = {}
        self._ttl = {"public_url": 300.0, "serve_target": 15.0, "peers": 10.0, "backends": 5.0,
                     "desktop": 3.0}

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

    # --- lecture -----------------------------------------------------------
    def snapshot(self) -> dict:
        pid = listener_pid()
        info = proc_info(pid)
        http = http_status()
        state = {
            "port_free": pid is None,
            "owner_pid": pid,
            "owner": info,
            "http": http,
            "ours": pid is not None and pid == self.owned_pid,
            "public_url": self._cached("public_url", public_url),
            "serve_target": self._cached("serve_target", tailscale_serve_target),
            "peers": self._cached("peers", tailscale_peers),
            "backends": self._cached("backends", hermes_backends),
            "desktop_pid": self._cached("desktop", desktop_running),
            "ready_at": self.ready_at,
        }
        if pid is None:
            state["phase"] = "stopped"
        elif not http["up"]:
            state["phase"] = "starting"
        else:
            state["phase"] = "running"
        return state

    # --- écriture ----------------------------------------------------------
    def start(self, wait: float = 75.0) -> str:
        if listener_pid() is not None:
            return "Un process écoute déjà sur le port 9119."
        hermes = find_hermes()
        if not hermes:
            return "Exécutable `hermes` introuvable."
        # `serve` et `dashboard` acceptent les mêmes options ; `dashboard --no-open`
        # est la recette vérifiée pour la chaîne Tailscale (UI web + API).
        args = [hermes, "dashboard", "--host", HOST, "--port", str(PORT),
                "--no-open", "--skip-build"]
        self._log(f"Démarrage : hermes dashboard --host {HOST} --port {PORT} --no-open --skip-build")
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        try:
            self.proc = subprocess.Popen(
                args, creationflags=creation,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            self._log(f"Échec du lancement : {exc}")
            return f"Échec du lancement : {exc}"
        self.invalidate()

        # On attend la preuve réelle : le port écoute ET l'API répond.
        deadline = time.time() + wait
        announced = False
        while time.time() < deadline:
            time.sleep(0.4)
            pid = listener_pid()
            if pid is not None and not announced:
                self.owned_pid = pid
                announced = True
                self._log(f"Port {PORT} ouvert (pid {pid}) — attente de l'API…")
            if announced and http_status(timeout=1.5)["up"]:
                self.ready_at = time.time()
                self._log(f"Serveur prêt en {wait - (deadline - time.time()):.1f} s")
                return "Serveur démarré."
        if announced:
            return "Le port écoute mais l'API ne répond pas encore (patience, ou voir le log)."
        return "Le serveur n'a pas ouvert le port 9119 (voir le log)."

    def stop(self, wait: float = 15.0) -> str:
        pid = listener_pid()
        if pid is None:
            self._reset()
            return "Le serveur n'était pas lancé."
        if self.owned_pid is None or pid != self.owned_pid:
            info = proc_info(pid)
            name = info.get("name", "?")
            return (f"Refusé : le port 9119 est tenu par un autre process "
                    f"(pid {pid}, {name}). Je n'arrête que le serveur que j'ai lancé.")
        if psutil is not None:
            try:
                p = psutil.Process(pid)
                for child in p.children(recursive=True):
                    try:
                        child.terminate()
                    except Exception:
                        pass
                p.terminate()
                gone, alive = psutil.wait_procs([p], timeout=wait)
                if alive:
                    for proc in alive:
                        try:
                            proc.kill()
                        except Exception:
                            pass
            except Exception as exc:
                self._log(f"Arrêt via psutil impossible ({exc}), repli taskkill")
                run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=15)
        else:
            run(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=15)

        deadline = time.time() + wait
        while time.time() < deadline and listener_pid() is not None:
            time.sleep(0.3)
        still = listener_pid()
        if still is None:
            self._reset()
            return "Serveur arrêté."
        return f"Le port 9119 est toujours occupé (pid {still})."

    def _reset(self) -> None:
        self.proc = None
        self.owned_pid = None
        self.ready_at = None
        self.invalidate()

    def adopt(self) -> None:
        """Si un serveur tourne déjà sur 9119, on le revendique (au lancement de l'UI)."""
        pid = listener_pid()
        if pid is not None and self.owned_pid is None:
            self.owned_pid = pid
            self._log(f"Serveur déjà en écoute sur {PORT} (pid {pid}) — adopté.")


class StatusPoller(threading.Thread):
    """Sonde l'état en continu HORS du fil d'affichage.

    C'est la correction du gel « ne répond plus » : la fenêtre ne fait plus qu'afficher le
    dernier instantané connu (opération instantanée au lieu d'attendre des sous-processus).
    """

    def __init__(self, controller: ServerController, interval: float = 2.0):
        super().__init__(daemon=True)
        self.controller = controller
        self.interval = interval
        self.latest: dict | None = None
        self.last_duration = 0.0
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.latest = self.controller.snapshot()
            except Exception:
                pass
            self.last_duration = time.time() - t0
            self._stop.wait(max(0.2, self.interval - self.last_duration))


# ─────────────────────────────────────────────────────────────────────────────
#  Widgets arrondis dessinés au canvas (pour un rendu propre)
# ─────────────────────────────────────────────────────────────────────────────


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


class Card(tk.Canvas):
    def __init__(self, master, height, **kw):
        super().__init__(master, height=sc(height), bg=BG, highlightthickness=0, bd=0, **kw)
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _evt=None):
        self.delete("card")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        round_rect(self, sc(1), sc(1), w - sc(1), h - sc(1), sc(12),
                   fill=CARD, outline=BORDER, width=1, tags="card")
        self.tag_lower("card")


class Button(tk.Canvas):
    """Bouton plat arrondi avec survol, dessiné au canvas."""

    def __init__(self, master, text, command, width=150, height=38,
                 fill=ACCENT, hover=ACCENT_HI, fg="#ffffff", radius=10, font=None,
                 outline=""):
        super().__init__(master, width=sc(width), height=sc(height), bg=BG,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._text, self._command = text, command
        self._fill, self._hover, self._fg = fill, hover, fg
        self._outline = outline
        self._radius = sc(radius)
        self._font = font or ("Segoe UI", -sc(11), "bold")
        self._enabled = True
        self._hovering = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def set_text(self, text):
        self._text = text
        self._draw()

    def set_style(self, fill, hover):
        self._fill, self._hover = fill, hover
        self._draw()

    def set_fg(self, fg):
        self._fg = fg
        self._draw()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._draw()

    def _on_enter(self, _e):
        self._hovering = True
        self._draw()

    def _on_leave(self, _e):
        self._hovering = False
        self._draw()

    def _on_click(self, _e):
        if self._enabled and self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        base = self._fill if self._enabled else "#2a2f3d"
        col = self._hover if (self._hovering and self._enabled) else base
        border = (self._outline if self._enabled else BORDER) or ""
        round_rect(self, sc(1), sc(1), w - sc(1), h - sc(1), self._radius,
                   fill=col, outline=border if border else "", width=1 if border else 0)
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg if self._enabled else FAINT,
                         font=self._font)


class Toggle(tk.Canvas):
    """Petit interrupteur on/off dessiné (pour les options)."""

    def __init__(self, master, label, value=False, command=None):
        super().__init__(master, height=sc(22), bg=BG, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.label, self.value, self.command = label, value, command
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", lambda e: self._draw())

    def _click(self, _e):
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)

    def _draw(self):
        self.delete("all")
        h = self.winfo_height() or sc(22)
        tw, th = sc(30), sc(16)
        y = h / 2
        fill = ACCENT if self.value else "#333a4c"
        round_rect(self, sc(1), y - th / 2, sc(1) + tw, y + th / 2, th / 2, fill=fill, outline="")
        cx = sc(1) + tw - th / 2 if self.value else sc(1) + th / 2
        self.create_oval(cx - th / 2 + sc(2), y - th / 2 + sc(2), cx + th / 2 - sc(2),
                         y + th / 2 - sc(2), fill="#ffffff", outline="")
        self.create_text(sc(1) + tw + sc(10), y,
                         text=f"{self.label} · {'activé' if self.value else 'désactivé'}",
                         anchor="w", fill=TEXT if self.value else MUTED,
                         font=("Segoe UI", -sc(10)))


# ─────────────────────────────────────────────────────────────────────────────
#  Icône de notification (zone de notification Windows) — ctypes, sans pystray
# ─────────────────────────────────────────────────────────────────────────────

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
NIF_INFO = 0x10
NIIF_INFO, NIIF_WARNING = 0x00000001, 0x00000002
WM_TRAYICON = 0x8000 + 1  # WM_APP + 1
WM_DESTROY, WM_NULL, WM_CLOSE = 0x0002, 0x0000, 0x0010
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
WM_CONTEXTMENU = 0x007B
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0000, 0x0800, 0x0001
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x0002, 0x0100, 0x0080


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class _NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class TrayIcon:
    """Icône de la zone de notification : clic gauche = afficher, clic droit = menu.

    Win32 natif (Shell_NotifyIcon) : aucune dépendance à installer, donc rien à empaqueter
    en plus dans l'exe. La fenêtre de réception et sa boucle de messages vivent dans un
    thread dédié ; les actions remontent par un simple appel de rappel (l'app les remet
    dans sa file Tk, jamais de widget touché hors du fil d'affichage).
    """

    MENU_BASE = 1001

    def __init__(self, icon_path: str, tooltip: str, on_action, menu_provider):
        self.icon_path = icon_path
        self.tooltip = (tooltip or "HermesServ")[:120]
        self.on_action = on_action
        self.menu_provider = menu_provider
        self.ok = False
        self.error = ""
        self._hwnd = None
        self._hicon = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._declare()

    def _declare(self) -> None:
        u = self._user32
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                      wintypes.HINSTANCE, ctypes.c_void_p]
        u.RegisterClassW.restype = wintypes.ATOM
        u.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.CreatePopupMenu.restype = wintypes.HMENU
        u.AppendMenuW.restype = wintypes.BOOL
        u.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        u.TrackPopupMenu.restype = ctypes.c_int
        u.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.DestroyWindow.restype = wintypes.BOOL
        u.DestroyWindow.argtypes = [wintypes.HWND]
        u.SetForegroundWindow.restype = wintypes.BOOL
        u.SetForegroundWindow.argtypes = [wintypes.HWND]
        u.GetCursorPos.restype = wintypes.BOOL
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.DestroyMenu.restype = wintypes.BOOL
        u.DestroyMenu.argtypes = [wintypes.HMENU]
        u.LoadIconW.restype = wintypes.HICON
        u.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        u.GetMessageW.restype = ctypes.c_int
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                  wintypes.UINT, wintypes.UINT]
        u.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        u.DispatchMessageW.restype = ctypes.c_ssize_t
        u.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        self._shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                                    ctypes.POINTER(_NOTIFYICONDATAW)]

    # --- cycle de vie ------------------------------------------------------
    def start(self, wait: float = 4.0) -> bool:
        self._thread.start()
        self._ready.wait(wait)
        return self.ok

    def stop(self) -> None:
        with self._lock:
            if self._hwnd:
                self._notify(NIM_DELETE)
                self._user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    # --- shell / icône -----------------------------------------------------
    def _notify(self, message: int, flags: int = NIF_MESSAGE | NIF_ICON | NIF_TIP) -> bool:
        data = _NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = flags
        data.uCallbackMessage = WM_TRAYICON
        data.hIcon = self._hicon
        data.szTip = self.tooltip
        return bool(self._shell32.Shell_NotifyIconW(message, ctypes.byref(data)))

    def balloon(self, title: str, text: str, warning: bool = False) -> bool:
        """Petite bulle d'information (ex. « je continue dans la zone de notification »)."""
        with self._lock:
            if not self.ok:
                return False
            data = _NOTIFYICONDATAW()
            data.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
            data.hWnd = self._hwnd
            data.uID = 1
            data.uFlags = NIF_INFO
            data.szInfoTitle = (title or "")[:60]
            data.szInfo = (text or "")[:250]
            data.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
            return bool(self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data)))

    def set_tooltip(self, text: str) -> None:
        text = (text or "")[:120]
        with self._lock:
            if text == self.tooltip or not self.ok:
                return
            self.tooltip = text
            self._notify(NIM_MODIFY, NIF_TIP)

    def _load_icon(self):
        icon = self._user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not icon:
            icon = self._user32.LoadIconW(None, ctypes.cast(32512, wintypes.LPCWSTR))
        return icon

    # --- thread de messages ------------------------------------------------
    def _run(self) -> None:
        try:
            self._hicon = self._load_icon()
            class_name = f"HermesServTray{os.getpid()}_{id(self)}"
            self._wndproc_ref = ctypes.WINFUNCTYPE(
                ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )(self._wndproc)
            wc = _WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(self._wndproc_ref, ctypes.c_void_p)
            wc.hInstance = self._kernel32.GetModuleHandleW(None)
            wc.lpszClassName = class_name
            if not self._user32.RegisterClassW(ctypes.byref(wc)):
                raise OSError(f"RegisterClass a échoué (erreur {ctypes.get_last_error()})")
            self._hwnd = self._user32.CreateWindowExW(0, class_name, "HermesServTray", 0, 0, 0, 0, 0,
                                                      None, None, wc.hInstance, None)
            if not self._hwnd:
                raise OSError(f"CreateWindowEx a échoué (erreur {ctypes.get_last_error()})")
            self.ok = self._notify(NIM_ADD)
            if not self.ok:
                raise OSError(f"Shell_NotifyIcon a échoué (erreur {ctypes.get_last_error()})")
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)
            self.ok = False
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            self._user32.TranslateMessage(ctypes.byref(msg))
            self._user32.DispatchMessageW(ctypes.byref(msg))

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_TRAYICON:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.on_action("show")
            elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._popup()
            return 0
        if message == WM_CLOSE:
            self._user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            self._user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _popup(self) -> None:
        items = [it for it in (self.menu_provider() or [])]
        menu = self._user32.CreatePopupMenu()
        if not menu:
            return
        mapping = {}
        for index, (label, action, enabled) in enumerate(items):
            if label == "-":
                self._user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            ident = self.MENU_BASE + index
            flags = MF_STRING if enabled and action else MF_STRING | MF_GRAYED
            self._user32.AppendMenuW(menu, flags, ident, label)
            if action and enabled:
                mapping[ident] = action
        pt = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(pt))
        self._user32.SetForegroundWindow(self._hwnd)
        chosen = self._user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, self._hwnd, None
        )
        self._user32.DestroyMenu(menu)
        self._user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        action = mapping.get(chosen)
        if action:
            self.on_action(action)


# ─────────────────────────────────────────────────────────────────────────────
#  Interface
# ─────────────────────────────────────────────────────────────────────────────


class App:
    def __init__(self):
        global SC
        self.root = tk.Tk()
        self.root.withdraw()
        try:  # netteté DPI + titre de fenêtre sombre
            import ctypes

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
            dpi = ctypes.windll.user32.GetDpiForSystem()
            SC = max(1.0, dpi / 96.0)
        except Exception:
            SC = 1.0

        self.controller = ServerController(log=self.log_bg)
        self.poller = StatusPoller(self.controller, interval=2.0)
        self.events: queue.Queue = queue.Queue()
        self.log_lines: deque[str] = deque(maxlen=400)
        self.busy = False
        settings = load_settings()
        self.stop_on_exit = bool(settings.get("stop_on_exit", False))
        self.auto_desktop = bool(settings.get("auto_desktop", False))
        self.hide_on_close = bool(settings.get("hide_on_close", True))
        self._auto_desktop_done = False
        self._window_hidden = False
        self.last_phase = None

        self._build()
        self.root.deiconify()
        self._dark_titlebar()
        self._set_icon()
        self.tray = None
        self._log_intro()
        try:
            ico = resource_path("hermes_serv.ico")
            if os.path.isfile(ico):
                candidate = TrayIcon(ico, f"HermesServ — port {PORT}", self._tray_action,
                                     self._tray_menu)
                if candidate.start():
                    self.tray = candidate
                    self.log("Icône de notification présente : clic gauche = afficher, "
                             "clic droit = menu.")
                    print("icône de notification : ajoutée", flush=True)
                else:
                    self.log(f"Icône de notification indisponible : {candidate.error or 'échec'}"
                             " — l'app fonctionne sans.")
        except Exception as exc:  # pragma: no cover
            self.log(f"Icône de notification indisponible : {exc}")
        self.controller.adopt()
        self.log("HermesServ prêt.")
        self.poller.start()
        self._drain()

    # --- log ---------------------------------------------------------------
    def log_bg(self, msg: str) -> None:
        self.events.put(("log", msg))

    def log(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append(f"{stamp}  {msg}")
        if hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", stamp + "  ", "ts")
            self.log_text.insert("end", msg + "\n", "msg")
            if len(self.log_lines) > 400:
                self.log_text.delete("1.0", "2.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def _log_intro(self) -> None:
        self.log("Je ne touche jamais au backend du desktop, ni à « hermes serve --stop ».")
        self.log(f"Je lance : dashboard sur {HOST}:{PORT} (--no-open, --skip-build)")
        url = public_url()
        self.log(f"URL téléphone : {url}" if url else "URL publique non configurée.")

    # --- fenêtre -----------------------------------------------------------
    def _set_icon(self) -> None:
        try:
            ico = resource_path("hermes_serv.ico")
            if os.path.isfile(ico):
                self.root.iconbitmap(default=ico)
        except Exception:
            pass

    def _dark_titlebar(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes

            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20 = 20H1+)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    wintypes.HWND(hwnd), attr, ctypes.byref(value), ctypes.sizeof(value)
                )
        except Exception:
            pass

    def _build(self) -> None:
        r = self.root
        r.title(APP_TITLE)
        r.configure(bg=BG)
        w, h = sc(470), sc(728)
        r.geometry(f"{w}x{h}")
        r.minsize(w, h)
        r.resizable(False, False)

        pad = sc(16)

        # ── en-tête
        head = tk.Frame(r, bg=BG)
        head.pack(fill="x", padx=pad, pady=(sc(14), sc(10)))
        tk.Label(head, text="HermesServ", bg=BG, fg=TEXT,
                 font=("Segoe UI", -sc(19), "bold")).pack(anchor="w")
        tk.Label(head, text="Serveur · accès téléphone via Tailscale",
                 bg=BG, fg=MUTED, font=("Segoe UI", -sc(9))).pack(anchor="w")

        self.status_card = Card(r, height=96)
        self.status_card.pack(fill="x", padx=pad, pady=(0, sc(10)))

        self.rows_card = Card(r, height=150)
        self.rows_card.pack(fill="x", padx=pad, pady=(0, sc(12)))

        # ── boutons
        btns = tk.Frame(r, bg=BG)
        btns.pack(fill="x", padx=pad)
        self.main_btn = Button(btns, "Démarrer le serveur", self._toggle, width=210, height=42)
        self.main_btn.pack(side="left")
        self.desktop_btn = Button(btns, "Desktop", lambda: self._open_desktop(), width=100, height=42,
                                  fill=CARD_HI, hover="#252a3a", fg=ACCENT, radius=10,
                                  outline="#33507f")
        self.desktop_btn.pack(side="left", padx=(sc(8), 0))
        self.open_btn = Button(btns, "Navigateur", lambda: self._open_url(), width=112, height=42,
                               fill=CARD_HI, hover="#252a3a", fg=TEXT, radius=10,
                               outline=BORDER)
        self.open_btn.pack(side="left", padx=(sc(8), 0))

        btns2 = tk.Frame(r, bg=BG)
        btns2.pack(fill="x", padx=pad, pady=(sc(8), 0))
        self.copy_btn = Button(btns2, "Copier l'URL publique", lambda: self._copy_url(),
                               width=210, height=34, fill=CARD_HI, hover="#252a3a", fg=TEXT,
                               radius=10, outline=BORDER, font=("Segoe UI", -sc(10)))
        self.copy_btn.pack(side="left")

        opts = tk.Frame(r, bg=BG)
        opts.pack(fill="x", padx=pad, pady=(sc(12), sc(14)))
        self.close_toggle = Toggle(opts, "Fermer la fenêtre = réduire dans la zone de notification",
                                   self.hide_on_close, command=self._set_hide_on_close)
        self.close_toggle.pack(fill="x")
        self.auto_toggle = Toggle(opts, "Lancer l'app desktop quand le serveur est prêt",
                                  self.auto_desktop, command=self._set_auto_desktop)
        self.auto_toggle.pack(fill="x", pady=(sc(6), 0))
        self.toggle = Toggle(opts, "Arrêter le serveur quand je quitte HermesServ",
                             self.stop_on_exit, command=self._set_stop_on_exit)
        self.toggle.pack(fill="x", pady=(sc(6), 0))

        # ── journal
        tk.Label(r, text="JOURNAL", bg=BG, fg=MUTED,
                 font=("Segoe UI", -sc(9), "bold")).pack(anchor="w", padx=pad, pady=(0, sc(4)))
        log_wrap = tk.Frame(r, bg=BORDER)
        log_wrap.pack(fill="both", expand=True, padx=pad, pady=(0, sc(14)))
        self.log_text = tk.Text(log_wrap, bg=CARD, fg="#a9b2c6", bd=0, highlightthickness=0,
                                font=("Consolas", -sc(9)), wrap="word", height=6,
                                insertbackground=MUTED, state="disabled",
                                padx=sc(8), pady=sc(6), spacing1=sc(1), spacing3=sc(1))
        self.log_text.tag_configure("ts", foreground=FAINT)
        self.log_text.tag_configure("msg", foreground="#a9b2c6")
        self.log_text.pack(fill="both", expand=True, padx=1, pady=1)

        r.protocol("WM_DELETE_WINDOW", self._request_close)

    # --- actions -----------------------------------------------------------
    def _toggle(self) -> None:
        if self.busy:
            return
        snap = self.poller.latest
        if snap is None:
            self.log("État encore en cours de lecture, un instant…")
            return
        if snap["phase"] == "stopped":
            self._run_async(self.controller.start, "arrêt")
        else:
            if not snap["ours"] and snap["owner_pid"]:
                self.log(f"Attention : le port 9119 est tenu par le pid {snap['owner_pid']} "
                         f"({snap['owner'].get('name', '?')}) — arrêt refusé.")
                return
            self._run_async(self.controller.stop, "démarrage")

    def _run_async(self, fn, _label: str) -> None:
        self.busy = True
        self.main_btn.set_enabled(False)

        def work():
            try:
                msg = fn()
            except Exception as exc:  # pragma: no cover
                msg = f"Erreur : {exc}"
            self.events.put(("done", msg))

        threading.Thread(target=work, daemon=True).start()

    def _open_url(self) -> None:
        url = public_url() or f"http://{HOST}:{PORT}"
        self.log(f"Navigateur : ouverture de {url}")
        webbrowser.open(url)

    def _open_desktop(self) -> None:
        """Bouton « Desktop » : lance l'app Electron (jamais de rebuild)."""
        snap = self.poller.latest
        if snap and snap["phase"] == "stopped":
            self.log("Serveur 9119 arrêté — le desktop démarrera son propre backend local.")
        self._launch_desktop("à la demande")

    def _launch_desktop(self, source: str) -> None:
        cmd = desktop_command()
        if not cmd:
            self.log("App desktop introuvable (ni Hermes.exe ni `hermes desktop`).")
            return
        running = desktop_running()
        if running:
            self.log(f"L'app desktop tourne déjà (pid {running}) — pas de second lancement.")
            return
        try:
            subprocess.Popen(
                cmd, cwd=os.path.dirname(cmd[0]) or None,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                close_fds=True,
            )
        except Exception as exc:
            self.log(f"Échec du lancement de l'app desktop : {exc}")
            return
        self.log(f"App desktop lancée ({source}) : {os.path.basename(cmd[0])}")
        self.controller.invalidate()

    def _maybe_launch_desktop(self, snap: dict) -> None:
        """Interrupteur : une seule tentative par montée du serveur, jamais de doublon."""
        if not self.auto_desktop or snap["phase"] != "running" or self._auto_desktop_done:
            return
        self._auto_desktop_done = True
        if snap.get("desktop_pid"):
            self.log("Serveur prêt — l'app desktop est déjà ouverte.")
            return
        self.log("Serveur prêt → lancement automatique de l'app desktop.")
        self._launch_desktop("automatique")

    def _copy_url(self) -> None:
        url = public_url() or f"http://{HOST}:{PORT}"
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.log(f"URL copiée : {url}")

    def _tray_action(self, action: str) -> None:
        """Appelé depuis le fil de l'icône : ne touche jamais Tk directement."""
        self.events.put(("tray", action))

    def _tray_menu(self) -> list:
        """Menu du clic droit, construit à la demande avec l'état courant."""
        snap = self.poller.latest
        phase = snap["phase"] if snap else None
        if phase == "running":
            main = ("Arrêter le serveur", "toggle", not self.busy)
        elif phase == "stopped":
            main = ("Démarrer le serveur", "toggle", not self.busy)
        else:
            main = ("Lecture de l'état…", None, False)
        return [
            (("Afficher HermesServ", "show", True) if self._window_hidden
             else ("Masquer dans la zone de notification", "hide", True)),
            main,
            ("Ouvrir l'app desktop", "desktop", True),
            ("-", None, False),
            ("Quitter", "quit", True),
        ]

    def _handle_tray(self, action: str) -> None:
        if action == "show":
            self._show_window()
        elif action == "hide":
            self._hide_to_tray()
        elif action == "toggle":
            self._toggle()
        elif action == "desktop":
            self._open_desktop()
        elif action == "quit":
            self.log("Quitter demandé depuis l'icône de notification.")
            self._on_close()

    def _show_window(self) -> None:
        self.root.deiconify()
        try:
            self.root.state("normal")
        except Exception:
            pass
        self.root.lift()
        self.root.focus_force()
        self._window_hidden = False

    def _update_tray_tip(self, snap: dict) -> None:
        if not self.tray:
            return
        phase = snap["phase"]
        tip = {"running": "en ligne", "starting": "démarrage…", "stopped": "arrêté"}[phase]
        if phase == "running" and not snap["ours"]:
            tip = "en ligne (process externe)"
        if phase == "running" and (snap["http"]["code"] or 0) >= 400:
            tip = "problème HTTP"
        suffix = f" · pid {snap['owner_pid']}" if snap.get("owner_pid") else ""
        self.tray.set_tooltip(f"HermesServ — {tip}{suffix}")

    def _set_hide_on_close(self, value: bool) -> None:
        self.hide_on_close = value
        self._save_settings()
        self.log("Fermeture de la fenêtre : "
                 + ("réduite dans la zone de notification." if value and self.tray
                    else "quitte l'app." + ("" if self.tray else " (pas d'icône de notification.)")))

    def _set_stop_on_exit(self, value: bool) -> None:
        self.stop_on_exit = value
        self._save_settings()
        self.log(f"À la fermeture : {'le serveur sera arrêté' if value else 'le serveur continuera de tourner'}.")

    def _set_auto_desktop(self, value: bool) -> None:
        self.auto_desktop = value
        self._save_settings()
        self.log(f"Lancement automatique du desktop : {'activé' if value else 'désactivé'}.")
        if value:
            self._auto_desktop_done = False
            snap = self.poller.latest
            if snap is not None:
                self._maybe_launch_desktop(snap)

    def _save_settings(self) -> None:
        save_settings({"stop_on_exit": self.stop_on_exit, "auto_desktop": self.auto_desktop,
                       "hide_on_close": self.hide_on_close})

    def _request_close(self) -> None:
        """Bouton X : on va dans la zone de notification au lieu de quitter (si possible)."""
        if self.hide_on_close and self.tray:
            self._hide_to_tray()
        else:
            self._on_close()

    def _hide_to_tray(self) -> None:
        if self._window_hidden:
            return
        self.root.withdraw()
        self._window_hidden = True
        self.log("Fenêtre réduite dans la zone de notification — clic gauche sur l'icône "
                 "pour la rouvrir, « Quitter » pour arrêter l'app.")

    def _on_close(self) -> None:
        snap = self.poller.latest
        if self.stop_on_exit and snap and snap["ours"] and snap["phase"] != "stopped":
            self.log("Arrêt du serveur avant de quitter (interrupteur activé).")
            self.controller.stop()
        if getattr(self, "tray", None):
            self.tray.stop()
            self.tray = None
        self.poller.stop()
        if getattr(self, "_drain_job", None):
            try:
                self.root.after_cancel(self._drain_job)
            except Exception:
                pass
        self.root.destroy()

    # --- boucle de rafraîchissement ---------------------------------------
    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "tray":
                    self._handle_tray(payload)
                elif kind == "done":
                    self.log(payload)
                    self.busy = False
                    self.main_btn.set_enabled(True)
        except queue.Empty:
            pass
        self._paint_current()
        self._drain_job = self.root.after(300, self._drain)

    def _paint_current(self) -> None:
        """Peint le DERNIER instantané du sondeur — jamais de sondage ici (fil d'affichage)."""
        snap = self.poller.latest
        if snap is None:
            self._paint_loading()
            return
        self._note_phase(snap)
        try:
            self._paint_status(snap)
            self._paint_rows(snap)
            self._paint_button(snap)
            self._paint_desktop_btn(snap)
        except Exception as exc:  # pragma: no cover
            self.log(f"Erreur d'affichage : {exc}")
        self._maybe_launch_desktop(snap)
        self._update_tray_tip(snap)

    def _paint_desktop_btn(self, snap: dict) -> None:
        running = bool(snap.get("desktop_pid"))
        self.desktop_btn.set_text("Desktop ✓" if running else "Desktop")
        self.desktop_btn.set_fg(GREEN if running else ACCENT)

    def _note_phase(self, snap: dict) -> None:
        phase = snap["phase"]
        if self.last_phase is not None and phase != self.last_phase:
            self.log({
                "running": "Serveur en ligne : le port 9119 répond.",
                "starting": "Port 9119 ouvert, l'API démarre…",
                "stopped": "Serveur arrêté : le port 9119 est libre.",
            }[phase])
        self.last_phase = phase
        if phase == "stopped":
            self._auto_desktop_done = False

    def _paint_loading(self) -> None:
        c = self.status_card
        c.delete("content")
        c.create_oval(sc(17), sc(31), sc(39), sc(53), outline=MUTED, width=sc(2), tags="content")
        c.create_text(sc(52), sc(33), text="Lecture de l'état…", anchor="w", fill=MUTED,
                      font=("Segoe UI", -sc(15), "bold"), tags="content")
        c.create_text(sc(14), sc(72), text="premier sondage en cours", anchor="w",
                      fill=FAINT, font=("Segoe UI", -sc(9)), tags="content")
        self.rows_card.delete("content")
        self.main_btn.set_text("Lecture de l'état…")
        self.main_btn.set_style("#3a4257", "#3a4257")
        self.main_btn.set_enabled(False)

    def _refresh(self) -> None:
        """Conservé pour compatibilité CLI/outils : peint l'état courant."""
        self._paint_current()

    # --- rendu -------------------------------------------------------------
    def _paint_status(self, snap: dict) -> None:
        c = self.status_card
        c.delete("content")
        w = c.winfo_width()
        phase = snap["phase"]
        color = {"running": GREEN, "starting": AMBER, "stopped": MUTED}[phase]
        label = {"running": "En ligne", "starting": "Démarrage…", "stopped": "Arrêté"}[phase]
        if phase == "running" and not snap["ours"]:
            label, color = "En ligne (process externe)", AMBER
        if phase == "running" and (snap["http"]["code"] or 0) >= 400:
            label, color = "Problème HTTP", RED

        cx, cy = sc(28), sc(42)
        c.create_oval(cx - sc(11), cy - sc(11), cx + sc(11), cy + sc(11),
                      outline=color, width=sc(2), tags="content")
        c.create_oval(cx - sc(5), cy - sc(5), cx + sc(5), cy + sc(5), fill=color,
                      outline="", tags="content")
        c.create_text(cx + sc(24), cy - sc(9), text=label, anchor="w", fill=color,
                      font=("Segoe UI", -sc(15), "bold"), tags="content")

        url = snap["public_url"] or "(URL publique non configurée)"
        c.create_text(cx + sc(24), cy + sc(12), text=url, anchor="w", fill=MUTED,
                      font=("Segoe UI", -sc(9)), tags="content")

        detail = []
        if snap["owner_pid"]:
            detail.append(f"pid {snap['owner_pid']}")
        if snap["phase"] == "running" and snap["owner"]:
            detail.append(f"RAM {snap['owner'].get('rss_mb', 0):.0f} Mo")
            detail.append(f"depuis {fmt_uptime(snap['owner'].get('uptime', 0))}")
        c.create_text(sc(14), sc(72), text="   ·   ".join(detail) or
                      "aucun process en écoute sur le port 9119", anchor="w",
                      fill=FAINT, font=("Segoe UI", -sc(9)), tags="content")

    def _paint_rows(self, snap: dict) -> None:
        c = self.rows_card
        c.delete("content")
        width = c.winfo_width()
        if width < 50:
            width = sc(438)
        http = snap["http"]
        data = http.get("data") or {}
        providers = data.get("auth_providers") or []
        peers = snap["peers"]
        phone = peers.get("mon-telephone")
        stopped = snap["phase"] == "stopped"
        desktop = [b for b in snap["backends"]
                   if b["kind"] == "serve" and snap["owner_pid"] not in b.get("pids", [])]

        rows = [
            ("Port 9119",
             "libre" if snap["port_free"] else f"écoute (pid {snap['owner_pid']})",
             None if stopped else True),
            ("API",
             (f"répond (/api/status {http.get('code')})" if http.get("up")
              else "— (serveur arrêté)" if stopped else "ne répond pas"),
             True if http.get("up") else (None if stopped else False)),
            ("Authentification",
             (f"requise : {', '.join(providers)}" if providers
              else "requise" if data.get("auth_required")
              else "— (serveur arrêté)" if stopped else "aucun provider actif"),
             True if providers or data.get("auth_required") else (None if stopped else False)),
            ("Tunnel Tailscale",
             (snap["serve_target"] or "non configuré")
             + (" → route prête" if (snap["serve_target"].endswith(str(PORT)) and stopped)
                else " → OK" if snap["serve_target"].endswith(str(PORT)) else ""),
             True if (snap["serve_target"].endswith(str(PORT)) and not stopped) else None),
            ("Téléphone",
             (phone["label"] + (" · en ligne" if phone["online"] else " · hors ligne"))
             if phone else "aucun appareil Android vu",
             True if (phone and phone["online"]) else ("warn" if phone else None)),
            ("Backend desktop",
             (f"{len(desktop)} backend(s) serveur — jamais touché(s)" if desktop
              else "aucun backend serveur détecté"),
             None),
        ]
        y = sc(18)
        for name, value, ok in rows:
            dot = (GREEN if ok is True else AMBER if ok == "warn"
                   else FAINT if ok is None else RED)
            c.create_oval(sc(16), y - sc(3), sc(22), y + sc(3), fill=dot, outline="",
                          tags="content")
            c.create_text(sc(30), y, text=name, anchor="w", fill=MUTED,
                          font=("Segoe UI", -sc(10)), tags="content")
            c.create_text(width - sc(16), y, text=value, anchor="e",
                          fill=TEXT, font=("Segoe UI", -sc(10)), tags="content")
            y += sc(22)

    def _paint_button(self, snap: dict) -> None:
        self.main_btn.set_enabled(not self.busy)
        self.open_btn.set_enabled(snap["phase"] != "stopped")
        if snap["phase"] == "stopped":
            self.main_btn.set_text("Démarrer le serveur")
            self.main_btn.set_style(ACCENT, ACCENT_HI)
        elif snap["phase"] == "starting":
            self.main_btn.set_text("Démarrage…")
            self.main_btn.set_style("#3a4257", "#3a4257")
        else:
            self.main_btn.set_text("Arrêter le serveur")
            self.main_btn.set_style("#8f3f3f", "#a84c4c")

    def run(self) -> None:
        self.root.mainloop()

    def capture(self, path: str, delay_ms: int = 2500) -> None:
        """Capture la fenêtre (outil de contrôle visuel : --shot <fichier>)."""
        def shot():
            try:
                from PIL import ImageGrab

                self.root.update_idletasks()
                x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
                w, h = self.root.winfo_width(), self.root.winfo_height()
                img = ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
                img.save(path)
                print(f"capture -> {path} {img.size}")
            except Exception as exc:
                print(f"capture impossible : {exc}")
            if getattr(self, "tray", None):
                self.tray.stop()
                self.tray = None
            self.root.after(200, self.root.destroy)

        self.root.after(delay_ms, shot)


# ─────────────────────────────────────────────────────────────────────────────
#  Test fonctionnel en ligne de commande
# ─────────────────────────────────────────────────────────────────────────────


def selftest() -> int:
    ctl = ServerController(log=lambda m: print(f"  · {m}"))
    print("0) app desktop (lecture seule)")
    desk = find_desktop()
    print(f"   exe : {desk or 'introuvable'}")
    print(f"   lancement : {' '.join(desktop_command() or ['—'])}")
    print(f"   déjà ouverte : pid {desktop_running() or '—'}")
    print("1) état initial")
    snap = ctl.snapshot()
    print(f"   port libre : {snap['port_free']}  |  api : {snap['http'].get('up')}")

    if not snap["port_free"]:
        print(f"   port occupé par pid {snap['owner_pid']} — je l'adopte pour le test")
        ctl.adopt()

    print("2) démarrage")
    t0 = time.time()
    print(f"   -> {ctl.start()}")
    print(f"   temps total : {time.time() - t0:.1f} s")
    snap = ctl.snapshot()
    print(f"   pid={snap['owner_pid']}  http={snap['http'].get('code')}  "
          f"auth={bool((snap['http'].get('data') or {}).get('auth_required'))}  "
          f"ram={snap['owner'].get('rss_mb', 0):.0f} Mo")
    print(f"   tunnel={snap['serve_target']}  url={snap['public_url']}")
    ok_running = snap["phase"] == "running"

    print("3) arrêt")
    print(f"   -> {ctl.stop()}")
    snap = ctl.snapshot()
    print(f"   port libre : {snap['port_free']}")
    ok_stopped = snap["port_free"]

    print("4) garde-fou : le backend du desktop a-t-il survécu ?")
    backends = hermes_backends()
    for b in backends:
        print(f"   {b['kind']} pids {b.get('pids')}")
    ok_desktop = any(b["kind"] == "serve" for b in backends)

    print()
    print(f"RÉSULTAT : démarrage={'OK' if ok_running else 'KO'}  "
          f"arrêt={'OK' if ok_stopped else 'KO'}  "
          f"backend desktop intact={'OK' if ok_desktop else 'KO (ou desktop fermé)'}")
    return 0 if (ok_running and ok_stopped) else 1


def perf(iterations: int = 6) -> int:
    """Mesure ce qui gelait l'interface : sondage à froid vs cache, et coût de la peinture."""
    ctl = ServerController(log=lambda m: None)
    hermes = find_hermes()
    print("Coût unitaire des sondes lentes (ce que faisait l'ancien cycle à CHAQUE fois) :")
    slow = [
        ("hermes serve --status", lambda: run([hermes, "serve", "--status"]) if hermes else None),
        ("hermes config get dashboard.public_url", lambda: public_url()),
        ("tailscale serve status", lambda: tailscale_serve_target()),
        ("tailscale status --json", lambda: tailscale_peers()),
    ]
    for label, fn in slow:
        t0 = time.time()
        fn()
        print(f"   {label:<45} {1000 * (time.time() - t0):7.0f} ms")

    t0 = time.time()
    ctl.snapshot()
    cold = time.time() - t0
    print(f"\nSondage à froid (tout, une fois)             {1000 * cold:7.0f} ms")
    times = []
    for _ in range(iterations):
        t0 = time.time()
        ctl.snapshot()
        times.append(time.time() - t0)
    print(f"Sondage chaud (cache TTL, cas courant)       "
          f"min {1000 * min(times):.0f} ms · moy {1000 * sum(times) / len(times):.0f} ms · "
          f"max {1000 * max(times):.0f} ms")

    app = App()
    app.root.update()
    deadline = time.time() + 5
    while app.poller.latest is None and time.time() < deadline:
        time.sleep(0.2)
        app.root.update()
    paints = []
    for _ in range(6):
        t0 = time.time()
        app._paint_current()
        app.root.update()
        paints.append(time.time() - t0)
    print(f"Peinture d'un cycle sur le fil d'affichage   "
          f"min {1000 * min(paints):.0f} ms · moy {1000 * sum(paints) / len(paints):.0f} ms · "
          f"max {1000 * max(paints):.0f} ms")
    print(f"Durée du sondage en tâche de fond            {1000 * app.poller.last_duration:.0f} ms")
    app.poller.stop()
    if getattr(app, "tray", None):
        app.tray.stop()
        app.tray = None
    app.root.destroy()

    verdict = "OK" if max(paints) < 0.2 else "TROP LENT"
    print(f"\nRÉSULTAT : fil d'affichage < 200 ms par cycle = {verdict}")
    return 0 if verdict == "OK" else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--perf" in sys.argv:
        raise SystemExit(perf())
    if "--shot" in sys.argv:
        path = sys.argv[sys.argv.index("--shot") + 1]
        app = App()
        app.capture(path)
        app.run()
        raise SystemExit(0)
    App().run()
