"""Robustesse : journal fichier, instance unique, instantané des écouteurs, arrêt propre,
`ui_call` annulable (revue du 16 nov 2026)."""
from __future__ import annotations

import logging
import queue
import socket
import sys
import threading
import time

import pytest

from bgc import applog, instance, sysinfo


def test_applog_writes_file_and_hooks(tmp_path):
    path = applog.setup(str(tmp_path / "logs"))
    applog.LOGGER.info("bonjour é")
    for handler in applog.LOGGER.handlers:
        handler.flush()
    assert threading.excepthook is not threading.__excepthook__
    # Le fichier existe dans le dossier demandé lors du premier setup (idempotent ensuite).
    files = [h.baseFilename for h in applog.LOGGER.handlers
             if isinstance(h, logging.FileHandler)]
    assert files
    with open(files[0], encoding="utf-8") as fh:
        assert "bonjour é" in fh.read()


def test_thread_exception_is_logged(tmp_path):
    applog.setup(str(tmp_path / "logs"))
    worker = threading.Thread(target=lambda: 1 / 0, name="boom-thread")
    worker.start()
    worker.join()
    for handler in applog.LOGGER.handlers:
        handler.flush()
    files = [h.baseFilename for h in applog.LOGGER.handlers
             if isinstance(h, logging.FileHandler)]
    with open(files[0], encoding="utf-8") as fh:
        content = fh.read()
    assert "boom-thread" in content and "ZeroDivisionError" in content


def test_instance_key_depends_on_config_path():
    a = instance.key_for(r"C:\Users\x\AppData\Local\HermesServ\config.json")
    b = instance.key_for(r"C:\temp\test-config.json")
    assert a != b and len(a) == 12
    assert instance.tray_title(a).startswith("BackgroundCheckTray:")


@pytest.mark.skipif(sys.platform != "win32", reason="mutex nommé Windows")
def test_mutex_refuses_second_owner():
    key = instance.key_for(f"pytest-{time.time()}")
    assert instance.acquire(key) is True
    assert instance.acquire(key) is False   # même processus, second CreateMutex


def test_listener_snapshot_shared_and_finds_our_socket(monkeypatch):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        snap = sysinfo.listeners_snapshot(fresh=True)
        if snap is None:
            pytest.skip("psutil ne peut pas lire la table TCP ici")
        assert port in snap
        assert sysinfo.listener_pid(port) is not None
        calls = []
        real = sysinfo.psutil.net_connections
        monkeypatch.setattr(sysinfo.psutil, "net_connections",
                            lambda **kw: calls.append(1) or real(**kw))
        for _ in range(5):            # cinq tuiles dans le même cycle
            sysinfo.listener_pid(port)
        assert len(calls) == 0         # instantané partagé (< LISTENERS_TTL)
        sysinfo.listener_pid(port, fresh=True)
        assert len(calls) == 1
    finally:
        server.close()
    assert sysinfo.listener_pid(port, fresh=True) is None


def test_terminate_tree_graceful_falls_back_to_kill():
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        started = time.time()
        assert sysinfo.terminate_tree(proc.pid, wait=10.0, graceful=1.0) is True
        assert time.time() - started < 10
    finally:
        if proc.poll() is None:
            proc.kill()


@pytest.mark.skipif(sys.platform != "win32", reason="CTRL_BREAK : Windows seulement")
def test_ctrl_break_stops_a_console_child_gracefully(tmp_path):
    """Un enfant créé comme le fait CommandController reçoit CTRL_BREAK et se ferme seul.

    Sauté sous pytest lancé depuis une console (l'envoi n'est volontairement pas tenté quand
    l'app a sa propre console). Vérifié le 16 nov 2026 sous pythonw (sans console) : arrêt
    propre en 0,06 s, y compris via `shell=True` (cmd.exe chef de groupe).
    """
    import subprocess
    marker = tmp_path / "bye.txt"
    code = ("import signal, sys, time\n"
            f"def bye(*_):\n    open(r'{marker}', 'w').write('ok'); sys.exit(0)\n"
            "signal.signal(signal.SIGBREAK, bye)\n"
            # sleep() court : sous Windows, un long sleep n'est interrompu que par SIGINT.
            "while True: time.sleep(0.1)\n")
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen([sys.executable, "-c", code], creationflags=flags)
    time.sleep(1.0)
    try:
        sent = sysinfo.send_ctrl_break(proc.pid)
        if not sent:
            pytest.skip("console de test attachée : envoi volontairement non tenté")
        proc.wait(timeout=10)
        assert marker.read_text() == "ok"
    finally:
        if proc.poll() is None:
            proc.kill()


def test_ui_call_cancels_queued_job():
    tk = pytest.importorskip("tkinter")  # noqa: F841 (hermes_control importe tkinter)
    import background_check
    app = object.__new__(background_check.App)
    app.events = queue.Queue()
    ran = []
    errors = []

    def caller():
        try:
            app.ui_call(lambda: ran.append(1), timeout=0.2)
        except TimeoutError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=caller)
    worker.start()
    worker.join()
    assert errors                      # l'appelant a reçu un refus…
    app.tray = None
    app._drain()                       # …et le fil Tk ne l'exécute PAS plus tard
    assert ran == []


def test_ui_call_runs_when_drained():
    pytest.importorskip("tkinter")
    import background_check
    app = object.__new__(background_check.App)
    app.events = queue.Queue()
    app.tray = None
    result = []
    worker = threading.Thread(target=lambda: result.append(app.ui_call(lambda: 42, 5.0)))
    worker.start()
    deadline = time.time() + 5
    while worker.is_alive() and time.time() < deadline:
        app._drain()
        time.sleep(0.01)
    worker.join()
    assert result == [42]
