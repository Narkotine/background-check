"""Actions sur les tuiles, partagées par l'interface, le tray et le serveur MCP.

Avant le découpage du 16 nov 2026, la même logique existait trois fois : `App.run_action`
(boutons), les entrées du tray (`App._tray_items_for`, avec les identifiants Hermes en dur) et
`McpTools._start_server/_stop_server`. Ici, une seule implémentation, sans Tk :

* `perform()` — exécute une action (synchrone) : drapeau « occupé », arrêt attendu (pas
  d'alerte de chute), relance du sondage, puis repeinture demandée à l'hôte ;
* `run_async()` — la même dans un fil (interface, tray) ;
* `main_actions()` — les actions principales d'une tuile (celles du tray et de la vue
  d'ensemble), **déclarées par le plugin** (`"main": True`) — voir `pres.primary_actions` ;
* `menu_entries()` — le sous-menu tray d'une tuile.

L'hôte (l'app) fournit : `log_app(clé, **params)`, `request_repaint()` et `runtimes`.
"""

from __future__ import annotations

import subprocess
import threading
import time
import webbrowser

from bgc import i18n
from bgc import presentation as pres

#: Fenêtre (s) pendant laquelle une chute après un arrêt demandé n'est pas une alerte.
EXPECT_STOP_WINDOW = 20.0


def is_stop(action: dict | None, runtime=None) -> bool:
    """Cette action arrête-t-elle quelque chose, dans l'état actuel ?

    Le plugin peut le dire (`"stops": True`) ; sinon : un libellé « …stop… » (btn.stop,
    btn.hermes_stop…) ou une bascule `toggle` pendant que la tuile tourne.
    """
    if not action:
        return False
    if "stops" in action:
        return bool(action["stops"])
    key = str(action.get("label_key") or "")
    if "stop" in key.lower():
        return True
    return (str(action.get("id") or "") == "toggle" and runtime is not None
            and runtime.phase == "running")


def find_action(runtime, action_id: str, item_id: str | None = None) -> dict | None:
    snap = runtime.snapshot_data or {}
    if item_id:
        item = next((i for i in snap.get("items") or [] if str(i.get("id")) == item_id), None)
        actions = (item or {}).get("actions") or []
    else:
        actions = snap.get("actions") or []
    return next((a for a in actions if a.get("id") == action_id), None)


def main_actions(runtime) -> list[dict]:
    """Actions principales (marche/arrêt) : celles que le plugin marque `main`, sinon `toggle`.

    Plus aucun identifiant propre à un plugin ici : Hermes marque ses deux bascules
    (passerelle locale + accès distant) `main` dans son instantané.
    """
    return pres.primary_actions((runtime.snapshot_data or {}).get("actions") or [])


def has_something_to_stop(runtime) -> bool:
    """Arrêt « à la fermeture » utile ? La tuile ou un élément tourne, ou une action
    principale est un arrêt (Hermes : passerelle locale en marche, tuile « arrêtée »)."""
    snap = runtime.snapshot_data or {}
    if pres.anything_running(snap):
        return True
    return any(is_stop(a, runtime) for a in main_actions(runtime) if a.get("enabled", True))


