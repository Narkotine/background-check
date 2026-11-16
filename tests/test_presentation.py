"""Refonte UX du 16 nov 2026 : logique d'affichage pure (sans Tk)."""
from __future__ import annotations

from bgc import discovery, i18n, service, sysinfo
from bgc import presentation as pres
from bgc.plugins.hermes import HermesController


# ── pastilles et mots d'état ──────────────────────────────────────────────────
def test_badge_states():
    assert pres.badge_state({"phase": "running"}) == "ok"
    assert pres.badge_state({"phase": "running", "extra": {"ours": False}}) == "warn"
    assert pres.badge_state({"phase": "running", "http": {"code": 502}}) == "bad"
    assert pres.badge_state({"phase": "starting"}) == "warn"
    assert pres.badge_state({"phase": "stopped"}) == "idle"
    assert pres.badge_state({"phase": "stopped"}, fell=True) == "bad"
    assert pres.badge_state({"phase": "running"}, enabled=False) == "paused"


def test_health_overrides_running_colour_only():
    # Hermes : « en marche » mais chaîne incomplète → ambre (la phase ne change pas).
    assert pres.badge_state({"phase": "running", "health": "warn"}) == "warn"
    assert pres.badge_state({"phase": "stopped", "health": "ok"}) == "idle"


def test_status_label_fell_and_paused():
    command = service.get("command")
    assert pres.status_label(command, {"phase": "stopped"}, fell=True)[0] == "status.fell"
    assert pres.status_label(command, {"phase": "running"}, enabled=False)[0] == "status.paused"
    assert pres.status_label(command, None)[0] == "status.loading"


def test_uptime_prefers_process_age():
    snap = {"phase": "running", "meta": {"info": {"uptime": 42}}}
    assert pres.uptime_seconds(snap) == 42
    assert pres.uptime_seconds({"phase": "running"}, {"since": 100.0}, now=160.0) == 60.0
    assert pres.uptime_seconds({"phase": "stopped"}, {"since": 100.0}) is None


# ── URL et boutons ────────────────────────────────────────────────────────────
def test_url_locality_and_copy_label():
    for url in ("http://127.0.0.1:8080", "localhost:3000", "http://192.168.1.10/",
                "http://pc.local:9119", ""):
        assert pres.url_is_local(url), url
    assert not pres.url_is_local("https://box.tail1234.ts.net/")
    assert pres.copy_url_label_key("https://box.tail1234.ts.net/") == "btn.copy_url_public"
    assert pres.copy_url_label_key("http://127.0.0.1:1234") == "btn.copy_url"


def test_adjust_actions_greys_web_buttons_when_stopped():
    actions = [{"id": "toggle", "label_key": "btn.start"},
               {"id": "browser", "label_key": "btn.browser"},
               {"id": "copy_url", "label_key": "btn.copy_url"}]
    stopped = pres.adjust_actions(actions, {"phase": "stopped", "url": "http://127.0.0.1:1"})
    assert [a.get("enabled", True) for a in stopped] == [True, False, False]
    running = pres.adjust_actions(actions, {"phase": "running", "url": "https://x.ts.net"})
    assert running[2]["label_key"] == "btn.copy_url_public"
    assert actions[1].get("enabled", True) is True  # l'original n'est pas modifié


def test_primary_and_web_action():
    actions = [{"id": "toggle_gateway"}, {"id": "toggle"}, {"id": "browser"}]
    assert [a["id"] for a in pres.primary_actions(actions)] == ["toggle_gateway", "toggle"]
    assert pres.web_action(actions)["id"] == "browser"
    custom = [{"label": "Dossier", "target": "C:\\x"}, {"label": "UI", "target": "http://x"}]
    web = pres.web_action([], custom, {1: False})
    assert web["id"] == "custom:1" and web["enabled"] is False
    assert pres.web_action([], [{"label": "x", "target": "notepad"}]) is None


