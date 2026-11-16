"""Chargement, normalisation et écriture de `config.json` (fichiers temporaires)."""
from __future__ import annotations

import json
import os

from bgc import config as config_mod


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def test_new_install_creates_empty_config(tmp_path):
    path = tmp_path / "HermesServ" / "config.json"
    cfg = config_mod.Config(str(path)).load()
    assert cfg.created and path.is_file()
    assert cfg.servers == []
    assert cfg.general["language"] == "fr"


def test_roundtrip_add_move_duplicate_remove(tmp_path):
    path = str(tmp_path / "config.json")
    cfg = config_mod.Config(path).load()
    a = cfg.add_server("command", "A", {"port": 1})
    b = cfg.add_server("command", "B", {"port": 2})
    assert [s["id"] for s in cfg.servers] == ["command-1", "command-2"]
    assert cfg.move_server_to(b["id"], 0)
    copy = cfg.duplicate_server(a["id"], "")
    assert copy["label"].startswith("A")
    again = config_mod.Config(path).load()
    assert [s["label"] for s in again.servers] == ["B", "A", copy["label"]]
    assert again.remove_server(a["id"])
    assert len(config_mod.Config(path).load().servers) == 2


def test_portwatch_migrates_to_command(tmp_path):
    path = str(tmp_path / "config.json")
    _write(path, {"servers": [{"id": "p", "type": "portwatch", "params": {"port": 5240}}]})
    cfg = config_mod.Config(path).load()
    assert cfg.servers[0]["type"] == "command"
    assert cfg.servers[0]["params"]["port"] == 5240


def test_unknown_general_keys_dropped(tmp_path):
    path = str(tmp_path / "config.json")
    _write(path, {"general": {"language": "en", "bogus": 1}, "servers": []})
    cfg = config_mod.Config(path).load()
    assert cfg.general["language"] == "en" and "bogus" not in cfg.general


def test_actions_normalised(tmp_path):
    actions = [{"label": "x", "target": "y", "check": "port:1"}, {"label": ""}, "bad",
               {"label": "a", "target": "b"}, {"label": "c", "target": "d"},
               {"label": "e", "target": "f"}]
    out = config_mod.Config._normalise_actions(actions)
    assert out[0] == {"label": "x", "target": "y", "check": "port:1"}
    assert len(out) <= 3


def test_save_is_atomic(tmp_path):
    path = str(tmp_path / "config.json")
    cfg = config_mod.Config(path).load()
    cfg.save()
    assert not os.path.exists(path + ".tmp")


def test_corrupt_config_is_set_aside_not_overwritten(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"servers": [ broken', encoding="utf-8")
    good = {"general": {"language": "en"}, "servers": [{"id": "a", "type": "command",
                                                         "label": "A", "params": {}}]}
    old = tmp_path / "config.json.bak-20261101T000000"
    old.write_text(json.dumps({"servers": []}), encoding="utf-8")
    new = tmp_path / "config.json.bak-20261110T000000"
    new.write_text(json.dumps(good), encoding="utf-8")
    os.utime(old, (1, 1))
    (tmp_path / "config.json.bak-bad").write_text("nope", encoding="utf-8")

    cfg = config_mod.Config(str(path)).load()
    assert cfg.corrupt is not None
    aside = cfg.corrupt["path"]
    assert ".corrupt-" in aside and os.path.isfile(aside)
    assert open(aside, encoding="utf-8").read() == '{"servers": [ broken'
    assert not path.exists()                       # rien n'a été écrit par-dessus
    assert cfg.corrupt["backups"][0] == str(new)   # la plus récente, lisible
    assert str(tmp_path / "config.json.bak-bad") not in cfg.corrupt["backups"]

    restored = cfg.restore_backup(cfg.corrupt["backups"][0])
    assert restored.corrupt is None and restored.servers[0]["label"] == "A"
    assert restored.general["language"] == "en"


def test_non_object_json_is_corrupt(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    cfg = config_mod.Config(str(path)).load()
    assert cfg.corrupt and cfg.servers == []
