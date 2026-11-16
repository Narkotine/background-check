"""Vue d'ensemble : une ligne par service (revue du 16 nov 2026, b-2 / e-1).

Pastille d'état + nom + mot d'état, port (avec son rôle : « dashboard », « API »…), temps
de marche, CPU / RAM / VRAM, bouton principal et bouton web. Sous une tuile : ses éléments
(puces) puis ses **serveurs liés** (sous-lignes en retrait, surveillés seulement). Un clic sur
le nom ouvre le détail. La vue est construite une fois puis **repeinte** à chaque tick.

**Largeur.** Le nom garde toujours sa place (`NAME_MIN`). Quand la fenêtre est trop étroite
pour toutes les colonnes (constaté à 916 px : la colonne SERVICE disparaissait), la vue passe
en mode **compact** : temps de marche et CPU/RAM/VRAM quittent leurs colonnes et s'affichent
sous le nom. Le basculement suit la largeur (`<Configure>`), sans reconstruire les lignes.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from bgc import i18n, sysinfo
from bgc import presentation as pres
from bgc.core import deps as deps_mod
from bgc.ui.common import ghost_button
from bgc.widgets import (Button, ScrollFrame, Tooltip, draw_badge, fit_text, font, sc)
from bgc.theme import T


#: Colonnes : 0 pastille, 1 nom, 2 port, 3 temps de marche, 4-6 ressources, 7 boutons.
UPTIME_COLUMN = 3
METRIC_COLUMNS = (4, 5, 6)
WIDE_ONLY = (UPTIME_COLUMN, *METRIC_COLUMNS)
BUTTONS_COLUMN = 7
NAME_MIN = 190          # px (avant échelle) : le nom ne disparaît jamais
PORT_MIN = 104
UPTIME_MIN = 84
METRIC_MIN = 62
FIRST_DEP_ROW = 3       # grille d'une ligne : 0-1 nom/mot, 2 puces, 3+ serveurs liés


class OverviewView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self._rows: dict[str, dict] = {}
        self._signature = None
        self._compact = False
        self._widest = sc(250)
        self._build()
        self.bind("<Configure>", lambda _e: self._apply_layout(), add="+")

    def _build(self) -> None:
        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=sc(18), pady=(sc(14), sc(8)))
        tk.Label(head, text=i18n.t("overview.title"), bg=T.BG, fg=T.TEXT, font=font(20, True),
                 anchor="w").pack(side="left")
        self.summary = tk.Label(head, text="", bg=T.BG, fg=T.MUTED, font=font(10))
        self.summary.pack(side="left", padx=(sc(14), 0), pady=(sc(6), 0))
        self.gpu = tk.Label(head, text="", bg=T.BG, fg=T.FAINT, font=font(9))
        self.gpu.pack(side="left", padx=(sc(14), 0), pady=(sc(7), 0))
        Tooltip(self.gpu, lambda: i18n.t("overview.gpu_tip"), wrap=360)
        ghost_button(head, "＋ " + i18n.t("rail.add"), self.app.show_add, height=32, size=10
                     ).pack(side="right")

        # En-têtes de colonnes (alignés sur la grille des lignes).
        self.header = tk.Frame(self, bg=T.BG)
        self.header.pack(fill="x", padx=sc(18) + sc(1), pady=(0, sc(2)))
        self._configure_columns(self.header)
        self._header_cells: dict[int, tk.Label] = {}
        tip = i18n.t("overview.metrics_tip")
        for column, key in ((1, "overview.col_service"), (2, "overview.col_port"),
                            (UPTIME_COLUMN, "overview.col_uptime"),
                            (4, "overview.col_cpu"), (5, "overview.col_ram"),
                            (6, "overview.col_vram")):
            label = tk.Label(self.header, text=i18n.t(key).upper(), bg=T.BG, fg=T.FAINT,
                             font=font(8, True), anchor="w")
            label.grid(row=0, column=column, sticky="w", padx=(sc(4), 0))
            if column in METRIC_COLUMNS:
                Tooltip(label, tip, wrap=360)
            self._header_cells[column] = label

        scroll = ScrollFrame(self, bg=T.BG)
        scroll.pack(fill="both", expand=True, padx=sc(18), pady=(0, sc(12)))
        self.list = scroll.inner

    def _configure_columns(self, frame) -> None:
        frame.columnconfigure(0, minsize=sc(34))
        frame.columnconfigure(1, weight=1, minsize=sc(NAME_MIN))
        frame.columnconfigure(2, minsize=sc(PORT_MIN))
        wide = not self._compact
        frame.columnconfigure(UPTIME_COLUMN, minsize=sc(UPTIME_MIN) if wide else 0)
        for column in METRIC_COLUMNS:
            frame.columnconfigure(column, minsize=sc(METRIC_MIN) if wide else 0)
        frame.columnconfigure(BUTTONS_COLUMN, minsize=self._widest)

    # --- largeur : colonnes complètes ou mode compact ------------------------
    def _available_width(self) -> int:
        width = self.winfo_width()
        if width > 100:
            return width
        root = self.app.root.winfo_width() if hasattr(self.app, "root") else 0
        return (root - sc(120)) if root > 200 else sc(1000)

    def needed_width(self) -> int:
        """Largeur qu'il faut pour toutes les colonnes (marges de la vue comprises)."""
        return (sc(34) + sc(NAME_MIN) + sc(PORT_MIN) + sc(UPTIME_MIN) + 3 * sc(METRIC_MIN)
                + self._widest + sc(18) * 2 + sc(40))

    def _apply_layout(self, force: bool = False) -> None:
        width = self._available_width()
        need = self.needed_width()
        # Hystérésis : on ne rebascule pas à chaque pixel autour du seuil.
        compact = (width < need) if not self._compact else (width < need + sc(24))
        if compact == self._compact and not force:
            return
        self._compact = compact
        for column, label in self._header_cells.items():
            if column in WIDE_ONLY:
                (label.grid_remove if compact else label.grid)()
        for frame in [self.header] + [w["row"] for w in self._rows.values()]:
            self._configure_columns(frame)
        for widgets in self._rows.values():
            for widget in widgets["wide_only"]:
                (widget.grid_remove if compact else widget.grid)()
            for widget in widgets["compact_only"]:
                (widget.grid if compact else widget.grid_remove)()
        self.paint()

    # --- données d'une ligne ----------------------------------------------
    def _line(self, runtime) -> dict:
        snapshot = runtime.snapshot_data or {}
        actions = pres.adjust_actions(snapshot.get("actions") or [], snapshot)
        if not runtime.enabled:
            actions = []
        primary = pres.primary_actions(actions)
        web = pres.web_action(actions, runtime.server.get("actions") or [],
                              runtime.custom_states) if runtime.enabled else None
        uptime = pres.uptime_seconds(runtime.snapshot_data, runtime.stats)
        word = runtime.status_text()
        items = (pres.items_of(snapshot) if runtime.enabled and pres.has_items(snapshot)
                 else [])
        if runtime.enabled and pres.has_items(snapshot):
            running, total = pres.item_counts(snapshot)
            if total:   # « 0/0 en marche » n'apprend rien (Docker absent, aucune distribution)
                word = f"{word} · " + i18n.t("items.count", running=running, total=total)
        ports, roles, port_tip = pres.port_cells(runtime.server, snapshot)
        return {"state": runtime.state, "word": word,
                "port": ports, "port_role": roles, "port_tip": port_tip,
                "uptime": sysinfo.fmt_uptime(uptime) if uptime else pres.DASH,
                "primary": primary, "web": web, "items": items[:self.MAX_ITEMS],
                "more": max(0, len(items) - self.MAX_ITEMS),
                "deps": pres.deps_of(snapshot) if runtime.enabled else []}

    #: Éléments montrés sous une ligne (au-delà : « +n », le détail de la tuile les a tous).
    MAX_ITEMS = 8

    @staticmethod
    def _items_signature(line) -> tuple:
        return tuple((i["id"], i["label"], i["phase"],
                      tuple((a.get("id"), a.get("label_key"), bool(a.get("enabled", True)))
                            for a in pres.primary_actions(i["actions"])))
                     for i in line["items"]) + (line["more"],)

    @staticmethod
    def _deps_signature(line) -> tuple:
        return tuple((d["id"], pres.dep_label(d), pres.dep_target(d)) for d in line["deps"])

    def _compact_text(self, word: str, uptime: str, metrics: dict | None) -> str:
        """Mode compact : ce que les colonnes masquées auraient montré, sous le nom."""
        parts = [pres.one_line(word)]
        if uptime and uptime != pres.DASH:
            parts.append(uptime)
        if metrics:
            parts.append(pres.metrics_text(metrics))
        return "  ·  ".join(parts)

    @staticmethod
    def _fit(label: tk.Label, text: str, fnt) -> str:
        width = label.winfo_width()
        return fit_text(text, fnt, width - sc(4))[0] if width > 20 else text

    def paint(self) -> None:
        lines = [(runtime, self._line(runtime)) for runtime in self.app.runtimes]
        signature = tuple(
            (runtime.id, runtime.label,
             tuple((a["id"], a.get("label_key"), a.get("style")) for a in line["primary"]),
             (line["web"] or {}).get("id"), self._items_signature(line),
             self._deps_signature(line), runtime.busy)
            for runtime, line in lines)
        if signature != self._signature:
            self._signature = signature
            self._rebuild(lines)
        running = 0
        for runtime, line in lines:
            widgets = self._rows.get(runtime.id)
            if not widgets:
                continue
            if line["state"] == "ok":
                running += 1
            canvas = widgets["badge"]
            canvas.delete("all")
            draw_badge(canvas, sc(15), sc(15), sc(7), line["state"], ring=T.CARD)
            colour = T.STATE_COLOURS.get(line["state"], T.FAINT)
            metrics = pres.metrics_for(self.app, runtime) if runtime.enabled else None
            word = (self._compact_text(line["word"], line["uptime"], metrics) if self._compact
                    else pres.one_line(line["word"]))
            widgets["word"].configure(text=self._fit(widgets["word"], word, font(9)),
                                      fg=colour if line["state"] not in ("idle", "paused")
                                      else T.MUTED)
            widgets["port"].configure(text=line["port"])
            widgets["port_role"].configure(text=pres.truncate(line["port_role"], 22))
            widgets["port_tip"][0] = line["port_tip"]
            widgets["uptime"].configure(text=line["uptime"])
            for cell, text in zip(widgets["cells"], pres.metrics_cells(metrics)):
                if cell.cget("text") != text:
                    cell.configure(text=text)
            for action in line["primary"]:
                button = widgets["buttons"].get(action["id"])
                if button:
                    button.set_text(i18n.t(action["label_key"]))
                    button.set_enabled(bool(action.get("enabled", True)) and not runtime.busy)
            web = line["web"]
            button = widgets["buttons"].get("__web__")
            if button and web:
                button.set_enabled(bool(web.get("enabled", True)) and not runtime.busy)
            self._paint_deps(runtime, line, widgets)
        sampler = getattr(self.app, "metrics", None)
        gpu = pres.gpu_text(sampler.gpu_total() if sampler else None)
        if self.gpu.cget("text") != gpu:
            self.gpu.configure(text=gpu)
        active = sum(1 for r in self.app.runtimes if r.enabled)
        self.summary.configure(text=i18n.t("overview.summary", running=running,
                                           total=active))

    def _paint_deps(self, runtime, line, widgets) -> None:
        for dep, dw in zip(line["deps"], widgets["deps"]):
            state = pres.dep_state(dep)
            dw["dot"].delete("all")
            draw_badge(dw["dot"], sc(7), sc(7), sc(4), state, ring=T.CARD)
            metrics = pres.metrics_for(self.app, runtime, deps_mod.dep_key(dep["id"]))
            uptime = pres.dep_uptime(dep)
            word = i18n.t("dep.up" if dep.get("up") else "dep.down")
            colour = T.STATE_COLOURS.get(state, T.FAINT) if dep.get("up") else T.FAINT
            # compact : la colonne du nom est étroite, l'état rejoint temps de marche et
            # ressources sur la place libre à droite
            dw["word"].configure(text="" if self._compact else word, fg=colour)
            if self._compact:
                parts = [p for p in (word, uptime if uptime != pres.DASH else "",
                                     pres.metrics_text(metrics) if metrics else "") if p]
                dw["extra"].configure(text=self._fit(dw["extra"], "  ·  ".join(parts), font(8)),
                                      fg=colour)
            dw["uptime"].configure(text=uptime)
            for cell, value in zip(dw["cells"], pres.metrics_cells(metrics)):
                if cell.cget("text") != value:
                    cell.configure(text=value)

    def _rebuild(self, lines) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        self._rows.clear()
        if not lines:
            tk.Label(self.list, text=i18n.t("overview.empty"), bg=T.BG, fg=T.MUTED,
                     font=font(10)).pack(pady=sc(20))
            return
        for runtime, line in lines:
            row = tk.Frame(self.list, bg=T.CARD, highlightthickness=1,
                           highlightbackground=T.BORDER, highlightcolor=T.BORDER)
            row.pack(fill="x", pady=(0, sc(6)))
            self._configure_columns(row)
            badge = tk.Canvas(row, width=sc(30), height=sc(30), bg=T.CARD, highlightthickness=0)
            badge.grid(row=0, column=0, rowspan=2, padx=(sc(6), 0), pady=sc(6))
            short = pres.truncate(runtime.label, 30)
            name = tk.Label(row, text=short, bg=T.CARD, fg=T.TEXT, font=font(11, True),
                            anchor="w", cursor="hand2")
            if short != runtime.label:
                Tooltip(name, runtime.label)
            name.grid(row=0, column=1, sticky="ew", padx=(sc(4), 0), pady=(sc(6), 0))
            word = tk.Label(row, text="", bg=T.CARD, fg=T.MUTED, font=font(9), anchor="w",
                            cursor="hand2")
            word.grid(row=1, column=1, sticky="ew", padx=(sc(4), 0), pady=(0, sc(6)))
            for widget in (name, word, badge):
                widget.bind("<Button-1>", lambda _e, sid=runtime.id: self.app.select_server(sid))
            # Port + son rôle en petit dessous (« 9119 · 8642 » / « dashboard · gateway »).
            port = tk.Label(row, text="", bg=T.CARD, fg=T.MUTED, font=font(10), anchor="w")
            port.grid(row=0, column=2, sticky="sw", padx=(sc(4), 0), pady=(sc(6), 0))
            port_role = tk.Label(row, text="", bg=T.CARD, fg=T.FAINT, font=font(8), anchor="w")
            port_role.grid(row=1, column=2, sticky="nw", padx=(sc(4), 0), pady=(0, sc(6)))
            port_tip = [""]
            for widget in (port, port_role):
                Tooltip(widget, lambda box=port_tip: box[0], wrap=360)
            uptime = tk.Label(row, text="", bg=T.CARD, fg=T.MUTED, font=font(10), anchor="w")
            uptime.grid(row=0, column=UPTIME_COLUMN, rowspan=2, sticky="w", padx=(sc(4), 0))
            cells = []
            for column in METRIC_COLUMNS:
                cell = tk.Label(row, text=pres.DASH, bg=T.CARD, fg=T.MUTED, font=font(9),
                                anchor="w")
                cell.grid(row=0, column=column, rowspan=2, sticky="w", padx=(sc(4), 0))
                cells.append(cell)
            buttons_frame = tk.Frame(row, bg=T.CARD)
            buttons_frame.grid(row=0, column=BUTTONS_COLUMN, rowspan=2, sticky="e",
                               padx=(0, sc(8)))
            buttons: dict = {}
            for action in line["primary"]:
                style = action.get("style", "ghost")
                if style == "primary":
                    fill, hover, fg, outline = T.ACCENT_DARK, T.ACCENT_HI, T.ON_ACCENT, ""
                elif style == "danger":
                    fill, hover, fg, outline = T.DANGER, T.DANGER_HI, T.ON_ACCENT, ""
                else:
                    fill, hover, fg, outline = T.CARD_HI, T.CARD_HOVER, T.TEXT, T.BORDER
                button = Button(buttons_frame, i18n.t(action["label_key"]),
                                lambda aid=action["id"], r=runtime: self.app.run_action(r, aid),
                                height=30, fill=fill, hover=hover, fg=fg, radius=8, size=9,
                                outline=outline, parent_bg=T.CARD)
                button.pack(side="left", padx=(0, sc(6)))
                buttons[action["id"]] = button
            web = line["web"]
            if web:
                button = ghost_button(buttons_frame, "↗ " + i18n.t("btn.open_web"),
                                      lambda aid=web["id"], r=runtime: self.app.run_action(r, aid),
                                      parent_bg=T.CARD)
                button.pack(side="left")
                buttons["__web__"] = button
            if line["items"]:
                self._pack_items(row, runtime, line)
            dep_widgets = self._pack_deps(row, runtime, line)
            wide_only = [uptime, *cells] + [w for d in dep_widgets for w in d["wide_only"]]
            compact_only = [w for d in dep_widgets for w in d["compact_only"]]
            self._rows[runtime.id] = {"badge": badge, "word": word, "port": port,
                                      "port_role": port_role, "port_tip": port_tip,
                                      "uptime": uptime, "buttons": buttons, "cells": cells,
                                      "row": row, "buttons_frame": buttons_frame,
                                      "deps": dep_widgets, "wide_only": wide_only,
                                      "compact_only": compact_only}
        # Chaque ligne est sa propre grille : on aligne les colonnes en donnant à toutes la
        # largeur des boutons de la plus large (Hermes en a trois).
        self.update_idletasks()
        self._widest = max(sc(250), max((w["buttons_frame"].winfo_reqwidth()
                                         for w in self._rows.values()), default=0) + sc(8))
        self._apply_layout(force=True)

    def _pack_deps(self, row, runtime, line) -> list[dict]:
        """Serveurs liés : une sous-ligne en retrait chacun (pastille, nom, cible surveillée,
        temps de marche, CPU/RAM/VRAM). Surveillés seulement : aucun bouton."""
        out = []
        tip = i18n.t("dep.tooltip")
        for index, dep in enumerate(line["deps"]):
            grid_row = FIRST_DEP_ROW + index
            last = index == len(line["deps"]) - 1
            pady = (0, sc(6) if last else sc(1))
            left = tk.Frame(row, bg=T.CARD)
            left.grid(row=grid_row, column=1, sticky="ew", padx=(sc(4), 0), pady=pady)
            tk.Label(left, text="└", bg=T.CARD, fg=T.FAINT, font=font(9)).pack(side="left",
                                                                           padx=(sc(8), 0))
            dot = tk.Canvas(left, width=sc(14), height=sc(14), bg=T.CARD, highlightthickness=0)
            dot.pack(side="left", padx=(sc(2), sc(2)))
            label = pres.dep_label(dep)
            name = tk.Label(left, text=pres.truncate(label, 28), bg=T.CARD, fg=T.TEXT,
                            font=font(9), anchor="w")
            name.pack(side="left")
            word = tk.Label(left, text="", bg=T.CARD, fg=T.FAINT, font=font(8), anchor="w")
            word.pack(side="left", fill="x", expand=True, padx=(sc(6), 0))
            target = tk.Label(row, text=pres.dep_target(dep), bg=T.CARD, fg=T.FAINT,
                              font=font(9), anchor="w")
            target.grid(row=grid_row, column=2, sticky="w", padx=(sc(4), 0), pady=pady)
            full = f"{label} — {pres.dep_target(dep)}\n{tip}"
            for widget in (left, name, word, target):
                Tooltip(widget, full, wrap=380)
            uptime = tk.Label(row, text="", bg=T.CARD, fg=T.FAINT, font=font(9), anchor="w")
            uptime.grid(row=grid_row, column=UPTIME_COLUMN, sticky="w", padx=(sc(4), 0),
                        pady=pady)
            cells = []
            for column in METRIC_COLUMNS:
                cell = tk.Label(row, text=pres.DASH, bg=T.CARD, fg=T.FAINT, font=font(8),
                                anchor="w")
                cell.grid(row=grid_row, column=column, sticky="w", padx=(sc(4), 0), pady=pady)
                cells.append(cell)
            extra = tk.Label(row, text="", bg=T.CARD, fg=T.FAINT, font=font(8), anchor="w")
            extra.grid(row=grid_row, column=UPTIME_COLUMN, columnspan=BUTTONS_COLUMN - 2,
                       sticky="ew", padx=(sc(4), sc(8)), pady=pady)
            out.append({"dot": dot, "word": word, "uptime": uptime, "cells": cells,
                        "extra": extra, "wide_only": [uptime, *cells], "compact_only": [extra]})
        return out

    def _pack_items(self, row, runtime, line) -> None:
        """Puces sous la ligne : un élément = pastille + nom + son action principale."""
        strip = tk.Frame(row, bg=T.CARD)
        strip.grid(row=2, column=1, columnspan=BUTTONS_COLUMN, sticky="ew", padx=(sc(4), sc(8)),
                   pady=(0, sc(6)))
        # Les puces passent à la ligne (largeur estimée : nom + bouton) au lieu de déborder.
        metrics = tkfont.Font(font=font(8))
        # Largeur de la VUE (celle de la liste grandit avec son contenu : piège constaté).
        view_width = self.winfo_width() if self.winfo_width() > 100 else sc(860)
        available = view_width - sc(36) - sc(34) - sc(24)
        line_frame, used = None, available + 1
        for item in line["items"]:
            main = pres.primary_actions(item["actions"])[:1]
            label = pres.truncate(pres.one_line(item["label"]), 22)
            width = metrics.measure(label) + sc(40) + sum(
                max(sc(56), metrics.measure(i18n.t(str(a.get("label_key") or ""))) + sc(16))
                for a in main)
            if used + width > available:
                line_frame = tk.Frame(strip, bg=T.CARD)
                line_frame.pack(fill="x")
                used = 0
            used += width + sc(6)
            chip = tk.Frame(line_frame, bg=T.CARD_HI, highlightthickness=1,
                            highlightbackground=T.BORDER, highlightcolor=T.BORDER)
            chip.pack(side="left", padx=(0, sc(6)), pady=(0, sc(2)))
            dot = tk.Canvas(chip, width=sc(16), height=sc(16), bg=T.CARD_HI, highlightthickness=0)
            dot.pack(side="left", padx=(sc(4), 0))
            draw_badge(dot, sc(8), sc(8), sc(4), pres.item_state(item["phase"]), ring=T.CARD_HI)
            tk.Label(chip, text=label, bg=T.CARD_HI, fg=T.TEXT,
                     font=font(8), anchor="w").pack(side="left", padx=(sc(2), sc(4)))
            for action in main:
                button = Button(chip, i18n.t(str(action.get("label_key") or action.get("id"))),
                                lambda aid=action.get("id"), iid=item["id"], r=runtime:
                                self.app.run_action(r, aid, item_id=iid),
                                width=0, height=22, size=8, bold=False, radius=6,
                                fill=T.CARD, hover=T.CARD_HOVER, fg=T.TEXT, outline=T.BORDER,
                                parent_bg=T.CARD_HI)
                button.configure(width=max(sc(56), tkfont.Font(font=font(8)).measure(
                    i18n.t(str(action.get("label_key") or ""))) + sc(16)))
                button.set_enabled(bool(action.get("enabled", True)) and not runtime.busy)
                button.pack(side="left", padx=(0, sc(3)), pady=sc(2))
        if line["more"] and line_frame is not None:
            tk.Label(line_frame, text=f"+{line['more']}", bg=T.CARD, fg=T.FAINT,
                     font=font(8)).pack(side="left")