# ── services détectés ─────────────────────────────────────────────────────────
def test_group_detected_filters_and_groups():
    entries = [
        {"port": 11434, "pid": 10, "name": "ollama.exe"},
        {"port": 5000, "pid": 20, "name": "python.exe"},
        {"port": 5001, "pid": 20, "name": "python.exe"},
        {"port": 135, "pid": 30, "name": "svchost.exe"},       # système + port bas
        {"port": 7000, "pid": 31, "name": "svchost.exe"},      # système
        {"port": 51000, "pid": 40, "name": "random.exe"},      # plage dynamique
        {"port": 8765, "pid": 50, "name": "backgroundcheck.exe"},   # l'app elle-même
        {"port": 1234, "pid": 60, "name": "lm studio.exe"},    # déjà suivi
    ]
    groups = pres.group_detected(entries, tracked_ports={1234}, own_ports={8765},
                                 own_pids={50})
    # Deux services reconnus : triés par port.
    assert [g["name"] for g in groups] == ["python.exe", "ollama.exe"]
    assert groups[1]["preset"] == "preset.ollama"
    python = groups[0]
    assert python["ports"] == [5000, 5001] and python["port"] == 5000
    assert python["preset"] == "preset.koboldcpp"  # 5001 = port par défaut de KoboldCpp


def test_tracked_ports_include_extra_checks():
    servers = [{"params": {"port": "9119", "extra_checks": "API=127.0.0.1:8642/health\n7860"}}]
    assert pres.tracked_ports(servers) == {9119, 8642, 7860}


def test_display_name():
    assert pres.display_name("C:\\x\\ollama.exe") == "ollama"
    assert pres.display_name("node") == "node"


# ── modèles appliqués à une tuile existante ───────────────────────────────────
def test_preset_diff_lists_only_changes():
    current = {"params": {"host": "127.0.0.1", "port": "11434", "command": "ollama run"},
               "icon": "", "actions": []}
    preset = {"fields": {"host": "127.0.0.1", "port": "11434", "command": "ollama serve"},
              "icon": "C:\\o\\ollama.exe", "actions": [{"label": "UI", "target": "http://x"}]}
    diff = pres.preset_diff(current, preset, {"command": "Commande"})
    assert ("Commande", "ollama run", "ollama serve") in diff
    assert any(label == "field.icon" for label, _, _ in diff)
    assert any(label == "settings.custom_actions" for label, _, _ in diff)
    assert len(diff) == 3
    assert pres.preset_diff({"params": preset["fields"], "icon": preset["icon"],
                             "actions": preset["actions"]}, preset) == []


# ── menu de la zone de notification ───────────────────────────────────────────
def test_tray_menu_has_no_silent_cap():
    entries = [(f"S{i}", [("Démarrer", f"toggle:{i}", True)]) for i in range(13)]
    menu = pres.tray_service_menu(entries, "Autres ({n})", limit=10)
    assert len(menu) == 11
    label, sub, _ = menu[-1]
    assert label == "Autres (3)" and [e[0] for e in sub] == ["S10", "S11", "S12"]
    assert len(pres.tray_service_menu(entries[:4], "Autres ({n})")) == 4


# ── chaîne Hermes honnête ─────────────────────────────────────────────────────
def _hermes(port=9119):
    return HermesController({"id": "h", "type": "hermes", "label": "Hermes",
                             "params": {"port": port}}, lambda *a, **k: None)


def _chain(controller, **over):
    args = dict(phase="running", registry="https://box.tail1.ts.net", url="https://box.tail1.ts.net",
                remote_phase="running", api_up=True, pid=123, local_phase="stopped",
                gw_health=None, gw_port=8642, gateway_enabled=True,
                target="http://127.0.0.1:9119", target_alive=True, phone_state=None,
                phone_label="", desktop_pid=None)
    args.update(over)
    return controller.chain_status(**args)


def test_chain_green_only_when_full_remote_chain_answers():
    hermes = _hermes()
    assert _chain(hermes)["health"] == "ok"
    dead = _chain(hermes, target_alive=False)
    assert dead["health"] == "warn"
    assert ("chain.missing_target", {"port": 9119}) in dead["missing"]


