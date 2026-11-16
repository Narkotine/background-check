"""Logique d'affichage pure (sans Tk) : états, libellés, détection, menus.

Tout ce qui décide *quoi* montrer vit ici, testable sans fenêtre ; `ui.py` et ses voisins ne
font que *peindre* ce que ces fonctions renvoient (refonte UX du 16 nov 2026).
"""

from __future__ import annotations

import ipaddress
import os
import time
from urllib.parse import urlparse

from bgc.core.metrics import item_pids, snapshot_pids

# ── états ─────────────────────────────────────────────────────────────────────
#: États d'affichage normalisés (badge du rail, tableau de bord, bandeau d'état).
STATE_OK, STATE_WARN, STATE_BAD = "ok", "warn", "bad"
STATE_IDLE, STATE_PAUSED, STATE_UNKNOWN = "idle", "paused", "unknown"

_PHASE_LABELS = {"running": "status.online", "starting": "status.starting",
                 "stopped": "status.stopped"}


def badge_state(snapshot: dict | None, enabled: bool = True, fell: bool = False) -> str:
    """État d'une tuile en un mot : ok / warn / bad / idle / paused / unknown.

    * vert (`ok`) : le service répond ;
    * ambre (`warn`) : démarrage, chaîne incomplète, process externe ;
    * rouge (`bad`) : erreur HTTP, sonde en échec, ou **chute** non demandée ;
    * gris (`idle`) : arrêté normalement ; gris barré (`paused`) : surveillance en pause.

    Un plugin peut imposer son verdict par `snapshot["health"]` (Hermes le fait : vert
    seulement si la chaîne réellement utilisée répond).
    """
    if not enabled:
        return STATE_PAUSED
    if not snapshot:
        return STATE_UNKNOWN
    extra = snapshot.get("extra") or {}
    if extra.get("paused"):
        return STATE_PAUSED
    if snapshot.get("error"):
        return STATE_BAD
    phase = snapshot.get("phase", "unknown")
    if phase == "running":
        health = snapshot.get("health")
        if health in (STATE_OK, STATE_WARN, STATE_BAD):
            return health
        if ((snapshot.get("http") or {}).get("code") or 0) >= 400:
            return STATE_BAD
        if not extra.get("ours", True):
            return STATE_WARN
        return STATE_OK
    if phase == "starting":
        return STATE_WARN
    if phase == "stopped":
        return STATE_BAD if fell else STATE_IDLE
    return STATE_UNKNOWN


def status_label(server_type, snapshot: dict | None, enabled: bool = True,
                 fell: bool = False) -> tuple[str, dict]:
    """Le mot d'état (clé de traduction + paramètres) qui accompagne la couleur."""
    if not enabled or (snapshot and (snapshot.get("extra") or {}).get("paused")):
        return "status.paused", {}
    if not snapshot:
        return "status.loading", {}
    phase = snapshot.get("phase", "unknown")
    extra = snapshot.get("extra") or {}
    own = getattr(server_type, "phase_labels", None) or {}
    key, params = own.get(phase, _PHASE_LABELS.get(phase, "status.loading")), {}
    if extra.get("status_label_key"):
        key, params = extra["status_label_key"], dict(extra.get("status_label_params") or {})
    if phase == "running" and not extra.get("ours", True):
        key, params = getattr(server_type, "external_label_key", "status.external"), {}
    if phase == "running" and ((snapshot.get("http") or {}).get("code") or 0) >= 400:
        key, params = "status.http_problem", {}
    if phase == "stopped" and fell:
        key, params = "status.fell", {}
    return key, params


def uptime_seconds(snapshot: dict | None, stats: dict | None = None,
                   now: float | None = None) -> float | None:
    """Depuis quand le service tourne : âge du process, sinon depuis que l'app l'a vu monter."""
    if not snapshot or snapshot.get("phase") != "running":
        return None
    info = (snapshot.get("meta") or {}).get("info") or {}
    if info.get("uptime"):
        return float(info["uptime"])
    since = float((stats or {}).get("since") or 0.0)
    if since > 0:
        return max(0.0, (now or time.time()) - since)
    return None


