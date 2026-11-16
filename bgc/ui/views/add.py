"""Ajout d'un service en trois étapes (refonte UX du 16 nov 2026, revue b-7 / b-8 / e-3).

1. **Quoi ?** — services détectés sur ce PC (filtrés, regroupés par processus, reconnus
   quand c'est possible), modèles rapides, ou un type de tuile ;
2. **Réglages** — champs pré-remplis + « Tester maintenant » (sonde à blanc, rien n'est lancé) ;
3. **Nom et icône** — en dernier, avec une icône proposée depuis l'exécutable.

L'ancien formulaire mettait le nom puis un gros bloc d'icône avant tout le reste, et la liste
« Détecté » montrait du bruit brut (y compris le port MCP de l'app elle-même).
"""

from __future__ import annotations

import threading
import tkinter as tk

from bgc import discovery, i18n, presets, service
from bgc import presentation as pres
from bgc.ui.common import (IconField, _collect_actions, _collect_params, _pack_actions_editor,
                 _pack_fields, _pack_preset_buttons, ghost_button, section_title)
from bgc.widgets import (Button, InfoIcon, ScrollFrame, TextField, Tooltip, font, sc)
from bgc.theme import T, ThemeMap

STEPS = ("add.step_what", "add.step_settings", "add.step_name")
TEST_COLOURS = ThemeMap({"ok": "GREEN", "warn": "AMBER", "bad": "RED", "info": "MUTED"})
TEST_MARKS = {"ok": "✓", "warn": "!", "bad": "✗", "info": "·"}


