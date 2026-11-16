"""Réglages communs des tests : jamais la vraie configuration de l'utilisateur.

`LOCALAPPDATA` et `BGC_CONFIG` sont redirigés vers un dossier temporaire **avant**
l'import du paquet (ses chemins sont calculés à l'import). Aucun test n'ouvre de fenêtre Tk.
"""
from __future__ import annotations

import os
import sys
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="bgc-tests-")
os.environ["LOCALAPPDATA"] = _SANDBOX
os.environ["BGC_CONFIG"] = os.path.join(_SANDBOX, "BackgroundCheck", "config.json")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from bgc import plugins  # noqa: E402,F401  (enregistre les types intégrés)
