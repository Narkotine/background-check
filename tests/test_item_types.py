"""Types à éléments : Docker/Podman, services Windows, WSL par distribution (CLI simulées)."""
from __future__ import annotations

import json

import pytest

from bgc.plugins import docker, winsvc, wsl


def _logger(store):
    return lambda key, **params: store.append((key, params))


# ── Docker / Podman ───────────────────────────────────────────────────────────
DOCKER_PS = "\n".join(json.dumps(x) for x in [
    {"ID": "abcdef1234567890", "Names": "compose-web-1", "Image": "nginx:latest",
     "State": "running", "Status": "Up 2 hours", "Ports": "0.0.0.0:8080->80/tcp, [::]:8080->80/tcp"},
    {"ID": "1234567890ab", "Names": "compose-db-1", "Image": "postgres:16", "State": "exited",
     "Status": "Exited (0) 3 days ago", "Ports": ""},
])


def test_parse_docker_and_podman_output():
    items = docker.parse_ps(DOCKER_PS)
    assert [c["id"] for c in items] == ["abcdef123456", "1234567890ab"]
    assert docker.published_port(items[0]["ports"]) == 8080
    assert docker.published_port(items[1]["ports"]) is None
    podman = json.dumps([{"Id": "ffff00001111aaaa", "Names": ["pod-api"], "Image": "api",
                          "State": "running",
                          "Ports": [{"host_ip": "", "host_port": 9000, "container_port": 80,
                                     "protocol": "tcp"}]}])
    parsed = docker.parse_ps(podman)
    assert parsed[0]["name"] == "pod-api" and docker.published_port(parsed[0]["ports"]) == 9000
    assert docker.container_phase("exited") == "stopped"


def test_docker_missing_is_graceful(monkeypatch):
    monkeypatch.setattr(docker, "find_engine", lambda pref="auto": ("", ""))
    ctl = docker.DockerController({"params": {}, "options": {}}, _logger([]))
    snap = ctl.snapshot()
    assert snap["items"] == [] and snap["items_empty_key"] == "items.docker_missing"
    assert ctl.item_action("x", "start") == "log.docker_missing"


def test_docker_items_and_actions(monkeypatch):
    calls = []

    def fake_cli(cli, args, timeout=15):
        calls.append(args)
        if args[0] == "ps":
            return DOCKER_PS, "", 0
        if args[0] == "logs":
            return "line 1\nline 2", "", 0
        return "", "", 0
    monkeypatch.setattr(docker, "find_engine", lambda pref="auto": ("docker", "docker"))
    monkeypatch.setattr(docker, "run_cli", fake_cli)
    logs = []
    ctl = docker.DockerController({"params": {}, "options": {"show_stopped": True}},
                                  _logger(logs))
    snap = ctl.snapshot()
    web, db = snap["items"]
    assert web["phase"] == "running" and [a["id"] for a in web["actions"]] == \
        ["stop", "restart", "logs", "open"]
    assert db["phase"] == "stopped" and db["actions"][0]["id"] == "start"
    assert ctl.item_action("abcdef123456", "stop") == "log.docker_stopped"
    assert ["stop", "abcdef123456"] in calls
    assert ctl.item_action("abcdef123456", "logs") == "log.docker_logs"
    assert ctl.output_lines()[-2:] == ["line 1", "line 2"]
    # un identifiant qui n'est pas dans la liste n'atteint jamais la CLI
    assert ctl.item_action("; rm -rf /", "stop") == "log.item_unknown"
    assert not any("; rm -rf /" in a for a in calls)
    ctl.options["show_stopped"] = False
    ctl.invalidate()
    assert [i["id"] for i in ctl.snapshot()["items"]] == ["abcdef123456"]


# ── Services Windows ──────────────────────────────────────────────────────────
def test_service_names_are_validated():
    t = winsvc.WinServiceType()
    assert t.validate({"services": "Spooler, wuauserv; Spooler"})["services"] == \
        "Spooler, wuauserv"
    for bad in ('Spooler" & calc', "a|b", ""):
        with pytest.raises(ValueError):
            t.validate({"services": bad})


def test_sc_query_parsing():
    out = "SERVICE_NAME: Spooler\n        STATE              : 4  RUNNING\n        PID : 3120"
    assert winsvc.parse_sc_query(out) == {"status": "running", "pid": 3120}
    assert winsvc.phase_of("start_pending") == "starting"


def _service(monkeypatch, states):
    monkeypatch.setattr(winsvc, "service_info", lambda name: (
        {"name": name, "display_name": name.title(), "status": states[name], "start_type": "manual",
         "pid": None} if name in states else {}))


