"""Journal repliable d'une tuile (onglets événements / sortie console)."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime

from bgc import i18n
from bgc.widgets import (LogView, Tooltip, font, sc)
from bgc.theme import T
from bgc.ui.common import ghost_button


# ── journal repliable (onglets Événements | Sortie console) ────────────────────
class LogPanel(tk.Frame):
    """Journal d'une tuile (ou de l'app), **repliable**, avec onglets, copie et export.

    Avant, le journal prenait 30 à 40 % du panneau même vide (revue b-6) et la sortie console
    s'ouvrait dans une fenêtre à part. Ici : un en-tête cliquable (▾/▸), un onglet
    « Sortie console » quand le type en fournit une, un filtre, « Copier » (texte prêt pour un
    rapport de bug) et « Exporter… » (.txt). L'état replié est mémorisé (`general.log_collapsed`).
    """

    def __init__(self, master, app, events, console=None, title_key="log.title",
                 report_title: str = "", docked: bool = False):
        super().__init__(master, bg=T.BG)
        self.app = app
        #: Ancré en bas d'une vue (hauteur fixe) plutôt que d'occuper la place restante.
        self.docked = docked
        self._events = events          # () -> [(heure, clé, paramètres)]
        self._console = console        # () -> [lignes] ou None
        self._title_key = title_key
        self._report_title = report_title
        self.tab = "events"
        self._count = 0
        self._console_sig = None
        self._build()

    # --- construction -----------------------------------------------------
    @property
    def collapsed(self) -> bool:
        return bool(self.app.cfg.general.get("log_collapsed", False))

    def _build(self) -> None:
        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", pady=(0, sc(4)))
        self.chevron = tk.Label(head, text="", bg=T.BG, fg=T.MUTED, font=font(9, True),
                                cursor="hand2")
        self.chevron.pack(side="left")
        self.chevron.bind("<Button-1>", lambda _e: self.toggle_collapsed())
        Tooltip(self.chevron, lambda: i18n.t("log.toggle_tip"))
        self.tab_labels: dict[str, tk.Label] = {}
        tabs = [("events", "log.tab_events")]
        if self._console is not None:
            tabs.append(("console", "log.tab_console"))
        if len(tabs) > 1:
            for tab_id, key in tabs:
                label = tk.Label(head, text=i18n.t(key), bg=T.BG, font=font(9), cursor="hand2",
                                 padx=sc(6))
                label.pack(side="left", padx=(sc(8) if tab_id == "events" else 0, 0))
                label.bind("<Button-1>", lambda _e, t=tab_id: self.set_tab(t))
                self.tab_labels[tab_id] = label
        self.tools = tk.Frame(head, bg=T.BG)
        self.tools.pack(side="right")
        ghost_button(self.tools, i18n.t("btn.export_log"), self.export, height=24, size=8
                     ).pack(side="right")
        ghost_button(self.tools, i18n.t("btn.copy_log"), self.copy, height=24, size=8
                     ).pack(side="right", padx=(0, sc(6)))
        self.filter_var = tk.StringVar()
        entry = tk.Entry(self.tools, textvariable=self.filter_var, bg=T.CARD, fg=T.TEXT, bd=0,
                         highlightthickness=0, insertbackground=T.TEXT, font=font(9), width=16)
        entry.pack(side="right", ipady=sc(2), ipadx=sc(4), padx=(0, sc(8)))
        tk.Label(self.tools, text=i18n.t("log.filter"), bg=T.BG, fg=T.FAINT,
                 font=font(8)).pack(side="right", padx=(sc(8), sc(6)))
        self.filter_var.trace_add("write", lambda *_args: self.reset())
        self.body = tk.Frame(self, bg=T.BORDER)
        self.view = LogView(self.body, height=9 if self.docked else 6)
        self.view.pack(fill="both", expand=True, padx=1, pady=1)
        self._apply_collapsed()

    def _apply_collapsed(self) -> None:
        arrow = "▸" if self.collapsed else "▾"
        self.chevron.configure(text=f"{arrow} {i18n.t(self._title_key)}")
        for tab_id, label in self.tab_labels.items():
            label.configure(fg=T.ACCENT if tab_id == self.tab else T.FAINT,
                            font=font(9, tab_id == self.tab))
        if self.collapsed:
            self.body.pack_forget()
            self.tools.pack_forget()
            if self.winfo_manager() == "pack":
                self.pack_configure(expand=False, fill="x")
        else:
            self.tools.pack(side="right")
            self.body.pack(fill="both", expand=True)
            if self.winfo_manager() == "pack" and not self.docked:
                self.pack_configure(expand=True, fill="both")
            self.reset()

    def toggle_collapsed(self) -> None:
        self.app.cfg.general["log_collapsed"] = not self.collapsed
        self.app.cfg.save()
        self._apply_collapsed()

    def open(self, tab: str = "events") -> None:
        if self.collapsed:
            self.app.cfg.general["log_collapsed"] = False
            self.app.cfg.save()
        self.tab = tab if (tab != "console" or self._console is not None) else "events"
        self._apply_collapsed()

    def set_tab(self, tab: str) -> None:
        if tab == self.tab:
            return
        self.tab = tab
        self._apply_collapsed()

    # --- contenu ----------------------------------------------------------
    def _event_lines(self) -> list[tuple[str, str]]:
        lines = [(stamp, i18n.t(key, **params)) for stamp, key, params in list(self._events())]
        needle = self.filter_var.get().strip().lower()
        if needle:
            lines = [item for item in lines if needle in item[1].lower()]
        return lines

    def _console_lines(self) -> list[str]:
        lines = list(self._console() or []) if self._console else []
        needle = self.filter_var.get().strip().lower()
        if needle:
            lines = [line for line in lines if needle in line.lower()]
        return lines

    def reset(self) -> None:
        try:
            self.view.configure(state="normal")
            self.view.delete("1.0", "end")
            self.view.configure(state="disabled")
        except Exception:
            pass
        self._count = 0
        self._console_sig = None
        self.paint()

    def paint(self) -> None:
        if self.collapsed:
            return
        if self.tab == "console":
            lines = self._console_lines()
            signature = (len(lines), lines[-1] if lines else "")
            if signature == self._console_sig:
                return
            self._console_sig = signature
            self.view.configure(state="normal")
            self.view.delete("1.0", "end")
            self.view.configure(state="disabled")
            if not lines:
                self.view.append("—", i18n.t("output.empty"))
            for line in lines[-200:]:
                self.view.append("", line)
            return
        lines = self._event_lines()
        if len(lines) == self._count:
            return
        if len(lines) < self._count:  # tampon circulaire qui a tourné : on repart de zéro
            self.reset()
            return
        for stamp, text in lines[self._count:]:
            self.view.append(stamp, text)
        self._count = len(lines)

    def report_text(self) -> str:
        """Texte du journal visible, avec un en-tête utile pour un rapport de bug."""
        from bgc import __version__
        head = [f"{i18n.t('app.title')} {__version__} — {self._report_title}".rstrip(" —"),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                i18n.t("log.tab_console" if self.tab == "console" else "log.tab_events"), ""]
        if self.tab == "console":
            body = self._console_lines()
        else:
            body = [f"{stamp}  {text}" for stamp, text in self._event_lines()]
        return "\n".join(head + body) + "\n"

    def copy(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.report_text())
        self.app.log_app("log.log_copied")

    def export(self) -> None:
        from tkinter import filedialog
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                       for ch in (self._report_title or "journal"))[:40]
        path = filedialog.asksaveasfilename(
            parent=self, title=i18n.t("btn.export_log"), defaultextension=".txt",
            initialfile=f"{base}-{stamp}.txt",
            filetypes=[(i18n.t("log.filter_txt"), "*.txt"), (i18n.t("browse.all"), "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.report_text())
        except OSError as exc:
            self.app.log_app("log.log_export_failed", error=str(exc)[:160])
            return
        self.app.log_app("log.log_exported", path=path)