def main_port(server: dict) -> int | None:
    try:
        port = int((server.get("params") or {}).get("port") or 0)
    except (TypeError, ValueError):
        return None
    return port or None


# ── adresses ──────────────────────────────────────────────────────────────────
def url_is_local(url: str) -> bool:
    """Vrai si l'URL ne sort pas de ce PC ou du réseau privé (rien de « public » à copier)."""
    text = str(url or "").strip()
    if not text:
        return True
    host = (urlparse(text if "://" in text else "http://" + text).hostname or "").lower()
    if host in ("", "localhost", "0.0.0.0") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # un nom (ex. machine.tailnet.ts.net) : adresse « publique » de l'utilisateur
    return ip.is_loopback or ip.is_private or ip.is_link_local


def copy_url_label_key(url: str) -> str:
    return "btn.copy_url" if url_is_local(url) else "btn.copy_url_public"


def adjust_actions(actions: list[dict], snapshot: dict | None) -> list[dict]:
    """Règles communes à tous les types, appliquées à l'affichage (copie des actions).

    * « Navigateur » et « Copier l'URL » grisés tant que le service ne tourne pas (un
      bouton qui mène à une page morte n'a pas l'air prêt) — on ne fait que **griser** :
      un plugin qui a déjà grisé garde la main ;
    * « Copier l'URL » dit si l'adresse est publique ou locale.
    """
    snapshot = snapshot or {}
    running = snapshot.get("phase") == "running"
    url = str(snapshot.get("url") or "")
    result = []
    for action in actions or []:
        item = dict(action)
        aid = item.get("id")
        if aid in ("browser", "copy_url") and not running:
            item["enabled"] = False
        if aid == "copy_url" and item.get("label_key") in ("btn.copy_url", "btn.copy_url_public"):
            item["label_key"] = copy_url_label_key(url)
        result.append(item)
    return result


def has_items(snapshot: dict | None) -> bool:
    """La tuile publie-t-elle des éléments (modèle « liste d'items ») ?"""
    return isinstance((snapshot or {}).get("items"), list)


def items_of(snapshot: dict | None, label: str = "") -> list[dict]:
    """Éléments d'une tuile ; un type sans `items` = **un élément implicite** (id "").

    C'est l'adaptateur qui laisse les types historiques (un service = une tuile) passer par
    les mêmes chemins que Docker/WSL (MCP `list_items`, vue d'ensemble) sans rien changer.
    """
    snap = snapshot or {}
    if has_items(snap):
        result = []
        for item in snap["items"]:
            if not isinstance(item, dict) or not str(item.get("id") or ""):
                continue
            result.append({"id": str(item["id"]), "label": str(item.get("label") or item["id"]),
                           "phase": str(item.get("phase") or "unknown"),
                           "rows": list(item.get("rows") or []),
                           "actions": list(item.get("actions") or []),
                           "detail_key": item.get("detail_key") or "",
                           "detail_params": dict(item.get("detail_params") or {}),
                           "pids": item_pids(item),
                           "implicit": False})
        return result
    return [{"id": "", "label": label, "phase": str(snap.get("phase") or "unknown"),
             "rows": list(snap.get("rows") or []), "actions": list(snap.get("actions") or []),
             "detail_key": "", "detail_params": {}, "pids": snapshot_pids(snap),
             "implicit": True}]


def item_counts(snapshot: dict | None) -> tuple[int, int]:
    """(en marche, total) des éléments explicites d'une tuile."""
    items = items_of(snapshot) if has_items(snapshot) else []
    return sum(1 for i in items if i["phase"] == "running"), len(items)


def one_line(text) -> str:
    """Une valeur affichée dans une ligne : retours à la ligne et espaces multiples aplatis
    (une sortie de commande brute ne doit jamais déborder sur les lignes voisines)."""
    return " ".join(str(text or "").split())


def anything_running(snapshot: dict | None) -> bool:
    """Quelque chose tourne-t-il dans cette tuile (elle-même ou l'un de ses éléments) ?"""
    if not snapshot:
        return False
    if snapshot.get("phase") in ("running", "starting"):
        return True
    return any(i["phase"] in ("running", "starting") for i in items_of(snapshot)
               if has_items(snapshot))


