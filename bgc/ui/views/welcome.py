"""Premier lancement : rien n'est configuré → on cherche et on propose (revue b-11 / e-4).

L'écran balaie la machine (dans un fil) : Hermes installé, distributions WSL, programmes
connus (LM Studio, Ollama, LlamaGUI…), services en écoute reconnus. Chaque proposition est
une case à cocher ; rien n'est créé sans clic sur « Créer les tuiles cochées ». Le serveur
MCP est expliqué au même endroit, avec « Copier la config Hermes ».
"""

from __future__ import annotations

import os
import threading
import tkinter as tk

from bgc import discovery, i18n, service
from bgc import presentation as pres
from bgc.ui.common import ghost_button, section_title
from bgc.widgets import (Button, ScrollFrame, Toggle, font, sc)
from bgc.theme import T


def scan_candidates(own_port: int | None = None) -> list[dict]:
    """Propositions de tuiles : [{key, label, detail, server, checked}] (sans Tk)."""
    candidates: list[dict] = []
    seen_presets: set[str] = set()
    try:
        from bgc.config import hermes_home
        root = hermes_home()
        hermes_type = service.get("hermes")
        if hermes_type and os.path.isdir(root):
            params = hermes_type.validate(hermes_type.default_params())
            candidates.append({"key": "hermes", "label": "Hermes",
                               "detail": i18n.t("welcome.found_hermes", path=root),
                               "server": {"type": "hermes", "label": "Hermes", "params": params,
                                          "icon": "", "actions": []},
                               "checked": True})
    except Exception:
        pass
    try:
        wsl_type = service.get("wsl")
        if wsl_type:
            from bgc.plugins import wsl as wsl_mod
            distros, _err = wsl_mod.list_distros()
            if distros:
                params = wsl_type.validate(wsl_type.default_params())
                names = ", ".join(d["name"] for d in distros[:4])
                candidates.append({"key": "wsl", "label": "WSL",
                                   "detail": i18n.t("welcome.found_wsl", names=names),
                                   "server": {"type": "wsl", "label": "WSL", "params": params,
                                              "icon": "", "actions": []},
                                   "checked": True})
    except Exception:
        pass
    try:
        groups = discovery.detected_services([], own_ports={own_port} if own_port else None)
    except Exception:
        groups = []
    running_presets = {g["preset"]: g for g in groups if g.get("preset")}
    for key in discovery.installed_presets() + [k for k in running_presets
                                                if k not in discovery.installed_presets()]:
        if key in seen_presets:
            continue
        seen_presets.add(key)
        group = running_presets.get(key)
        server = discovery.preset_server(key, port=group["port"] if group else None)
        if not server:
            continue
        detail = (i18n.t("welcome.found_running", port=group["port"]) if group
                  else i18n.t("welcome.found_installed"))
        candidates.append({"key": key, "label": server["label"] or i18n.t(key),
                           "detail": detail, "server": server, "checked": True})
    for group in groups:
        if group.get("preset"):
            continue
        server = discovery.watch_server(group)
        if not server:
            continue
        candidates.append({"key": f"port:{group['port']}", "label": server["label"],
                           "detail": i18n.t("welcome.found_port", port=group["port"]),
                           "server": server, "checked": False})
    return candidates[:14]


