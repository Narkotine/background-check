"""Démarrage avec Windows : une valeur dans la clé `Run` de l'utilisateur courant.

Rien n'est jamais posé sans un clic dans les réglages (« Démarrer avec Windows ») : ce
module ne fait qu'obéir, et l'état affiché vient du registre — pas d'une case à cocher qui
mentirait si la clé a été supprimée à la main.
"""

from __future__ import annotations

import os
import sys

VALUE_NAME = "BackgroundCheck"
#: Noms d'avant le renommage : repris (puis effacés) par `migrate()`.
OLD_VALUE_NAMES = ("HermesServ",)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    """Ligne de commande à inscrire : l'exe quand on est empaqueté, sinon pythonw + script."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "background_check.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.isfile(pythonw) else sys.executable
    return f'"{exe}" "{script}"'


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_enabled(flag: bool) -> bool:
    """True si l'état demandé est bien celui du registre après l'opération."""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if flag:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        return False
    return is_enabled() == bool(flag)


def migrate() -> bool:
    """Renommage HermesServ → Background Check : l'entrée « Démarrer avec Windows ».

    Seulement si l'**ancienne** entrée existait (l'utilisateur l'avait activée) : elle est
    recréée sous le nouveau nom, vers l'exécutable actuel, puis l'ancienne est effacée.
    Sinon rien n'est écrit (la règle « rien sans un clic » tient toujours). True si migré.
    """
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            found = []
            for name in OLD_VALUE_NAMES:
                try:
                    value, _kind = winreg.QueryValueEx(key, name)
                except FileNotFoundError:
                    continue
                if str(value).strip():
                    found.append(name)
            if not found:
                return False
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
            for name in found:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
    except OSError:
        return False
    return is_enabled()
