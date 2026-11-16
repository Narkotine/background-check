"""Background Check (BGC) — socle applicatif (configuration, traduction, plugins, UI).

Anciennement `hermesserv` : ce nom reste importable (voir `compat.py`)."""

__version__ = "2.4.0"

#: Nom affiché (titre de fenêtre, notifications, à propos) — l'UI passe par la clé `app.title`.
APP_NAME = "Background Check"
#: Nom court, là où la place manque (infobulle de la zone de notification).
APP_SHORT = "BGC"
#: Nom technique : dossier de données, exe, mutex, entrée « Démarrer avec Windows ».
APP_ID = "BackgroundCheck"
#: Ancien nom technique (jusqu'au 16 nov 2026) : données reprises au premier lancement.
OLD_APP_ID = "HermesServ"

from . import compat as _compat  # noqa: E402

_compat.install()
