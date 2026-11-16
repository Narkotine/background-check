"""Fichiers de tuile `.tile.json` : validation."""
from __future__ import annotations

import pytest

from bgc import tiles


def _payload(**extra):
    data = {"format": tiles.FORMAT, "version": 1, "type": "command", "label": "Demo",
            "params": {"port": 1234, "command": "echo hi"}, "options": {}, "actions": []}
    data.update(extra)
    return data


def test_parse_ok():
    parsed = tiles.parse(_payload())
    assert parsed["type"] == "command" and parsed["label"] == "Demo"


@pytest.mark.parametrize("bad,key", [
    ({"format": "nope"}, "tile.error.format"),
    ({"version": 99}, "tile.error.version"),
    ({"type": ""}, "tile.error.type"),
    ({"label": ""}, "tile.error.label"),
])
def test_parse_rejects(bad, key):
    with pytest.raises(ValueError) as exc:
        tiles.parse(_payload(**bad))
    assert str(exc.value) == key


def test_actions_capped():
    parsed = tiles.parse(_payload(actions=[{"label": str(i), "target": "x"} for i in range(5)]))
    assert len(parsed["actions"]) == 3


def test_export_drops_env_and_secrets_by_default():
    server = {"type": "command", "label": "X",
              "params": {"command": "run", "env": "API_KEY=abc", "api_token": "zzz"}}
    data = tiles.build(server)
    assert data["params"]["env"] == "" and data["params"]["api_token"] == ""
    assert data["params"]["command"] == "run"
    assert tiles.build(server, include_env=True)["params"]["env"] == "API_KEY=abc"
    assert tiles.has_env(server) and not tiles.has_env({"params": {"env": " "}})


def test_safe_options_forces_auto_start_off():
    options, disabled = tiles.safe_options({"start_on_app_start": True, "auto_restart": True,
                                            "stop_on_quit": True})
    assert options == {"start_on_app_start": False, "auto_restart": False, "stop_on_quit": True}
    assert set(disabled) == {"start_on_app_start", "auto_restart"}


def test_preview_lists_commands_actions_and_env():
    payload = tiles.parse(_payload(
        params={"command": "evil.exe", "env": "A=1\nB=2", "port": 1},
        actions=[{"label": "Run", "target": "calc.exe"}],
        options={"start_on_app_start": True}))
    keys = [key for key, _ in tiles.preview(payload)]
    assert "tile.preview_param" in keys and "tile.preview_action" in keys
    assert "tile.preview_env" in keys and "tile.preview_autostart_off" in keys
    assert "tile.preview_none" not in keys
    env = next(p for k, p in tiles.preview(payload) if k == "tile.preview_env")
    assert env["names"] == "A, B"          # les noms, jamais les valeurs


def test_preview_watch_only():
    payload = tiles.parse(_payload(params={"port": 5240},
                                   actions=[{"label": "Web", "target": "http://x"}]))
    assert "tile.preview_none" in [key for key, _ in tiles.preview(payload)]


def test_import_path_never_auto_starts(tmp_path):
    """Chemin réel de l'import (sans Tk) : options du fichier → safe_options → config."""
    from bgc import config as config_mod, service
    payload = tiles.parse(_payload(options={"start_on_app_start": True, "auto_restart": True}))
    server_type = service.get(payload["type"])
    options = dict(server_type.default_options())
    options.update(payload["options"])
    options, _ = tiles.safe_options(options)
    cfg = config_mod.Config(str(tmp_path / "c.json")).load()
    server = cfg.add_server(payload["type"], payload["label"],
                            server_type.validate(payload["params"]), options)
    assert server["options"]["start_on_app_start"] is False
    assert server["options"]["auto_restart"] is False
