"""Vue Réglages généraux."""

from __future__ import annotations

import os
import tkinter as tk

from bgc import applog, autostart, i18n, plugin_loader
from bgc.widgets import (Dropdown, ScrollFrame, Toggle, draw_badge, font, hint_for, label_with_info, sc)
from bgc.theme import T
from bgc.ui.common import ghost_button
from bgc.ui.logpanel import LogPanel


# ── réglages : général ────────────────────────────────────────────────────────
class GeneralSettingsView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self._build()

    def _build(self) -> None:
        scroll = ScrollFrame(self, bg=T.BG)
        scroll.pack(fill="both", expand=True, padx=sc(18), pady=(sc(14), sc(10)))
        body = scroll.inner
        self.body = body

        tk.Label(body, text=i18n.t("settings.title"), bg=T.BG, fg=T.TEXT, font=font(20, True),
                 anchor="w").pack(fill="x")
        tk.Label(body, text=i18n.t("settings.general"), bg=T.BG, fg=T.MUTED, font=font(10),
                 anchor="w").pack(fill="x", pady=(0, sc(8)))

        # ── Général : listes déroulantes et interrupteurs, en grille dense (revue b-10) ──
        self._section("settings.section_general")
        grid = tk.Frame(body, bg=T.BG)
        grid.pack(fill="x", pady=(0, sc(6)))
        grid.columnconfigure(1, weight=1)
        label_with_info(grid, i18n.t("settings.language"), i18n.t("settings.language_hint")
                        ).grid(row=0, column=0, sticky="w", padx=(0, sc(14)), pady=sc(3))
        Dropdown(grid, self.app.language_options(), value=i18n.TRANSLATOR.language,
                 command=self.app.set_language, width=220).grid(row=0, column=1, sticky="w",
                                                                pady=sc(3))
        label_with_info(grid, i18n.t("settings.ui_scale"), i18n.t("settings.ui_scale_hint")
                        ).grid(row=1, column=0, sticky="w", padx=(0, sc(14)), pady=sc(3))
        Dropdown(grid, [(1.0, "100 %"), (1.15, "115 %"), (1.3, "130 %"), (1.5, "150 %")],
                 value=round(float(self.app.cfg.general.get("ui_scale") or 1.3), 2),
                 command=self.app.set_ui_scale, width=220).grid(row=1, column=1, sticky="w",
                                                                pady=sc(3))
        label_with_info(grid, i18n.t("settings.theme"), i18n.t("settings.theme_hint")
                        ).grid(row=2, column=0, sticky="w", padx=(0, sc(14)), pady=sc(3))
        Dropdown(grid, self.app.theme_options(), value=self.app.theme_choice,
                 command=self.app.set_theme, width=220).grid(row=2, column=1, sticky="w",
                                                             pady=sc(3))
        toggles = [
            ("toggle.start_with_windows", autostart.is_enabled(),
             self.app.set_start_with_windows, i18n.t("settings.startup_hint")),
            ("toggle.hide_on_close", bool(self.app.cfg.general.get("hide_on_close", True)),
             self.app.set_hide_on_close, ""),
            ("toggle.notify_on_fall", bool(self.app.cfg.general.get("notify_on_fall", True)),
             self.app.set_notify_on_fall, ""),
            ("toggle.rail_compact", self.app.rail_compact, self.app.set_rail_compact,
             hint_for("toggle.rail_compact")),
            ("toggle.global_hotkey", bool(self.app.cfg.general.get("global_hotkey", False)),
             self.app.set_global_hotkey, hint_for("toggle.global_hotkey")),
        ]
        for key, value, command, hint in toggles:
            Toggle(body, i18n.t(key), value, command=command, hint=hint).pack(
                fill="x", pady=(sc(2), sc(4)))

        # ── MCP ──
        self._section("settings.mcp", hint=i18n.t("settings.mcp_hint"))
        Toggle(body, i18n.t("toggle.mcp_enabled"),
               bool(self.app.cfg.general.get("mcp_enabled", True)),
               command=self.app.set_mcp_enabled).pack(fill="x", pady=(sc(2), sc(6)))
        status = self.app.mcp_status()
        status_line = tk.Frame(body, bg=T.BG)
        status_line.pack(fill="x", pady=(0, sc(6)))
        dot = tk.Canvas(status_line, width=sc(14), height=sc(14), bg=T.BG,
                        highlightthickness=0)
        dot.pack(side="left", padx=(0, sc(6)))
        state_colour = {"running": T.GREEN, "error": T.RED}.get(status["state"], T.FAINT)
        dot.create_oval(sc(3), sc(3), sc(11), sc(11), fill=state_colour, outline="")
        if status["state"] == "running":
            text = i18n.t("mcp.status_running", url=status["url"])
        elif status["state"] == "error":
            text = i18n.t("mcp.status_error", error=status["error"])
        else:
            text = i18n.t("mcp.status_stopped", url=status["url"])
        tk.Label(status_line, text=text, bg=T.BG, fg=state_colour, font=font(9), anchor="w",
                 wraplength=sc(560), justify="left").pack(side="left", fill="x", expand=True)
        # Port modifiable (revue b-10) : appliqué à chaud, le serveur redémarre sur le nouveau.
        port_row = tk.Frame(body, bg=T.BG)
        port_row.pack(fill="x", pady=(0, sc(6)))
        label_with_info(port_row, i18n.t("settings.mcp_port"), hint_for("settings.mcp_port")
                        ).pack(side="left", padx=(0, sc(10)))
        wrap = tk.Frame(port_row, bg=T.BORDER)
        wrap.pack(side="left")
        self.mcp_port_var = tk.StringVar(value=str(status["port"]))
        port_entry = tk.Entry(wrap, textvariable=self.mcp_port_var, bg=T.CARD, fg=T.TEXT, bd=0,
                              highlightthickness=0, insertbackground=T.TEXT, font=font(10), width=7)
        port_entry.pack(padx=1, pady=1, ipady=sc(3), ipadx=sc(4))
        port_entry.bind("<Return>", lambda _e: (self._apply_mcp_port(), "break")[-1])
        ghost_button(port_row, i18n.t("btn.apply"), self._apply_mcp_port, height=28
                     ).pack(side="left", padx=(sc(8), 0))
        ghost_button(port_row, i18n.t("btn.copy_mcp_url"),
                     lambda url=status["url"]: self._copy_mcp_url(url), height=28
                     ).pack(side="left", padx=(sc(8), 0))
        self.mcp_port_error = tk.Label(port_row, text="", bg=T.BG, fg=T.RED, font=font(9))
        self.mcp_port_error.pack(side="left", padx=(sc(8), 0))
        # Jeton d'accès (revue du 16 nov 2026) : jamais affiché en entier à l'écran.
        token = self.app.mcp_token
        masked = ("•" * 8 + token[-4:]) if token else "—"
        label_with_info(body, i18n.t("settings.mcp_token", masked=masked),
                        i18n.t("settings.mcp_token_hint"), fg=T.TEXT).pack(
            fill="x", pady=(sc(2), sc(4)))
        token_row = tk.Frame(body, bg=T.BG)
        token_row.pack(fill="x", pady=(0, sc(6)))
        for label_key, handler in (("btn.copy_mcp_token", self._copy_mcp_token),
                                   ("btn.copy_mcp_hermes", self._copy_mcp_hermes),
                                   ("btn.regen_mcp_token", self.app.regenerate_mcp_token)):
            ghost_button(token_row, i18n.t(label_key), handler).pack(side="left",
                                                                     padx=(0, sc(8)))
        # Portée réelle du serveur MCP, dite franchement : c'est l'outil le plus puissant de
        # l'app, et il est actif par défaut (revue du 11 nov 2026).
        tk.Label(body, text=i18n.t("settings.mcp_scope"), bg=T.BG, fg=T.AMBER, font=font(8),
                 anchor="w", wraplength=sc(640), justify="left").pack(fill="x", pady=(0, sc(8)))

        # ── Tuiles, plugins ──
        self._section("settings.tiles", hint=i18n.t("settings.tiles_hint"))
        tiles_row = tk.Frame(body, bg=T.BG)
        tiles_row.pack(fill="x", pady=(0, sc(8)))
        for text, callback in ((i18n.t("btn.import_tile"), self._import_tile),
                               (i18n.t("btn.plugins_folder"), self._open_plugins_folder),
                               (i18n.t("btn.reload_plugins"), self._reload_plugins)):
            ghost_button(tiles_row, text, callback).pack(side="left", padx=(0, sc(8)))

        self._section("settings.servers", hint=i18n.t("settings.servers_hint"))
        if not self.app.runtimes:
            tk.Label(body, text=i18n.t("settings.no_servers"), bg=T.BG, fg=T.MUTED, font=font(10),
                     anchor="w").pack(fill="x", pady=(0, sc(6)))
        for runtime in self.app.runtimes:
            row = tk.Frame(body, bg=T.CARD)
            row.pack(fill="x", pady=(0, sc(4)))
            badge = tk.Canvas(row, width=sc(24), height=sc(24), bg=T.CARD, highlightthickness=0)
            badge.pack(side="left", padx=(sc(8), 0))
            draw_badge(badge, sc(12), sc(12), sc(5), runtime.state, ring=T.CARD)
            left = tk.Frame(row, bg=T.CARD)
            left.pack(side="left", fill="x", expand=True, padx=sc(8), pady=sc(5))
            type_label = i18n.t(runtime.type.label_key) if runtime.type else "?"
            tk.Label(left, text=f"{runtime.label}", bg=T.CARD, fg=T.TEXT, font=font(10, True),
                     anchor="w").pack(side="left")
            tk.Label(left, text=type_label, bg=T.CARD, fg=T.FAINT, font=font(8), anchor="w"
                     ).pack(side="left", padx=(sc(10), 0))
            ghost_button(row, i18n.t("btn.settings"),
                         lambda sid=runtime.id: self.app.show_server_settings(sid),
                         height=26, parent_bg=T.CARD).pack(side="right", padx=sc(8))

        self._section("settings.config_file")
        tk.Label(body, text=self.app.cfg.path, bg=T.BG, fg=T.ACCENT, font=font(9), anchor="w",
                 wraplength=sc(640), justify="left").pack(fill="x", pady=(0, sc(6)))
        config_row = tk.Frame(body, bg=T.BG)
        config_row.pack(fill="x", pady=(0, sc(8)))
        for key, handler in (("btn.open_folder", self._open_config_folder),
                             ("btn.copy_path", self._copy_config_path),
                             ("btn.open_logs", self._open_logs_folder)):
            ghost_button(config_row, i18n.t(key), handler).pack(side="left", padx=(0, sc(8)))

        self._section("settings.about")
        from bgc import __version__
        tk.Label(body, text=i18n.t("settings.version", app=i18n.t("app.title"),
                                   version=__version__),
                 bg=T.BG, fg=T.MUTED, font=font(9), anchor="w").pack(fill="x", pady=(0, sc(8)))

        self._section("settings.app_log")
        self.log_panel = LogPanel(body, self.app, lambda: self.app.app_log,
                                  title_key="settings.app_log",
                                  report_title=i18n.t("settings.app_log"))
        self.log_panel.pack(fill="both", expand=True, pady=(0, sc(10)))

    def _apply_mcp_port(self) -> None:
        error = self.app.set_mcp_port(self.mcp_port_var.get())
        if error:
            self.mcp_port_error.configure(text="⚠ " + i18n.t(error))

    def _section(self, key: str, hint: str = "") -> None:
        label_with_info(self.body, i18n.t(key).upper(), hint or hint_for(key), bg=T.BG, fg=T.MUTED,
                        bold=True).pack(fill="x", pady=(sc(10), sc(5)))

    def _open_config_folder(self) -> None:
        folder = os.path.dirname(self.app.cfg.path)
        try:
            os.startfile(folder)  # Windows : ouvre l'explorateur sur le dossier de config
        except Exception as exc:
            self.app.log_app("log.folder_missing", path=f"{folder} ({exc})")
            return
        self.app.log_app("log.folder_open", path=folder)

    def _open_logs_folder(self) -> None:
        """Dossier du journal fichier (`applog`) : utile après un plantage."""
        folder = applog.log_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            os.startfile(folder)
        except Exception as exc:
            self.app.log_app("log.folder_missing", path=f"{folder} ({exc})")
            return
        self.app.log_app("log.folder_open", path=folder)

    def _copy_config_path(self) -> None:
        path = self.app.cfg.path
        self.clipboard_clear()
        self.clipboard_append(path)
        self.app.log_app("log.path_copied", path=path)

    def _copy_mcp_url(self, url: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(url)
        self.app.log_app("log.mcp_url_copied", url=url)

    def _copy_mcp_token(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.app.mcp_token)
        self.app.log_app("log.mcp_token_copied")

    def _copy_mcp_hermes(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.app.mcp_hermes_snippet())
        self.app.log_app("log.mcp_hermes_copied")

    def _import_tile(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self, title=i18n.t("btn.import_tile"),
            filetypes=[(i18n.t("tile.filter"), "*.json"), (i18n.t("browse.all"), "*.*")])
        if path:
            self.app.import_tile(path)

    def _open_plugins_folder(self) -> None:
        folder = plugin_loader.ensure_dir()
        try:
            os.startfile(folder)
        except OSError as exc:
            self.app.log_app("log.folder_missing", path=f"{folder} ({exc})")
            return
        self.app.log_app("log.folder_open", path=folder)

    def _reload_plugins(self) -> None:
        self.app.reload_plugins()
