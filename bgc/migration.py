"""Renommage HermesServ → Background Check (16 nov 2026) : reprise des données.

Au premier lancement sous le nouveau nom, si `%LOCALAPPDATA%\\BackgroundCheck` n'existe pas et
que `%LOCALAPPDATA%\\HermesServ` existe, ce dernier est **copié** (config — jeton MCP compris —,
sauvegardes `.bak`, journaux, plugins). L'ancien dossier reste intact : revenir à l'ancien exe
reste possible. La copie passe par un dossier temporaire renommé à la fin : jamais de dossier
à moitié copié pris pour une vraie installation.

Rien n'est fait pour une instance de test (`BGC_CONFIG` / `HERMESSERV_CONFIG`).
"""
from __future__ import annotations

import os
import shutil

from . import config

#: Dossier repris au dernier appel (pour le journal de l'app), ou "".
MIGRATED_FROM = ""


def migrate_data_dir(new_dir: str | None = None, old_dir: str | None = None,
                     test_config: str | None = None) -> str:
    """Copie l'ancien dossier de données si besoin ; renvoie le dossier repris, ou ""."""
    global MIGRATED_FROM
    new_dir = new_dir or config.CONFIG_DIR
    old_dir = old_dir or config.OLD_CONFIG_DIR
    if (config.TEST_CONFIG if test_config is None else test_config):
        return ""
    if not old_dir or not os.path.isdir(old_dir) or os.path.exists(new_dir):
        return ""
    staging = new_dir + ".migration-tmp"
    try:
        if os.path.exists(staging):
            shutil.rmtree(staging, ignore_errors=True)
        # Les fichiers ouverts (journal d'une ancienne instance) ne bloquent pas la reprise.
        shutil.copytree(old_dir, staging, ignore_dangling_symlinks=True,
                        copy_function=_copy_quietly)
        os.replace(staging, new_dir)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        return ""
    MIGRATED_FROM = old_dir
    return old_dir


def _copy_quietly(src: str, dst: str) -> None:
    try:
        shutil.copy2(src, dst)
    except OSError:
        pass