def test_single_service_is_a_classic_tile(monkeypatch):
    _service(monkeypatch, {"spooler": "stopped"})
    ctl = winsvc.WinServiceController({"params": {"services": "spooler"}, "options": {}}, print)
    snap = ctl.snapshot()
    assert "items" not in snap and snap["actions"][0]["id"] == "toggle"
    assert snap["actions"][0]["main"] and snap["phase"] == "stopped"


def test_several_services_are_items(monkeypatch):
    _service(monkeypatch, {"a": "running", "b": "stopped"})
    ctl = winsvc.WinServiceController({"params": {"services": "a, b, ghost"}, "options": {}},
                                      print)
    snap = ctl.snapshot()
    assert [i["id"] for i in snap["items"]] == ["a", "b", "ghost"]
    assert snap["items"][2]["actions"] == []   # service introuvable : aucun bouton


def test_access_denied_asks_uac_and_reports_refusal(monkeypatch):
    states = {"spooler": "stopped"}
    _service(monkeypatch, states)
    monkeypatch.setattr(winsvc, "run_sc", lambda args, timeout=20: ("[SC] StartService ÉCHEC 5 :", "", 5))
    monkeypatch.setattr(winsvc, "run_elevated", lambda args: winsvc.ERROR_CANCELLED)
    logs = []
    ctl = winsvc.WinServiceController({"params": {"services": "spooler"}, "options": {}},
                                      _logger(logs))
    assert ctl.action("toggle") == "log.winsvc_denied"
    assert [k for k, _ in logs] == ["log.winsvc_uac", "log.winsvc_denied"]

    def elevated(args):
        states["spooler"] = "running"   # l'utilisateur a accepté : le service démarre
        return 0
    monkeypatch.setattr(winsvc, "run_elevated", elevated)
    assert ctl.action("toggle") == "log.winsvc_started"


# ── WSL : un élément par distribution ────────────────────────────────────────
def test_wsl_all_mode_lists_controllable_distros(monkeypatch):
    distros = [{"name": "Ubuntu", "state": "running", "version": "2", "default": True},
               {"name": "Debian", "state": "stopped", "version": "2", "default": False}]
    monkeypatch.setattr(wsl, "list_distros", lambda: (distros, ""))
    monkeypatch.setattr(wsl, "wsl_version", lambda: "2.4.0")
    monkeypatch.setattr(wsl, "vm_memory_mb", lambda: 0.0)
    calls = []

    def fake_wsl(args, timeout=15):
        calls.append(args)
        if args[:1] == ["-d"]:
            distros[1]["state"] = "running"
        if args[:1] == ["--terminate"]:
            next(d for d in distros if d["name"] == args[1])["state"] = "stopped"
        return "", "", 0
    monkeypatch.setattr(wsl, "_wsl", fake_wsl)
    logs = []
    ctl = wsl.WSLController({"params": {"distro": ""}, "options": {}}, _logger(logs))
    snap = ctl.snapshot()
    assert [i["id"] for i in snap["items"]] == ["Ubuntu", "Debian"]
    assert snap["items"][0]["actions"][0]["id"] == "stop"
    # Ubuntu tournait avant l'app : arrêt refusé (règle de sûreté inchangée)
    assert ctl.item_action("Ubuntu", "stop") == "log.wsl_stop_refused"
    assert ctl.item_action("Debian", "start") == "log.wsl_started"
    assert ctl.item_action("Debian", "stop") == "log.wsl_stopped"   # démarrée ici : permis
    assert ["--terminate", "Debian"] in calls and ["--terminate", "Ubuntu"] not in calls


def test_named_wsl_tile_is_unchanged(monkeypatch):
    monkeypatch.setattr(wsl, "list_distros", lambda: (
        [{"name": "Ubuntu", "state": "stopped", "version": "2", "default": True}], ""))
    monkeypatch.setattr(wsl, "wsl_version", lambda: "2.4.0")
    monkeypatch.setattr(wsl, "vm_memory_mb", lambda: 0.0)
    snap = wsl.WSLController({"params": {"distro": "Ubuntu"}, "options": {}}, print).snapshot()
    assert "items" not in snap and snap["actions"][0]["id"] == "toggle"


