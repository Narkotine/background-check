"""Modèle « liste d'items » : adaptateur (un élément implicite), outils MCP list_items/item_action."""
from __future__ import annotations

import json
from collections import deque

from bgc import mcpserver
from bgc import presentation as pres
from bgc.core.actions import Actions


def test_single_service_types_are_one_implicit_item():
    snap = {"phase": "running", "rows": [{"label_key": "row.port"}],
            "actions": [{"id": "toggle", "label_key": "btn.stop"}]}
    items = pres.items_of(snap, "Ollama")
    assert len(items) == 1 and items[0]["id"] == "" and items[0]["implicit"]
    assert items[0]["label"] == "Ollama" and items[0]["actions"][0]["id"] == "toggle"
    assert not pres.has_items(snap) and pres.item_counts(snap) == (0, 0)


def test_explicit_items_are_normalised():
    snap = {"phase": "running", "items": [
        {"id": "web", "label": "web", "phase": "running", "actions": [{"id": "stop"}]},
        {"id": "db", "phase": "stopped"},
        {"label": "sans id"}]}
    items = pres.items_of(snap)
    assert [i["id"] for i in items] == ["web", "db"]
    assert items[1]["label"] == "db" and items[1]["actions"] == []
    assert pres.item_counts(snap) == (1, 2)
    assert pres.item_state("running") == "ok" and pres.item_state("stopped") == "idle"


class _Ctl:
    def __init__(self):
        self.done = []

    def snapshot(self):
        return {"phase": "running", "rows": [], "actions": [],
                "items": [{"id": "web", "label": "web", "phase": "running",
                           "actions": [{"id": "stop", "label_key": "btn.stop", "main": True},
                                       {"id": "start", "label_key": "btn.start",
                                        "enabled": False}]}]}

    def item_action(self, item_id, action_id):
        self.done.append((item_id, action_id))
        return "log.phase_stopped"


class _Rt:
    id, label, enabled, busy = "dock", "Docker", True, False
    server = {"type": "docker", "params": {}, "options": {}, "actions": []}
    expect_stop_until = 0.0

    def __init__(self):
        self.controller = _Ctl()
        self.snapshot_data = self.controller.snapshot()
        self.log_lines = deque()
        self.stats = {"fell": False}

    @property
    def phase(self):
        return self.snapshot_data["phase"]

    def refresh(self):
        self.snapshot_data = self.controller.snapshot()


class _App:
    def __init__(self):
        self.runtimes = [_Rt()]
        self.logged = []
        self.actions = Actions(self)

    def request_repaint(self):
        pass

    def log_app(self, key, **params):
        self.logged.append(key)


def _call(tools, name, args):
    result = tools.call(name, args)
    return json.loads(result["content"][0]["text"]), result.get("isError", False)


def test_mcp_lists_and_runs_item_actions():
    app = _App()
    tools = mcpserver.McpTools(app)
    data, err = _call(tools, "list_items", {"server_id": "dock"})
    assert not err and data["has_items"] and data["items"][0]["id"] == "web"
    assert [a["id"] for a in data["items"][0]["actions"]] == ["stop", "start"]
    data, err = _call(tools, "item_action", {"server_id": "dock", "item_id": "web",
                                             "action": "stop"})
    assert not err and data["ok"] and app.runtimes[0].controller.done == [("web", "stop")]
    assert "log.mcp_agent_item" in app.logged
    # action grisée, inconnue, élément inconnu : refus explicites, rien d'exécuté
    for args in ({"item_id": "web", "action": "start"}, {"item_id": "web", "action": "rm"},
                 {"item_id": "nope", "action": "stop"}):
        data, err = _call(tools, "item_action", {"server_id": "dock", **args})
        assert err and not data["ok"]
    assert app.runtimes[0].controller.done == [("web", "stop")]


def test_get_server_reports_items():
    tools = mcpserver.McpTools(_App())
    status = tools._status(tools.app.runtimes[0])
    assert status["items"][0]["label"] == "web"