class Actions:
    def __init__(self, host):
        self.host = host

    # --- recherche ----------------------------------------------------------
    def runtime(self, server_id: str):
        return next((r for r in self.host.runtimes if r.id == str(server_id or "")), None)

    # --- exécution ----------------------------------------------------------
    def prepare(self, runtime, action_id: str, item_id: str | None = None) -> None:
        """Avant toute action venue de l'utilisateur ou d'un agent."""
        if action_id.startswith("toggle") or action_id in ("start", "stop"):
            runtime.stats["fell"] = False  # l'utilisateur a pris la main : plus d'alerte rouge
        action = find_action(runtime, action_id, item_id)
        if action_id == "stop" or is_stop(action, runtime):
            runtime.expect_stop_until = time.time() + EXPECT_STOP_WINDOW

    def perform(self, runtime, action_id: str, item_id: str | None = None) -> str:
        """Exécute l'action (bloquant) et renvoie la clé de résultat du contrôleur."""
        controller = runtime.controller
        if controller is None:
            return "log.not_running"
        self.prepare(runtime, action_id, item_id)
        runtime.busy = True
        try:
            if item_id:
                key = controller.item_action(item_id, action_id)
            elif action_id == "start":
                key = controller.start()
            elif action_id == "stop":
                key = controller.stop()
            else:
                key = controller.action(action_id)
        except Exception as exc:  # pragma: no cover
            runtime.log_key("log.start_failed", error=str(exc))
            key = "log.start_failed"
        finally:
            runtime.busy = False
        runtime.refresh()
        self.host.request_repaint()
        return str(key or "")

    def run_async(self, runtime, action_id: str, item_id: str | None = None) -> bool:
        """Même chose dans un fil (boutons, tray). False si la tuile est déjà occupée."""
        if runtime.busy:
            return False
        runtime.busy = True  # griser tout de suite, avant que le fil ne démarre

        def work():
            runtime.busy = False
            self.perform(runtime, action_id, item_id)

        threading.Thread(target=work, name=f"action-{runtime.id}", daemon=True).start()
        return True

    # --- boutons sur mesure -------------------------------------------------
    def run_custom(self, runtime, index: int) -> bool:
        """Bouton défini par l'utilisateur : une URL s'ouvre, une commande se lance."""
        try:
            custom = (runtime.server.get("actions") or [])[int(index)]
        except (IndexError, ValueError, TypeError):
            return False
        target = str(custom.get("target") or "").strip()
        label = str(custom.get("label") or "")
        if not target:
            return False
        if target.lower().startswith(("http://", "https://")):
            webbrowser.open(target)
            runtime.log_key("log.custom_url", label=label, target=target)
            return True
        workdir = str((runtime.server.get("params") or {}).get("workdir") or "").strip()
        try:
            subprocess.Popen(target, shell=True, cwd=workdir or None,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as exc:
            runtime.log_key("log.custom_failed", label=label, error=str(exc)[:120])
            return False
        runtime.log_key("log.custom_command", label=label, target=target[:120])
        return True

    @staticmethod
    def url_for(runtime) -> str:
        return str((runtime.snapshot_data or {}).get("url") or
                   (runtime.server.get("params") or {}).get("url") or "")

    # --- tray ---------------------------------------------------------------
    @staticmethod
    def toggle_entries(runtime) -> list[tuple[str, str, bool]]:
        """Entrées marche/arrêt d'une tuile pour le tray (déclarées par le plugin)."""
        result = []
        for action in main_actions(runtime):
            aid = str(action.get("id") or "")
            result.append((i18n.t(str(action.get("label_key") or "btn.start")),
                           f"action:{runtime.id}:{aid}",
                           bool(action.get("enabled", True)) and not runtime.busy))
        return result

    def menu_entries(self, runtime) -> list:
        """Sous-menu tray d'un service : marche/arrêt, éléments, web, copier l'URL, journal."""
        snap = runtime.snapshot_data or {}
        entries: list = list(self.toggle_entries(runtime))
        items = [i for i in snap.get("items") or [] if i.get("id")]
        if items:
            if entries:
                entries.append(("-", None, False))
            for item in items[:12]:
                sub = [(i18n.t(str(a.get("label_key") or a.get("id"))),
                        f"item:{runtime.id}:{item['id']}:{a.get('id')}",
                        bool(a.get("enabled", True)) and not runtime.busy)
                       for a in item.get("actions") or [] if a.get("main") or a.get("tray")]
                marker = {"running": "●", "starting": "◐"}.get(item.get("phase"), "○")
                label = f"{marker} {pres.truncate(str(item.get('label') or item['id']), 32)}"
                entries.append((label, sub or [(i18n.t("tray.no_action"), None, False)], True))
        adjusted = pres.adjust_actions(snap.get("actions") or [], snap)
        web = pres.web_action(adjusted, runtime.server.get("actions") or [],
                              runtime.custom_states)
        copy = next((a for a in adjusted if a.get("id") == "copy_url"), None)
        if entries and (web or copy):
            entries.append(("-", None, False))
        if web:
            entries.append((i18n.t("btn.open_web"), f"action:{runtime.id}:{web['id']}",
                            bool(web.get("enabled", True)) and not runtime.busy))
        if copy:
            entries.append((i18n.t(copy.get("label_key") or "btn.copy_url"),
                            f"action:{runtime.id}:copy_url", bool(copy.get("enabled", True))))
        entries.append(("-", None, False))
        entries.append((i18n.t("tray.show_log"), f"log:{runtime.id}", True))
        return entries
