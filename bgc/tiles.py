"""Tuile exportable : un petit fichier JSON qui décrit UN serveur configuré.

But : sauvegarder une tuile, l'envoyer à quelqu'un, la réimporter ailleurs — sans
embarquer la configuration entière ni les autres serveurs.

Ce qui est exporté est **ce qui définit la tuile** : son type, son nom, ses paramètres,
ses interrupteurs et son icône (chemin). Rien de la machine n'est copié (pas de fichier,
pas de secret ajouté). Les variables d'environnement saisies dans la tuile (souvent des clés
d'API) sont **retirées par défaut** ; l'écran d'export demande avant de les inclure.

Import (revue du 16 nov 2026) : une tuile reçue peut contenir des commandes. L'application
montre donc d'abord ce que la tuile exécutera (`preview`) et **coupe** les options de démarrage
automatique (`safe_options`) : importer ne lance jamais rien.
"""

from __future__ import annotations

import json
import os

FORMAT = "bgc-tile"
#: Tuiles exportées sous l'ancien nom (Background Check) : toujours importables.
OLD_FORMATS = ("hermesserv-tile",)
VERSION = 1
#: Interrupteurs qui lancent quelque chose sans clic : toujours coupés à l'import.
AUTO_START_OPTIONS = ("start_on_app_start", "auto_restart", "auto_launch_desktop")
#: Paramètres considérés comme secrets à l'export (en plus de `env`).
_SECRET_HINTS = ("token", "secret", "password", "api_key", "apikey")


def has_env(server: dict) -> bool:
    return bool(str((server.get("params") or {}).get("env") or "").strip())


def build(server: dict, include_env: bool = False) -> dict:
    """Payload JSON d'une tuile (ordre des clés stable, lisible à la main).

    `include_env=False` (défaut) vide `env` et tout paramètre au nom de secret.
    """
    params = dict(server.get("params") or {})
    if not include_env:
        for key in list(params):
            lowered = key.lower()
            if lowered == "env" or any(hint in lowered for hint in _SECRET_HINTS):
                params[key] = ""
    return {
        "format": FORMAT,
        "version": VERSION,
        "type": str(server.get("type") or ""),
        "label": str(server.get("label") or ""),
        "params": params,
        "options": dict(server.get("options") or {}),
        "actions": [dict(item) for item in (server.get("actions") or [])],
        "deps": str(server.get("deps") or ""),
        "icon": str(server.get("icon") or ""),
    }


def parse(data) -> dict:
    """Valide un fichier de tuile ; lève ValueError(clé de traduction) sinon."""
    if not isinstance(data, dict) or data.get("format") not in (FORMAT, *OLD_FORMATS):
        raise ValueError("tile.error.format")
    try:
        version = int(data.get("version") or 0)
    except (TypeError, ValueError):
        raise ValueError("tile.error.format")
    if version < 1 or version > VERSION:
        raise ValueError("tile.error.version")
    type_id = str(data.get("type") or "").strip()
    if not type_id:
        raise ValueError("tile.error.type")
    label = str(data.get("label") or "").strip()
    if not label:
        raise ValueError("tile.error.label")
    params = data.get("params")
    options = data.get("options")
    actions = data.get("actions")
    return {
        "type": type_id,
        "label": label,
        "params": dict(params) if isinstance(params, dict) else {},
        "options": dict(options) if isinstance(options, dict) else {},
        "actions": [item for item in (actions or []) if isinstance(item, dict)][:3],
        "deps": str(data.get("deps") or "")[:1000],
        "icon": str(data.get("icon") or ""),
    }


def write(path: str, server: dict, include_env: bool = False) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build(server, include_env=include_env), fh, indent=2, ensure_ascii=False)
    return path


def safe_options(options: dict) -> tuple[dict, list[str]]:
    """Copie des interrupteurs avec le démarrage automatique coupé ; renvoie aussi les clés
    qui étaient actives (pour le dire à l'utilisateur)."""
    cleaned = dict(options or {})
    disabled = [key for key in AUTO_START_OPTIONS if cleaned.get(key)]
    for key in AUTO_START_OPTIONS:
        if key in cleaned:
            cleaned[key] = False
    return cleaned, disabled


def preview(payload: dict, fields: list | None = None) -> list[tuple[str, dict]]:
    """Ce que la tuile exécutera ou ouvrira : [(clé i18n, paramètres)] pour l'écran de
    confirmation. `fields` = champs du type (pour nommer les paramètres) ; facultatif."""
    labels = {getattr(f, "key", ""): getattr(f, "label_key", "") for f in (fields or [])}
    lines: list[tuple[str, dict]] = [
        ("tile.preview_type", {"type": payload.get("type", "")}),
        ("tile.preview_label", {"label": payload.get("label", "")}),
    ]
    params = payload.get("params") or {}
    runs_something = False
    for key, value in params.items():
        text = str(value if value is not None else "").strip()
        if not text:
            continue
        if key == "env":
            names = [line.partition("=")[0].strip() for line in text.splitlines() if "=" in line]
            lines.append(("tile.preview_env", {"names": ", ".join(names) or "?"}))
            continue
        if key in ("command", "stop_command", "dashboard_args"):
            runs_something = True
        lines.append(("tile.preview_param", {"name": labels.get(key) or key,
                                             "value": text[:200]}))
    for action in payload.get("actions") or []:
        target = str(action.get("target") or "")
        if not target.lower().startswith(("http://", "https://")):
            runs_something = True
        lines.append(("tile.preview_action", {"label": str(action.get("label") or ""),
                                              "target": target[:200]}))
    if not runs_something:
        lines.append(("tile.preview_none", {}))
    _, disabled = safe_options(payload.get("options") or {})
    if disabled:
        lines.append(("tile.preview_autostart_off", {"options": ", ".join(disabled)}))
    return lines


def read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return parse(json.load(fh))


def default_filename(label: str) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in label).strip() or "tuile"
    return f"{safe}.tile.json"


def icon_if_exists(path: str) -> str:
    """L'icône d'une tuile importée n'est reprise que si le fichier existe sur cette machine."""
    return path if path and os.path.isfile(path) else ""
