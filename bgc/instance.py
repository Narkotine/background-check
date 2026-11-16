"""Une seule instance de Background Check par fichier de configuration.

Deux instances sur la même config, c'était deux sondeurs, deux relances automatiques en
concurrence et un serveur MCP qui échoue sur le port déjà pris (revue du 16 nov 2026).

Mécanisme (Windows) : un **mutex nommé** dont le nom dérive du chemin de la config — une
instance de test lancée avec `BGC_CONFIG` reste donc possible à côté de la vraie.
La seconde instance retrouve la fenêtre cachée de l'icône de notification de la première
(titre `BackgroundCheckTray:<clé>`) et lui poste `WM_SHOW_REQUEST` : la première se montre.
Hors Windows, tout est permis (rien à verrouiller).
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import sys

from . import APP_ID

ERROR_ALREADY_EXISTS = 183
ASFW_ANY = 0xFFFFFFFF
#: Message privé reçu par la fenêtre du tray : « montre-toi ».
WM_SHOW_REQUEST = 0x8000 + 2

_HANDLE = None


def key_for(config_path: str) -> str:
    normalised = os.path.normcase(os.path.abspath(config_path or ""))
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()[:12]


def tray_title(key: str) -> str:
    return f"{APP_ID}Tray:{key}"


def acquire(key: str) -> bool:
    """True si cette instance est la seule (le mutex vit jusqu'à la fin du processus)."""
    global _HANDLE
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, False, f"Local\\{APP_ID}-{key}")
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    except Exception:
        return True  # en cas de doute, ne jamais empêcher l'app de démarrer
    if not handle:
        return True
    if already:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return False
    _HANDLE = handle
    return True


def signal_existing(key: str) -> bool:
    """Demande à l'instance déjà lancée de montrer sa fenêtre. True si le message est parti."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                        ctypes.c_ssize_t]
        hwnd = user32.FindWindowW(None, tray_title(key))
        if not hwnd:
            return False
        # Autorise l'autre processus à passer au premier plan (sinon Windows fait clignoter).
        user32.AllowSetForegroundWindow.argtypes = [ctypes.c_uint32]
        user32.AllowSetForegroundWindow(ASFW_ANY)
        return bool(user32.PostMessageW(hwnd, WM_SHOW_REQUEST, 0, 0))
    except Exception:
        return False
