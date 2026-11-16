"""Briques communes des vues : boutons, titres, formulaires de champs, icône."""

from __future__ import annotations

import os
import tkinter as tk
import tkinter.font as tkfont

from bgc import i18n, presets, service, widgets
from bgc.widgets import (Button, ChoiceField, TextField, font, hint_for, label_with_info, sc)
from bgc.theme import T, ThemeMap


PHASE_COLOURS = ThemeMap({"running": "GREEN", "starting": "AMBER", "stopped": "MUTED", "unknown": "FAINT"})


def ghost_button(parent, text, command, height=30, size=9, parent_bg=None, **kw):
    """Bouton secondaire (le style le plus répandu de l'app), en une ligne."""
    parent_bg = T.BG if parent_bg is None else parent_bg
    return Button(parent, text, command, height=height, fill=T.CARD_HI, hover=T.CARD_HOVER, fg=T.TEXT,
                  radius=8, bold=False, size=size, outline=T.BORDER, parent_bg=parent_bg, **kw)


def section_title(parent, key: str, bg=None, pady=None) -> None:
    """Titre de section en capitales + ⓘ si la clé `<clé>_hint` existe."""
    bg = T.BG if bg is None else bg
    pady = pady if pady is not None else (sc(10), sc(4))
    label_with_info(parent, i18n.t(key).upper(), hint_for(key), bg=bg, fg=T.MUTED, size=9,
                    bold=True).pack(fill="x", pady=pady)
ROW_COLOURS = ThemeMap({service.ROW_OK: "GREEN", service.ROW_WARN: "AMBER", service.ROW_IDLE: "FAINT",
               service.ROW_BAD: "RED"})
TYPE_GLYPHS = {"hermes": "H", "wsl": "W", "stt": "S", "command": "›_", "docker": "D",
               "winsvc": "Sv"}
TYPE_COLOURS = ThemeMap({"hermes": "GREEN", "wsl": "#8b7cf6", "stt": "#e0b341", "command": "#2dd4bf",
                         "docker": "#38bdf8", "winsvc": "#f472b6"})
# Pictogramme vectoriel par type (widgets.draw_icon) ; un type inconnu garde sa lettre.
TYPE_ICONS = {"hermes": "hermes", "wsl": "wsl", "stt": "stt", "command": "command",
              "docker": "docker", "winsvc": "winsvc"}


def _first_existing(*paths: str) -> str:
    """Premier chemin de fichier qui existe vraiment (détection d'un programme installé)."""
    for path in paths:
        if path and os.path.isfile(path):
            return path
    return ""


def _pick_path(field, kind: str) -> None:
    """Bouton « Parcourir… » : choix d'un dossier (`dir`) ou d'un programme (`exe`)."""
    from tkinter import filedialog

    if kind == "dir":
        path = filedialog.askdirectory(parent=field, title=i18n.t("browse.dir_title"))
    else:
        path = filedialog.askopenfilename(
            parent=field, title=i18n.t("browse.exe_title"),
            filetypes=[(i18n.t("browse.programs"), "*.exe *.bat *.cmd *.ps1 *.py *.jar"),
                       (i18n.t("browse.all"), "*.*")])
    if not path:
        return
    path = os.path.normpath(path)
    # Une commande avec des espaces doit être entre guillemets pour le shell.
    field.set(f'"{path}"' if kind == "exe" and " " in path else path)


def _pack_fields(body, server_type, values: dict, store: dict, gap: int) -> None:
    """Empile les champs d'un type, en insérant les **titres de section** qu'il déclare.

    Un type qui pose `section_key` sur ses champs (le « Service surveillé » le fait) obtient
    un formulaire découpé en blocs lisibles — sans que l'UI ait à connaître ses champs.
    Le titre est la clé `section.…` ; son aide facultative est la clé `section.…_hint`.
    Un champ qui déclare `browse` ("dir" / "exe") reçoit un bouton « Parcourir… ».
    """
    current = None
    target_keys = tuple(getattr(server_type, "TARGET_FIELDS", None) or ())
    target_done = False
    for field in server_type.fields:
        section = getattr(field, "section_key", None)
        if section and section != current:
            current = section
            # Titre + aide en ⓘ (l'aide n'occupe plus de lignes à l'écran, revue b-5).
            label_with_info(body, i18n.t(section), hint_for(section), bg=T.BG, fg=T.MUTED,
                            bold=True).pack(fill="x", pady=(sc(10), sc(4)))
        # Hôte + port + chemin de vérification : une seule ligne « cible » à l'écran
        # (le type les déclare dans TARGET_FIELDS ; le config garde les trois champs).
        if target_keys and field.key in target_keys:
            if target_done:
                continue
            target_done = True
            widget = TextField(body, i18n.t("field.target"),
                               server_type.target_text(values),
                               hint=i18n.t("field.target_hint"))
            widget.pack(fill="x", pady=(0, gap))
            store["__target__"] = widget
            continue
        holder: dict = {}
        action = None
        if getattr(field, "browse", None):
            action = (i18n.t("btn.browse"),
                      lambda kind=field.browse, box=holder: _pick_path(box["w"], kind))
        hint = i18n.t(field.hint_key) if field.hint_key else ""
        if field.kind == "choice" and field.choices:
            widget = ChoiceField(body, i18n.t(field.label_key),
                                 [(value, i18n.t(key)) for value, key in field.choices],
                                 values.get(field.key, field.default), hint=hint)
        else:
            widget = TextField(body, i18n.t(field.label_key), values.get(field.key, field.default),
                               hint=hint, action=action)
        widget.pack(fill="x", pady=(0, gap))
        holder["w"] = widget
        store[field.key] = widget


