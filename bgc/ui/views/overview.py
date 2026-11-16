"""Vue d'ensemble : une ligne par service (revue du 16 nov 2026, b-2 / e-1).

Pastille d'état colorée + mot, port, temps de marche, bouton principal (démarrer/arrêter)
et bouton web. Un clic sur le nom ouvre le détail. La vue est construite une fois puis
**repeinte** à chaque tick ; elle ne se reconstruit que si la liste des boutons change.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from bgc import i18n, sysinfo
from bgc import presentation as pres
from bgc.ui.common import ghost_button
from bgc.widgets import (Button, ScrollFrame, Tooltip, draw_badge, font, sc)
from bgc.theme import T


#: Colonnes des ressources (CPU, RAM, VRAM) puis celle des boutons.
METRIC_COLUMNS = (4, 5, 6)
BUTTONS_COLUMN = 7


class OverviewView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self._rows: dict[str, dict] = {}
        self._signature = None
        self._build()

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
        for column, key in ((1, "overview.col_service"), (2, "overview.col_port"),
                            (3, "overview.col_uptime")):
            tk.Label(self.header, text=i18n.t(key).upper(), bg=T.BG, fg=T.FAINT, font=font(8, True),
                     anchor="w").grid(row=0, column=column, sticky="w", padx=(sc(4), 0))
        tip = i18n.t("overview.metrics_tip")
        for column, key in zip(METRIC_COLUMNS, ("overview.col_cpu", "overview.col_ram",
                                                "overview.col_vram")):
            label = tk.Label(self.header, text=i18n.t(key).upper(), bg=T.BG, fg=T.FAINT,
                             font=font(8, True), anchor="w")
            label.grid(row=0, column=column, sticky="w", padx=(sc(4), 0))
            Tooltip(label, tip, wrap=360)

        scroll = ScrollFrame(self, bg=T.BG)
        scroll.pack(fill="both", expand=True, padx=sc(18), pady=(0, sc(12)))
        self.list = scroll.inner

    @staticmethod
    def _configure_columns(frame) -> None:
        frame.columnconfigure(0, minsize=sc(34))
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, minsize=sc(64))
        frame.columnconfigure(3, minsize=sc(90))
        for column in METRIC_COLUMNS:          # CPU, RAM, VRAM (compactes)
            frame.columnconfigure(column, minsize=sc(62))
        frame.columnconfigure(BUTTONS_COLUMN, minsize=sc(250))

    # --- données d'une ligne ----------------------------------------------
    def _line(self, runtime) -> dict:
        snapshot = runtime.snapshot_data or {}
        actions = pres.adjust_actions(snapshot.get("actions") or [], snapshot)
        if not runtime.enabled:
            actions = []
        primary = pres.primary_actions(actions)
        web = pres.web_action(actions, runtime.server.get("actions") or [],
                              runtime.custom_states) if runtime.enabled else None
        port = pres.main_port(runtime.server)
        uptime = pres.uptime_seconds(runtime.snapshot_data, runtime.stats)
        word = runtime.status_text()
        items = (pres.items_of(snapshot) if runtime.enabled and pres.has_items(snapshot)
                 else [])
        if runtime.enabled and pres.has_items(snapshot):
            running, total = pres.item_counts(snapshot)
            if total:   # « 0/0 en marche » n'apprend rien (Docker absent, aucune distribution)
                word = f"{word} · " + i18n.t("items.count", running=running, total=total)
        return {"state": runtime.state, "word": word,
                "port": str(port) if port else "—",
                "uptime": sysinfo.fmt_uptime(uptime) if uptime else "—",
                "primary": primary, "web": web, "items": items[:self.MAX_ITEMS],
                "more": max(0, len(items) - self.MAX_ITEMS)}

    #: Éléments montrés sous une ligne (au-delà : « +n », le détail de la tuile les a tous).
    MAX_ITEMS = 8

    @staticmethod
    def _items_signature(line) -> tuple:
        return tuple((i["id"], i["label"], i["phase"],
                      tuple((a.get("id"), a.get("label_key"), bool(a.get("enabled", True)))
                            for a in pres.primary_actions(i["actions"])))
                     for i in line["items"]) + (line["more"],)

    def paint(self) -> None:
        lines = [(runtime, self._line(runtime)) for runtime in self.app.runtimes]
        signature = tuple(
            (runtime.id, runtime.label,
             tuple((a["id"], a.get("label_key"), a.get("style")) for a in line["primary"]),
             (line["web"] or {}).get("id"), self._items_signature(line), runtime.busy)
            for runtime, line in lines)
        if signature != self._signature:
            self._rebuild(lines)
            self._signature = signature
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
            widgets["word"].configure(text=pres.truncate(pres.one_line(line["word"]), 70),
                                      fg=colour if line["state"] not in ("idle", "paused")
                                      else T.MUTED)
            widgets["port"].configure(text=line["port"])
            widgets["uptime"].configure(text=line["uptime"])
            metrics = pres.metrics_for(self.app, runtime) if runtime.enabled else None
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
        sampler = getattr(self.app, "metrics", None)
        gpu = pres.gpu_text(sampler.gpu_total() if sampler else None)
        if self.gpu.cget("text") != gpu:
            self.gpu.configure(text=gpu)
        active = sum(1 for r in self.app.runtimes if r.enabled)
        self.summary.configure(text=i18n.t("overview.summary", running=running,
                                           total=active))

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
            port = tk.Label(row, text="", bg=T.CARD, fg=T.MUTED, font=font(10), anchor="w")
            port.grid(row=0, column=2, rowspan=2, sticky="w", padx=(sc(4), 0))
            uptime = tk.Label(row, text="", bg=T.CARD, fg=T.MUTED, font=font(10), anchor="w")
            uptime.grid(row=0, column=3, rowspan=2, sticky="w", padx=(sc(4), 0))
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
            self._rows[runtime.id] = {"badge": badge, "word": word, "port": port,
                                      "uptime": uptime, "buttons": buttons, "cells": cells,
                                      "row": row, "buttons_frame": buttons_frame}
        # Chaque ligne est sa propre grille : on aligne les colonnes en donnant à toutes la
        # largeur des boutons de la plus large (Hermes en a trois).
        self.update_idletasks()
        widest = max((w["buttons_frame"].winfo_reqwidth() for w in self._rows.values()),
                     default=0) + sc(8)
        for frame in [self.header] + [w["row"] for w in self._rows.values()]:
            frame.columnconfigure(BUTTONS_COLUMN, minsize=max(sc(250), widest))

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
