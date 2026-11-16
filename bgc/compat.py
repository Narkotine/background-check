"""Compatibilité : l'ancien nom du paquet, `hermesserv`, reste importable.

Le paquet s'appelait `hermesserv` jusqu'au renommage en « Background Check » (16 nov 2026).
Un plugin utilisateur écrit avant (`from hermesserv.service import ServerType`) doit continuer
à marcher **et** partager le même registre de types : `hermesserv.X` n'est pas une copie, c'est
**le même module** que `bgc.X` (même objet dans `sys.modules`).
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

OLD = "hermesserv"
NEW = "bgc"


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module):  # déjà exécuté sous son vrai nom
        pass


class _AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != OLD and not fullname.startswith(OLD + "."):
            return None
        real = NEW + fullname[len(OLD):]
        if importlib.util.find_spec(real) is None:
            return None
        is_pkg = hasattr(importlib.import_module(real), "__path__")
        return importlib.util.spec_from_loader(fullname, _AliasLoader(real), is_package=is_pkg)


def install() -> None:
    if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _AliasFinder())