def _view_type(view):
    """Le type de serveur d'une vue (ajout ou réglages)."""
    runtime = getattr(view, "runtime", None)
    if runtime is not None:
        return runtime.type
    return service.get(getattr(view, "type_id", ""))


def _collect_params(server_type, fields: dict) -> dict:
    """Valeurs du formulaire → paramètres ; la ligne « cible » est redécomposée ici."""
    raw = {key: widget.get() for key, widget in fields.items() if not key.startswith("__")}
    target = fields.get("__target__")
    if target is not None and getattr(server_type, "TARGET_FIELDS", None):
        raw = server_type.parse_target(target.get(), raw)
    return raw


def _apply_preset_to(view, data: dict) -> None:
    """Pose les valeurs d'un preset dans le formulaire d'une vue (ajout ou réglages)."""
    for field_key, value in (data.get("fields") or {}).items():
        widget = view.fields.get(field_key)
        if widget is not None:
            widget.set(value)
    name = data.get("name") or ""
    if name and not view.name_field.get().strip():
        view.name_field.set(name)
    icon = data.get("icon") or ""
    if icon:
        view.icon_field.set(icon)
    actions = data.get("actions") or []
    custom_fields = getattr(view, "custom_fields", None) or []
    if actions and custom_fields:
        for index, (label_field, target_field) in enumerate(custom_fields):
            item = actions[index] if index < len(actions) else {}
            label_field.set(item.get("label", ""))
            target_field.set(item.get("target", ""))
    # La cible est une seule ligne : on recombine hôte/port/chemin du preset dedans.
    server_type = _view_type(view)
    target_widget = view.fields.get("__target__")
    if target_widget is not None and server_type is not None:
        base = server_type.parse_target(target_widget.get(), {})
        touched = False
        for key, value in (data.get("fields") or {}).items():
            if key in (server_type.TARGET_FIELDS or ()):
                base[key] = value
                touched = True
        if touched:
            target_widget.set(server_type.target_text(base))
    focus = data.get("focus")
    if focus in view.fields:
        view.fields[focus].focus_entry()
    elif focus in ("port", "host") and target_widget is not None:
        target_widget.focus_entry()


def _pack_actions_editor(body, existing=None) -> list:
    """3 couples « libellé / adresse ou commande » : les boutons sur mesure d'une tuile."""
    existing = existing or []
    fields = []
    for index in range(3):
        item = existing[index] if index < len(existing) else {}
        row = tk.Frame(body, bg=T.BG)
        row.pack(fill="x", pady=(0, sc(6)))
        name_field = TextField(row, i18n.t("field.action_label"), item.get("label", ""))
        name_field.pack(side="left", fill="x", expand=True)
        target_field = TextField(row, i18n.t("field.action_target"), item.get("target", ""))
        target_field.pack(side="left", fill="x", expand=True, padx=(sc(6), 0))
        fields.append((name_field, target_field))
    return fields


def _pack_preset_buttons(parent, command) -> None:
    """Rangée « Modèles rapides » : largeur mesurée par libellé, passage à la ligne.

    Une largeur fixe de 150 px coupait « Programme ou script local » (constaté à l'image).
    Les modèles sont **groupés** (surveillance / moteurs locaux / interfaces web) : c'est ce
    qui rend lisible qu'un frontend a besoin d'un moteur à côté (10 nov 2026).
    """
    metrics = tkfont.Font(font=font(9))
    gap = sc(6)
    limit = sc(700)
    for group_key, keys in presets.LAYOUT:
        tk.Label(parent, text=i18n.t(group_key).upper(), bg=T.BG, fg=T.FAINT, font=font(8, True),
                 anchor="w").pack(fill="x", pady=(sc(2), sc(2)))
        rows: list[list] = [[]]
        used = 0
        for key in keys:
            label = i18n.t(key)
            width = max(sc(140), metrics.measure(label) + sc(28))
            need = width + (gap if rows[-1] else 0)
            if rows[-1] and used + need > limit:
                rows.append([])
                used = 0
                need = width
            rows[-1].append((key, label, width))
            used += need
        for items in rows:
            row = tk.Frame(parent, bg=T.BG)
            row.pack(fill="x", pady=(0, sc(4)))
            for key, label, width in items:
                button = Button(row, label, lambda k=key: command(k), width=0, height=30,
                                fill=T.CARD_HI, hover=T.CARD_HOVER, fg=T.TEXT, radius=8, bold=False,
                                size=9, outline=T.BORDER)
                button.configure(width=width)
                button.pack(side="left", padx=(0, gap))


