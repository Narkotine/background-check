"""Rail de tuiles (colonne de gauche) : sélection, glisser-déposer, recherche."""

from __future__ import annotations

import tkinter as tk

from bgc import i18n, widgets
from bgc.widgets import (ScrollFrame, Tile, Tooltip, font, sc)
from bgc.theme import T
from bgc.ui.common import TYPE_COLOURS, TYPE_GLYPHS, TYPE_ICONS


# ── rail ──────────────────────────────────────────────────────────────────────
class _RailDrag:
    """Relie le geste d'une tuile au rail (glisser pour réordonner)."""

    def __init__(self, rail):
        self.rail = rail

    def start(self, tile, event):
        self.rail.drag_start(tile, event)

    def motion(self, tile, event):
        self.rail.drag_motion(tile, event)

    def end(self, tile, event):
        self.rail.drag_end(tile, event)


class Rail(tk.Frame):
    """Colonne de tuiles : Vue d'ensemble, une tuile par serveur, Ajouter, Réglages.

    * pastille d'état colorée par tuile (vert / ambre / rouge / gris / pause) ;
    * mode **compact** (icônes seules, libellé en infobulle) : `general.rail_compact` ;
    * au-delà de `SEARCH_FROM` tuiles, un champ de recherche filtre la colonne (Ctrl+K).
    """

    SEARCH_FROM = 8
    WIDTH, WIDTH_COMPACT = 96, 68

    def __init__(self, master, app):
        super().__init__(master, bg=T.RAIL, width=sc(self.width_units(app)))
        self.app = app
        self.pack_propagate(False)
        self.tiles: dict[str, Tile] = {}
        self.drag = _RailDrag(self)
        self.scroll: ScrollFrame | None = None
        self._drop_indicator = None
        self._drop_index: int | None = None
        self.search_var: tk.StringVar | None = None
        self.search_entry: tk.Entry | None = None
        self._build()

    @classmethod
    def width_units(cls, app) -> int:
        return cls.WIDTH_COMPACT if app.rail_compact else cls.WIDTH

    def _build(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        self.tiles.clear()
        compact = self.app.rail_compact
        self.search_var = None
        self.search_entry = None
        if len(self.app.runtimes) > self.SEARCH_FROM:
            self._build_search()
        # Zone haute défilante : avec 6+ serveurs et une taille d'interface agrandie, les
        # tuiles débordaient sous le bouton Réglages (constaté le 7 nov 2026). Une barre
        # fine apparaît à droite dès que la colonne ne tient plus (9 nov 2026).
        column = tk.Frame(self, bg=T.RAIL)
        column.pack(side="top", fill="both", expand=True)
        scroll = ScrollFrame(column, bg=T.RAIL)
        scroll.pack(side="top", fill="both", expand=True)
        # La barre fine est POSÉE par-dessus la colonne (place), dans la marge droite du rail,
        # au lieu d'être packée à côté de la zone défilante : packée, elle lui volait 7 px et
        # les tuiles se retrouvaient décalées de ~4 px vers la gauche par rapport à la tuile
        # Réglages du bas, qui est centrée sur toute la largeur du rail (constaté le 9 nov 2026).
        self.scrollbar = widgets.ThinBar(column, scroll.canvas, bg=T.RAIL)
        self.scrollbar.place(relx=1.0, y=0, anchor="ne", relheight=1.0)
        scroll.on_scroll = self.scrollbar.redraw
        scroll.canvas.bind("<Configure>", lambda _e: self.scrollbar.redraw(), add="+")
        self.scroll = scroll
        # Indicateur de dépôt : un widget placé AU-DESSUS de la zone défilante (un trait
        # dessiné sur le canvas serait caché par la fenêtre interne, toujours au premier plan).
        self._drop_indicator = tk.Frame(column, bg=T.ACCENT, height=sc(3))
        top = scroll.inner
        if self.app.runtimes:
            overview = Tile(top, "▦", i18n.t("rail.overview"), self.app.show_overview,
                            accent=T.ACCENT, parent_bg=T.RAIL, compact=compact,
                            tip=lambda: i18n.t("rail.tip_overview"))
            overview.pack(pady=(sc(4), sc(2)))
            self.tiles["__overview__"] = overview
        for runtime in self.app.runtimes:
            glyph = TYPE_GLYPHS.get(runtime.server.get("type", ""), "?")
            colour = TYPE_COLOURS.get(runtime.server.get("type", ""), T.ACCENT)
            icon_img = widgets.load_icon(str(runtime.server.get("icon") or ""),
                                         sc(30 if compact else 44))
            tile = Tile(top, glyph, runtime.label,
                        lambda sid=runtime.id: self.app.select_server(sid),
                        selected=(self.app.selected_id == runtime.id), accent=colour,
                        parent_bg=T.RAIL,
                        icon_kind=TYPE_ICONS.get(runtime.server.get("type", "")),
                        icon_img=icon_img, drag=self.drag, compact=compact,
                        state=runtime.state, tip=runtime.status_text)
            tile.runtime_id = runtime.id  # le glisser-déposer retrouve la tuile par son serveur
            tile.pack(pady=sc(2))
            self.tiles[runtime.id] = tile
        add = Tile(top, "＋", i18n.t("rail.add"), self.app.show_add, accent=T.ACCENT,
                   parent_bg=T.RAIL, compact=compact, tip=lambda: i18n.t("rail.tip_add"))
        add.pack(side="top", pady=(sc(4), sc(10)))
        self.tiles["__add__"] = add
        settings_tile = Tile(self, "⚙", i18n.t("rail.settings"), self.app.show_general,
                             accent=T.MUTED, parent_bg=T.RAIL, compact=compact,
                             tip=lambda: i18n.t("rail.tip_settings"))
        settings_tile.pack(side="bottom", pady=(0, sc(10)))
        self.tiles["__settings__"] = settings_tile

    def _build_search(self) -> None:
        """Champ de recherche en tête du rail (au-delà de 8 tuiles) ; Ctrl+K y mène."""
        wrap = tk.Frame(self, bg=T.BORDER)
        wrap.pack(side="top", fill="x", padx=sc(6), pady=(sc(6), sc(2)))
        self.search_var = tk.StringVar()
        entry = tk.Entry(wrap, textvariable=self.search_var, bg=T.CARD, fg=T.TEXT, bd=0,
                         highlightthickness=0, insertbackground=T.TEXT, font=font(9), width=6)
        entry.pack(fill="x", padx=1, pady=1, ipady=sc(3), ipadx=sc(3))
        Tooltip(entry, lambda: i18n.t("rail.search_tip"))
        entry.bind("<Escape>", lambda _e: (self.search_var.set(""), self.focus_set(), "break")[-1])
        self.search_var.trace_add("write", lambda *_a: self.apply_filter())
        self.search_entry = entry

    def focus_search(self) -> bool:
        if self.search_entry is None:
            return False
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return True

    def apply_filter(self) -> None:
        """Masque les tuiles dont le nom ne contient pas la recherche (insensible à la casse)."""
        needle = (self.search_var.get() if self.search_var else "").strip().lower()
        add = self.tiles.get("__add__")
        # Tout dépacker puis repacker dans l'ordre : l'ordre du rail reste celui de la config.
        tiles = [(runtime, self.tiles.get(runtime.id)) for runtime in self.app.runtimes]
        for _runtime, tile in tiles:
            if tile is not None:
                tile.pack_forget()
        for runtime, tile in tiles:
            if tile is not None and (not needle or needle in runtime.label.lower()):
                tile.pack(pady=sc(2), before=add)

    def refresh_states(self) -> None:
        for runtime in self.app.runtimes:
            tile = self.tiles.get(runtime.id)
            if tile:
                tile.set_state(selected=(self.app.selected_id == runtime.id
                                         and self.app.view in ("server", "server_settings")),
                               state=runtime.state, label=runtime.label)
        self.tiles["__settings__"].set_state(selected=self.app.view == "settings")
        self.tiles["__add__"].set_state(selected=self.app.view == "add")
        overview = self.tiles.get("__overview__")
        if overview:
            overview.set_state(selected=self.app.view == "overview")

    # ── glisser-déposer des tuiles ─────────────────────────────────────────
    def _runtime_tiles(self) -> list:
        return [self.tiles[runtime.id] for runtime in self.app.runtimes
                if runtime.id in self.tiles]

    def drag_start(self, tile, event) -> None:
        self._drop_index = None
        self._clear_drop_line()

    def drag_motion(self, tile, event) -> None:
        index = self._drop_index_for(event.y_root)
        if index != self._drop_index:
            self._drop_index = index
            self._draw_drop_line(index)
        self._autoscroll(event.y_root)

    def drag_end(self, tile, event) -> None:
        index = self._drop_index
        self._drop_index = None
        self._clear_drop_line()
        server_id = getattr(tile, "runtime_id", None)
        if server_id is None or index is None:
            return
        order = [runtime.id for runtime in self.app.runtimes]
        if server_id not in order:
            return
        current = order.index(server_id)
        target = index - 1 if index > current else index  # la tuile quitte sa place d'abord
        target = max(0, min(target, len(order) - 1))
        if target != current:
            self.app.move_server_to(server_id, target)

    def _drop_index_for(self, y_root: int) -> int:
        """Index d'insertion prédit : on coupe au milieu de chaque tuile."""
        for index, tile in enumerate(self._runtime_tiles()):
            middle = tile.winfo_rooty() + tile.winfo_height() / 2
            if y_root < middle:
                return index
        return len(self._runtime_tiles())

    def _draw_drop_line(self, index: int) -> None:
        """Trait d'insertion entre deux tuiles, posé par-dessus la colonne."""
        tiles = self._runtime_tiles()
        if not tiles or self.scroll is None:
            return
        column = self.scroll.master
        if index >= len(tiles):
            last = tiles[-1]
            y = last.winfo_rooty() + last.winfo_height() - column.winfo_rooty() - sc(3)
        else:
            y = tiles[index].winfo_rooty() - column.winfo_rooty() - sc(1)
        y = max(0, min(y, column.winfo_height() - sc(3)))
        try:
            self._drop_indicator.place(x=sc(6), y=y, width=max(1, column.winfo_width() - sc(12)))
            self._drop_indicator.lift()
        except Exception:
            pass

    def _clear_drop_line(self) -> None:
        try:
            if self._drop_indicator is not None:
                self._drop_indicator.place_forget()
        except Exception:
            pass

    def _autoscroll(self, y_root: int) -> None:
        """Approcher du haut ou du bas de la colonne fait défiler pendant le geste."""
        if not self.scroll:
            return
        canvas = self.scroll.canvas
        top = canvas.winfo_rooty()
        bottom = top + canvas.winfo_height()
        if y_root < top + sc(28):
            self.scroll.scroll_by(-1)
        elif y_root > bottom - sc(28):
            self.scroll.scroll_by(1)