class WelcomeView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=T.BG)
        self.app = app
        self.candidates: list[dict] | None = None
        self.choices: dict[str, bool] = {}
        self._build()
        self._scan()

    def _build(self) -> None:
        footer = tk.Frame(self, bg=T.BG)
        footer.pack(side="bottom", fill="x", padx=sc(18), pady=(sc(6), sc(12)))
        self.create_btn = Button(footer, i18n.t("welcome.create"), self._create, height=38)
        self.create_btn.pack(side="left")
        self.create_btn.set_enabled(False)
        ghost_button(footer, i18n.t("welcome.manual"), self.app.show_add, height=38, size=11
                     ).pack(side="left", padx=(sc(8), 0))

        scroll = ScrollFrame(self, bg=T.BG)
        scroll.pack(fill="both", expand=True, padx=sc(18), pady=(sc(14), sc(6)))
        body = scroll.inner
        tk.Label(body, text=i18n.t("welcome.title", app=i18n.t("app.title")), bg=T.BG, fg=T.TEXT,
                 font=font(20, True), anchor="w").pack(fill="x")
        tk.Label(body, text=i18n.t("welcome.intro"), bg=T.BG, fg=T.MUTED, font=font(10),
                 anchor="w", wraplength=sc(640), justify="left").pack(fill="x",
                                                                       pady=(sc(4), sc(8)))
        section_title(body, "welcome.found")
        self.list = tk.Frame(body, bg=T.BG)
        self.list.pack(fill="x", pady=(0, sc(8)))
        tk.Label(self.list, text=i18n.t("add.scanning"), bg=T.BG, fg=T.FAINT, font=font(9),
                 anchor="w").pack(fill="x")

        section_title(body, "welcome.mcp_title")
        card = tk.Frame(body, bg=T.CARD)
        card.pack(fill="x", pady=(0, sc(8)))
        tk.Label(card, text=i18n.t("welcome.mcp_text", app=i18n.t("app.title")), bg=T.CARD,
                 fg=T.MUTED, font=font(9), anchor="w", wraplength=sc(620), justify="left").pack(
            fill="x", padx=sc(12), pady=(sc(10), sc(6)))
        row = tk.Frame(card, bg=T.CARD)
        row.pack(fill="x", padx=sc(12), pady=(0, sc(10)))
        ghost_button(row, i18n.t("btn.copy_mcp_hermes"), self._copy_hermes, parent_bg=T.CARD
                     ).pack(side="left")
        self.copied = tk.Label(row, text="", bg=T.CARD, fg=T.FAINT, font=font(9))
        self.copied.pack(side="left", padx=(sc(10), 0))

    def _scan(self) -> None:
        box: dict = {}
        own_port = self.app.mcp_status().get("port")

        def work() -> None:
            try:
                box["items"] = scan_candidates(own_port)
            except Exception:
                box["items"] = []
            box["done"] = True

        threading.Thread(target=work, daemon=True, name="welcome-scan").start()

        def poll() -> None:
            try:
                if not self.winfo_exists():
                    return
                if not box.get("done"):
                    self.after(150, poll)
                    return
                self.candidates = box.get("items") or []
                self._fill()
            except Exception:
                pass

        self.after(150, poll)

    def _fill(self) -> None:
        for child in self.list.winfo_children():
            child.destroy()
        if not self.candidates:
            tk.Label(self.list, text=i18n.t("welcome.nothing"), bg=T.BG, fg=T.MUTED, font=font(9),
                     anchor="w", wraplength=sc(640), justify="left").pack(fill="x")
            return
        for item in self.candidates:
            self.choices[item["key"]] = bool(item["checked"])
            row = tk.Frame(self.list, bg=T.CARD)
            row.pack(fill="x", pady=(0, sc(4)))
            Toggle(row, item["label"], item["checked"], parent_bg=T.CARD,
                   command=lambda value, k=item["key"]: self._set(k, value)).pack(
                side="left", padx=(sc(8), 0), pady=sc(6))
            tk.Label(row, text=pres.truncate(item["detail"], 80), bg=T.CARD, fg=T.FAINT,
                     font=font(8), anchor="w").pack(side="left", padx=(sc(12), sc(8)))
        self._update_button()

    def _set(self, key: str, value: bool) -> None:
        self.choices[key] = bool(value)
        self._update_button()

    def _update_button(self) -> None:
        count = sum(1 for v in self.choices.values() if v)
        self.create_btn.set_text(i18n.t("welcome.create_n", n=count) if count
                                 else i18n.t("welcome.create"))
        self.create_btn.set_enabled(count > 0)

    def _copy_hermes(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.app.mcp_hermes_snippet())
        self.copied.configure(text="✓ " + i18n.t("log.mcp_hermes_copied"))
        self.app.log_app("log.mcp_hermes_copied")

    def _create(self) -> None:
        chosen = [c for c in (self.candidates or []) if self.choices.get(c["key"])]
        if not chosen:
            return
        for item in chosen:
            server = item["server"]
            self.app.create_server(server["type"], server["label"], server["params"],
                                   icon=server.get("icon") or "", actions=server.get("actions"),
                                   select=False)
        self.app.log_app("log.welcome_created", n=len(chosen))
        self.app.show_overview()
