"""Serveurs liés (16 nov 2026) : déclaration, sondage, mesures à part, MCP, vue d'ensemble."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import psutil
import pytest

from bgc import config, i18n, mcpserver, tiles
from bgc import presentation as pres
from bgc.core import deps
from bgc.core import metrics as m


def test_parse_target_forms():
    assert deps.parse_target("8178")["port"] == 8178
    t = deps.parse_target("http://127.0.0.1:8178/health")
    assert (t["host"], t["port"], t["url"]) == ("127.0.0.1", 8178, "http://127.0.0.1:8178/health")
    assert deps.parse_target("https://nas.local/")["port"] == 443
    assert deps.parse_target("nas.local:8123")[ "host"] == "nas.local"
    assert deps.parse_target("ollama")["process"] == "ollama.exe"
    assert deps.parse_target("llama-server.exe")["process"] == "llama-server.exe"
    assert deps.parse_target("")["port"] == 0 and deps.parse_target("")["process"] == ""


def test_parse_spec_and_limits():
    spec = deps.parse_spec("Ollama = 11434 ; whisper=http://127.0.0.1:8178\nollama.exe;;")
    assert [(d["label"], d["target"]) for d in spec] == [
        ("Ollama", "11434"), ("whisper", "http://127.0.0.1:8178"), ("ollama.exe", "ollama.exe")]
    assert all(d["source"] == "user" for d in spec)
    assert len(deps.parse_spec(";".join(f"s{i}={i + 1000}" for i in range(20)))) == deps.MAX_DEPS


class _Ctl:
    def __init__(self, found):
        self.found = found

    def dependencies(self):
        return self.found


class _Rt:
    def __init__(self, found, spec="", host=""):
        self.controller = _Ctl(found)
        self.server = {"deps": spec, "params": {"host": host} if host else {}}


def test_declared_merges_plugin_and_tile_without_duplicates():
    rt = _Rt([{"id": "upstream", "label_key": "dep.stt_upstream",
               "target": "http://127.0.0.1:8178"}],
             "whisper bis = 8178; Ollama = 11434")
    found = deps.declared(rt)
    assert [d["id"] for d in found] == ["upstream", "user1"]       # 8178 n'apparaît qu'une fois
    assert found[0]["source"] == "plugin" and found[1]["source"] == "user"

    class Broken:
        def dependencies(self):
            raise RuntimeError("boom")
    rt.controller = Broken()
    assert [d["label"] for d in deps.declared(rt)] == ["whisper bis", "Ollama"]


def test_probe_one_uses_listener_process_and_plugin_pids(monkeypatch):
    monkeypatch.setattr(deps.sysinfo, "listener_pid", lambda port, fresh=False:
                        os.getpid() if port == 8178 else None)
    monkeypatch.setattr(deps.sysinfo, "process_running", lambda name: None)
    deps._cache.clear()
    up = deps.probe_one({"id": "a", "target": "8178"})
    assert up["up"] and up["pids"] == [os.getpid()] and up["started"] and up["name"]
    down = deps.probe_one({"id": "b", "target": "8179"})
    assert not down["up"] and down["pids"] == [] and down["started"] is None
    assert not deps.probe_one({"id": "c", "target": "ghost.exe"})["up"]
    given = deps.probe_one({"id": "d", "label_key": "dep.hermes_desktop", "pids": [os.getpid()],
                            "up": True})
    assert given["up"] and given["pids"] == [os.getpid()]
    # hôte distant : simple test TCP (mis en cache), jamais de PID
    monkeypatch.setattr(deps.sysinfo, "tcp_open", lambda h, p, t=0.4: True)
    remote = deps.probe_one({"id": "e", "target": "nas.local:8123"})
    assert remote["up"] and remote["pids"] == [] and pres.dep_target(remote) == "nas.local:8123"
    assert pres.dep_target(up) == "8178"


def test_plugins_declare_their_dependencies():
    from bgc.plugins import command, sttbridge
    log = lambda *a, **k: None
    stt = sttbridge.SttBridgeController({"id": "stt", "type": "stt", "options": {}, "params": {
        "port": 8770, "upstream": "http://127.0.0.1:8178"}}, log)
    found = stt.dependencies()
    assert found[0]["target"] == "http://127.0.0.1:8178"
    assert found[0]["label_key"] == "dep.stt_upstream"
    cmd = command.CommandController({"id": "cmd", "type": "command", "options": {}, "params": {
        "command": "x", "port": 5240, "extra_checks": "llama-server = 9931"}}, log)
    assert [(d["label"], d["target"]) for d in cmd.dependencies()] == [("llama-server", "9931")]


def test_deps_field_persists_and_travels_in_tiles(tmp_path):
    cfg = config.Config(str(tmp_path / "config.json"))
    sid = cfg.add_server("command", "LlamaGUI", {"command": "x", "port": 5240})["id"]
    cfg.update_server(sid, deps="llama = 9931")
    cfg.save()
    again = config.Config(str(tmp_path / "config.json")).load()
    assert again.get_server(sid)["deps"] == "llama = 9931"
    payload = tiles.build(again.get_server(sid))
    assert tiles.parse(json.loads(json.dumps(payload)))["deps"] == "llama = 9931"


@pytest.fixture
def parent_with_child():
    code = ("import subprocess, sys, time; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "time.sleep(30)")
    proc = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.time() + 10
    while time.time() < deadline and len(psutil.Process(proc.pid).children(recursive=True)) < (
            3 if os.name == "nt" and sys.prefix != sys.base_prefix else 1):
        time.sleep(0.05)
    yield proc.pid
    for child in psutil.Process(proc.pid).children(recursive=True):
        child.kill()
    proc.kill()
    proc.wait()


def test_dependency_tree_is_excluded_from_the_parent(parent_with_child):
    """llama-server enfant de LlamaGUI : compté dans « serveur lié », pas deux fois."""
    cache = m.ProcessCache()
    children = psutil.Process(parent_with_child).children(recursive=False)
    dep_root = children[-1].pid if os.name != "nt" else children[0].pid
    whole = m.measure(cache, [parent_with_child], None, 4)
    without = m.measure(cache, [parent_with_child], None, 4, exclude=[dep_root])
    alone = m.measure(cache, [dep_root], None, 4)
    assert without["procs"] + alone["procs"] == whole["procs"] and without["procs"] >= 1
    # tuile enfant d'un serveur lié (dashboard lancé par Hermes Desktop) : la tuile garde son
    # arbre, le serveur lié ne la compte pas
    tile_only = m.measure(cache, [dep_root], None, 4, exclude=[parent_with_child])
    dep_only = m.measure(cache, [parent_with_child], None, 4, exclude=[dep_root])
    assert tile_only["procs"] == alone["procs"]
    assert tile_only["procs"] + dep_only["procs"] == whole["procs"]
    # une dépendance qui EST la racine de la tuile n'est pas retirée
    assert m.measure(cache, [parent_with_child], None, 4,
                     exclude=[parent_with_child])["procs"] == whole["procs"]


def test_sampler_accepts_targets_with_exclusions(parent_with_child):
    child = psutil.Process(parent_with_child).children(recursive=False)[0].pid
    sampler = m.MetricsSampler(lambda: [("t", [parent_with_child], [child]),
                                        ("t/dep:x", [child])], gpu=None)
    sampler.sample_once()
    assert sampler.latest("t")["procs"] >= 1 and sampler.latest("t/dep:x")["procs"] >= 1


def test_port_cells_name_each_port():
    i18n.set_language("en")
    try:
        top, roles, tip = pres.port_cells(
            {"type": "hermes", "params": {"port": 9119}},
            {"meta": {"ports": [{"port": 9119, "role_key": "port.dashboard"},
                                {"port": 8642, "role_key": "port.gateway"}]}})
        assert (top, roles) == ("9119 · 8642", "dashboard · gateway")
        assert "8642 — gateway" in tip
        assert pres.port_cells({"type": "stt", "params": {"port": 8770}}, {})[1] == "STT bridge"
        assert pres.port_cells({"type": "command", "params": {"port": 1234,
                                "check_url": "http://127.0.0.1:1234/v1/models"}}, {})[1] == "API"
        assert pres.port_cells({"type": "command", "params": {"port": 5240,
                                "url": "http://127.0.0.1:5240"}}, {})[1] == "web UI"
        assert pres.port_cells({"type": "wsl", "params": {}}, {}) == (pres.DASH, "", "")
        many = {"meta": {"ports": [{"port": p} for p in (1, 2, 3, 4)]}}
        assert pres.port_cells({}, many)[0] == "1 · 2 +2"
    finally:
        i18n.set_language("fr")


def test_mcp_exposes_dependencies_with_their_metrics():
    from tests.test_items import _App

    class _DApp(_App):
        def metrics_of(self, runtime, item_id=""):
            return ({"cpu": 7.0, "ram_mb": 900.0, "vram_mb": 2048.0, "procs": 1, "at": 1.0}
                    if item_id == "dep:upstream" else None)

    app = _DApp()
    app.runtimes[0].snapshot_data["deps"] = [{
        "id": "upstream", "label": "", "label_key": "dep.stt_upstream", "target": "8178",
        "host": "127.0.0.1", "port": 8178, "process": "", "up": True, "pids": [42],
        "name": "whisper-server.exe", "started": time.time() - 60, "source": "plugin"}]
    tools = mcpserver.McpTools(app)
    data = json.loads(tools.call("list_items", {"server_id": "dock"})["content"][0]["text"])
    dep = data["dependencies"][0]
    assert dep["id"] == "upstream" and dep["up"] and dep["target"] == "8178"
    assert dep["metrics"]["vram_mb"] == 2048.0 and 55 <= dep["uptime_s"] <= 70
    assert all(i["id"] != "dep:upstream" for i in data["items"])   # pas un élément actionnable
    servers = json.loads(tools.call("list_servers", {})["content"][0]["text"])["servers"]
    assert servers[0]["dependencies"][0]["label"] == i18n.t("dep.stt_upstream")
    assert tools._status(app.runtimes[0])["dependencies"][0]["pids"] == [42]


def test_runtime_refresh_publishes_dependencies(monkeypatch):
    """Instantané publié AVEC ses serveurs liés (jamais un instantané sans `deps` visible)."""
    from bgc import service
    from bgc.core import runtime as rt_mod
    monkeypatch.setattr(deps.sysinfo, "listener_pid", lambda port, fresh=False: None)
    seen = []

    class Ctl(service.Controller):
        def snapshot(self):
            return {"phase": "stopped", "rows": [], "actions": [], "meta": {}}

        def dependencies(self):
            seen.append(getattr(runtime, "snapshot_data", None))
            return [{"id": "x", "label": "X", "target": "9931"}]

    class Typ(service.ServerType):
        id = "test_deps_type"
        label_key = "type.command.label"
        desc_key = "type.command.desc"

        def controller(self, server, log):
            return Ctl(server, log)

    monkeypatch.setitem(service._REGISTRY, Typ.id, Typ())   # retiré après le test

    class App:
        def __getattr__(self, name):
            return lambda *a, **k: None
    runtime = rt_mod.ServerRuntime(App(), {"id": "t", "type": "test_deps_type", "label": "T",
                                           "params": {}, "options": {}, "actions": []})
    runtime.refresh()
    first = runtime.snapshot_data
    assert first["deps"][0]["id"] == "x" and first["deps"][0]["up"] is False
    runtime.refresh()
    # pendant le sondage suivant, l'instantané visible était encore le précédent, complet
    assert seen[-1] is first and "deps" in seen[-1]


# --- vue d'ensemble : noms toujours visibles, mode compact, sous-lignes -------------------
class _ORt:
    enabled, busy, stats, custom_states = True, False, {}, {}

    def __init__(self, rid, label, server, snapshot):
        self.id, self.label, self.server, self.snapshot_data = rid, label, server, snapshot

    @property
    def state(self):
        return "ok"

    def status_text(self):
        return "En marche"


class _OApp:
    metrics = None

    def __init__(self, root, runtimes):
        self.root, self.runtimes = root, runtimes

    def show_add(self):
        pass

    def select_server(self, sid):
        pass

    def run_action(self, *a, **k):
        pass

    def metrics_of(self, runtime, item_id=""):
        return {"cpu": 3.0, "ram_mb": 512.0, "vram_mb": 1024.0, "procs": 1, "at": 1.0}


def test_overview_keeps_names_and_goes_compact_when_narrow():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("pas d'affichage")
    from bgc.ui.views import overview
    try:
        snap = {"phase": "running", "rows": [], "url": "",
                "actions": [{"id": "toggle", "label_key": "btn.stop", "style": "danger",
                             "main": True}],
                "meta": {"ports": [{"port": 8770, "role_key": "port.stt_bridge"}]},
                "deps": [{"id": "upstream", "label_key": "dep.stt_upstream", "label": "",
                          "target": "http://127.0.0.1:8178", "host": "127.0.0.1",
                          "port": 8178, "process": "", "up": True, "pids": [1],
                          "name": "whisper", "started": time.time() - 90}]}
        rt = _ORt("stt-1", "STT OpnWh to Herm", {"type": "stt", "params": {"port": 8770}}, snap)
        app = _OApp(root, [rt])
        view = overview.OverviewView(root, app)
        view.pack(fill="both", expand=True)
        root.geometry("1400x600")
        view.paint()
        root.update()
        assert not view._compact
        row = view._rows["stt-1"]
        assert row["port"].cget("text") == "8770" and row["port_role"].cget("text")
        assert len(row["deps"]) == 1 and row["deps"][0]["cells"][2].cget("text") != pres.DASH
        root.geometry("820x600")          # ≈ fenêtre de l'utilisateur moins le rail
        root.update()
        view._apply_layout()
        root.update()
        assert view._compact
        assert row["row"].grid_bbox(1, 0)[2] >= overview.sc(overview.NAME_MIN) - 2
        assert "RAM" in row["word"].cget("text")       # ressources passées sous le nom
        assert not row["uptime"].winfo_ismapped()
        root.geometry("1400x600")
        root.update()
        view._apply_layout()
        assert not view._compact
    finally:
        root.destroy()