def test_nested_translation_params():
    from bgc import i18n
    i18n.set_language("fr")
    assert i18n.t("value.wsl_item_detail", version="2", state={"key": "wsl.state.running"}) == \
        "WSL 2 · en marche"
    i18n.set_language("en")
    assert winsvc.state_text("STOP_PENDING") == {"key": "winsvc.state.stop_pending"}
    assert i18n.t("value.wsl_text", text=winsvc.start_text("demand")) == "manual"
    assert winsvc.state_text("weird") == "weird"
    i18n.set_language("fr")


def test_absent_engine_or_distro_says_so_instead_of_loading(monkeypatch):
    from bgc import presentation as pres
    monkeypatch.setattr(docker, "find_engine", lambda pref="auto": ("", ""))
    snap = docker.DockerController({"params": {}, "options": {}}, print).snapshot()
    assert pres.status_label(docker.DockerType(), snap)[0] == "status.docker_missing"
    assert snap["phase"] == "unknown"
    monkeypatch.setattr(wsl, "list_distros", lambda: ([], "no distribution"))
    monkeypatch.setattr(wsl, "wsl_version", lambda: "")
    monkeypatch.setattr(wsl, "vm_memory_mb", lambda: 0.0)
    snap = wsl.WSLController({"params": {"distro": ""}, "options": {}}, _logger([])).snapshot()
    assert pres.status_label(wsl.WSLType(), snap)[0] == "status.wsl_none"
    snap = wsl.WSLController({"params": {"distro": "Ubuntu"}, "options": {}}, _logger([])).snapshot()
    assert pres.status_label(wsl.WSLType(), snap) == ("value.wsl_missing", {"name": "Ubuntu"})
    assert snap["phase"] == "unknown"


def test_choice_field_is_a_dropdown():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("pas d'affichage")
    try:
        from bgc.ui.common import _collect_params, _pack_fields
        from bgc.widgets import ChoiceField
        store = {}
        _pack_fields(root, docker.DockerType(), {"engine": "podman", "filter": ""}, store, 4)
        assert isinstance(store["engine"], ChoiceField)
        assert _collect_params(docker.DockerType(), store)["engine"] == "podman"
        store["engine"].set("docker")
        store["engine"].set("rm -rf")          # valeur hors liste : ignorée
        assert store["engine"].get() == "docker"
    finally:
        root.destroy()


def test_elevated_sc_errors_are_reported(monkeypatch):
    states = {"spooler": "stopped"}
    _service(monkeypatch, states)
    monkeypatch.setattr(winsvc, "run_sc", lambda args, timeout=20: ("[SC] StartService FAILED 5:", "", 5))
    logs = []
    ctl = winsvc.WinServiceController({"params": {"services": "spooler"}, "options": {}},
                                      _logger(logs))
    ctl.WAIT = 0.2
    monkeypatch.setattr(winsvc, "run_elevated", lambda args: 1058)   # service désactivé
    assert ctl.action("toggle") == "log.winsvc_failed"
    assert "désactivé" in logs[-1][1]["error"]
    monkeypatch.setattr(winsvc, "run_elevated", lambda args: -1)     # UAC sans réponse
    assert ctl.action("toggle") == "log.winsvc_failed"
    assert "UAC" in logs[-1][1]["error"]


# ── WSL : sortie localisée (Windows français) ─────────────────────────────────
FR_VERBOSE = ("  NOM      ÉTAT                     VERSION\n"
              "* Ubuntu   En cours d'exécution     2\n"
              "  Debian   Arrêté                   2\n")


def test_wsl_parser_handles_localised_states(monkeypatch):
    calls = []

    def fake(args, timeout=15):
        calls.append(args)
        return {("-l", "-v"): (FR_VERBOSE, "", 0), ("-l", "-q"): ("Ubuntu\nDebian\n", "", 0),
                ("-l", "--running", "-q"): ("Ubuntu\n", "", 0)}[tuple(args)]
    monkeypatch.setattr(wsl, "_wsl", fake)
    distros, error = wsl.list_distros()
    assert error == ""
    assert distros == [{"name": "Ubuntu", "state": "running", "version": "2", "default": True},
                       {"name": "Debian", "state": "stopped", "version": "2", "default": False}]


def test_wsl_parser_english_and_no_distro(monkeypatch):
    en = "  NAME      STATE           VERSION\n* Ubuntu    Stopped         2\n"
    assert wsl.parse_list(en) == [{"name": "Ubuntu", "state": "stopped", "version": "2",
                                   "default": True}]
    message = ("Le Sous-système Windows pour Linux n'a aucune distribution installée.\n"
               "Utiliser 'wsl.exe --list --online' pour répertorier les distributions")
    monkeypatch.setattr(wsl, "_wsl", lambda args, timeout=15: (message, "", 4294967295))
    distros, error = wsl.list_distros()
    assert distros == [] and "aucune distribution" in error


