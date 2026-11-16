"""Renommage HermesServ → Background Check (16 nov 2026) : reprise des données, compatibilité."""
from __future__ import annotations

import json

from bgc import APP_ID, APP_NAME, OLD_APP_ID, config, i18n, migration, tiles


def _old_install(tmp_path):
    old = tmp_path / OLD_APP_ID
    (old / "logs").mkdir(parents=True)
    (old / "plugins" / "mine").mkdir(parents=True)
    (old / "config.json").write_text(json.dumps({"general": {"mcp_token": "secret-token"},
                                                 "servers": [{"id": "a"}]}), encoding="utf-8")
    (old / "config.json.bak-1").write_text("{}", encoding="utf-8")
    (old / "logs" / "hermesserv.log").write_text("ancien journal\n", encoding="utf-8")
    (old / "plugins" / "mine" / "plugin.json").write_text("{}", encoding="utf-8")
    return old


def test_data_dir_is_copied_once_and_old_kept(tmp_path):
    old = _old_install(tmp_path)
    new = tmp_path / APP_ID
    assert migration.migrate_data_dir(str(new), str(old), test_config="") == str(old)
    data = json.loads((new / "config.json").read_text(encoding="utf-8"))
    assert data["general"]["mcp_token"] == "secret-token"     # le jeton MCP reste valable
    assert (new / "logs" / "hermesserv.log").is_file()
    assert (new / "plugins" / "mine" / "plugin.json").is_file()
    assert (new / "config.json.bak-1").is_file()
    assert (old / "config.json").is_file()                     # copie, pas déplacement
    assert not (tmp_path / (APP_ID + ".migration-tmp")).exists()
    # Une seconde fois : le nouveau dossier existe, rien n'est recopié par-dessus.
    (new / "config.json").write_text("{}", encoding="utf-8")
    assert migration.migrate_data_dir(str(new), str(old), test_config="") == ""
    assert (new / "config.json").read_text(encoding="utf-8") == "{}"


def test_no_migration_without_old_dir_or_for_test_instances(tmp_path):
    new = tmp_path / APP_ID
    assert migration.migrate_data_dir(str(new), str(tmp_path / "absent"), test_config="") == ""
    assert not new.exists()
    old = _old_install(tmp_path)
    assert migration.migrate_data_dir(str(new), str(old), test_config="x.json") == ""
    assert not new.exists()


def test_paths_use_new_name():
    assert config.CONFIG_DIR.endswith(APP_ID)
    assert config.OLD_CONFIG_DIR.endswith(OLD_APP_ID)


def test_old_tile_format_still_imports():
    data = tiles.build({"type": "command", "label": "X", "params": {}, "options": {}})
    assert data["format"] == "bgc-tile"
    data["format"] = "hermesserv-tile"
    assert tiles.parse(data)["label"] == "X"


def test_app_name_in_every_locale():
    for code, _name in i18n.TRANSLATOR.available:
        i18n.set_language(code)
        assert i18n.t("app.title") == APP_NAME
        assert "HermesServ" not in i18n.t("tray.summary", running=1, stopped=2)
    i18n.set_language("fr")
