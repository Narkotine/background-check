"""Compatibilité : déplacé dans `bgc/ui/views/add.py` (découpage core/ui du 16 nov 2026)."""
import sys as _sys

from bgc.ui.views import add as _module

_sys.modules[__name__] = _module
