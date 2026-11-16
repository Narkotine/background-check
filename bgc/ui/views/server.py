"""Vue détail d'une tuile : état, chaîne, lignes, boutons, interrupteurs, journal."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from bgc import i18n
from bgc import presentation as pres
from bgc import sysinfo
from bgc.widgets import (Button, Card, ScrollFrame, Toggle, Tooltip, draw_badge, fit_text, font,
                         hint_for, label_with_info, sc)
from bgc.theme import T
from bgc.core.runtime import ServerRuntime
from bgc.ui.common import ROW_COLOURS, ghost_button
from bgc.ui.logpanel import LogPanel


# ── panneau d'un serveur ──────────────────────────────────────────────────────
class ServerView(tk.Frame):
    def __init__(self, master, app, runtime: ServerRuntime):
        super().__init__(master, bg=T.BG)
        self.app, self.runtime = app, runtime
        self._buttons: dict[str, Button] = {}
        self._action_signature = ()
        self._toggle_keys: set | None = None
        self._chain_sig = None
        self._build()

    def _build(self) -> None:
        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=sc(18), pady=(sc(14), sc(10)))
        left = tk.Frame(head, bg=T.BG)
        left.pack(side="left", fill="x", expand=True)
        title = tk.Frame(left, bg=T.BG)
        title.pack(fill="x")
        tk.Label(title, text=self.runtime.label, bg=T.BG, fg=T.TEXT, font=font(20, True),
                 anchor="w").pack(side="left")
        type_label = i18n.t(self.runtime.type.label_key) if self.runtime.type else "?"
        summary = None
        if self.runtime.type:
            try:
                summary = self.runtime.type.summary(self.runtime.server.get("params") or {})
            except Exception:  # un plugin fautif ne doit pas casser la vue
                summary = None
        sub = label_with_info(left, type_label,
                              i18n.t(summary[0], **summary[1]) if summary else "", bg=T.BG,
                              fg=T.MUTED)
        sub.pack(fill="x")
        ghost_button(head, i18n.t("btn.settings"),
                     lambda: self.app.show_server_settings(self.runtime.id),
                     height=32, size=10).pack(side="right")

        # Journal ancré en bas (hauteur fixe) ; le reste défile au-dessus — avant, le journal
        # n'avait que la place restante, soit quelques pixels sur la tuile Hermes.
        controller = self.runtime.controller
        console = getattr(controller, "output_lines", None)
        self.log_panel = LogPanel(self, self.app, lambda: self.runtime.log_lines,
                                  console=console if callable(console) else None,
                                  report_title=self.runtime.label, docked=True)
        self.log_panel.pack(side="bottom", fill="x", padx=sc(18), pady=(sc(4), sc(12)))
        self.upper = ScrollFrame(self, bg=T.BG)
        self.upper.pack(fill="both", expand=True)
        body = self.upper.inner

        self.status_card = Card(body, height=96, outer_bg=T.BG)
        self.status_card.pack(fill="x", padx=sc(18), pady=(0, sc(10)))
        # Ressources (CPU / RAM / VRAM + courbes des 5 dernières minutes) : packée seulement
        # quand le service a des processus mesurés.
        self.metrics_card = Card(body, height=104, outer_bg=T.BG)
        self.metrics_card.bind("<Configure>", lambda _e: setattr(self, "_metrics_sig", None),
                               add="+")
        self._metrics_sig = None
        self._item_metrics: dict[str, tk.Label] = {}
        # Chaîne Hermes (ou de tout type qui fournit `extra.chain`) : affichée seulement si
        # le snapshot en porte une — packée juste avant les lignes.
        self.chain_card = Card(body, height=sc(70), outer_bg=T.BG)
        self.rows_card = Card(body, height=sc(120), outer_bg=T.BG)
        self.rows_card.pack(fill="x", padx=sc(18), pady=(0, sc(12)))
        # Infobulle par ligne : texte complet d'une valeur coupée (ou détail brut du plugin).
        self._row_tips: list[str] = []
        self._row_hover = -1
        self._row_tooltip = Tooltip(self.rows_card, self._row_tip_text, wrap=520)
        self.rows_card.bind("<Motion>", self._row_motion, add="+")
        self.rows_card.bind("<Leave>", lambda _e: setattr(self, "_row_hover", -1), add="+")
        # Éléments (conteneurs, distributions…) : une ligne par élément, avec ses boutons.
        # Packé seulement si le snapshot publie `items` (modèle « liste d'items »).
        self.items_frame = tk.Frame(body, bg=T.BG)
        self._items_sig = None

        self.buttons_row = tk.Frame(body, bg=T.BG)
        self.buttons_row.pack(fill="x", padx=sc(18))

        self.toggles_frame = tk.Frame(body, bg=T.BG)
        self.toggles_frame.pack(fill="x", padx=sc(18), pady=(sc(10), sc(10)))
        self._toggle_widgets: dict[str, Toggle] = {}
        for toggle in (self.runtime.type.toggles if self.runtime.type else []):
            widget = Toggle(self.toggles_frame, i18n.t(toggle.label_key),
                            bool(self.runtime.server.get("options", {}).get(toggle.key)),
                            command=lambda value, key=toggle.key: self.runtime.set_option(key, value),
                            hint=hint_for(toggle.label_key))
            widget.pack(fill="x", pady=(sc(2), 0))
            self._toggle_widgets[toggle.key] = widget

    def show_log(self, tab: str = "events") -> None:
        self.log_panel.open(tab)

    # --- peinture ---------------------------------------------------------
    def paint(self) -> None:
        snapshot = self.runtime.snapshot_data
        if snapshot is None:
            self._paint_loading()
            return
        self._paint_status(snapshot)
        self._paint_metrics(snapshot)
        self._paint_chain(snapshot)
        self._paint_rows(snapshot)
        self._paint_items(snapshot)
        self._paint_actions(snapshot)
        self._paint_toggles(snapshot)
        self.log_panel.paint()

    def _paint_loading(self) -> None:
        canvas = self.status_card
        canvas.delete("content")
        canvas.create_text(sc(16), sc(30), text=i18n.t("status.loading"), anchor="w", fill=T.MUTED,
                           font=font(15, True), tags="content")
        canvas.create_text(sc(16), sc(62), text=i18n.t("status.first_probe"), anchor="w",
                           fill=T.FAINT, font=font(9), tags="content")

    def _paint_status(self, snapshot: dict) -> None:
        canvas = self.status_card
        canvas.delete("content")
        state = self.runtime.state
        colour = T.STATE_COLOURS.get(state, T.FAINT)
        extra = snapshot.get("extra") or {}
        width = canvas.winfo_width()
        if width <= 4:
            width = sc(760)
        room = int(width - sc(60))
        # Chaque texte du bandeau tient sur une ligne, coupé à la largeur (jamais de débord).
        label = fit_text(pres.one_line(self.runtime.status_text()), font(15, True), room)[0]

        cx, cy = sc(26), sc(38)
        draw_badge(canvas, cx, cy, sc(10), state, ring=T.CARD, tags=("content",))
        canvas.create_text(cx + sc(22), cy - sc(8), text=label, anchor="w",
                           fill=colour if state not in ("idle", "paused") else T.MUTED,
                           font=font(15, True), tags="content")
        url = snapshot.get("url") or ""
        if url:
            # Une URL publique n'est montrée « active » que si elle mène quelque part.
            active = extra.get("url_active", snapshot.get("phase") == "running")
            text = url if active else i18n.t("status.url_inactive", url=url)
            text = fit_text(pres.one_line(text), font(9), room)[0]
            canvas.create_text(cx + sc(22), cy + sc(12), text=text, anchor="w",
                               fill=T.MUTED if active else T.FAINT, font=font(9), tags="content")

        port = (self.runtime.server.get("params") or {}).get("port", "?")
        meta = snapshot.get("meta") or {}
        info = meta.get("info") or {}
        parts = []
        if meta.get("pid"):
            parts.append(i18n.t("row.pid", pid=meta["pid"]))
        if info.get("rss_mb"):
            parts.append(i18n.t("row.ram", mb=f"{info['rss_mb']:.0f}"))
        uptime = pres.uptime_seconds(snapshot, self.runtime.stats)
        if uptime:
            parts.append(i18n.t("row.since", uptime=sysinfo.fmt_uptime(uptime)))
        if not parts:
            if extra.get("paused"):
                parts.append(i18n.t("status.paused_hint"))
            else:
                line = meta.get("line")
                if line:  # un type sans process (distro WSL…) fournit sa propre ligne d'état
                    parts.append(i18n.t(line["key"], **line.get("params", {})))
                else:
                    parts.append(i18n.t("status.no_listener", port=port))
        if self.runtime.stats.get("falls"):
            parts.append(i18n.t("row.falls", n=self.runtime.stats["falls"]))
        summary = fit_text(pres.one_line("   ·   ".join(parts)), font(9), int(width - sc(28)))[0]
        canvas.create_text(sc(12), sc(72), text=summary, anchor="w", fill=T.FAINT,
                           font=font(9), tags="content")

    def _paint_metrics(self, snapshot: dict) -> None:
        """Carte « Ressources » : valeur courante + courbe des 5 dernières minutes."""
        canvas = self.metrics_card
        metrics = (pres.metrics_for(self.app, self.runtime)
                   if self.runtime.enabled and not (snapshot.get("extra") or {}).get("paused")
                   else None)
        if not metrics:
            if canvas.winfo_manager():
                canvas.pack_forget()
            self._metrics_sig = None
            return
        if not canvas.winfo_manager():
            canvas.pack(fill="x", padx=sc(18), pady=(0, sc(10)), after=self.status_card)
        width = canvas.winfo_width()
        if width <= 4:
            width = sc(760)
        signature = (metrics.get("at"), width)
        if signature == self._metrics_sig:
            return
        self._metrics_sig = signature
        sampler = getattr(self.app, "metrics", None)
        history = sampler.history(self.runtime.id) if sampler else []
        canvas.delete("content")
        canvas.create_text(sc(14), sc(16), text=i18n.t("metrics.title").upper(), anchor="w",
                           fill=T.MUTED, font=font(8, True), tags="content")
        canvas.create_text(width - sc(14), sc(16), anchor="e", fill=T.FAINT, font=font(8),
                           text=i18n.t("metrics.window", n=metrics.get("procs", 1)),
                           tags="content")
        panels = (("overview.col_cpu", pres.fmt_cpu(metrics.get("cpu")), 1, 5.0),
                  ("overview.col_ram", pres.fmt_mb(metrics.get("ram_mb")), 2, 64.0),
                  ("overview.col_vram", pres.fmt_mb(metrics.get("vram_mb")), 3, 64.0))
        gap = sc(14)
        panel_w = (width - gap * 4) / 3
        for index, (key, value, column, floor) in enumerate(panels):
            x0 = gap + index * (panel_w + gap)
            canvas.create_text(x0, sc(38), text=i18n.t(key), anchor="w", fill=T.FAINT,
                               font=font(8, True), tags="content")
            canvas.create_text(x0 + sc(44), sc(38), text=value, anchor="w", fill=T.TEXT,
                               font=font(12, True), tags="content")
            series = [h[column] for h in history if h[column] is not None]
            self._sparkline(canvas, x0, sc(54), panel_w, sc(38), series, floor)

    @staticmethod
    def _sparkline(canvas, x0, y0, width, height, series, floor) -> None:
        """Courbe minimale : ligne de base + tracé (échelle = max de la fenêtre)."""
        canvas.create_line(x0, y0 + height, x0 + width, y0 + height, fill=T.BORDER,
                           tags="content")
        if len(series) < 2:
            return
        top = max(floor, max(series) * 1.15)
        step = width / max(1, len(series) - 1)
        points = []
        for i, value in enumerate(series):
            points += [x0 + i * step, y0 + height - (value / top) * height]
        canvas.create_line(*points, fill=T.ACCENT, width=sc(2) if sc(2) > 1 else 2,
                           smooth=False, tags="content")

    def _paint_chain(self, snapshot: dict) -> None:
        """Chaîne linéaire « Téléphone → Tunnel → Dashboard → API » + ce qui manque."""
        extra = snapshot.get("extra") or {}
        chain = extra.get("chain")
        lines = pres.chain_lines(chain)
        canvas = self.chain_card
        if not lines or extra.get("paused"):
            if canvas.winfo_manager():
                canvas.pack_forget()
            self._chain_sig = None
            return
        if not canvas.winfo_manager():
            canvas.pack(fill="x", padx=sc(18), pady=(0, sc(10)), before=self.rows_card)
        missing = chain.get("missing") or extra.get("chain_missing") or []
        width = canvas.winfo_width()
        if width <= 4:
            width = sc(760)
        signature = (repr(chain), repr(missing), width)
        if signature == self._chain_sig:
            return
        self._chain_sig = signature
        canvas.delete("content")
        y = sc(20)
        title_w = sc(150)
        for line in lines:
            used = bool(line.get("used"))
            links = line.get("links") or []
            title = i18n.t(str(line.get("title_key") or ""))
            canvas.create_text(sc(14), y, text=title, anchor="w",
                               fill=T.TEXT if used else T.MUTED, font=font(9, used), tags="content")
            if used and line.get("note_key"):
                canvas.create_text(sc(14), y + sc(14), text=i18n.t(line["note_key"]),
                                   anchor="w", fill=T.ACCENT, font=font(8), tags="content")
            x = sc(14) + title_w
            for index, item in enumerate(links):
                if index:
                    canvas.create_text(x, y, text="→", anchor="w", fill=T.FAINT, font=font(10),
                                       tags="content")
                    x += sc(20)
                draw_badge(canvas, x + sc(5), y, sc(5), item.get("state", "idle"), ring=T.CARD,
                           tags=("content",))
                text = i18n.t(item["label_key"], **item.get("label_params", {}))
                value = i18n.t(item["value_key"], **item.get("value_params", {}))
                item_id = canvas.create_text(x + sc(15), y, text=f"{text} · {value}", anchor="w",
                                             fill=T.TEXT, font=font(9), tags="content")
                bbox = canvas.bbox(item_id)
                x = (bbox[2] if bbox else x + sc(120)) + sc(8)
            y += sc(34)
        # Ce qui manque, sous les deux lignes (le texte long passe à la ligne).
        top = y - sc(8)
        for key, params in missing:
            item_id = canvas.create_text(sc(14), top, text="⚠ " + i18n.t(key, **params),
                                         anchor="nw", fill=T.AMBER, font=font(9), tags="content",
                                         width=width - sc(28))
            bbox = canvas.bbox(item_id)
            top = (bbox[3] if bbox else top + sc(16)) + sc(4)
        height = (top + sc(8)) if missing else (y - sc(6))
        if abs(canvas.winfo_height() - height) > 1:
            canvas.configure(height=height)

    def _paint_rows(self, snapshot: dict) -> None:
        """Une ligne = pastille, libellé, valeur **sur une seule ligne**.

        Une valeur longue (sortie brute d'une commande, chemin…) est aplatie puis coupée avec
        « … » à la largeur disponible ; le texte complet (ou `row["tooltip"]`) s'affiche au
        survol. Avant, une sortie multi-ligne de wsl.exe débordait sur les lignes voisines.
        """
        rows = snapshot.get("rows") or []
        canvas = self.rows_card
        canvas.delete("content")
        height = sc(12) + max(1, len(rows)) * sc(28)
        if canvas.winfo_height() != height:
            canvas.configure(height=height)
        width = canvas.winfo_width()
        if width <= 4:
            width = sc(760)
        self._row_tips = []
        for index, row in enumerate(rows):
            y = sc(16) + index * sc(28)
            colour = ROW_COLOURS.get(row.get("status"), T.FAINT)
            canvas.create_oval(sc(14), y - sc(4), sc(22), y + sc(4), fill=colour, outline="",
                               tags="content")
            label = pres.one_line(i18n.t(row["label_key"], **row.get("label_params", {})))
            label, label_cut = fit_text(label, font(10), int(width * 0.45))
            label_id = canvas.create_text(sc(34), y, text=label, anchor="w", fill=T.TEXT,
                                          font=font(10), tags="content")
            bbox = canvas.bbox(label_id)
            left = (bbox[2] if bbox else sc(200)) + sc(24)
            value = pres.one_line(i18n.t(row["value_key"], **row.get("value_params", {})))
            shown, cut = fit_text(value, font(10), int(width - sc(16) - left))
            canvas.create_text(width - sc(16), y, text=shown, anchor="e", fill=T.MUTED,
                               font=font(10), tags="content")
            extra = str(row.get("tooltip") or "").strip()
            tip = extra or (value if (cut or label_cut) else "")
            self._row_tips.append(tip)

    def _row_tip_text(self) -> str:
        index = getattr(self, "_row_hover", -1)
        tips = getattr(self, "_row_tips", [])
        return tips[index] if 0 <= index < len(tips) else ""

    def _row_motion(self, event) -> None:
        index = int((event.y - sc(2)) // sc(28))
        if index != getattr(self, "_row_hover", -1):
            self._row_hover = index
            self._row_tooltip.hide()
            if self._row_tip_text():
                self._row_tooltip._schedule()

    def _paint_items(self, snapshot: dict) -> None:
        """Une ligne par élément : pastille, nom, détail, boutons de l'élément."""
        frame = self.items_frame
        if not pres.has_items(snapshot) or (snapshot.get("extra") or {}).get("paused"):
            if frame.winfo_manager():
                frame.pack_forget()
            self._items_sig = None
            return
        if not frame.winfo_manager():
            frame.pack(fill="x", padx=sc(18), pady=(0, sc(12)), before=self.buttons_row)
        items = pres.items_of(snapshot)
        busy = self.runtime.busy
        signature = (busy, tuple(
            (i["id"], i["label"], i["phase"], i["detail_key"], repr(i["detail_params"]),
             tuple((a.get("id"), a.get("label_key"), bool(a.get("enabled", True)), a.get("style"))
                   for a in i["actions"]))
            for i in items))
        if signature == self._items_sig:
            self._paint_item_metrics()
            return
        self._items_sig = signature
        for child in frame.winfo_children():
            child.destroy()
        self._item_metrics = {}
        title = tk.Frame(frame, bg=T.BG)
        title.pack(fill="x", pady=(0, sc(4)))
        running, total = pres.item_counts(snapshot)
        tk.Label(title, text=i18n.t(str(snapshot.get("items_title_key") or "items.title")).upper(),
                 bg=T.BG, fg=T.MUTED, font=font(9, True), anchor="w").pack(side="left")
        tk.Label(title, text=i18n.t("items.count", running=running, total=total), bg=T.BG,
                 fg=T.FAINT, font=font(9), anchor="w").pack(side="left", padx=(sc(10), 0))
        if not items:
            tk.Label(frame, text=i18n.t(str(snapshot.get("items_empty_key") or "items.empty")),
                     bg=T.BG, fg=T.FAINT, font=font(9), anchor="w", justify="left",
                     wraplength=sc(700)).pack(fill="x", pady=(0, sc(4)))
            return
        for item in items:
            row = tk.Frame(frame, bg=T.CARD, highlightthickness=1, highlightbackground=T.BORDER,
                           highlightcolor=T.BORDER)
            row.pack(fill="x", pady=(0, sc(4)))
            badge = tk.Canvas(row, width=sc(26), height=sc(26), bg=T.CARD, highlightthickness=0)
            badge.pack(side="left", padx=(sc(8), sc(2)), pady=sc(6))
            draw_badge(badge, sc(13), sc(13), sc(6), pres.item_state(item["phase"]), ring=T.CARD)
            text = tk.Frame(row, bg=T.CARD)
            text.pack(side="left", fill="x", expand=True, padx=(sc(4), sc(8)))
            label = pres.one_line(item["label"])
            name = tk.Label(text, text=pres.truncate(label, 48), bg=T.CARD, fg=T.TEXT,
                            font=font(10, True), anchor="w")
            name.pack(fill="x")
            if len(label) > 48:
                Tooltip(name, label)
            detail = pres.one_line(i18n.t(item["detail_key"], **item["detail_params"])
                                   if item["detail_key"] else "")
            if detail:
                small = tk.Label(text, text=pres.truncate(detail, 90), bg=T.CARD, fg=T.MUTED,
                                 font=font(8), anchor="w")
                small.pack(fill="x")
                if len(detail) > 90:
                    Tooltip(small, detail, wrap=520)
            buttons = tk.Frame(row, bg=T.CARD)
            buttons.pack(side="right", padx=(0, sc(8)))
            usage = tk.Label(row, text="", bg=T.CARD, fg=T.FAINT, font=font(8), anchor="e")
            usage.pack(side="right", padx=(0, sc(8)))
            self._item_metrics[item["id"]] = usage
            for action in reversed(item["actions"]):
                style = action.get("style", "ghost")
                if style == "primary":
                    fill, hover, fg, outline = T.ACCENT_DARK, T.ACCENT_HI, T.ON_ACCENT, ""
                elif style == "danger":
                    fill, hover, fg, outline = T.DANGER, T.DANGER_HI, T.ON_ACCENT, ""
                else:
                    fill, hover, fg, outline = T.CARD_HI, T.CARD_HOVER, T.TEXT, T.BORDER
                button = Button(buttons, i18n.t(str(action.get("label_key") or action.get("id"))),
                                lambda aid=action.get("id"), iid=item["id"], then=action.get("then"):
                                self._item_clicked(iid, aid, then),
                                height=28, fill=fill, hover=hover, fg=fg, radius=8, size=9,
                                bold=False, outline=outline, parent_bg=T.CARD)
                button.set_enabled(bool(action.get("enabled", True)) and not busy)
                button.pack(side="right", padx=(sc(4), 0))

        self._paint_item_metrics()

    def _paint_item_metrics(self) -> None:
        """CPU / RAM / VRAM de chaque élément (mis à jour sans reconstruire les lignes)."""
        for item_id, label in self._item_metrics.items():
            text = pres.metrics_text(pres.metrics_for(self.app, self.runtime, item_id))
            if label.cget("text") != text:
                label.configure(text=text)

    def _item_clicked(self, item_id: str, action_id: str, then: str | None) -> None:
        self.app.run_action(self.runtime, action_id, item_id=item_id)
        if then == "console":  # « Journaux » d'un conteneur : la sortie arrive dans la console
            self.show_log("console")

    def _paint_actions(self, snapshot: dict) -> None:
        # Règles communes (presentation.adjust_actions) : « Navigateur » et « Copier l'URL »
        # grisés quand le service ne tourne pas ; « Copier l'URL (publique) » selon l'adresse.
        actions = pres.adjust_actions(snapshot.get("actions") or [], snapshot)
        # Actions sur mesure définies par l'utilisateur pour CETTE tuile : même rangée,
        # même bouton, même chemin de clic (`custom:<n>` traité par l'application).
        for index, custom in enumerate(self.runtime.server.get("actions") or []):
            label = str(custom.get("label") or "").strip()
            if label:
                # `enabled` vient de la sonde de l'interface web (grisé si elle ne répond
                # pas) : un bouton qui ouvre une page morte ne doit pas avoir l'air prêt.
                actions.append({"id": f"custom:{index}", "style": "ghost",
                                "label_key": label,
                                "enabled": self.runtime.custom_states.get(index, True)})
        # La largeur suit la fenêtre redimensionnée : les boutons restent alignés sur les cartes.
        # Au-delà de ce qui tient sur une ligne, ils passent à la ligne suivante plutôt que
        # d'être coupés au bord (constaté avec 7 boutons sur la tuile LlamaGUI).
        total = self.buttons_row.winfo_width()
        if total <= 4:
            total = sc(760)
        signature = tuple((a["id"], a["label_key"], a.get("enabled"), a.get("accent"))
                          for a in actions) + (total,)
        if signature != self._action_signature:
            for child in self.buttons_row.winfo_children():
                child.destroy()
            self._buttons.clear()
            # Chaque bouton reçoit la largeur de son libellé (mesuré), et une ligne se ferme
            # dès qu'il n'y a plus la place : un libellé long n'est plus coupé au bord.
            metrics = tkfont.Font(font=font(11, True))
            gap = sc(8)
            rows: list[list] = [[]]
            used = 0
            for action in actions:
                label = i18n.t(action["label_key"])
                width = max(sc(110), metrics.measure(label) + sc(34))
                need = width + (gap if rows[-1] else 0)
                if rows[-1] and used + need > total:
                    rows.append([(action, label, width)])
                    used = width
                else:
                    rows[-1].append((action, label, width))
                    used += need
            for chunk in rows:
                row = tk.Frame(self.buttons_row, bg=T.BG)
                row.pack(fill="x", pady=(0, sc(6)))
                for action, label, width in chunk:
                    style = action.get("style", "ghost")
                    if style == "primary":
                        fill, hover, fg, outline = T.ACCENT_DARK, T.ACCENT_HI, T.ON_ACCENT, ""
                    elif style == "danger":
                        fill, hover, fg, outline = T.DANGER, T.DANGER_HI, T.ON_ACCENT, ""
                    else:
                        fill, hover, fg, outline = T.CARD_HI, T.CARD_HOVER, T.TEXT, T.BORDER
                    if action.get("accent") and style == "ghost":
                        outline = T.GREEN  # l'app liée est ouverte (ex. Hermes Desktop)
                    button = Button(row, label,
                                    lambda aid=action["id"]: self.app.run_action(self.runtime, aid),
                                    width=0, height=42, fill=fill, hover=hover, fg=fg, radius=10,
                                    outline=outline)
                    button.configure(width=width)
                    button.pack(side="left", padx=(0, gap))
                    self._buttons[action["id"]] = button
            self._action_signature = signature
        for action in actions:
            button = self._buttons.get(action["id"])
            if not button:
                continue
            button.set_text(i18n.t(action["label_key"]))
            button.set_enabled(bool(action.get("enabled", True)) and not self.runtime.busy)

    def _paint_toggles(self, snapshot: dict) -> None:
        """Les interrupteurs affichés suivent le snapshot, pas la seule déclaration du type.

        Un serveur en **observation pure** (port/URL sans commande) ne montre aucun
        interrupteur de lancement/arrêt : un réglage sans effet est un piège (9 nov 2026).
        """
        keys = {t.get("key") for t in (snapshot.get("toggles") or [])}
        if keys == self._toggle_keys:
            return
        self._toggle_keys = keys
        for widget in self._toggle_widgets.values():
            widget.pack_forget()
        visible = 0
        for key, widget in self._toggle_widgets.items():
            if key in keys:
                widget.pack(fill="x", pady=(sc(2), 0))
                visible += 1
        if visible:
            if not self.toggles_frame.winfo_manager():
                self.toggles_frame.pack(fill="x", padx=sc(18), pady=(sc(10), sc(10)))
        elif self.toggles_frame.winfo_manager():
            self.toggles_frame.pack_forget()
