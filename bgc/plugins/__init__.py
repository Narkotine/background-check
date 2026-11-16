"""Découverte des plugins (types de serveur).

Ajouter un type = écrire un module ici et l'importer ci-dessous. Volontairement explicite :
une liste lisible vaut mieux qu'une découverte dynamique opaque, et PyInstaller embarque
exactement ce qui est importé (aucune surprise à l'empaquetage).
"""

from . import command, hermes, sttbridge, wsl  # noqa: F401  (l'import enregistre les types)
# Types à éléments (16 nov 2026) : un conteneur / un service Windows = un élément.
from . import docker, winsvc  # noqa: F401,E402
# `portwatch` n'est plus enregistré : fusionné dans « command » (7 nov 2026). Le module est
# conservé comme référence ; les configs existantes sont migrées au chargement (config.py).

from .. import plugin_loader  # noqa: E402  (import après les types intégrés : ordre voulu)

# Plugins **utilisateur** : chaque dossier de `%LOCALAPPDATA%\Background Check\plugins` peut
# enregistrer un type de plus. Un plugin fautif est isolé : l'erreur part dans LOAD_ERRORS
# (journal de l'application) et les autres types continuent de fonctionner.
USER_PLUGINS = plugin_loader.load_all()
LOAD_ERRORS = plugin_loader.LOAD_ERRORS

__all__ = ["command", "hermes", "sttbridge", "wsl", "docker", "winsvc", "USER_PLUGINS",
           "LOAD_ERRORS"]
