"""Cœur sans Tk : actions partagées (UI, tray, MCP), règle des actions principales, sondage."""
from __future__ import annotations

import queue
import time

from bgc import presentation as pres
from bgc.core import actions as core_actions
from bgc.core.actions import Actions
from bgc.core.poller import Poller, transition


class _Controller:
    def __init__(self):
        self.calls = []
        self.phase = "stopped"

    def snapshot(self):
        return {"phase": self.phase, "rows": [], "url": "",
                "actions": [{"id": "toggle", "label_key": "btn.stop" if self.phase == "running"
                             else "btn.start"},
                            {"id": "browser", "label_key": "btn.browser"}],
                "items": [{"id": "web", "label": "web", "phase": "running",
                           "actions": [{"id": "stop", "label_key": "btn.stop", "main": True}]}]}

    def start(self):
        self.calls.append("start"); self.phase = "running"; return "log.started"

    def stop(self):
        self.calls.append("stop"); self.phase = "stopped"; return "log.stopped"

    def action(self, action_id):
        self.calls.append(action_id); return "log.done"

    def item_action(self, item_id, action_id):
        self.calls.append((item_id, action_id)); return "log.item_done"


class _Runtime:
    def __init__(self):
        self.id, self.label, self.server = "t1", "Test", {"actions": [], "params": {}}
        self.controller = _Controller()
        self.snapshot_data = None
        self.busy = False
        self.stats = {"falls": 0, "starts": 0, "since": 0.0, "fell": False}
        self.expect_stop_until = 0.0
        self.custom_states = {}
        self.last_phase = None
        self.logs = []

    @property
    def phase(self):
        return (self.snapshot_data or {}).get("phase", "unknown")

    def refresh(self):
        self.snapshot_data = self.controller.snapshot()
        self.last_phase = self.phase

    def log_key(self, key, **params):
        self.logs.append(key)


class _Host:
    def __init__(self, runtimes):
        self.runtimes = runtimes
        self.events = queue.Queue()

    def request_repaint(self):
        self.events.put("repaint")


def test_perform_start_stop_and_item_action_share_one_path():
    runtime = _Runtime(); runtime.refresh()
    host = _Host([runtime]); actions = Actions(host)
    assert actions.perform(runtime, "start") == "log.started"
    assert runtime.phase == "running" and not runtime.busy
    assert actions.perform(runtime, "stop") == "log.stopped"
    assert runtime.expect_stop_until > time.time()   # arrêt demandé : pas d'alerte de chute
    assert actions.perform(runtime, "stop", item_id="web") == "log.item_done"
    assert runtime.controller.calls == ["start", "stop", ("web", "stop")]
    assert host.events.qsize() == 3


def test_toggle_while_running_counts_as_expected_stop():
    runtime = _Runtime(); runtime.controller.phase = "running"; runtime.refresh()
    Actions(_Host([runtime])).prepare(runtime, "toggle")
    assert runtime.expect_stop_until > time.time()


def test_main_actions_are_declared_by_the_plugin():
    acts = [{"id": "toggle_gateway", "main": True}, {"id": "toggle", "main": True},
            {"id": "toggle_extra"}, {"id": "browser"}]
    assert [a["id"] for a in pres.primary_actions(acts)] == ["toggle_gateway", "toggle"]
    # sans déclaration : règle historique (préfixe « toggle »)
    assert [a["id"] for a in pres.primary_actions(acts[2:])] == ["toggle_extra"]


def test_is_stop_reads_declaration_then_label():
    assert core_actions.is_stop({"id": "x", "stops": True})
    assert not core_actions.is_stop({"id": "x", "label_key": "btn.stop", "stops": False})
    assert core_actions.is_stop({"id": "toggle_gateway", "label_key": "btn.hermes_gw_stop"})
    assert not core_actions.is_stop({"id": "toggle", "label_key": "btn.start"})


def test_tray_menu_has_items_and_no_hardcoded_ids():
    runtime = _Runtime(); runtime.refresh()
    entries = Actions(_Host([runtime])).menu_entries(runtime)
    payloads = [e[1] for e in entries]
    assert "action:t1:toggle" in payloads
    item = next(e for e in entries if isinstance(e[1], list))
    assert item[1][0][1] == "item:t1:web:stop"


def test_transition_detects_falls_but_not_requested_stops():
    runtime = _Runtime()
    assert transition(runtime, "stopped", "running") is None and runtime.stats["starts"] == 1
    assert transition(runtime, "running", "stopped") == "fall" and runtime.stats["fell"]
    runtime.expect_stop_until = time.time() + 20
    assert transition(runtime, "running", "stopped") is None


def test_poller_probes_and_reports():
    runtime = _Runtime()
    seen = []
    poller = Poller(lambda: [runtime], lambda r, before: seen.append((r.id, before)),
                    interval=0.05).start()
    deadline = time.time() + 3
    while not seen and time.time() < deadline:
        time.sleep(0.02)
    poller.stop()
    assert seen and seen[0][0] == "t1" and runtime.snapshot_data is not None
