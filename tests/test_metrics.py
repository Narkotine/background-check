"""Ressources par service : PID des sondes, arbre de processus, échantillonneur, VRAM, MCP."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import psutil
import pytest

from bgc import gpu, i18n, mcpserver
from bgc import presentation as pres
from bgc.core import metrics as m


def test_pids_come_from_meta_and_items():
    assert m.snapshot_pids({"meta": {"pid": 12, "pids": [12, "7", None, -1, "x"]}}) == [12, 7]
    assert m.snapshot_pids({"meta": {"pid": None}}) == [] and m.snapshot_pids(None) == []
    assert m.item_pids({"pid": 5}) == [5] and m.item_pids({}) == []
    snap = {"phase": "running", "meta": {"pid": 3}}
    assert pres.items_of(snap)[0]["pids"] == [3]                    # élément implicite
    snap = {"items": [{"id": "a", "pid": 9}, {"id": "b"}]}
    assert [i["pids"] for i in pres.items_of(snap)] == [[9], []]


@pytest.fixture
def child_tree():
    """Un parent Python qui lance un enfant (sleep) : l'arbre mesuré doit compter les deux."""
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


def test_measure_sums_the_process_tree(child_tree):
    cache = m.ProcessCache()
    # (sous Windows, le python.exe d'un venv est un lanceur : chaque « python » = 2 processus)
    pids = [child_tree] + [c.pid for c in psutil.Process(child_tree).children(recursive=True)]
    first = m.measure(cache, [child_tree], None, 4)
    assert first["procs"] == len(pids) >= 2 and first["ram_mb"] > 1 and first["vram_mb"] is None
    assert first["cpu"] == 0.0                     # amorce psutil : premier passage à 0
    vram = {pids[0]: 100 * m.MB, pids[1]: 50 * m.MB, 999999: 1}
    assert m.measure(cache, [child_tree], vram, 4)["vram_mb"] == 150.0
    assert cache.get(child_tree) is cache.get(child_tree)      # Process conservé
    assert m.measure(m.ProcessCache(), [2 ** 22 + 12345], None, 4) is None


def test_shared_pid_is_measured_once_per_pass():
    calls = []

    class _P:
        pid = 42

        def cpu_percent(self, _interval):
            calls.append(1)
            return 50.0 if len(calls) == 1 else 0.0

        def memory_info(self):
            return type("M", (), {"rss": 10 * m.MB})()

    cache = m.ProcessCache()
    assert cache.usage(_P()) == cache.usage(_P()) == (50.0, 10 * m.MB) and len(calls) == 1
    cache.prune()
    cache.usage(_P())
    assert len(calls) == 2


def test_own_process_is_measured_without_its_children(child_tree):
    # Pont intégré (pid = BGC) : les enfants de BGC sont les autres services, pas lui.
    procs = m.tree(m.ProcessCache(), os.getpid())
    assert [p.pid for p in procs] == [os.getpid()]


class _FakeGpu:
    def __init__(self, per_pid):
        self.per_pid = per_pid

    def sample(self):
        return self.per_pid, 2048 * m.MB

    def close(self):
        pass


def test_sampler_publishes_latest_history_and_gpu_total():
    me = os.getpid()
    targets = [("tile", [me]), ("tile/item", [me]), ("empty", [])]
    sampler = m.MetricsSampler(lambda: targets, gpu=_FakeGpu({me: 300 * m.MB}))
    sampler.sample_once()
    sampler.sample_once()
    latest = sampler.latest("tile")
    assert latest["vram_mb"] == 300.0 and latest["ram_mb"] > 1 and latest["procs"] == 1
    assert sampler.latest("tile/item") and sampler.latest("empty") is None
    assert len(sampler.history("tile")) == 2
    assert sampler.gpu_total() == {"used_mb": 2048.0, "total_mb": None}
    targets[:] = [("tile/item", [me])]                         # la tuile disparaît
    sampler.sample_once()
    assert sampler.latest("tile") is None and sampler.history("tile") == []


def test_sampler_without_gpu_counters_shows_dash():
    class _NoGpu(_FakeGpu):
        def sample(self):
            return None
    sampler = m.MetricsSampler(lambda: [("t", [os.getpid()])], gpu=_NoGpu({}))
    sampler.sample_once()
    assert sampler.latest("t")["vram_mb"] is None and sampler.gpu_total() is None
    i18n.set_language("fr")
    assert pres.metrics_cells(sampler.latest("t"))[2] == "—"


def test_gpu_instance_names_are_parsed_per_pid():
    values = {"pid_11576_luid_0x00000000_0x0000D1E5_phys_0": 1000,
              "pid_11576_luid_0x00000000_0x0000AAAA_phys_0": 24,
              "pid_4_luid_0x0_0x1_phys_0": 0,
              "_Total": 5}
    assert gpu.parse_process_instances(values) == {11576: 1024}
    if os.name != "nt":
        assert not gpu.GpuCounters().available and gpu.total_vram_bytes() is None


def test_formatting_is_localised():
    i18n.set_language("fr")
    assert pres.fmt_cpu(0.44) == "0,4 %" and pres.fmt_cpu(37.6) == "38 %"
    assert pres.fmt_mb(412.4) == "412 Mo" and pres.fmt_mb(2150) == "2,1 Go"
    assert pres.fmt_mb(None) == "—" and pres.metrics_text(None) == ""
    assert pres.gpu_text({"used_mb": 6348, "total_mb": 16368}) == "VRAM 6,2 Go / 16,0 Go"
    i18n.set_language("en")
    assert pres.fmt_cpu(0.44) == "0.4%" and pres.fmt_mb(2150) == "2.1 GB"
    assert pres.metrics_text({"cpu": 1, "ram_mb": 10, "vram_mb": None}) == \
        "CPU 1.0%  ·  RAM 10 MB  ·  VRAM —"
    i18n.set_language("fr")


def test_mcp_exposes_metrics_per_tile_and_item():
    from tests.test_items import _App

    class _MApp(_App):
        def metrics_of(self, runtime, item_id=""):
            return {"cpu": 2.5, "ram_mb": 100.0, "vram_mb": None, "procs": 2, "at": 1.0,
                    "item": item_id}

    tools = mcpserver.McpTools(_MApp())
    result = tools.call("list_items", {"server_id": "dock"})
    data = json.loads(result["content"][0]["text"])
    assert data["items"][0]["metrics"] == {"cpu_percent": 2.5, "ram_mb": 100.0,
                                           "vram_mb": None, "processes": 2, "sampled_at": 1.0}
    result = tools.call("list_servers", {})
    servers = json.loads(result["content"][0]["text"])["servers"]
    assert servers[0]["metrics"]["ram_mb"] == 100.0
    assert tools._status(tools.app.runtimes[0])["metrics"]["cpu_percent"] == 2.5
    # app sans échantillonneur (tests, anciens appels) : null, jamais d'erreur
    assert mcpserver.McpTools(_App())._summary(_App().runtimes[0])["metrics"] is None