def _collect_actions(fields, existing=None) -> list[dict]:
    """Actions sur mesure saisies : libellé + cible, en **gardant la sonde** (`check`).

    Le formulaire ne montre que deux champs par bouton ; sans ce report, enregistrer une
    tuile effacerait silencieusement la sonde posée par un modèle rapide (« Fermer LM Studio »
    ne serait plus grisé quand l'application ne tourne pas — 10 nov 2026). La sonde n'est
    reprise que si la cible n'a pas changé : une autre cible demande une autre sonde.
    """
    existing = existing or []
    actions: list[dict] = []
    for index, (label_field, target_field) in enumerate(fields):
        label = label_field.get().strip()
        target = target_field.get().strip()
        item = {"label": label, "target": target}
        previous = existing[index] if index < len(existing) else {}
        check = str(previous.get("check") or "").strip()
        if check and str(previous.get("target") or "").strip() == target:
            item["check"] = check
        actions.append(item)
    return actions


class IconField(tk.Frame):
    """Icône d'une tuile : un aperçu et deux boutons, jamais un chemin à taper.

    « Choisir une icône… » ouvre le navigateur de fichiers (images, .exe, .dll) et applique
    aussitôt ; « Retirer » revient au pictogramme du type. Le chemin complet n'est pas montré :
    seul le nom du fichier l'est — l'utilisateur n'a rien à saisir (demande du 9 nov 2026).
    """

    def __init__(self, master, path: str = "", on_change=None, icon_kind: str | None = None,
                 bg=None):
        bg = T.BG if bg is None else bg
        super().__init__(master, bg=bg)
        self._on_change = on_change
        self._icon_kind = icon_kind
        self._path = str(path or "")
        self._last_good = self._path
        self._image = None

        label_with_info(self, i18n.t("field.icon"), i18n.t("field.icon_hint"), bg=bg
                        ).pack(fill="x")
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x", pady=(sc(3), 0))
        self.preview = tk.Canvas(row, width=sc(44), height=sc(44), bg=T.CARD,
                                 highlightthickness=0, bd=0)
        self.preview.pack(side="left")
        self.name_label = tk.Label(row, text="", bg=bg, fg=T.MUTED, font=font(9), anchor="w")
        self.name_label.pack(side="left", fill="x", expand=True, padx=(sc(10), sc(8)))
        self.remove_btn = Button(row, i18n.t("icon.remove"), self.clear,
                                 height=30, fill=T.CARD_HI, hover=T.CARD_HOVER, fg=T.TEXT, radius=8,
                                 bold=False, size=9, outline=T.BORDER)
        self.remove_btn.pack(side="right")
        Button(row, i18n.t("icon.choose"), self.choose, height=30,
               fill=T.CARD_HI, hover=T.CARD_HOVER, fg=T.TEXT, radius=8, bold=False, size=9,
               outline=T.BORDER).pack(side="right", padx=(0, sc(6)))
        self._paint()

    def get(self) -> str:
        return self._path

    def set(self, path) -> None:
        self._path = str(path or "")
        self._last_good = self._path
        self._paint()

    def choose(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self, title=i18n.t("browse.icon_title"),
            filetypes=[(i18n.t("browse.filter"),
                        "*.png *.ico *.jpg *.jpeg *.bmp *.gif *.exe *.dll"),
                       (i18n.t("browse.all"), "*.*")])
        if not path:
            return
        self._path = os.path.normpath(path)
        self._paint()
        self._notify()

    def clear(self) -> None:
        if not self._path:
            return
        self._path = ""
        self._paint()
        self._notify()

    def _notify(self) -> None:
        if self._on_change is None:
            self._last_good = self._path
            return
        ok = self._on_change(self._path)
        if ok is False:  # fichier illisible : on remet l'ancienne icône à l'écran
            self._path = self._last_good
            self._paint()
        else:
            self._last_good = self._path

    def _paint(self) -> None:
        self.preview.delete("all")
        self.preview.create_rectangle(1, 1, sc(43), sc(43), outline=T.BORDER)
        self._image = None
        if self._path:
            image = widgets.load_icon(self._path, sc(38))
            if image is not None:
                self._image = image  # référence gardée : sinon Tk efface l'image
                self.preview.create_image(sc(22), sc(22), image=image)
                self.name_label.configure(text=os.path.basename(self._path), fg=T.TEXT)
                self.remove_btn.set_enabled(True)
                return
            self.preview.create_text(sc(22), sc(22), text="!", fill=T.RED,
                                     font=font(16, True))
            self.name_label.configure(
                text="⚠ " + i18n.t("icon.missing", name=os.path.basename(self._path)), fg=T.RED)
            self.remove_btn.set_enabled(True)
            return
        drawn = False
        if self._icon_kind:
            drawn = widgets.draw_icon(self.preview, self._icon_kind, sc(22), sc(22), sc(24),
                                      T.MUTED)
        if not drawn:
            self.preview.create_text(sc(22), sc(22), text="?", fill=T.FAINT, font=font(14, True))
        self.name_label.configure(text=i18n.t("icon.none"), fg=T.FAINT)
        self.remove_btn.set_enabled(False)