def item_state(phase: str) -> str:
    """Pastille d'un élément : vert en marche, ambre au démarrage, gris sinon."""
    return {"running": "ok", "starting": "warn", "stopped": "idle"}.get(phase, "unknown")


def chain_lines(chain) -> list[dict]:
    """Lignes d'une chaîne de maillons (`extra.chain`), format générique."""
    if not isinstance(chain, dict):
        return []
    return [line for line in chain.get("lines") or [] if isinstance(line, dict)]


def primary_actions(actions: list[dict]) -> list[dict]:
    """Les boutons principaux (démarrer/arrêter) d'une tuile ou d'un élément.

    Le plugin les déclare (`"main": True`) ; à défaut, ceux dont l'id commence par
    « toggle » (règle historique, gardée pour les plugins existants). Utilisé par la vue
    d'ensemble, le tray (`core.actions`) et le MCP : une seule règle.
    """
    marked = [a for a in actions or [] if a.get("main")]
    if marked:
        return marked
    return [a for a in actions or [] if str(a.get("id") or "").startswith("toggle")]


def web_action(actions: list[dict], custom: list[dict] | None = None,
               custom_states: dict | None = None) -> dict | None:
    """Le bouton « web » d'une ligne : l'action navigateur, sinon le premier bouton sur mesure
    qui ouvre une adresse web."""
    for action in actions or []:
        if action.get("id") == "browser":
            return action
    for index, item in enumerate(custom or []):
        target = str(item.get("target") or "").strip().lower()
        if item.get("label") and target.startswith(("http://", "https://")):
            return {"id": f"custom:{index}", "label_key": "btn.open_web",
                    "enabled": (custom_states or {}).get(index, True)}
    return None


# ── services détectés (formulaire d'ajout, premier lancement) ─────────────────
#: Processus système ou outils qui ouvrent des ports sans être des « services » à suivre.
SYSTEM_PROCESSES = {
    "", "system", "system idle process", "svchost.exe", "lsass.exe", "wininit.exe",
    "services.exe", "spoolsv.exe", "smss.exe", "csrss.exe", "winlogon.exe", "lsaiso.exe",
    "searchindexer.exe", "msmpeng.exe", "nissrv.exe", "jhi_service.exe", "mdnsresponder.exe",
    "nvcontainer.exe", "nvdisplay.container.exe", "wudfhost.exe", "dashost.exe",
    "officeclicktorun.exe", "onedrive.exe", "steam.exe", "steamwebhelper.exe",
    "discord.exe", "spotify.exe", "teams.exe", "ms-teams.exe", "msedge.exe",
    "chrome.exe", "firefox.exe", "code.exe", "cursor.exe", "antigravity.exe",
    "language_server_windows_x64.exe", "tailscaled.exe", "tailscale-ipn.exe",
    "vmms.exe", "vmcompute.exe", "wslservice.exe", "wslhost.exe", "com.docker.backend.exe",
    "adobe desktop service.exe", "razer synapse service.exe", "lghub_agent.exe",
    "explorer.exe", "googledrivefs.exe", "dropbox.exe", "nvidia share.exe",
    "nvidia web helper.exe", "epicgameslauncher.exe", "battle.net.exe", "ea desktop.exe",
}

#: Nom de processus → modèle rapide (la correspondance la plus sûre).
PRESET_BY_PROCESS = {
    "ollama.exe": "preset.ollama", "ollama": "preset.ollama",
    "ollama app.exe": "preset.ollama",
    "lm studio.exe": "preset.lmstudio", "lms.exe": "preset.lmstudio",
    "llama-server.exe": "preset.llamacpp", "llama-server": "preset.llamacpp",
    "koboldcpp.exe": "preset.koboldcpp", "koboldcpp": "preset.koboldcpp",
}

#: Port par défaut → modèle rapide (quand le nom du processus est générique : python, node…).
PRESET_BY_PORT = {
    11434: "preset.ollama", 1234: "preset.lmstudio", 5240: "preset.llamagui",
    8080: "preset.llamacpp", 7860: "preset.textgen", 5001: "preset.koboldcpp",
    8188: "preset.comfyui", 3000: "preset.openwebui",
}