class AddServerView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self.step = 1
        self.type_id: str | None = None
        self.fields: dict = {}
        self.custom_fields: list = []
        self.draft_raw: dict = {}
        self.draft_actions: list[dict] = []
        self.draft_name = ""
        self.draft_icon = ""
        self.origin = ""
        self.params: dict | None = None
        self._detected: list[dict] | None = None
        self._build()

    # ── squelette commun ───────────────────────────────────────────────────
    def _build(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self.fields = {}
        footer = tk.Frame(self, bg=T.BG)
        footer.pack(side="bottom", fill="x", padx=sc(18), pady=(sc(6), sc(12)))
        self.error_label = tk.Label(footer, text="", bg=T.BG, fg=T.RED, font=font(9), anchor="w",
                                    wraplength=sc(640), justify="left")
        self.error_label.pack(fill="x", pady=(0, sc(6)))
        # Résultats de « Tester maintenant » : dans le pied, donc toujours visibles.
        self.test_frame = tk.Frame(footer, bg=T.BG)
        self.test_frame.pack(fill="x")
        actions = tk.Frame(footer, bg=T.BG)
        actions.pack(fill="x")
        if self.step > 1:
            ghost_button(actions, i18n.t("btn.previous"), self.previous, height=38,
                         size=11).pack(side="left", padx=(0, sc(8)))
        if self.step == 2:
            Button(actions, i18n.t("btn.next"), self.primary, height=38).pack(side="left")
        elif self.step == 3:
            Button(actions, i18n.t("btn.add"), self.primary, height=38).pack(side="left")
        ghost_button(actions, i18n.t("btn.cancel"), self.app.close_view, height=38,
                     size=11).pack(side="left", padx=(sc(8), 0))
        if self.step == 2:
            InfoIcon(actions, i18n.t("add.test_hint")).pack(side="right")
            ghost_button(actions, "▶ " + i18n.t("btn.test_now"), self._test_now, height=38,
                         size=11).pack(side="right", padx=(0, sc(6)))

        scroll = ScrollFrame(self, bg=T.BG)
        scroll.pack(fill="both", expand=True, padx=sc(18), pady=(sc(14), sc(6)))
        body = scroll.inner
        self.body = body
        tk.Label(body, text=i18n.t("add.title"), bg=T.BG, fg=T.TEXT, font=font(18, True),
                 anchor="w").pack(fill="x", pady=(0, sc(6)))
        self._stepper(body)
        {1: self._build_what, 2: self._build_settings, 3: self._build_name}[self.step](body)

    def _stepper(self, body) -> None:
        row = tk.Frame(body, bg=T.BG)
        row.pack(fill="x", pady=(0, sc(12)))
        for index, key in enumerate(STEPS, start=1):
            current = index == self.step
            done = index < self.step
            colour = T.ACCENT if current else (T.GREEN if done else T.FAINT)
            mark = "✓" if done else str(index)
            tk.Label(row, text=f"{mark}  {i18n.t(key)}", bg=T.BG, fg=colour,
                     font=font(10, current)).pack(side="left")
            if index < len(STEPS):
                tk.Label(row, text="  —  ", bg=T.BG, fg=T.FAINT, font=font(9)).pack(side="left")

    # ── étape 1 : quoi ? ───────────────────────────────────────────────────
    def _build_what(self, body) -> None:
        section_title(body, "add.detected", pady=(sc(2), sc(4)))
        self.detected_frame = tk.Frame(body, bg=T.BG)
        self.detected_frame.pack(fill="x", pady=(0, sc(6)))
        if self._detected is None:
            tk.Label(self.detected_frame, text=i18n.t("add.scanning"), bg=T.BG, fg=T.FAINT,
                     font=font(9), anchor="w").pack(fill="x")
            self._scan()
        else:
            self._fill_detected()

        section_title(body, "preset.label")
        frame = tk.Frame(body, bg=T.BG)
        frame.pack(fill="x", pady=(0, sc(6)))
        _pack_preset_buttons(frame, self._choose_preset)

        section_title(body, "add.other_types")
        row = tk.Frame(body, bg=T.BG)
        row.pack(fill="x", pady=(0, sc(8)))
        for index, server_type in enumerate(service.all_types()):
            button = ghost_button(row, i18n.t(server_type.label_key),
                                  lambda tid=server_type.id: self._choose_type(tid), height=32,
                                  size=10)
            button.grid(row=index // 3, column=index % 3, padx=(0, sc(6)), pady=(0, sc(6)),
                        sticky="w")
            Tooltip(button, i18n.t(server_type.desc_key))

    def _scan(self) -> None:
        """Balayage dans un fil (psutil peut être lent) ; la liste apparaît quand elle arrive."""
        box: dict = {}
        servers = [r.server for r in self.app.runtimes]
        own_port = self.app.mcp_status().get("port")

        def work() -> None:
            try:
                box["items"] = discovery.detected_services(servers, own_ports={own_port})
            except Exception:
                box["items"] = []
            box["done"] = True

        threading.Thread(target=work, daemon=True, name="detect-scan").start()

        def poll() -> None:
            try:
                if not self.winfo_exists():
                    return
                if not box.get("done"):
                    self.after(120, poll)
                    return
                self._detected = box.get("items") or []
                if self.step == 1:
                    self._fill_detected()
            except Exception:
                pass

        self.after(120, poll)

    def _fill_detected(self) -> None:
        frame = self.detected_frame
        for child in frame.winfo_children():
            child.destroy()
        items = (self._detected or [])[:8]
        if not items:
            tk.Label(frame, text=i18n.t("add.detected_none"), bg=T.BG, fg=T.FAINT, font=font(9),
                     anchor="w").pack(fill="x")
            return
        for group in items:
            row = tk.Frame(frame, bg=T.CARD)
            row.pack(fill="x", pady=(0, sc(4)))
            text = tk.Frame(row, bg=T.CARD)
            text.pack(side="left", fill="x", expand=True, padx=sc(10), pady=sc(5))
            name = pres.display_name(group["name"]) or i18n.t("add.unknown_process")
            tk.Label(text, text=name, bg=T.CARD, fg=T.TEXT, font=font(10, True)).pack(side="left")
            ports = ", ".join(str(p) for p in group["ports"][:4])
            tk.Label(text, text=i18n.t("add.detected_ports", ports=ports), bg=T.CARD, fg=T.MUTED,
                     font=font(9)).pack(side="left", padx=(sc(10), 0))
            if group.get("preset"):
                tk.Label(text, text="→ " + i18n.t(group["preset"]), bg=T.CARD, fg=T.GREEN,
                         font=font(9)).pack(side="left", padx=(sc(10), 0))
            ghost_button(row, i18n.t("btn.choose") + " ›",
                         lambda g=group: self._choose_detected(g), height=26,
                         parent_bg=T.CARD).pack(side="right", padx=sc(8))
        rest = len(self._detected or []) - len(items)
        if rest > 0:
            tk.Label(frame, text=i18n.t("add.detected_more", n=rest), bg=T.BG, fg=T.FAINT,
                     font=font(8), anchor="w").pack(fill="x")

    # --- choix -------------------------------------------------------------
    def _start_draft(self, type_id: str, origin: str) -> None:
        server_type = service.get(type_id)
        self.type_id = type_id
        self.draft_raw = server_type.default_params() if server_type else {}
        self.draft_actions = []
        self.draft_name = ""
        self.draft_icon = ""
        self.origin = origin
        self.params = None

    def _apply_preset_data(self, data: dict) -> None:
        self.draft_raw.update({k: v for k, v in (data.get("fields") or {}).items()})
        self.draft_name = data.get("name") or self.draft_name
        self.draft_icon = data.get("icon") or self.draft_icon
        self.draft_actions = list(data.get("actions") or [])

    def _choose_preset(self, key: str) -> None:
        self._start_draft("command", i18n.t("add.origin_preset", name=i18n.t(key)))
        self._apply_preset_data(presets.values(key))
        self._go(2)

    def _choose_detected(self, group: dict) -> None:
        name = pres.display_name(group["name"])
        self._start_draft("command", i18n.t("add.origin_detected", name=name or "?",
                                            port=group["port"]))
        if group.get("preset"):
            self._apply_preset_data(presets.values(group["preset"]))
        else:
            # Service inconnu : surveillance du port, sans commande (rien de deviné).
            self.draft_raw.update({"host": "127.0.0.1", "command": "", "stop_command": ""})
            self.draft_name = name or f"Port {group['port']}"
        self.draft_raw["port"] = str(group["port"])
        exe = str(group.get("exe") or "")
        if not self.draft_icon and exe.lower().endswith(".exe") \
                and exe == discovery.auto_icon(f'"{exe}"'):
            self.draft_icon = exe
        self._go(2)

    def _choose_type(self, type_id: str) -> None:
        server_type = service.get(type_id)
        self._start_draft(type_id, i18n.t(server_type.label_key) if server_type else type_id)
        self._go(2)

    def _go(self, step: int) -> None:
        self.step = step
        # Reconstruire depuis le callback d'un bouton qui va être détruit : un tick plus tard.
        self.after(1, self._build)

    # ── étape 2 : réglages ─────────────────────────────────────────────────
    def _build_settings(self, body) -> None:
        server_type = service.get(self.type_id) if self.type_id else None
        if server_type is None:
            self.step = 1
            self._build_what(body)
            return
        head = tk.Frame(body, bg=T.BG)
        head.pack(fill="x", pady=(0, sc(6)))
        tk.Label(head, text=i18n.t(server_type.label_key), bg=T.BG, fg=T.TEXT,
                 font=font(12, True)).pack(side="left")
        InfoIcon(head, i18n.t(server_type.desc_key)).pack(side="left", padx=(sc(6), 0))
        if self.origin:
            tk.Label(head, text=self.origin, bg=T.BG, fg=T.MUTED, font=font(9)).pack(
                side="left", padx=(sc(12), 0))
        if any(f.key == "distro" for f in server_type.fields):
            self._distro_suggestions(body)
        params_frame = tk.Frame(body, bg=T.BG)
        params_frame.pack(fill="x")
        _pack_fields(params_frame, server_type, self.draft_raw, self.fields, gap=sc(8))


        section_title(body, "settings.custom_actions", pady=(sc(8), sc(4)))
        self.custom_fields = _pack_actions_editor(body, self.draft_actions)
        self.after(50, self._focus_first)

    def _focus_first(self) -> None:
        for widget in self.fields.values():
            try:
                widget.focus_entry()
                return
            except Exception:
                continue

    def _distro_suggestions(self, body) -> None:
        frame = tk.Frame(body, bg=T.BG)
        frame.pack(fill="x", pady=(0, sc(6)))
        box: dict = {}

        def work() -> None:
            try:
                from bgc.plugins import wsl as wsl_mod
                distros, _err = wsl_mod.list_distros()
                box["items"] = [d["name"] for d in distros[:8]]
            except Exception:
                box["items"] = []
            box["done"] = True

        threading.Thread(target=work, daemon=True, name="detect-wsl").start()

        def poll() -> None:
            try:
                if not frame.winfo_exists():
                    return
                if not box.get("done"):
                    self.after(120, poll)
                    return
                names = box.get("items") or []
                if not names:
                    return
                tk.Label(frame, text=i18n.t("add.detected").upper(), bg=T.BG, fg=T.MUTED,
                         font=font(9, True), anchor="w").pack(fill="x", pady=(0, sc(4)))
                row = tk.Frame(frame, bg=T.BG)
                row.pack(fill="x")
                for name in names:
                    ghost_button(row, name, lambda n=name: self._set_distro(n), height=28
                                 ).pack(side="left", padx=(0, sc(6)))
            except Exception:
                pass

        self.after(120, poll)

    def _set_distro(self, name: str) -> None:
        field = self.fields.get("distro")
        if field is not None:
            field.set(name)
        if not self.draft_name:
            self.draft_name = name

    def _collect(self) -> tuple[dict | None, str]:
        server_type = service.get(self.type_id) if self.type_id else None
        if server_type is None:
            return None, "add.error.type"
        raw = _collect_params(server_type, self.fields)
        self.draft_raw = dict(raw)
        self.draft_actions = _collect_actions(self.custom_fields, self.draft_actions)
        try:
            return server_type.validate(raw), ""
        except ValueError as exc:
            return None, str(exc)

    def _test_now(self) -> None:
        params, error = self._collect()
        for child in self.test_frame.winfo_children():
            child.destroy()
        if params is None:
            self.error_label.configure(text="⚠ " + i18n.t(error))
            return
        self.error_label.configure(text="")
        tk.Label(self.test_frame, text=i18n.t("test.running"), bg=T.BG, fg=T.FAINT, font=font(9),
                 anchor="w").pack(fill="x")
        server_type = service.get(self.type_id)
        box: dict = {}

        def work() -> None:
            try:
                box["results"] = discovery.dry_run(server_type, params)
            except Exception as exc:
                box["results"] = [("bad", "test.failed", {"error": str(exc)[:120]})]
            box["done"] = True

        threading.Thread(target=work, daemon=True, name="add-test").start()

        def poll() -> None:
            try:
                if not self.test_frame.winfo_exists():
                    return
                if not box.get("done"):
                    self.after(120, poll)
                    return
                for child in self.test_frame.winfo_children():
                    child.destroy()
                for status, key, params_ in box["results"]:
                    tk.Label(self.test_frame,
                             text=f"{TEST_MARKS.get(status, '·')}  {i18n.t(key, **params_)}",
                             bg=T.BG, fg=TEST_COLOURS.get(status, T.MUTED), font=font(9),
                             anchor="w", wraplength=sc(640), justify="left").pack(fill="x")
                tk.Frame(self.test_frame, bg=T.BG, height=sc(6)).pack(fill="x")
            except Exception:
                pass

        self.after(120, poll)

    # ── étape 3 : nom et icône ─────────────────────────────────────────────
    def _build_name(self, body) -> None:
        server_type = service.get(self.type_id) if self.type_id else None
        if server_type is None or self.params is None:
            self.step = 1
            self._build_what(body)
            return
        if not self.draft_name:
            self.draft_name = self._suggest_name(server_type)
        self.name_field = TextField(body, i18n.t("field.label"), self.draft_name,
                                    hint=i18n.t("add.name_hint"))
        self.name_field.pack(fill="x", pady=(0, sc(10)))
        self.name_field.focus_entry()
        self.icon_field = IconField(body, self.draft_icon, icon_kind=self.type_id)
        self.icon_field.pack(fill="x", pady=(0, sc(10)))
        if self.draft_icon:
            tk.Label(body, text=i18n.t("add.icon_auto"), bg=T.BG, fg=T.FAINT, font=font(8),
                     anchor="w").pack(fill="x", pady=(0, sc(8)))
        # Récapitulatif : ce que la tuile fera (phrase du type).
        try:
            summary = server_type.summary(self.params)
        except Exception:
            summary = None
        if summary:
            section_title(body, "add.summary")
            tk.Label(body, text=i18n.t(summary[0], **summary[1]), bg=T.BG, fg=T.MUTED, font=font(9),
                     anchor="w", wraplength=sc(640), justify="left").pack(fill="x")

    def _suggest_name(self, server_type) -> str:
        if self.type_id == "wsl":
            distro = str(self.draft_raw.get("distro") or "").strip()
            if distro:
                return distro
        return i18n.t(server_type.label_key)

    # ── navigation ─────────────────────────────────────────────────────────
    def previous(self) -> None:
        if self.step == 3:
            self.draft_name = self.name_field.get().strip()
            self.draft_icon = self.icon_field.get().strip()
        elif self.step == 2:
            self._collect()
        self._go(max(1, self.step - 1))

    def primary(self) -> None:
        """Bouton principal (et touche Entrée) : « Suivant » à l'étape 2, « Ajouter » à la 3."""
        if self.step == 2:
            params, error = self._collect()
            if params is None:
                self.error_label.configure(text="⚠ " + i18n.t(error))
                return
            self.params = params
            if not self.draft_icon and self.type_id == "command":
                self.draft_icon = discovery.auto_icon(str(params.get("command") or ""),
                                                      str(params.get("workdir") or ""))
            self._go(3)
        elif self.step == 3:
            self._create()

    def _create(self) -> None:
        if self.step != 3:
            self.primary()
            return
        label = self.name_field.get().strip()
        if not label:
            self.error_label.configure(text="⚠ " + i18n.t("add.error.name"))
            return
        self.app.create_server(self.type_id, label, self.params,
                               icon=self.icon_field.get().strip(),
                               actions=self.draft_actions)
