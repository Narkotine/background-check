"""Configuration de Background Check : un seul `config.json`, sections « général » et « serveurs ».

Dimensions :
  * `general` : réglages valables pour l'application entière (langue, comportement de fermeture).
  * `servers` : une entrée par serveur surveillé, chacune avec son `type` (plugin), son libellé,
    ses `params` (ce que le plugin interroge) et ses `options` (interrupteurs du serveur).

Migration : l'ancien `settings.json` (3 booléens) est absorbé une seule fois dans `general` et un
serveur Hermes est recréé avec les chemins détectés — on ne casse pas une installation existante.
Une installation neuve, elle, démarre **sans aucun serveur**.
"""

from __future__ import annotations

import json
import os
import secrets
import time

from . import APP_ID, OLD_APP_ID

CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), APP_ID)
#: Dossier de l'ancien nom (HermesServ) : copié — jamais déplacé — par `migration.py`.
OLD_CONFIG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), OLD_APP_ID)
#: `BGC_CONFIG` (ou l'ancien `HERMESSERV_CONFIG`) fait tourner une instance de test sur un
#: fichier à part (captures, essais) sans toucher la configuration réelle de l'utilisateur.
TEST_CONFIG = os.environ.get("BGC_CONFIG") or os.environ.get("HERMESSERV_CONFIG") or ""
CONFIG_PATH = TEST_CONFIG or os.path.join(CONFIG_DIR, "config.json")
LEGACY_SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

DEFAULT_LANGUAGE = "fr"


def default_general() -> dict:
    return {
        "language": DEFAULT_LANGUAGE,
        "hide_on_close": True,
        # Taille de l'interface (1.0 = référence). L'utilisateur trouve l'UI « trop petite »
        # par défaut : 1.3 agrandit tout uniformément (textes, boutons, fenêtre).
        "ui_scale": 1.3,
        # Notification Windows quand un service en marche s'arrête sans qu'on l'ait demandé.
        "notify_on_fall": True,
        # Serveur MCP local (127.0.0.1) pour qu'un agent IA pilote et configure les tuiles.
        "mcp_enabled": True,
        "mcp_port": 8781,
        # Jeton exigé par le serveur MCP (`Authorization: Bearer …`) — créé au premier
        # lancement par `ensure_mcp_token()` ; vide = pas encore créé.
        "mcp_token": "",
        # Refonte UX du 16 nov 2026 : rail en icônes seules, journal replié, raccourci global.
        "rail_compact": False,
        "log_collapsed": False,
        "global_hotkey": False,
        # Thème (16 nov 2026) : "dark", "light" ou "system" (suit le mode des applis Windows).
        "theme": "dark",
    }


def new_mcp_token() -> str:
    """Jeton aléatoire (256 bits, URL-safe) pour le serveur MCP."""
    return secrets.token_urlsafe(32)


def hermes_home() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes")


def migrated_hermes_params(phone_node: str = "") -> dict:
    """Paramètres du serveur Hermes pour une installation qui existait déjà (Hermes seul)."""
    return {
        "hermes_root": hermes_home(),
        "host": "127.0.0.1",
        "port": 9119,
        "url": "",
        "phone_node": phone_node,
        "dashboard_args": "dashboard --no-open --skip-build",
    }