#: Au-dessus : plage des ports dynamiques de Windows (RPC, clients…), presque toujours du bruit.
DYNAMIC_PORT_START = 49152


def match_preset(name: str, ports: list[int]) -> str | None:
    """Le modèle rapide qui correspond le plus probablement à un service détecté."""
    key = PRESET_BY_PROCESS.get(str(name or "").lower())
    if key:
        return key
    for port in sorted(ports):
        if port in PRESET_BY_PORT:
            return PRESET_BY_PORT[port]
    return None


def group_detected(entries: list[dict], tracked_ports: set[int] | None = None,
                   own_ports: set[int] | None = None, own_pids: set[int] | None = None,
                   min_port: int = 1024) -> list[dict]:
    """Ports en écoute → services lisibles, **filtrés et regroupés par processus**.

    Exclut : les ports déjà suivis par une tuile, les ports et processus de l'app elle-même
    (dont son serveur MCP), les processus système, les ports < `min_port` et la plage
    dynamique (sauf si le port correspond à un modèle connu). Chaque groupe :
    `{name, pid, ports, port, preset}` — `port` est le plus petit port, celui proposé.
    """
    tracked = set(tracked_ports or ())
    own_p = set(own_ports or ())
    own_pid = set(own_pids or ())
    groups: dict[tuple, dict] = {}
    for entry in entries or []:
        port = int(entry.get("port") or 0)
        pid = entry.get("pid")
        name = str(entry.get("name") or "")
        if port < min_port or port in tracked or port in own_p:
            continue
        if pid is not None and pid in own_pid:
            continue
        if name.lower() in SYSTEM_PROCESSES:
            continue
        if port >= DYNAMIC_PORT_START and match_preset(name, [port]) is None:
            continue
        group = groups.setdefault((name.lower(), pid), {"name": name, "pid": pid, "ports": [],
                                                        "exe": entry.get("exe") or ""})
        group["ports"].append(port)
    result = []
    for group in groups.values():
        group["ports"].sort()
        group["port"] = group["ports"][0]
        group["preset"] = match_preset(group["name"], group["ports"])
        result.append(group)
    # Les services reconnus d'abord, puis par port.
    result.sort(key=lambda g: (g["preset"] is None, g["port"]))
    return result


