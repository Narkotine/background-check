"""Compatibilité : déplacé dans `bgc/ui/views/overview.py` (découpage core/ui du 16 nov 2026)."""
import sys as _sys

from bgc.ui.views import overview as _module

_sys.modules[__name__] = _module