def test_chain_tunnel_configured_target_stopped():
    hermes = _hermes()
    # Le gateway local tourne (phase « running ») mais le desktop vise le distant, arrêté.
    status = _chain(hermes, remote_phase="stopped", api_up=False, pid=None,
                    target_alive=False, local_phase="running", gw_health={"ok": True})
    assert status["health"] == "warn"
    keys = [key for key, _ in status["missing"]]
    assert "chain.missing_target" in keys and "chain.missing_remote" in keys
    tunnel = next(l for l in status["links"]["remote"] if l["label_key"] == "chain.tunnel")
    assert tunnel["state"] == "warn" and tunnel["value_key"] == "chain.tunnel_dead"


def test_chain_local_mode_uses_gateway():
    hermes = _hermes()
    ok = _chain(hermes, registry="", local_phase="running", gw_health={"ok": True},
                remote_phase="stopped", api_up=False, pid=None)
    assert ok["mode"] == "local" and ok["health"] == "ok"
    bad = _chain(hermes, registry="", gw_health=None, local_phase="stopped")
    assert bad["health"] == "warn"
    assert ("chain.missing_gateway", {"port": 8642}) in bad["missing"]


def test_chain_no_health_when_stopped():
    assert _chain(_hermes(), phase="stopped")["health"] is None


def test_chain_missing_tunnel_and_phone():
    status = _chain(_hermes(), target="", target_alive=False, phone_state=False,
                    phone_label="pixel")
    keys = [key for key, _ in status["missing"]]
    assert "chain.missing_tunnel" in keys and "chain.phone_offline" in keys


def test_target_alive_parses_port(monkeypatch):
    hermes = _hermes()
    assert hermes._target_alive("http://127.0.0.1:9119", 42)
    assert not hermes._target_alive("http://127.0.0.1:9119", None)
    monkeypatch.setattr(sysinfo, "listener_pid", lambda port, *a, **k: 7 if port == 8000 else None)
    assert hermes._target_alive("http://127.0.0.1:8000/x", None)
    assert not hermes._target_alive("http://127.0.0.1:8001", None)


# ── « Tester maintenant » (sonde à blanc) ─────────────────────────────────────
def test_dry_run_reports_port_and_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sysinfo, "listener_pid", lambda port, *a, **k: None)
    command = service.get("command")
    params = command.validate({**command.default_params(), "host": "127.0.0.1",
                               "port": "18001", "workdir": str(tmp_path / "absent")})
    results = discovery.dry_run(command, params)
    keys = [key for _, key, _ in results]
    assert "test.port_free" in keys
    assert "test.dir_missing" in keys
    for _, key, params_ in results:  # chaque message se traduit sans trou
        assert "{" not in i18n.t(key, **params_), key


def test_dry_run_nothing_to_check():
    command = service.get("command")
    results = discovery.dry_run(command, {})
    assert results == [("info", "test.nothing", {})]


# ── premier lancement ─────────────────────────────────────────────────────────
def test_welcome_scan_proposes_known_services(monkeypatch, tmp_path):
    from bgc import config, onboarding
    from bgc.plugins import wsl
    monkeypatch.setattr(config, "hermes_home", lambda: str(tmp_path))
    monkeypatch.setattr(wsl, "list_distros", lambda: ([{"name": "Ubuntu"}], ""))
    monkeypatch.setattr(discovery, "installed_presets", lambda: ["preset.lmstudio"])
    monkeypatch.setattr(discovery, "detected_services", lambda servers, own_ports=None: [
        {"name": "ollama.exe", "pid": 1, "ports": [11434], "port": 11434,
         "preset": "preset.ollama", "exe": ""},
        {"name": "node.exe", "pid": 2, "ports": [4000], "port": 4000, "preset": None,
         "exe": ""}])
    items = onboarding.scan_candidates(18765)
    keys = [item["key"] for item in items]
    assert keys[:2] == ["hermes", "wsl"]
    assert "preset.lmstudio" in keys and "preset.ollama" in keys
    unknown = next(item for item in items if item["key"] == "port:4000")
    assert unknown["checked"] is False  # un service inconnu n'est jamais coché d'office
    ollama = next(item for item in items if item["key"] == "preset.ollama")
    assert ollama["server"]["params"]["port"] == 11434
