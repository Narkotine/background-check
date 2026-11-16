"""Plugins utilisateur : chargement, isolation des erreurs, identifiants protégés."""
from __future__ import annotations

import json

import pytest

from bgc import plugin_loader, service


@pytest.fixture
def plugin_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    root.mkdir()
    monkeypatch.setattr(plugin_loader, "user_dir", lambda: str(root))
    plugin_loader.LOAD_ERRORS.clear()
    before = dict(service._REGISTRY)
    owners = dict(service._OWNERS)
    yield root
    service._REGISTRY.clear()
    service._REGISTRY.update(before)
    service._OWNERS.clear()
    service._OWNERS.update(owners)


def _plugin(root, name, code, module="plugin.py"):
    folder = root / name
    folder.mkdir()
    (folder / "plugin.json").write_text(json.dumps({"id": name, "module": module}),
                                        encoding="utf-8")
    (folder / "plugin.py").write_text(code, encoding="utf-8")


GOOD = """
from bgc.service import ServerType, Field
class T(ServerType):
    id = "{id}"
    fields = [Field("port", "Port", "port", 8080)]
TYPE = T()
"""


def test_good_plugin_registers(plugin_root):
    _plugin(plugin_root, "demo", GOOD.format(id="demo-type"))
    assert plugin_loader.load_all() == [("demo", None)]
    assert service.get("demo-type") is not None


def test_broken_plugin_is_isolated(plugin_root):
    _plugin(plugin_root, "bad", "raise RuntimeError('boom')")
    _plugin(plugin_root, "ok", GOOD.format(id="ok-type"))
    results = dict(plugin_loader.load_all())
    assert "boom" in results["bad"] and results["ok"] is None
    assert service.get("ok-type") is not None


def test_type_cannot_override_builtin(plugin_root):
    original = service.get("command")
    _plugin(plugin_root, "evil", GOOD.format(id="command"))
    results = dict(plugin_loader.load_all())
    assert results["evil"] and service.get("command") is original


def test_module_must_stay_in_folder(plugin_root):
    _plugin(plugin_root, "escape", GOOD.format(id="x"), module="../../outside.py")
    assert dict(plugin_loader.load_all())["escape"]


REGISTER_EVIL = """
from bgc import service
from bgc.service import ServerType
class T(ServerType):
    id = "hermes"
def register():
    service.register(T())
"""


def test_register_function_cannot_override_builtin(plugin_root):
    original = service.get("hermes")
    _plugin(plugin_root, "evil2", REGISTER_EVIL)
    results = dict(plugin_loader.load_all())
    assert "hermes" in results["evil2"]
    assert service.get("hermes") is original
    assert service.CURRENT_OWNER is None


def test_plugin_can_reload_itself(plugin_root):
    _plugin(plugin_root, "again", GOOD.format(id="again-type"))
    assert dict(plugin_loader.load_all())["again"] is None
    assert dict(plugin_loader.load_all())["again"] is None  # « Recharger les plugins »


def test_plugin_cannot_take_another_plugins_id(plugin_root):
    _plugin(plugin_root, "first", GOOD.format(id="shared-id"))
    _plugin(plugin_root, "second", GOOD.format(id="shared-id"))
    results = dict(plugin_loader.load_all())
    assert results["first"] is None and results["second"]


def test_old_package_name_still_works():
    """Un plugin écrit pour `hermesserv` (ancien nom) partage le même registre que `bgc`."""
    import bgc.service
    import hermesserv.service as old_service
    from hermesserv.service import ServerType as OldServerType
    assert old_service is bgc.service
    assert OldServerType is bgc.service.ServerType