def test_wsl_output_decoding():
    assert wsl._decode("Arrêté".encode("utf-16-le")) == "Arrêté"
    assert wsl._decode("Arrêté".encode("utf-8")) == "Arrêté"


# ── Arrêt à la fermeture, --selftest générique ────────────────────────────────
class _Runtime:
    def __init__(self, snap):
        self.snapshot_data = snap
        self.phase = snap.get("phase")


def test_stop_on_quit_only_when_something_runs():
    from bgc.core.actions import has_something_to_stop
    stopped = {"phase": "stopped", "actions": [{"id": "toggle", "label_key": "btn.start",
                                                 "main": True, "stops": False}]}
    assert not has_something_to_stop(_Runtime(stopped))
    assert has_something_to_stop(_Runtime({"phase": "running", "actions": []}))
    # Hermes : tuile « arrêtée » mais passerelle locale en marche (bascule principale = arrêt)
    gateway = {"phase": "stopped", "actions": [{"id": "toggle_gateway", "main": True,
                                                 "label_key": "btn.hermes_gateway_stop"}]}
    assert has_something_to_stop(_Runtime(gateway))
    items = {"phase": "stopped", "actions": [], "items": [{"id": "a", "label": "a",
                                                           "phase": "running", "actions": []}]}
    assert has_something_to_stop(_Runtime(items))


def test_selftest_target_is_generic():
    import background_check as bc
    servers = [{"id": "w", "type": "wsl"}, {"id": "c", "type": "command"},
               {"id": "h", "type": "hermes"}]
    assert bc.selftest_target(servers)["id"] == "h"            # comportement historique
    assert bc.selftest_target(servers, "c")["id"] == "c"
    assert bc.selftest_target(servers[:2])["id"] == "w"        # premier type pilotable
    assert bc.selftest_target(servers, "nope") is None


# ── Lignes d'état : jamais de texte brut multi-ligne (retour utilisateur 16 nov 2026) ──
RAW_FR = ("Sous-système Windows pour Linux n’a aucune distribution installée.\r\n"
          "Vous pouvez résoudre ce problème en installant une distribution avec les "
          "instructions ci-dessous :\r\n\r\nUtiliser wsl.exe --list --online'")


def test_wsl_without_distro_shows_short_message_not_raw_output(monkeypatch):
    from bgc.service import ROW_IDLE, ROW_WARN
    monkeypatch.setattr(wsl, "list_distros", lambda: ([], RAW_FR))
    monkeypatch.setattr(wsl, "wsl_version", lambda: "")
    monkeypatch.setattr(wsl, "vm_memory_mb", lambda: 0.0)
    logs = []
    ctl = wsl.WSLController({"params": {"distro": "Ubuntu"}, "options": {}}, _logger(logs))
    rows = ctl.snapshot()["rows"]
    distro, probe = rows
    assert distro["status"] == ROW_WARN                      # distribution absente : ambre
    assert probe["status"] == ROW_IDLE and probe["value_key"] == "value.wsl_none_installed"
    assert "aucune distribution" in probe["tooltip"] and "\n\n" not in probe["tooltip"]
    assert not any("Sous-système" in str(r.get("value_params")) for r in rows)
    ctl.snapshot()
    assert [k for k, _ in logs].count("log.wsl_probe_raw") == 1   # journalisé une fois
    all_rows = wsl.WSLController({"params": {"distro": ""}, "options": {}},
                                 _logger([])).snapshot()["rows"]
    assert all_rows[0]["value_key"] == "value.wsl_none_installed"


def test_wsl_probe_problems_are_classified():
    from bgc.service import ROW_BAD
    assert wsl.probe_problem(wsl.WSL_EXE_MISSING) == (ROW_BAD, "value.wsl_exe_missing")
    assert wsl.probe_problem(wsl.WSL_TIMEOUT) == (ROW_BAD, "value.wsl_timeout")


def test_row_values_are_flattened_and_fitted():
    from bgc import presentation as pres
    assert pres.one_line("a\r\n\r\n  b\tc ") == "a b c"
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("pas d'affichage")
    try:
        from bgc.widgets import fit_text
        from tkinter import font as tkfont
        f = tkfont.Font(root=root, family="TkDefaultFont", size=10)
        text, cut = fit_text("x" * 400, f, 120)
        assert cut and text.endswith("…") and f.measure(text) <= 120
        assert fit_text("court", f, 400) == ("court", False)
    finally:
        root.destroy()
