#!/usr/bin/env python3
"""Background Check — tableau de bord local : un onglet par serveur surveillé.

Point d'entrée : fenêtre, rail de tuiles, sondeur en tâche de fond, icône de notification.
Le reste vit dans le paquet `bgc/` (anciennement `hermesserv/`) (configuration, traductions, plugins, interface).

Usage :
    python background_check.py               # interface
    python background_check.py --shot x.png  # capture de la fenêtre (contrôle visuel)
    python background_check.py --selftest [id]  # cycle démarrage/arrêt d'une tuile (Hermes par défaut)
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime

import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bgc import config as config_mod
from bgc import applog, i18n, instance, mcpserver, service, ui, widgets
from bgc import APP_NAME, migration
from bgc.core.actions import Actions, has_something_to_stop, is_stop
from bgc.core import deps as deps_mod
from bgc.core.metrics import MetricsSampler, item_key
from bgc.core.poller import Poller, transition
from bgc.core.runtime import ServerRuntime
from bgc.ui.views import add as addflow, overview, welcome as onboarding
from bgc import presentation as pres
from bgc import plugins  # noqa: F401  (enregistre les types de serveurs)
from bgc.tray import TrayIcon
from bgc import theme
from bgc.theme import T

#: Nom technique (fenêtre, icône de notification) ; le nom affiché vient de `app.title` (i18n),
#: pour qu'un changement de nom ne touche qu'un endroit.
APP_TITLE = APP_NAME
#: Icône de l'app (exe, fenêtre, zone de notification) — neutre, plus celle de Hermes.
APP_ICON = "bgc.ico"
WINDOW_W, WINDOW_H = 900, 760
# Taille minimale de la fenêtre (elle est redimensionnable) : en dessous, les rangées de
# boutons et les cartes ne tiennent plus.
WINDOW_MIN_W, WINDOW_MIN_H = 860, 560


def _ui_scale(value) -> float:
    """Coerce le réglage « taille de l'interface » (borné 1.0–2.0, jamais 0)."""
    try:
        return min(2.0, max(1.0, round(float(value), 2)))
    except (TypeError, ValueError):
        return 1.3


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def apply_dark_titlebar(window, dark: bool | None = None) -> None:
    """Barre de titre accordée au thème (sombre ou claire) pour une fenêtre Tk.

    Oubliée sur la fenêtre « Sortie console » : une barre blanche au milieu d'une app
    sombre (revue du 11 nov 2026). Depuis le thème clair, la valeur suit la palette.
    """
    dark = T.palette.dark if dark is None else dark
    try:
        import ctypes
        from ctypes import wintypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), attr, ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        applog.tk_hook(self.root)
        self._apply_dpi()

        self.cfg = config_mod.Config().load()
        corrupt_note = self._handle_corrupt_config() if self.cfg.corrupt else None
        if self.cfg.ensure_mcp_token():  # premier lancement depuis la revue du 16 nov 2026
            self.cfg.save()
        i18n.set_language(str(self.cfg.general.get("language") or "fr"))
        self.app_log: deque[tuple[str, str]] = deque(maxlen=300)
        self.events: queue.Queue = queue.Queue()
        self.actions = Actions(self)   # boutons, tray et MCP : une seule implémentation
        self.poller: Poller | None = None
        self.metrics: MetricsSampler | None = None
        self.runtimes: list[ServerRuntime] = [
            ServerRuntime(self, server) for server in self.cfg.servers]
        self.selected_id: str | None = (self.runtimes[0].id if self.runtimes else None)
        # Accueil : la vue d'ensemble ; sans aucune tuile, l'écran de premier lancement.
        self.view = "overview" if self.runtimes else "welcome"
        self.hide_on_close = bool(self.cfg.general.get("hide_on_close", True))
        self.tray = None
        self.mcp: mcpserver.McpServer | None = None
        self._window_hidden = False
        self._job = None
        self._quitting = False
        if corrupt_note:
            self.log_app(*corrupt_note[:1], **corrupt_note[1])

        widgets.UI_SCALE = _ui_scale(self.cfg.general.get("ui_scale"))
        theme.apply(theme.resolve(self.theme_choice))   # avant la construction de la fenêtre
        self._build()
        self.root.deiconify()
        self._dark_titlebar()
        self._set_icon()
        self._start_tray()
        self._intro_log()
        self._watch_windows_theme()
        self._start_poller()
        self._tick()
        self._start_mcp()

    # ── configuration illisible ───────────────────────────────────────────
    def _handle_corrupt_config(self) -> tuple[str, dict]:
        """`config.json` illisible : il a été mis de côté (jamais écrasé). On le dit, et on
        propose la sauvegarde la plus récente. Renvoie l'entrée de journal à écrire."""
        from tkinter import messagebox
        info = self.cfg.corrupt or {}
        i18n.set_language(config_mod.DEFAULT_LANGUAGE)
        applog.LOGGER.error("config illisible mise de côté : %s (%s)",
                            info.get("path"), info.get("error"))
        text = i18n.t("config.corrupt_text", path=info.get("path", "?"),
                      error=info.get("error", "?"))
        backups = info.get("backups") or []
        if not backups:
            messagebox.showwarning(i18n.t("config.corrupt_title"),
                                   text + "\n\n" + i18n.t("config.corrupt_no_backup"))
            return ("log.config_corrupt", {"path": info.get("path", "?")})
        latest = backups[0]
        when = datetime.fromtimestamp(os.path.getmtime(latest)).strftime("%d/%m/%Y %H:%M")
        if messagebox.askyesno(i18n.t("config.corrupt_title"),
                               text + "\n\n" + i18n.t("config.corrupt_restore",
                                                         backup=os.path.basename(latest),
                                                         when=when),
                               icon="warning"):
            try:
                self.cfg = self.cfg.restore_backup(latest)
                return ("log.config_restored", {"backup": latest})
            except Exception as exc:
                applog.LOGGER.exception("restauration impossible")
                return ("log.config_restore_failed", {"error": str(exc)[:160]})
        return ("log.config_corrupt", {"path": info.get("path", "?")})

    # ── fenêtre ────────────────────────────────────────────────────────────
    def _apply_dpi(self) -> None:
        import ctypes
        try:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
            widgets.SC = max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
        except Exception:
            widgets.SC = 1.0

    def _set_icon(self) -> None:
        try:
            ico = resource_path(APP_ICON)
            if os.path.isfile(ico):
                self.root.iconbitmap(default=ico)
        except Exception:
            pass

    def _dark_titlebar(self) -> None:
        apply_dark_titlebar(self.root)

    def _build(self) -> None:
        root = self.root
        root.title(APP_TITLE)
        root.configure(bg=T.BG)
        width, height = widgets.sc(WINDOW_W), widgets.sc(WINDOW_H)
        root.geometry(f"{width}x{height}")
        root.minsize(widgets.sc(WINDOW_MIN_W), widgets.sc(WINDOW_MIN_H))
        root.resizable(True, True)  # redimensionnable, à la demande de l'utilisateur (7 nov 2026)

        header = tk.Frame(root, bg=T.BG)
        header.pack(fill="x", padx=widgets.sc(18), pady=(widgets.sc(12), widgets.sc(4)))
        tk.Label(header, text=i18n.t("app.title"), bg=T.BG, fg=T.TEXT,
                 font=widgets.font(17, True)).pack(side="left")
        tk.Label(header, text=i18n.t("app.subtitle"), bg=T.BG, fg=T.MUTED,
                 font=widgets.font(9)).pack(side="left", padx=(widgets.sc(12), 0),
                                             pady=(widgets.sc(4), 0))
        # Version visible (revue c-19) : utile pour un rapport de bug.
        from bgc import __version__
        tk.Label(header, text=f"v{__version__}", bg=T.BG, fg=T.FAINT,
                 font=widgets.font(8)).pack(side="right", pady=(widgets.sc(4), 0))

        body = tk.Frame(root, bg=T.BG)
        body.pack(fill="both", expand=True)
        rail_frame = tk.Frame(body, bg=T.RAIL,
                              width=widgets.sc(ui.Rail.width_units(self)))
        rail_frame.pack(side="left", fill="y")
        rail_frame.pack_propagate(False)
        self.content = tk.Frame(body, bg=T.BG)
        self.content.pack(side="left", fill="both", expand=True)

        self.rail = ui.Rail(rail_frame, self)
        self.rail.place(x=0, y=0, relwidth=1, relheight=1)
        self._current_view = None
        self._render_view()

        root.protocol("WM_DELETE_WINDOW", self._request_close)
        # Clavier : Échap revient en arrière, Entrée enregistre dans les formulaires.
        root.bind("<Escape>", self._on_escape)
        root.bind("<Return>", self._on_return)
        # Ctrl+K : recherche dans le rail (au-delà de 8 tuiles), sinon la vue d'ensemble.
        root.bind("<Control-k>", self._on_search)
        root.bind("<Control-K>", self._on_search)

    # ── raccourcis clavier ─────────────────────────────────────────────────
    def _on_escape(self, _event=None) -> str:
        if self.view in ("server_settings", "add", "settings"):
            self.close_view()
            return "break"
        return ""

    def _on_search(self, _event=None) -> str:
        if not self.rail.focus_search():
            self.show_overview()
        return "break"

    def _on_return(self, _event=None) -> str:
        """Entrée agit comme le bouton principal du formulaire affiché."""
        view = self._current_view
        if isinstance(view, addflow.AddServerView):
            view.primary()
            return "break"
        if isinstance(view, ui.ServerSettingsView):
            view._save()
            return "break"
        return ""

    # ── vues ───────────────────────────────────────────────────────────────
    def current_runtime(self) -> ServerRuntime | None:
        return next((r for r in self.runtimes if r.id == self.selected_id), None)

    def _render_view(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._current_view = None
        runtime = self.current_runtime()
        if self.view == "overview" and not self.runtimes:
            self.view = "welcome"
        if self.view == "server" and runtime:
            view = ui.ServerView(self.content, self, runtime)
            view.paint()
        elif self.view == "server_settings" and runtime:
            view = ui.ServerSettingsView(self.content, self, runtime)
        elif self.view == "settings":
            view = ui.GeneralSettingsView(self.content, self)
        elif self.view == "add":
            view = addflow.AddServerView(self.content, self)
        elif self.view == "overview":
            view = overview.OverviewView(self.content, self)
            view.paint()
        elif self.view == "welcome":
            view = onboarding.WelcomeView(self.content, self)
        else:
            view = self._empty_view()
        view.pack(fill="both", expand=True)
        self._current_view = view
        self.rail.refresh_states()

    def _empty_view(self) -> tk.Frame:
        frame = tk.Frame(self.content, bg=T.BG)
        box = tk.Frame(frame, bg=T.BG)
        box.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(box, text=i18n.t("empty.title"), bg=T.BG, fg=T.TEXT,
                 font=widgets.font(20, True)).pack()
        tk.Label(box, text=i18n.t("empty.hint"), bg=T.BG, fg=T.MUTED,
                 font=widgets.font(10), wraplength=widgets.sc(420), justify="center").pack(
            pady=(widgets.sc(6), widgets.sc(14)))
        widgets.Button(box, i18n.t("empty.add"), self.show_add, width=240, height=42).pack()
        return frame

    def rebuild_rail(self) -> None:
        self.rail._build()
        self.rail.refresh_states()

    def rebuild_all(self) -> None:
        """Après un changement de langue ou de thème : tout est reconstruit."""
        for child in self.root.winfo_children():
            child.destroy()
        self._build()
        self._dark_titlebar()

    # ── thème ──────────────────────────────────────────────────────────────
    @property
    def theme_choice(self) -> str:
        choice = str(self.cfg.general.get("theme") or "dark")
        return choice if choice in theme.THEME_CHOICES else "dark"

    def theme_options(self) -> list[tuple[str, str]]:
        return [(name, i18n.t(f"theme.{name}")) for name in theme.THEME_CHOICES]

    def set_theme(self, choice: str) -> None:
        """Sombre, clair ou « comme Windows » : enregistré, puis la fenêtre est reconstruite."""
        if choice not in theme.THEME_CHOICES:
            return
        self.cfg.general["theme"] = choice
        self.cfg.save()
        self.log_app("log.theme_changed", theme=i18n.t(f"theme.{choice}"))
        self._apply_theme()

    def _apply_theme(self) -> None:
        wanted = theme.resolve(self.theme_choice)
        if wanted != T.palette.name:
            theme.apply(wanted)
            self.root.after(60, self.rebuild_all)

    def _watch_windows_theme(self) -> None:
        """En mode « comme Windows », suit un changement de thème des applis sans relancer."""
        if self._quitting:
            return
        if self.theme_choice == "system":
            self._apply_theme()
        self.root.after(5000, self._watch_windows_theme)

    def select_server(self, server_id: str) -> None:
        self.selected_id = server_id
        self.view = "server"
        self._render_view()

    def show_general(self) -> None:
        self.view = "settings"
        self._render_view()

    def show_overview(self) -> None:
        self.view = "overview" if self.runtimes else "welcome"
        self._render_view()

    def show_add(self) -> None:
        self.view = "add"
        self._render_view()

    def show_server_settings(self, server_id: str) -> None:
        self.selected_id = server_id
        self.view = "server_settings"
        self._render_view()

    def close_view(self) -> None:
        """Retour : les réglages d'une tuile reviennent à la tuile, le reste à l'accueil."""
        if self.view == "server_settings" and self.current_runtime():
            self.view = "server"
        else:
            self.view = "overview" if self.runtimes else "welcome"
        self._render_view()

    # ── réglages ───────────────────────────────────────────────────────────
    def language_options(self) -> list[tuple[str, str]]:
        return i18n.TRANSLATOR.available

    def set_language(self, code: str) -> None:
        if not i18n.set_language(code):
            return
        self.cfg.general["language"] = code
        self.cfg.save()
        self.log_app("log.language_changed", language=i18n.TRANSLATOR.name(code))
        self.rebuild_all()

    def set_ui_scale(self, value) -> None:
        """Taille de l'interface : tout passe par widgets.sc(), donc ce multiplicateur
        agrandit d'un bloc textes, boutons, cartes et fenêtre."""
        widgets.UI_SCALE = _ui_scale(value)
        self.cfg.general["ui_scale"] = widgets.UI_SCALE
        self.cfg.save()
        self.rebuild_all()

    def set_hide_on_close(self, value: bool) -> None:
        self.hide_on_close = bool(value)
        self.cfg.general["hide_on_close"] = bool(value)
        self.cfg.save()
        key = "log.hide_on_close_on" if value and self.tray else "log.hide_on_close_off"
        self.log_app(key)

    @property
    def rail_compact(self) -> bool:
        return bool(self.cfg.general.get("rail_compact", False))

    def set_rail_compact(self, value: bool) -> None:
        """Rail en icônes seules (le libellé passe en infobulle) — reconstruit la fenêtre."""
        self.cfg.general["rail_compact"] = bool(value)
        self.cfg.save()
        self.root.after(60, self.rebuild_all)

    def set_global_hotkey(self, value: bool) -> None:
        """Ctrl+Alt+H affiche la fenêtre depuis n'importe où (enregistré par l'icône tray)."""
        self.cfg.general["global_hotkey"] = bool(value)
        self.cfg.save()
        if not self.tray:
            self.log_app("log.hotkey_failed")
            return
        self.tray.set_hotkey(bool(value))
        self.root.after(300, lambda: self._report_hotkey(bool(value)))

    def _report_hotkey(self, wanted: bool) -> None:
        if not self.tray:
            return
        if not wanted:
            self.log_app("log.hotkey_off")
        elif self.tray.hotkey_ok:
            self.log_app("log.hotkey_on")
        else:
            self.log_app("log.hotkey_failed")

    def set_mcp_port(self, raw) -> str:
        """Change le port du serveur MCP (à chaud). Renvoie une clé d'erreur, ou ""."""
        try:
            port = int(str(raw).strip())
        except (TypeError, ValueError):
            return "settings.mcp_port_invalid"
        if not 1024 <= port <= 65535:
            return "settings.mcp_port_invalid"
        if port == self.mcp_status()["port"] and self.mcp and self.mcp.running:
            return ""
        self.cfg.general["mcp_port"] = port
        self.cfg.save()
        if self.mcp:
            self.mcp.stop()
            self.mcp = None
        self.log_app("log.mcp_port_changed", port=port)
        if bool(self.cfg.general.get("mcp_enabled", True)):
            self._ensure_mcp()
        self.root.after(80, self.show_general)
        return ""

    def set_notify_on_fall(self, value: bool) -> None:
        self.cfg.general["notify_on_fall"] = bool(value)
        self.cfg.save()
        self.log_app("log.notify_on_fall_on" if value else "log.notify_on_fall_off")

    def set_start_with_windows(self, value: bool) -> None:
        """Écrit (ou retire) la valeur Run du registre — jamais sans ce clic."""
        from bgc import autostart
        if autostart.set_enabled(bool(value)):
            self.log_app("log.autostart_on" if value else "log.autostart_off")
        else:
            self.log_app("log.autostart_failed")
        # La vue est reconstruite : l'interrupteur relit le registre, il ne ment pas.
        self.root.after(60, self.show_general)

    def reload_plugins(self) -> None:
        """Relit le dossier des plugins (sans redémarrer) et adopte les nouveaux types."""
        from bgc import plugin_loader
        results = plugin_loader.load_all()
        loaded = [name for name, error in results if error is None]
        for name, error in results:
            if error:
                self.log_app("log.plugin_failed", name=name, error=error)
        if loaded:
            self.log_app("log.plugins_loaded", names=", ".join(loaded))
        if not results:
            self.log_app("log.plugins_none")
        for runtime in self.runtimes:  # une tuile d'un type arrivé après coup se répare ici
            if runtime.type is None:
                runtime.type = service.get(str(runtime.server.get("type") or ""))
                if runtime.type and runtime.controller is None:
                    runtime.controller = runtime.type.controller(runtime.server, runtime.log_key)
        self.rebuild_all()

    # ── tuiles : ordre, copie, pause ───────────────────────────────────────
    def move_server(self, server_id: str, delta: int) -> None:
        if not self.cfg.move_server(server_id, delta):
            return
        self._apply_order()

    def move_server_to(self, server_id: str, index: int) -> None:
        """Glisser-déposer : la tuile prend la place demandée dans la colonne."""
        if not self.cfg.move_server_to(server_id, index):
            return
        self._apply_order()

    def _apply_order(self) -> None:
        order = {server["id"]: index for index, server in enumerate(self.cfg.servers)}
        self.runtimes.sort(key=lambda item: order.get(item.id, 1 << 30))
        self.rebuild_rail()
        self._render_view()

    def duplicate_server(self, server_id: str) -> None:
        server = self.cfg.get_server(server_id)
        if not server:
            return
        label = i18n.t("dup.label", label=server.get("label", ""))
        copy = self.cfg.duplicate_server(server_id, label)
        if not copy:
            return
        runtime = ServerRuntime(self, copy)
        position = next((index + 1 for index, item in enumerate(self.runtimes)
                         if item.id == server_id), len(self.runtimes))
        self.runtimes.insert(position, runtime)
        self.selected_id = runtime.id
        self.view = "server"
        self.log_app("log.server_duplicated", label=copy["label"])
        runtime.refresh()
        self.rebuild_rail()
        self._render_view()

    def set_server_enabled(self, server_id: str, value: bool) -> None:
        runtime = next((item for item in self.runtimes if item.id == server_id), None)
        if not runtime:
            return
        self.cfg.update_server(server_id, enabled=bool(value))
        runtime.server["enabled"] = bool(value)
        self.log_app("log.server_resumed" if value else "log.server_paused",
                     label=runtime.label)
        runtime.refresh()  # bascule immédiate : « en pause » ou re-sondage
        self.rebuild_rail()
        self._render_view()

    # ── tuile importée ─────────────────────────────────────────────────────
    def import_tile(self, path: str) -> None:
        from bgc import tiles
        try:
            payload = tiles.read(path)
        except ValueError as exc:
            self.log_app("log.tile_import_failed", error=i18n.t(str(exc)))
            return
        except (OSError, json.JSONDecodeError) as exc:
            self.log_app("log.tile_import_failed", error=str(exc)[:160])
            return
        server_type = service.get(payload["type"])
        if not server_type:
            self.log_app("log.tile_import_failed",
                         error=i18n.t("tile.error.unknown_type", type=payload["type"]))
            return
        try:
            params = server_type.validate(payload["params"])
        except ValueError as exc:
            self.log_app("log.tile_import_failed", error=i18n.t(str(exc)))
            return
        # Revue du 16 nov 2026 : une tuile reçue peut exécuter des commandes. On montre
        # d'abord ce qu'elle lancera, et le démarrage automatique est toujours coupé.
        if not self._confirm_tile_import(payload, server_type):
            self.log_app("log.tile_import_cancelled", label=payload["label"])
            return
        options = dict(server_type.default_options())
        options.update(payload["options"])
        options, disabled = tiles.safe_options(options)
        server = self.cfg.add_server(payload["type"], payload["label"], params, options,
                                     actions=payload.get("actions"))
        if disabled:
            self.log_app("log.tile_autostart_disabled", label=payload["label"])
        if payload.get("deps"):
            self.cfg.update_server(server["id"], deps=payload["deps"])
        icon = tiles.icon_if_exists(payload["icon"])
        if icon:
            self.cfg.update_server(server["id"], icon=icon)
            server["icon"] = icon
        runtime = ServerRuntime(self, server)
        self.runtimes.append(runtime)
        self.selected_id = runtime.id
        self.view = "server"
        self.log_app("log.tile_imported", label=server["label"])
        runtime.refresh()
        self.rebuild_rail()
        self._render_view()

    def _confirm_tile_import(self, payload: dict, server_type) -> bool:
        """Fenêtre de confirmation : ce que la tuile exécutera ou ouvrira, avant tout ajout."""
        from bgc import tiles
        from tkinter import messagebox
        toggle_names = {t.key: i18n.t(t.label_key) for t in (server_type.toggles or [])}
        lines = []
        for key, params in tiles.preview(payload, server_type.fields):
            params = dict(params)
            if key == "tile.preview_param":
                params["name"] = i18n.t(params["name"])
            elif key == "tile.preview_type":
                params["type"] = i18n.t(server_type.label_key)
            elif key == "tile.preview_autostart_off":
                params["options"] = ", ".join(toggle_names.get(k, k)
                                              for k in params["options"].split(", "))
            lines.append("• " + i18n.t(key, **params))
        message = i18n.t("tile.preview_intro") + "\n\n" + "\n".join(lines)
        return bool(messagebox.askokcancel(i18n.t("tile.preview_title"), message,
                                           icon="warning", default="cancel",
                                           parent=self.root))

    def create_server(self, type_id: str, label: str, params: dict, icon: str = "",
                      actions: list | None = None, options: dict | None = None,
                      enabled: bool = True, select: bool = True) -> dict:
        server_type = service.get(type_id)
        opts = server_type.default_options() if server_type else {}
        if options:
            opts.update({key: value for key, value in options.items() if key in opts})
        server = self.cfg.add_server(type_id, label, params, opts, actions=actions,
                                     enabled=enabled)
        if icon:
            self.cfg.update_server(server["id"], icon=icon)
            server["icon"] = icon
        runtime = ServerRuntime(self, server)
        self.runtimes.append(runtime)
        if select:
            self.selected_id = runtime.id
            self.view = "server"
        self.log_app("log.server_added", label=server["label"])
        runtime.refresh()
        self.rebuild_rail()
        if select:
            self._render_view()
        return server

    def apply_server_update(self, server_id: str, label: str | None = None,
                            params: dict | None = None, options: dict | None = None,
                            icon: str | None = None, actions: list | None = None,
                            enabled: bool | None = None,
                            deps: str | None = None) -> dict | None:
        """Applique une modification venue du serveur MCP (ou d'ailleurs) sur le fil Tk.

        Le formulaire de réglages n'est PAS reconstruit : si l'utilisateur est en train de
        l'éditer, ses valeurs non enregistrées restent à l'écran. Le rail et le panneau suivent.
        """
        runtime = next((item for item in self.runtimes if item.id == server_id), None)
        server = self.cfg.get_server(server_id)
        if runtime is None or server is None:
            return None
        changes: dict = {}
        if label is not None:
            server["label"] = label
            changes["label"] = label
        if params is not None:
            server["params"] = dict(params)
            if runtime.controller:
                runtime.controller.params = dict(params)
            changes["params"] = dict(params)
        if options is not None:
            merged = dict(server.get("options") or {})
            merged.update(options)
            server["options"] = merged
            if runtime.controller:
                runtime.controller.options = dict(merged)
            changes["options"] = merged
        if icon is not None:
            server["icon"] = icon
            changes["icon"] = icon
        if actions is not None:
            normalized = self.cfg._normalise_actions(actions)
            server["actions"] = normalized
            changes["actions"] = normalized
        if enabled is not None:
            server["enabled"] = bool(enabled)
            changes["enabled"] = bool(enabled)
        if deps is not None:
            server["deps"] = str(deps)[:1000]
            changes["deps"] = server["deps"]
        self.cfg.update_server(server_id, **changes)
        runtime.refresh()
        self.rebuild_rail()
        if self.view == "server" and self.selected_id == server_id:
            self._render_view()
        return server

    def delete_server(self, server_id: str) -> None:
        runtime = next((r for r in self.runtimes if r.id == server_id), None)
        label = runtime.label if runtime else server_id
        if runtime and runtime.controller:
            runtime.controller.dispose()
        self.cfg.remove_server(server_id)
        self.runtimes = [r for r in self.runtimes if r.id != server_id]
        self.selected_id = self.runtimes[0].id if self.runtimes else None
        self.view = "overview" if self.runtimes else "welcome"
        self.log_app("log.server_deleted", label=label)
        self.rebuild_rail()
        self._render_view()

    # ── journal applicatif ─────────────────────────────────────────────────
    def log_app(self, key: str, **params) -> None:
        self.app_log.append((datetime.now().strftime("%H:%M:%S"), key, params))
        try:  # copie dans le fichier journal (texte traduit, lisible sans l'app)
            applog.LOGGER.info("[app] %s", i18n.t(key, **params))
        except Exception:
            pass

    def _intro_log(self) -> None:
        self.log_app("log.app_ready")
        if migration.MIGRATED_FROM:
            self.log_app("log.data_migrated", old=migration.MIGRATED_FROM, new=self.cfg.path)
        if AUTOSTART_MIGRATED:
            self.log_app("log.autostart_migrated")
        if self.cfg.migrated:
            self.log_app("log.config_migrated")
        if self.cfg.created:
            self.log_app("log.config_created", path=self.cfg.path)
        for name, error in getattr(plugins, "LOAD_ERRORS", []):
            self.log_app("log.plugin_failed", name=name, error=error)

    # ── actions ------------------------------------------------------------
    def request_repaint(self) -> None:
        """Appelable depuis n'importe quel fil (core.actions, MCP) : repeindre au prochain tick."""
        self.events.put(("repaint", None))

    def run_action(self, runtime: ServerRuntime, action_id: str,
                   item_id: str | None = None) -> None:
        """Clic sur un bouton (ou entrée du tray) : ce qui touche Tk ici, le reste dans core."""
        if runtime.busy:
            return
        if action_id == "copy_url" and not item_id:
            url = self.actions.url_for(runtime)
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            runtime.log_key("log.url_copied", url=url)
            return
        if action_id == "output" and not item_id:
            self.show_log(runtime, "console")
            return
        if action_id.startswith("custom:"):
            self.run_custom_action(runtime, action_id)
            return
        if self.actions.run_async(runtime, action_id, item_id):
            # Griser les boutons SANS reconstruire la vue : un simple clic d'action ne doit
            # pas effacer le filtre du journal ni sa position de défilement.
            self._paint()

    def run_custom_action(self, runtime: ServerRuntime, action_id: str) -> None:
        """Bouton défini par l'utilisateur : une URL s'ouvre, une commande se lance."""
        try:
            index = int(action_id.split(":", 1)[1])
        except (IndexError, ValueError):
            return
        self.actions.run_custom(runtime, index)

    # ── journal / sortie console d'une tuile ───────────────────────────────
    def show_log(self, runtime: ServerRuntime, tab: str = "events") -> None:
        """Ouvre la tuile et déplie son journal sur l'onglet voulu (« console » ou non).

        Remplace la fenêtre séparée « Sortie console » : la sortie est un onglet du journal
        (refonte UX du 16 nov 2026, revue b-6).
        """
        if not (self.view == "server" and self.selected_id == runtime.id):
            self.selected_id = runtime.id
            self.view = "server"
            self._render_view()
        view = self._current_view
        if isinstance(view, ui.ServerView):
            view.show_log(tab)

    def _note_transition(self, runtime: ServerRuntime, before: str | None, after: str) -> None:
        """Petit historique par tuile + alerte quand un service en marche tombe tout seul."""
        if transition(runtime, before, after) != "fall":
            return
        self.log_app("log.fall_detected", label=runtime.label)
        if self.cfg.general.get("notify_on_fall", True):
            self.events.put(("balloon", (i18n.t("tray.fall_title"),
                                         i18n.t("tray.fall_text", label=runtime.label))))

    # ── sondeur ────────────────────────────────────────────────────────────
    def _start_poller(self) -> None:
        """Sondage en tâche de fond (voir `bgc/core/poller.py`)."""
        def probed(runtime: ServerRuntime, before: str | None) -> None:
            if runtime.enabled:
                self._note_transition(runtime, before, runtime.phase)

        self.poller = Poller(lambda: self.runtimes, probed).start()
        self.metrics = MetricsSampler(self._metric_targets).start()

    def _metric_targets(self) -> list[tuple[str, list[int]]]:
        """Fil des mesures : les PID que les sondes ont trouvés (tuile, puis éléments)."""
        targets = []
        for runtime in list(self.runtimes):
            snapshot = runtime.snapshot_data
            if not runtime.enabled or not snapshot:
                continue
            items = pres.items_of(snapshot, runtime.label)
            tile = pres.snapshot_pids(snapshot)
            if not tile:
                tile = [pid for item in items for pid in item["pids"]]
            deps = [d for d in snapshot.get("deps") or [] if d.get("pids")]
            dep_pids = [pid for d in deps for pid in d["pids"]]
            targets.append((runtime.id, tile, dep_pids))
            # …et réciproquement : un serveur lié parent de la tuile ne compte pas la tuile
            targets.extend((item_key(runtime.id, deps_mod.dep_key(d["id"])), d["pids"], tile)
                           for d in deps)
            if pres.has_items(snapshot):
                targets.extend((item_key(runtime.id, item["id"]), item["pids"])
                               for item in items if item["pids"])
        return targets

    def metrics_of(self, runtime, item_id: str = "") -> dict | None:
        if self.metrics is None:
            return None
        return self.metrics.latest(item_key(runtime.id, item_id) if item_id else runtime.id)

    # ── boucle d'affichage ─────────────────────────────────────────────────
    def _tick(self) -> None:
        self._drain()
        # « Quitter » du tray est traité dans _drain() : la fenêtre peut déjà être détruite,
        # il ne faut alors ni repeindre ni se reprogrammer (sinon « invalid command name »).
        if self._quitting:
            return
        self._paint()
        self._job = self.root.after(300, self._tick)

    def _drain(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "balloon":
                if self.tray and payload:
                    self.tray.balloon(payload[0], payload[1], warning=True)
                continue
            if kind == "ui":
                # Appel synchrone d'un fil externe (serveur MCP) sur le fil Tk.
                fn, done, holder = payload
                with holder["lock"]:
                    if holder["state"] == "cancelled":
                        continue  # l'appelant a abandonné : ne PAS exécuter plus tard
                    holder["state"] = "running"
                try:
                    holder["value"] = fn()
                except Exception as exc:
                    holder["error"] = exc
                finally:
                    done.set()
                continue
            if kind == "repaint":
                continue
            if isinstance(payload, str) and payload.startswith(("toggle:", "action:", "log:",
                                                                "item:")):
                self._handle_tray_payload(payload)
            elif payload in ("show", "hide", "quit", "settings", "toggle") or payload is None:
                self._handle_tray_payload(payload)

    # ── pont MCP (fils externes → fil Tk) ──────────────────────────────────
    def ui_call(self, fn, timeout: float = 20.0):
        """Exécute `fn` sur le fil Tk et renvoie son résultat (ou lève son exception).

        Appelé depuis le fil du serveur MCP : Tk n'est jamais touché hors du fil principal.
        """
        if threading.current_thread() is threading.main_thread():
            return fn()
        done = threading.Event()
        holder: dict = {"lock": threading.Lock(), "state": "queued"}
        self.events.put(("ui", (fn, done, holder)))
        if not done.wait(timeout):
            # Revue du 16 nov 2026 : avant, le travail restait en file et s'exécutait plus
            # tard alors que l'agent avait reçu une erreur (et recommençait : doublon).
            with holder["lock"]:
                if holder["state"] == "queued":
                    holder["state"] = "cancelled"
                    raise TimeoutError("l'interface n'a pas répondu : rien n'a été fait")
            # Déjà en cours d'exécution : on attend sa fin plutôt que de mentir.
            if not done.wait(timeout):
                raise TimeoutError("l'interface est toujours occupée par cette opération")
        if "error" in holder:
            raise holder["error"]
        return holder.get("value")

    def _start_mcp(self) -> None:
        """Démarre le serveur MCP si le réglage général le demande."""
        if not bool(self.cfg.general.get("mcp_enabled", True)):
            return
        self._ensure_mcp()

    def _ensure_mcp(self) -> bool:
        if self.mcp is None:
            try:
                port = int(self.cfg.general.get("mcp_port") or mcpserver.DEFAULT_PORT)
            except (TypeError, ValueError):
                port = mcpserver.DEFAULT_PORT
            self.mcp = mcpserver.McpServer(self, port=port, token=self.mcp_token)
        if self.mcp.running:
            return True
        ok = self.mcp.start()
        if ok:
            self.log_app("log.mcp_server_listening", url=self.mcp.url)
        else:
            self.log_app("log.mcp_server_failed", error=self.mcp.error or "?")
        return ok

    @property
    def mcp_token(self) -> str:
        return str(self.cfg.general.get("mcp_token") or "")

    def regenerate_mcp_token(self) -> None:
        """Nouveau jeton : l'ancien cesse de fonctionner immédiatement (serveur à chaud)."""
        self.cfg.general["mcp_token"] = config_mod.new_mcp_token()
        self.cfg.save()
        if self.mcp:
            self.mcp.token = self.mcp_token
        self.log_app("log.mcp_token_regenerated")
        self.root.after(80, self.show_general)

    def mcp_hermes_snippet(self) -> str:
        """Bloc YAML à coller sous `mcp_servers:` dans la config de Hermes."""
        url = self.mcp_status()["url"]
        return ("bgc:\n"
                f"  url: {url}\n"
                "  headers:\n"
                f"    Authorization: Bearer {self.mcp_token}\n")

    def mcp_status(self) -> dict:
        """État lisible par la vue Réglages (sans sonder : le serveur sait s'il tourne)."""
        enabled = bool(self.cfg.general.get("mcp_enabled", True))
        if self.mcp:
            port = self.mcp.port
        else:
            try:
                port = int(self.cfg.general.get("mcp_port") or mcpserver.DEFAULT_PORT)
            except (TypeError, ValueError):
                port = mcpserver.DEFAULT_PORT
        if self.mcp and self.mcp.running:
            return {"state": "running", "url": self.mcp.url, "port": port, "error": ""}
        return {"state": "error" if enabled and self.mcp and self.mcp.error else "stopped",
                "url": f"http://127.0.0.1:{port}/mcp", "port": port,
                "error": self.mcp.error if self.mcp else ""}

    def set_mcp_enabled(self, value: bool) -> None:
        self.cfg.general["mcp_enabled"] = bool(value)
        self.cfg.save()
        if value:
            self._ensure_mcp()
        elif self.mcp:
            self.mcp.stop()
            self.log_app("log.mcp_server_stopped")
        # La vue est reconstruite : l'état affiché vient du serveur, pas de l'intention.
        self.root.after(80, self.show_general)

    def _handle_tray_payload(self, payload) -> None:
        if payload == "show":
            self.show_window()
        elif payload == "hide":
            self._hide_to_tray()
        elif payload == "settings":
            self.show_general()
            self.show_window()
        elif isinstance(payload, str) and payload.startswith("action:"):
            parts = payload.split(":", 2)
            if len(parts) == 3:
                _, server_id, action_id = parts
                runtime = next((item for item in self.runtimes if item.id == server_id), None)
                if runtime:
                    self.run_action(runtime, action_id)
        elif isinstance(payload, str) and payload.startswith("item:"):
            parts = payload.split(":", 3)   # item:<tuile>:<élément>:<action>
            if len(parts) == 4:
                runtime = self.actions.runtime(parts[1])
                if runtime:
                    self.run_action(runtime, parts[3], item_id=parts[2])
        elif isinstance(payload, str) and payload.startswith("log:"):
            runtime = next((item for item in self.runtimes
                            if item.id == payload.split(":", 1)[1]), None)
            if runtime:
                self.show_window()
                self.show_log(runtime, "events")
        elif isinstance(payload, str) and payload.startswith("toggle:"):
            server_id = payload.split(":", 1)[1]
            runtime = next((item for item in self.runtimes if item.id == server_id), None)
            if runtime:
                self.run_action(runtime, "toggle")
        elif payload == "toggle":
            runtime = self.current_runtime()
            if runtime:
                self.run_action(runtime, "toggle")
        elif payload == "quit":
            self.log_app("log.quit_tray")
            self.quit()

    def _paint(self) -> None:
        if self._quitting:
            return
        try:
            self.rail.refresh_states()
            if isinstance(self._current_view, (ui.ServerView, overview.OverviewView)):
                self._current_view.paint()
            self._update_tray()
        except Exception as exc:  # pragma: no cover
            self.log_app("log.state_read_error", error=str(exc))

    # ── tray ───────────────────────────────────────────────────────────────
    def _start_tray(self) -> None:
        try:
            ico = resource_path(APP_ICON)
            if not os.path.isfile(ico):
                return
            candidate = TrayIcon(ico, APP_TITLE, self._tray_action, self._tray_menu,
                                 window_title=instance.tray_title(
                                     instance.key_for(self.cfg.path)))
            candidate.set_hotkey(bool(self.cfg.general.get("global_hotkey", False)))
            if candidate.start():
                self.tray = candidate
                self.log_app("log.tray_ready")
            else:
                self.log_app("log.tray_unavailable", error=candidate.error or "?")
        except Exception as exc:  # pragma: no cover
            self.log_app("log.tray_unavailable", error=str(exc))

    def _tray_action(self, action: str) -> None:
        self.events.put(("tray", action))

    def _tray_service_entries(self, runtime: ServerRuntime) -> list:
        """Sous-menu d'un service (construit par `core.actions`, partagé avec le MCP)."""
        return self.actions.menu_entries(runtime)

    def _tray_label(self, runtime: ServerRuntime) -> str:
        """« ● Hermes — En marche » : l'état lisible sans ouvrir la fenêtre."""
        marker = {"ok": "●", "warn": "◐", "bad": "✕"}.get(runtime.state, "○")
        return f"{marker} {pres.truncate(runtime.label, 32)} — {runtime.status_text()}"

    def _tray_menu(self) -> list:
        items: list = [
            (i18n.t("tray.show") if self._window_hidden else i18n.t("tray.hide"),
             "show" if self._window_hidden else "hide", True)]
        # Un sous-menu par service actif, sans plafond muet : au-delà de 10, « Autres (n) ».
        services = [(self._tray_label(runtime), self._tray_service_entries(runtime))
                    for runtime in self.runtimes if runtime.enabled]
        if services:
            items.append(("-", None, False))
            items.extend(pres.tray_service_menu(services, i18n.t("tray.more")))
        items.append(("-", None, False))
        items.append((i18n.t("btn.settings"), "settings", True))
        items.append((i18n.t("tray.quit"), "quit", True))
        return items

    def _update_tray(self) -> None:
        """Infobulle = résumé global des tuiles actives.

        Elle nommait le seul serveur « sélectionné » dans une fenêtre le plus souvent
        cachée : depuis la zone de notification, cette information ne veut rien dire
        (revue du 11 nov 2026).
        """
        if not self.tray:
            return
        active = [runtime for runtime in self.runtimes if runtime.enabled]
        if not active:
            self.tray.set_tooltip(APP_NAME)
            return
        running = sum(1 for runtime in active if runtime.phase == "running")
        self.tray.set_tooltip(i18n.t("tray.summary", running=running,
                                     stopped=len(active) - running))

    # ── fermeture ----------------------------------------------------------
    def _request_close(self) -> None:
        # `alive` et pas seulement `self.tray` : cacher la fenêtre dans un tray disparu
        # laissait une app invisible et impossible à fermer (bug du 16 nov 2026).
        if self.hide_on_close and self.tray and self.tray.alive:
            self._hide_to_tray()
        else:
            self.quit()

    def _hide_to_tray(self) -> None:
        if self._window_hidden:
            return
        self.root.withdraw()
        self._window_hidden = True
        self.log_app("log.hide")

    def show_window(self) -> None:
        self.root.deiconify()
        try:
            self.root.state("normal")
        except Exception:
            pass
        self.root.lift()
        self.root.focus_force()
        self._window_hidden = False

    #: Délai maximal accordé aux arrêts « à la fermeture » avant de quitter quand même.
    QUIT_DEADLINE = 25.0

    def quit(self) -> None:
        """Ferme l'app sans figer la fenêtre (revue du 16 nov 2026).

        Avant, chaque arrêt « à la fermeture » tournait sur le fil Tk, l'un après l'autre
        (jusqu'à 15 s chacun) : fenêtre figée, puis « l'application ne répond pas ». Ici la
        fenêtre disparaît tout de suite, les arrêts tournent en parallèle dans des fils, et
        la boucle Tk attend leur fin (avec un plafond) avant de détruire la fenêtre.
        """
        if self._quitting:
            return
        self._quitting = True
        if self.poller is not None:
            self.poller.stop()
        if self.metrics is not None:
            self.metrics.stop()
        if self.mcp:
            self.mcp.stop()
            self.mcp = None
        if self.tray:
            self.tray.stop()
            self.tray = None
        try:
            self.root.withdraw()
        except Exception:
            pass
        workers = []
        for runtime in list(self.runtimes):
            if (runtime.server.get("options", {}).get("stop_on_quit") and runtime.controller
                    and has_something_to_stop(runtime)):
                # Journalisé seulement s'il y a vraiment quelque chose à arrêter (avant : à
                # chaque fermeture, même tout éteint).
                self.log_app("log.stop_before_quit")

                def stop(controller=runtime.controller, label=runtime.label):
                    try:
                        controller.stop()
                    except Exception:
                        applog.LOGGER.exception("arrêt à la fermeture impossible : %s", label)

                worker = threading.Thread(target=stop, name=f"quit-{runtime.id}", daemon=True)
                worker.start()
                workers.append(worker)
        deadline = time.time() + self.QUIT_DEADLINE

        def finish():
            if any(w.is_alive() for w in workers) and time.time() < deadline:
                self.root.after(100, finish)
                return
            for runtime in list(self.runtimes):
                if runtime.controller:
                    try:
                        runtime.controller.dispose()
                    except Exception:
                        applog.LOGGER.exception("dispose : %s", runtime.id)
            if self._job:
                try:
                    self.root.after_cancel(self._job)
                except Exception:
                    pass
            # Rappels Tk encore programmés (animations, survols) : annulés avant destroy(),
            # sinon ils tombent sur des widgets détruits (« invalid command name » au journal).
            try:
                for pending in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                    self.root.after_cancel(pending)
            except Exception:
                pass
            applog.LOGGER.info("Background Check fermé")
            self.root.destroy()

        finish()

    # ── boucle ─────────────────────────────────────────────────────────────
    def run(self) -> None:
        self.root.mainloop()

    def capture(self, path: str, delay_ms: int = 6500) -> None:
        def shot():
            try:
                from PIL import ImageGrab
                self.root.lift()
                self.root.update()
                for _ in range(6):
                    self.root.update()
                    time.sleep(0.05)
                # Capture de la FENÊTRE (PrintWindow via Pillow) : elle fonctionne même si
                # une autre fenêtre la recouvre, contrairement à une capture d'écran.
                import ctypes
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                img = ImageGrab.grab(window=hwnd)
                img.save(path)
                print(f"capture -> {path} {img.size}")
            except Exception as exc:
                print(f"capture impossible : {exc}")
            self.quit()

        self.root.after(delay_ms, shot)


# ── tests en ligne de commande ────────────────────────────────────────────────
def selftest_target(servers: list[dict], wanted: str | None = None) -> dict | None:
    """Tuile visée par --selftest : l'identifiant donné, sinon la première tuile Hermes
    (comportement historique), sinon la première tuile d'un type qui sait démarrer/arrêter."""
    if wanted:
        return next((s for s in servers if s.get("id") == wanted), None)
    hermes = next((s for s in servers if s.get("type") == "hermes"), None)
    if hermes:
        return hermes
    return next((s for s in servers if service.get(str(s.get("type")))
                 and service.get(str(s.get("type"))).supports_start_stop), None)


def selftest(argv: list[str] | None = None) -> int:
    """`--selftest [id]` : cycle réel démarrer / vérifier / arrêter d'une tuile.

    Générique (16 nov 2026) : plus de lecture de `remote_phase` propre à Hermes ; le garde
    porte sur la tuile **et** ses éléments (presentation.anything_running).
    """
    argv = list(sys.argv if argv is None else argv)
    index = argv.index("--selftest") if "--selftest" in argv else -1
    wanted = argv[index + 1] if 0 <= index < len(argv) - 1 and not argv[index + 1].startswith("-") \
        else None
    cfg = config_mod.Config().load()
    i18n.set_language(str(cfg.general.get("language") or "fr"))
    server = selftest_target(cfg.servers, wanted)
    if not server:
        print(f"aucune tuile « {wanted} »" if wanted else "aucune tuile pilotable — rien à tester")
        return 1
    server_type = service.get(str(server.get("type")))
    if server_type is None or not server_type.supports_start_stop:
        print(f"la tuile {server.get('id')} ne sait pas démarrer/arrêter — rien à tester")
        return 1
    logs: list[str] = []
    controller = server_type.controller(server, lambda key, **kw: logs.append(i18n.t(key, **kw)))

    def show(snap: dict) -> None:
        print(f"   phase={snap.get('phase')}  pid={(snap.get('meta') or {}).get('pid')}")
        for row in (snap.get("rows") or [])[:6]:
            print(f"   · {i18n.t(row.get('label_key', ''), **(row.get('label_params') or {}))} : "
                  f"{i18n.t(row.get('value_key', ''), **(row.get('value_params') or {}))}")
        for item in (snap.get("items") or [])[:8]:
            print(f"   - {item.get('label')} : {item.get('phase')}")

    print(f"tuile : {server.get('label')} ({server.get('id')}, type {server.get('type')})")
    print("1) état initial")
    snapshot = controller.snapshot()
    show(snapshot)
    # Garde dans le code, et pas seulement dans NOTES.md : ce cycle adopte un listener
    # existant puis le teste en l'arrêtant — il couperait le service en cours d'utilisation.
    if pres.anything_running(snapshot) or any(
            is_stop(a) for a in pres.primary_actions(snapshot.get("actions") or [])):
        print("   refus : cette tuile (ou l'un de ses éléments) est déjà en marche — ce test")
        print("   l'arrêterait. Arrête-la d'abord.")
        return 2
    if hasattr(controller, "adopt"):
        controller.adopt()
    print("2) démarrage")
    started = time.time()
    result = controller.start()
    # La ligne de journal porte les paramètres (pid, commande) ; la clé seule afficherait « {pid} ».
    print(f"   -> {logs[-1] if logs else i18n.t(result)}  ({time.time() - started:.1f} s)")
    show(controller.snapshot())
    print("3) arrêt")
    before = len(logs)
    result = controller.stop()
    print(f"   -> {logs[-1] if len(logs) > before else i18n.t(result)}")
    print("4) journal")
    for line in logs[-8:]:
        print(f"   · {line}")
    ok = controller.snapshot()["phase"] == "stopped"
    print(f"\nRÉSULTAT : {'OK' if ok else 'KO'}")
    return 0 if ok else 1


AUTOSTART_MIGRATED = False


def main() -> int:
    global AUTOSTART_MIGRATED
    # Renommage HermesServ → Background Check : reprendre les données AVANT d'ouvrir le
    # journal (il vit dans le dossier de données) ; l'ancien dossier reste intact.
    migration.migrate_data_dir()
    applog.setup()
    if not config_mod.TEST_CONFIG:
        from bgc import autostart
        AUTOSTART_MIGRATED = autostart.migrate()
    from bgc import __version__
    applog.LOGGER.info("Background Check %s démarre (%s)", __version__, sys.executable)
    if "--selftest" in sys.argv:
        return selftest(sys.argv)
    if "--shot" in sys.argv:
        path = sys.argv[sys.argv.index("--shot") + 1]
        app = App()
        app.capture(path)
        app.run()
        return 0
    # Une seule instance par fichier de configuration : la seconde réveille la première.
    key = instance.key_for(config_mod.CONFIG_PATH)
    if not instance.acquire(key):
        shown = instance.signal_existing(key)
        applog.LOGGER.info("instance déjà ouverte : %s", "fenêtre ramenée" if shown
                           else "fenêtre introuvable")
        return 0
    try:
        App().run()
    except Exception:
        applog.LOGGER.critical("plantage de l'application", exc_info=True)
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