def display_name(process_name: str) -> str:
    """« ollama.exe » → « ollama » (nom proposé pour une tuile)."""
    base = str(process_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    return base[:-4] if base.lower().endswith(".exe") else base


def tracked_ports(servers: list[dict]) -> set[int]:
    """Ports déjà surveillés : port principal + sondes supplémentaires de chaque tuile."""
    ports: set[int] = set()
    for server in servers or []:
        params = server.get("params") or {}
        for key in ("port",):
            try:
                value = int(params.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value:
                ports.add(value)
        for line in str(params.get("extra_checks") or "").splitlines():
            target = line.partition("=")[2].strip() if "=" in line else line.strip()
            target = target.split("://", 1)[-1].split("/", 1)[0]
            tail = target.rpartition(":")[2] if ":" in target else target
            if tail.isdigit():
                ports.add(int(tail))
    return ports


# ── modèles rapides appliqués à une tuile existante ───────────────────────────
def preset_diff(current: dict, preset: dict, field_labels: dict | None = None) -> list[tuple]:
    """Ce qu'un modèle changerait : [(libellé ou clé, ancienne valeur, nouvelle valeur)].

    `current` : {"params": {...}, "icon": str, "actions": [...]} ; `preset` : la sortie de
    `presets.values()`. Les valeurs identiques (à la casse/aux espaces près) sont ignorées.
    """
    labels = field_labels or {}
    params = current.get("params") or {}
    changes: list[tuple] = []
    for key, new in (preset.get("fields") or {}).items():
        old = params.get(key, "")
        if str(old if old is not None else "").strip() != str(new if new is not None else "").strip():
            changes.append((labels.get(key, key), str(old or ""), str(new or "")))
    icon = str(preset.get("icon") or "")
    if icon and icon != str(current.get("icon") or ""):
        changes.append(("field.icon", os.path.basename(str(current.get("icon") or "")),
                        os.path.basename(icon)))
    new_actions = [(a.get("label", ""), a.get("target", "")) for a in preset.get("actions") or []]
    old_actions = [(a.get("label", ""), a.get("target", ""))
                   for a in current.get("actions") or [] if a.get("label") or a.get("target")]
    if new_actions and new_actions != old_actions:
        changes.append(("settings.custom_actions", ", ".join(l for l, _ in old_actions),
                        ", ".join(l for l, _ in new_actions)))
    return changes


# ── menu de la zone de notification ───────────────────────────────────────────
#: Nombre de services montrés directement dans le menu ; le reste va dans « Plus (n) ».
TRAY_DIRECT = 10


def tray_service_menu(entries: list[tuple[str, list]], more_label: str,
                      limit: int = TRAY_DIRECT) -> list:
    """Services → entrées de menu, **sans plafond muet** : au-delà de `limit`, un sous-menu
    « Plus (n) » contient les suivants. Chaque entrée : (libellé, sous-menu, actif)."""
    items = [(label, submenu, True) for label, submenu in entries[:limit]]
    rest = entries[limit:]
    if rest:
        items.append((more_label.format(n=len(rest)) if "{n}" in more_label else more_label,
                      [(label, submenu, True) for label, submenu in rest], True))
    return items


def truncate(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


# --- ressources par service (CPU / RAM / VRAM, voir bgc/core/metrics.py) ------------------
DASH = "—"


def _decimal(value: float, digits: int) -> str:
    from bgc import i18n
    text = f"{value:.{digits}f}"
    return text.replace(".", i18n.t("unit.decimal")) if digits else text


def fmt_cpu(value) -> str:
    """12.3 → « 12 % » ; sous 10 %, une décimale (« 0,4 % ») ; None → « — »."""
    if value is None:
        return DASH
    from bgc import i18n
    value = max(0.0, float(value))
    return i18n.t("unit.percent", v=_decimal(value, 1 if value < 10 else 0))


def fmt_mb(value) -> str:
    """Mégaoctets lisibles : « 412 Mo », « 2,1 Go » ; None → « — »."""
    if value is None:
        return DASH
    from bgc import i18n
    value = max(0.0, float(value))
    if value >= 1024:
        return i18n.t("unit.gb", v=_decimal(value / 1024, 1))
    return i18n.t("unit.mb", v=_decimal(value, 0))


def metrics_for(app, runtime, item_id: str = "") -> dict | None:
    """Dernière mesure d'une tuile / d'un élément (None si l'app n'a pas d'échantillonneur)."""
    getter = getattr(app, "metrics_of", None)
    return getter(runtime, item_id) if getter else None


def metrics_cells(metrics: dict | None) -> tuple[str, str, str]:
    """(CPU, RAM, VRAM) en texte court pour la vue d'ensemble."""
    if not metrics:
        return DASH, DASH, DASH
    return (fmt_cpu(metrics.get("cpu")), fmt_mb(metrics.get("ram_mb")),
            fmt_mb(metrics.get("vram_mb")))


def metrics_text(metrics: dict | None) -> str:
    """« CPU 3 % · RAM 412 Mo · VRAM 1,2 Go » (vide sans mesure)."""
    if not metrics:
        return ""
    from bgc import i18n
    cpu, ram, vram = metrics_cells(metrics)
    return "  ·  ".join((f"{i18n.t('overview.col_cpu')} {cpu}",
                         f"{i18n.t('overview.col_ram')} {ram}",
                         f"{i18n.t('overview.col_vram')} {vram}"))


def gpu_text(total: dict | None) -> str:
    """Total de la carte : « GPU 6,2 / 16,0 Go » ; vide si inconnu."""
    if not total or total.get("used_mb") is None:
        return ""
    from bgc import i18n
    if total.get("total_mb"):
        return i18n.t("overview.gpu", used=fmt_mb(total["used_mb"]),
                      total=fmt_mb(total["total_mb"]))
    return i18n.t("overview.gpu_used", used=fmt_mb(total["used_mb"]))
