"""Contrôle d'accès du serveur MCP : Origin, Host, Content-Type, jeton (revue du 16 nov 2026)."""
from __future__ import annotations

import http.client
import json
import socket

import pytest

from bgc import config as config_mod
from bgc import mcpserver

TOKEN = "t" * 43


def _headers(**extra):
    base = {"Host": "127.0.0.1:8781", "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}"}
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


def test_valid_request_admitted():
    assert mcpserver.check_request(_headers(), "POST", 8781, TOKEN) is None
    assert mcpserver.check_request(_headers(Host="localhost:8781"), "POST", 8781, TOKEN) is None


@pytest.mark.parametrize("extra,status", [
    ({"Origin": "https://evil.example"}, 403),
    ({"Origin": "null"}, 403),
    ({"Host": "evil.example:8781"}, 403),          # DNS rebinding
    ({"Host": "127.0.0.1:9999"}, 403),
    ({"Host": None}, 403),
    ({"Content-Type": "text/plain"}, 415),         # requête « simple » d'un navigateur
    ({"Content-Type": None}, 415),
    ({"Authorization": None}, 401),
    ({"Authorization": "Bearer wrong"}, 401),
    ({"Authorization": f"Basic {TOKEN}"}, 401),
])
def test_refusals(extra, status):
    refusal = mcpserver.check_request(_headers(**extra), "POST", 8781, TOKEN)
    assert refusal is not None and refusal[0] == status


def test_no_token_configured_refuses():
    assert mcpserver.check_request(_headers(), "POST", 8781, "")[0] == 503


def test_config_token_created_once(tmp_path):
    cfg = config_mod.Config(str(tmp_path / "c.json")).load()
    assert cfg.ensure_mcp_token() is True
    token = cfg.general["mcp_token"]
    assert len(token) >= 32
    assert cfg.ensure_mcp_token() is False and cfg.general["mcp_token"] == token
    cfg.save()
    assert config_mod.Config(str(tmp_path / "c.json")).load().general["mcp_token"] == token


class _FakeApp:
    runtimes: list = []
    cfg = None

    def __init__(self):
        self.logged = []

    def log_app(self, key, **params):
        self.logged.append((key, params))


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server():
    app = _FakeApp()
    srv = mcpserver.McpServer(app, port=_free_port(), token=TOKEN)
    assert srv.start(), srv.error
    yield srv, app
    srv.stop()


def _post(port, body, headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", "/mcp", body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18"}})


def test_live_server_with_token(server):
    srv, _ = server
    status, data = _post(srv.port, INIT, {"Content-Type": "application/json",
                                          "Authorization": f"Bearer {TOKEN}"})
    assert status == 200
    assert json.loads(data)["result"]["serverInfo"]["name"] == "background-check"


def test_live_server_refuses_browser_csrf(server):
    srv, app = server
    # Ce qu'enverrait une page web : text/plain, Origin, pas de jeton.
    status, _ = _post(srv.port, INIT, {"Content-Type": "text/plain",
                                       "Origin": "https://evil.example"})
    assert status == 403
    assert app.logged and app.logged[0][0] == "log.mcp_refused"


def test_live_server_refuses_without_token(server):
    srv, _ = server
    status, _ = _post(srv.port, INIT, {"Content-Type": "application/json"})
    assert status == 401


def test_token_can_change_live(server):
    srv, _ = server
    srv.token = "n" * 43
    status, _ = _post(srv.port, INIT, {"Content-Type": "application/json",
                                       "Authorization": f"Bearer {TOKEN}"})
    assert status == 401
    status, _ = _post(srv.port, INIT, {"Content-Type": "application/json",
                                       "Authorization": f"Bearer {'n' * 43}"})
    assert status == 200