class Config:
    """Chargement, migration et écriture atomique du fichier de configuration."""

    def __init__(self, path: str | None = None):
        self.path = path or CONFIG_PATH
        self.data: dict = {"version": 1, "general": default_general(), "servers": []}
        self.created = False
        self.migrated = False
        #: Fichier illisible mis de côté au chargement : {"path", "error", "backups"} ou None.
        self.corrupt: dict | None = None

    # --- lecture ----------------------------------------------------------
    def load(self) -> "Config":
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                if not isinstance(raw, dict):
                    raise ValueError("le fichier ne contient pas un objet JSON")
                self.data = self._normalise(raw)
            except Exception as exc:
                # Revue du 16 nov 2026 : avant, un fichier illisible était remplacé en silence
                # par une config vide, que la sauvegarde suivante écrivait PAR-DESSUS les tuiles
                # de l'utilisateur. Désormais il est renommé, jamais écrasé, et l'app le dit.
                self._set_aside(exc)
            return self

        legacy = self._read_legacy()
        general = default_general()
        servers: list[dict] = []
        if legacy is not None:
            general["hide_on_close"] = bool(legacy.get("hide_on_close", True))
            general["language"] = str(legacy.get("language") or DEFAULT_LANGUAGE)
            servers.append({
                "id": self._new_id("hermes", servers),
                "type": "hermes",
                "label": "Hermes",
                "enabled": True,
                "params": migrated_hermes_params(),
                "options": {
                    "stop_on_quit": bool(legacy.get("stop_on_exit", False)),
                    "auto_launch_desktop": bool(legacy.get("auto_desktop", False)),
                },
            })
            self.migrated = True
        self.data = {"version": 1, "general": general, "servers": servers}
        self.created = True
        self.save()
        return self

    def _set_aside(self, exc: Exception) -> None:
        stamp = time.strftime("%Y%m%dT%H%M%S")
        target = f"{self.path}.corrupt-{stamp}"
        try:
            os.replace(self.path, target)
        except OSError:
            target = self.path  # renommage impossible : on ne l'écrasera pas pour autant
        self.data = {"version": 1, "general": default_general(), "servers": []}
        self.corrupt = {"path": target, "error": f"{type(exc).__name__}: {exc}"[:200],
                        "backups": self.list_backups()}

    def list_backups(self) -> list[str]:
        """Sauvegardes `config.json.bak*` lisibles, la plus récente d'abord."""
        folder = os.path.dirname(self.path) or "."
        base = os.path.basename(self.path)
        found = []
        try:
            names = os.listdir(folder)
        except OSError:
            return []
        for name in names:
            if not name.startswith(base + ".bak"):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    if not isinstance(json.load(fh), dict):
                        continue
            except Exception:
                continue
            found.append(path)
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return found

    def restore_backup(self, backup_path: str) -> "Config":
        """Remet une sauvegarde en place (copie, la sauvegarde reste) puis recharge."""
        import shutil
        shutil.copyfile(backup_path, self.path)
        self.corrupt = None
        self.created = False
        return self.load()

    @staticmethod
    def _read_legacy() -> dict | None:
        try:
            with open(LEGACY_SETTINGS_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except Exception:
            return None

    def _normalise(self, raw: dict) -> dict:
        general = default_general()
        general.update({k: v for k, v in (raw.get("general") or {}).items() if k in general})
        servers = []
        for item in raw.get("servers") or []:
            if not isinstance(item, dict) or not item.get("type"):
                continue
            type_id = str(item["type"])
            params = dict(item.get("params") or {})
            if type_id == "portwatch":
                # Fusionné dans « command » (7 nov 2026) : un type « surveiller » sans
                # commandes EST ce type. Migration transparente au chargement.
                type_id = "command"
                params = {
                    "host": str(params.get("host") or "127.0.0.1"),
                    "port": params.get("port", ""),
                    "check_url": str(params.get("check_url") or ""),
                    "url": str(params.get("url") or ""),
                    "command": "", "stop_command": "", "workdir": "", "env": "",
                }
            servers.append({
                "id": str(item.get("id") or self._new_id(type_id, servers)),
                "type": type_id,
                "label": str(item.get("label") or type_id),
                "enabled": bool(item.get("enabled", True)),
                "params": params,
                "options": dict(item.get("options") or {}),
                "icon": str(item.get("icon") or ""),
                # Actions sur mesure propre à la tuile (max 3) : [{label, target}]
                "actions": self._normalise_actions(item.get("actions")),
            })
        return {"version": int(raw.get("version") or 1), "general": general, "servers": servers}

    # --- écriture ---------------------------------------------------------
    @staticmethod
    def _normalise_actions(raw) -> list[dict]:
        """Actions sur mesure : [{label, target, check?}] — 3 au maximum, champs vides écartés.

        `check` (facultatif) est la **sonde du bouton** : `proc:LM Studio.exe`, `port:1234`
        ou une URL. Sans elle, un bouton dont la cible est une adresse est sondé sur sa cible,
        et un bouton de commande reste toujours actif (10 nov 2026).
        """
        actions: list[dict] = []
        for item in (raw or [])[:3]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            target = str(item.get("target") or "").strip()
            if label and target:
                action = {"label": label, "target": target}
                check = str(item.get("check") or "").strip()[:80]
                if check:
                    action["check"] = check
                actions.append(action)
        return actions

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)
        return self.path

    def ensure_mcp_token(self) -> bool:
        """Crée le jeton MCP s'il manque ; True si la config a changé (à enregistrer)."""
        token = str(self.general.get("mcp_token") or "").strip()
        if len(token) >= 16:
            return False
        self.general["mcp_token"] = new_mcp_token()
        return True

    # --- accès ------------------------------------------------------------
    @property
    def general(self) -> dict:
        return self.data.setdefault("general", default_general())

    @property
    def servers(self) -> list[dict]:
        return self.data.setdefault("servers", [])

    def get_server(self, server_id: str) -> dict | None:
        return next((s for s in self.servers if s["id"] == server_id), None)

    def _new_id(self, type_id: str, servers: list[dict] | None = None) -> str:
        taken = {s["id"] for s in (servers if servers is not None else self.servers)}
        index = 1
        while f"{type_id}-{index}" in taken:
            index += 1
        return f"{type_id}-{index}"

    def add_server(self, type_id: str, label: str, params: dict,
                   options: dict | None = None, actions: list | None = None,
                   enabled: bool = True) -> dict:
        server = {
            "id": self._new_id(type_id),
            "type": type_id,
            "label": label.strip() or type_id,
            "enabled": bool(enabled),
            "params": params,
            "options": options or {},
            "actions": self._normalise_actions(actions),
            "created": int(time.time()),
        }
        self.servers.append(server)
        self.save()
        return server

    def move_server(self, server_id: str, delta: int) -> bool:
        """Déplace une tuile dans le rail (delta -1 = monter, +1 = descendre)."""
        ids = [s["id"] for s in self.servers]
        if server_id not in ids or delta == 0:
            return False
        return self.move_server_to(server_id, ids.index(server_id) + delta)

    def move_server_to(self, server_id: str, index: int) -> bool:
        """Place une tuile à une position précise (glisser-déposer du rail)."""
        ids = [s["id"] for s in self.servers]
        if server_id not in ids:
            return False
        current = ids.index(server_id)
        target = max(0, min(int(index), len(self.servers) - 1))
        if target == current:
            return False
        server = self.servers.pop(current)
        self.servers.insert(target, server)
        self.save()
        return True

    def duplicate_server(self, server_id: str, label: str) -> dict | None:
        """Copie une tuile (mêmes paramètres, interrupteurs et icône) sous un nouveau nom."""
        server = self.get_server(server_id)
        if not server:
            return None
        copy = {
            "id": self._new_id(str(server.get("type") or "server")),
            "type": str(server.get("type") or ""),
            "label": (label or "").strip() or f"{server.get('label', '')} (copie)",
            "enabled": True,
            "params": dict(server.get("params") or {}),
            "options": dict(server.get("options") or {}),
            "actions": [dict(a) for a in (server.get("actions") or [])],
            "icon": str(server.get("icon") or ""),
            "created": int(time.time()),
        }
        index = next(i for i, s in enumerate(self.servers) if s["id"] == server_id)
        self.servers.insert(index + 1, copy)  # la copie se place juste après l'original
        self.save()
        return copy

    def update_server(self, server_id: str, **changes) -> dict | None:
        server = self.get_server(server_id)
        if not server:
            return None
        for key, value in changes.items():
            if key in ("params", "options"):
                server.setdefault(key, {}).update(value or {})
            elif key == "actions":
                server["actions"] = self._normalise_actions(value)
            elif key in ("label", "enabled", "type", "icon"):
                server[key] = value
        self.save()
        return server

    def remove_server(self, server_id: str) -> bool:
        before = len(self.servers)
        self.data["servers"] = [s for s in self.servers if s["id"] != server_id]
        changed = len(self.data["servers"]) != before
        if changed:
            self.save()
        return changed
