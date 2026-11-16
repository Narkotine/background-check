"""Vue Réglages d'une tuile (formulaire généré par le type)."""

from __future__ import annotations

import tkinter as tk

from bgc import i18n, presets, widgets
from bgc import presentation as pres
from bgc import tiles
from bgc.widgets import (Button, InfoIcon, ScrollFrame, TextField, Toggle, font, hint_for, label_with_info, sc)
from bgc.theme import T
from bgc.core.runtime import ServerRuntime
from bgc.ui.common import (IconField, _apply_preset_to, _collect_actions, _collect_params,
                           _pack_actions_editor, _pack_fields, ghost_button, section_title)


# ── réglages : un serveur ─────────────────────────────────────────────────────
class ServerSettingsView(tk.Frame):
    def __init__(self, master, app, runtime: ServerRuntime):
        super().__init__(master, bg=T.BG)
        self.app, self.runtime = app, runtime
        self.fields: dict[str, TextField] = {}
        self._build()

    def _build(self) -> None:
        # Pied de page FIXE : Enregistrer / Supprimer ne défilent pas avec le formulaire —
        # en fenêtre minimale (560 px), ils étaient hors écran sans aucun indice (revue du
        # 11 nov 2026).
        footer = tk.Frame(self, bg=T.BG)
        footer.pack(side="bottom", fill="x", padx=sc(18), pady=(sc(6), sc(12)))
        self.error_label = tk.Label(footer, text="", bg=T.BG, fg=T.RED, font=font(9), anchor="w",
                                    wraplength=sc(640), justify="left")
        self.error_label.pack(fill="x", pady=(0, sc(6)))
        actions = tk.Frame(footer, bg=T.BG)
        actions.pack(fill="x")
        Button(actions, i18n.t("btn.save"), self._save, height=38).pack(side="left")
        self.delete_btn = Button(actions, i18n.t("btn.delete"), self._delete,
                                 height=38, fill=T.DANGER, hover=T.DANGER_HI, radius=10)
        self.delete_btn.pack(side="left", padx=(sc(8), 0))
        InfoIcon(actions, i18n.t("settings.danger_hint")).pack(side="right")
        self.cancel_delete_btn = Button(actions, i18n.t("confirm.no"), self._cancel_delete,
                                        height=38, fill=T.CARD_HI, hover=T.CARD_HOVER,
                                        fg=T.TEXT, radius=10, outline=T.BORDER, bold=False)

        scroll = ScrollFrame(self, bg=T.BG)
        scroll.pack(fill="both", expand=True, padx=sc(18), pady=(sc(14), sc(6)))
        body = scroll.inner

        head = tk.Frame(body, bg=T.BG)
        head.pack(fill="x", pady=(0, sc(12)))
        Button(head, "‹ " + i18n.t("btn.back"),
               lambda: self.app.select_server(self.runtime.id), height=30,
               fill=T.CARD_HI, hover=T.CARD_HOVER, fg=T.TEXT, radius=8, bold=False, size=9,
               outline=T.BORDER).pack(side="left")
        tk.Label(body, text=i18n.t("settings.server", label=self.runtime.label), bg=T.BG, fg=T.TEXT,
                 font=font(18, True), anchor="w").pack(fill="x", pady=(0, sc(10)))

        # Le libellé est porté par le champ lui-même : pas de doublon « Nom affiché ».
        self.name_field = TextField(body, i18n.t("field.label"), self.runtime.label)
        self.name_field.pack(fill="x", pady=(sc(2), sc(10)))

        self.icon_field = IconField(
            body, self.runtime.server.get("icon", ""), on_change=self._apply_icon_value,
            icon_kind=(self.runtime.type.id if self.runtime.type else None))
        self.icon_field.pack(fill="x", pady=(sc(2), sc(6)))
        self.icon_status = tk.Label(body, text="", bg=T.BG, fg=T.MUTED, font=font(9),
                                    anchor="w", wraplength=sc(640), justify="left")
        self.icon_status.pack(fill="x", pady=(sc(4), sc(10)))

        server_type = self.runtime.type
        if server_type:
            # Les modèles ne sont plus une rangée de boutons au-dessus de « Supprimer » : un
            # clic écrasait des champs sans aperçu (revue b-9). Ils passent derrière
            # « Réinitialiser depuis un modèle… », avec la liste des changements à confirmer.
            if server_type.id == "command":
                reset_row = tk.Frame(body, bg=T.BG)
                reset_row.pack(fill="x", pady=(0, sc(6)))
                self.reset_btn = ghost_button(reset_row, i18n.t("btn.reset_from_preset"),
                                              self._open_preset_menu)
                self.reset_btn.pack(side="left")
                InfoIcon(reset_row, i18n.t("preset.reset_hint")).pack(side="left",
                                                                      padx=(sc(6), 0))
            _pack_fields(body, server_type, self.runtime.server.get("params") or {},
                         self.fields, gap=sc(10))

            if server_type.toggles:
                section_title(body, "settings.server_options", pady=(sc(4), sc(6)))
                for toggle in server_type.toggles:
                    Toggle(body, i18n.t(toggle.label_key),
                           bool(self.runtime.server.get("options", {}).get(toggle.key)),
                           command=lambda value, key=toggle.key: self.runtime.set_option(key, value),
                           hint=hint_for(toggle.label_key)).pack(fill="x", pady=(0, sc(6)))

        # ── organisation de la tuile : ordre (glisser-déposer dans le rail), copie, pause ──
        label_with_info(body, i18n.t("settings.organise").upper(),
                        i18n.t("settings.organise_hint"), fg=T.MUTED, bold=True).pack(
            fill="x", pady=(sc(10), sc(4)))
        organise = tk.Frame(body, bg=T.BG)
        organise.pack(fill="x", pady=(0, sc(8)))
        ghost_button(organise, i18n.t("btn.duplicate"), self._duplicate, height=32
                     ).pack(side="left")
        Toggle(body, i18n.t("toggle.server_enabled"), self.runtime.enabled,
               command=lambda value: self.app.set_server_enabled(self.runtime.id, value),
               hint=i18n.t("settings.enabled_hint")).pack(fill="x", pady=(0, sc(10)))

        # ── serveurs liés : surveillés (état, CPU/RAM/VRAM), jamais pilotés ──
        self.deps_field = TextField(body, i18n.t("field.deps"),
                                    str(self.runtime.server.get("deps") or ""),
                                    hint=i18n.t("field.deps_hint"))
        self.deps_field.pack(fill="x", pady=(sc(6), sc(10)))

        # ── actions sur mesure : 3 boutons propres à cette tuile ──
        label_with_info(body, i18n.t("settings.custom_actions").upper(),
                        i18n.t("settings.custom_actions_hint"), fg=T.MUTED, bold=True).pack(
            fill="x", pady=(sc(6), sc(4)))
        self.custom_fields: list[tuple[TextField, TextField]] = _pack_actions_editor(
            body, self.runtime.server.get("actions") or [])

        export_row = tk.Frame(body, bg=T.BG)
        export_row.pack(fill="x", pady=(sc(6), sc(8)))
        Button(export_row, i18n.t("btn.export_tile"), self._export_tile, height=32,
               fill=T.CARD_HI, hover=T.CARD_HOVER, fg=T.TEXT, radius=8, bold=False, size=9,
               outline=T.BORDER).pack(side="left")

        # Enregistrer / Supprimer vivent dans le pied de page fixe (créé plus haut).

    # --- réinitialiser depuis un modèle (diff + confirmation) --------------
    def _open_preset_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0, bg=T.CARD_HI, fg=T.TEXT, activebackground=T.ACCENT_DARK,
                       activeforeground=T.ON_ACCENT, bd=0, font=font(10))
        for index, (group_key, keys) in enumerate(presets.LAYOUT):
            if index:
                menu.add_separator()
            menu.add_command(label=i18n.t(group_key).upper(), state="disabled")
            for key in keys:
                menu.add_command(label="   " + i18n.t(key),
                                 command=lambda k=key: self._reset_from_preset(k))
        button = self.reset_btn
        try:
            menu.tk_popup(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())
        finally:
            menu.grab_release()

    def current_form(self) -> dict:
        """Valeurs actuellement à l'écran (pas celles enregistrées) : base du diff."""
        server_type = self.runtime.type
        raw = _collect_params(server_type, self.fields) if server_type else {}
        actions = [{"label": label.get().strip(), "target": target.get().strip()}
                   for label, target in getattr(self, "custom_fields", [])]
        return {"params": raw, "icon": self.icon_field.get(), "actions": actions,
                "deps": self.deps_field.get().strip()}

    def _reset_from_preset(self, key: str) -> None:
        from tkinter import messagebox
        data = presets.values(key)
        server_type = self.runtime.type
        labels = {f.key: i18n.t(f.label_key) for f in (server_type.fields if server_type else [])}
        changes = pres.preset_diff(self.current_form(), data, labels)
        if not changes:
            self.error_label.configure(text=i18n.t("preset.reset_nothing", preset=i18n.t(key)),
                                       fg=T.MUTED)
            return
        lines = []
        for name, old, new in changes[:14]:
            name = i18n.t(name) if name.startswith(("field.", "settings.")) else name
            lines.append(f"• {name} : {pres.truncate(old, 40) or '∅'} → "
                         f"{pres.truncate(new, 40) or '∅'}")
        if len(changes) > 14:
            lines.append("…")
        message = (i18n.t("preset.reset_question", preset=i18n.t(key)) + "\n\n"
                   + "\n".join(lines) + "\n\n" + i18n.t("preset.reset_unsaved"))
        if not messagebox.askokcancel(i18n.t("preset.reset_title"), message, icon="warning",
                                      default="cancel", parent=self):
            return
        _apply_preset_to(self, data)
        self.error_label.configure(text="✓ " + i18n.t("preset.reset_done", preset=i18n.t(key)),
                                   fg=T.AMBER)

    def _duplicate(self) -> None:
        self.app.duplicate_server(self.runtime.id)

    def _export_tile(self) -> None:
        """Écrit `*.tile.json` : la tuile seule, partageable et réimportable.

        Les variables d'environnement (souvent des clés d'API) ne partent qu'après un « oui »
        explicite ; la réponse par défaut est « non » (revue du 16 nov 2026).
        """
        from tkinter import filedialog, messagebox
        path = filedialog.asksaveasfilename(
            parent=self, title=i18n.t("btn.export_tile"), defaultextension=".json",
            initialfile=tiles.default_filename(self.runtime.label),
            filetypes=[(i18n.t("tile.filter"), "*.json"), (i18n.t("browse.all"), "*.*")])
        if not path:
            return
        include_env = False
        if tiles.has_env(self.runtime.server):
            include_env = bool(messagebox.askyesno(
                i18n.t("tile.export_env_title"), i18n.t("tile.export_env_question"),
                icon="warning", default="no", parent=self))
        try:
            tiles.write(path, self.runtime.server, include_env=include_env)
        except OSError as exc:
            self.error_label.configure(text="⚠ " + i18n.t("tile.error.write", error=str(exc)[:120]))
            return
        message = "✓ " + i18n.t("log.tile_exported", path=path)
        self.error_label.configure(text=message, fg=T.GREEN)
        self.app.log_app("log.tile_exported", path=path)

    def _apply_icon_value(self, path: str) -> bool:
        """Icône choisie dans le sélecteur : validée + appliquée tout de suite.

        Retourne False si le fichier est illisible — le sélecteur remet alors l'ancienne
        icône à l'écran, et rien n'est écrit dans la config.
        """
        path = (path or "").strip()
        if path and widgets.load_icon(path, sc(44)) is None:
            self.icon_status.configure(text="⚠ " + i18n.t("log.icon_bad", path=path), fg=T.RED)
            self.app.log_app("log.icon_bad", path=path)
            return False
        self.runtime.server["icon"] = path
        self.app.cfg.update_server(self.runtime.id, icon=path)
        self.app.rebuild_rail()  # la tuile change tout de suite
        key = "log.icon_applied" if path else "log.icon_default"
        self.icon_status.configure(text="✓ " + i18n.t(key, path=path), fg=T.GREEN)
        self.app.log_app(key, path=path)
        return True

    def _save(self) -> None:
        server_type = self.runtime.type
        raw = _collect_params(server_type, self.fields) if server_type else {}
        try:
            params = server_type.validate(raw) if server_type else {}
        except ValueError as exc:
            self.error_label.configure(text="⚠ " + i18n.t(str(exc)), fg=T.RED)
            return
        label = self.name_field.get().strip() or self.runtime.label
        icon = self.icon_field.get().strip()
        actions = _collect_actions(getattr(self, "custom_fields", []),
                                   self.runtime.server.get("actions") or [])
        deps = self.deps_field.get().strip()
        self.app.cfg.update_server(self.runtime.id, label=label, params=params, icon=icon,
                                   actions=actions, deps=deps)
        self.runtime.server["deps"] = deps
        self.runtime.server["label"] = label
        self.runtime.server["params"] = params
        self.runtime.server["icon"] = icon
        self.runtime.server["actions"] = [a for a in actions if a["label"] and a["target"]]
        if self.runtime.controller:
            self.runtime.controller.params = dict(params)
        self.app.log_app("log.settings_saved")
        self.app.rebuild_rail()
        self.app.select_server(self.runtime.id)

    def _delete(self) -> None:
        """Deux temps : un clic demande confirmation, le second supprime vraiment."""
        if getattr(self, "_confirming", False):
            self.app.delete_server(self.runtime.id)
            return
        self._confirming = True
        self.delete_btn.set_text(i18n.t("confirm.yes"))
        self.delete_btn.set_style(T.DANGER_ARMED, T.DANGER_ARMED_HI)
        self.cancel_delete_btn.pack(side="left", padx=(sc(8), 0))
        self.error_label.configure(text=i18n.t("confirm.delete", label=self.runtime.label), fg=T.RED)

    def _cancel_delete(self) -> None:
        self._confirming = False
        self.error_label.configure(text="")
        self.cancel_delete_btn.pack_forget()
        self.delete_btn.set_text(i18n.t("btn.delete"))
        self.delete_btn.set_style(T.DANGER, T.DANGER_HI)
