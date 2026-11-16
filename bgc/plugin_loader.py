"""Plugins utilisateur : **un dossier = un type de serveur**, déposable et partageable.

Emplacement : `%LOCALAPPDATA%\\BackgroundCheck\\plugins\\<nom>\\` avec deux fichiers :

    plugin.json      {"id": "mon-service", "name": "Mon service", "module": "plugin.py"}
    plugin.py        un module Python qui expose `TYPE` (une instance de ServerType)
                     — ou une fonction `register()` qui appelle `service.register(...)`.

Règles et limites, assumées :
  * un plugin **exécute du code** : en installer un est une décision de confiance, il n'y a
    pas de bac à sable possible dans ce modèle (le dit aussi l'aide de l'écran de réglages) ;
  * un plugin qui plante est **isolé** : l'erreur est enregistrée et les autres types
    continuent de fonctionner (jamais d'exception remontée à l'application) ;
  * un plugin ne peut pas écraser un type intégré ni celui d'un autre plugin (identifiant
    déjà pris = refus, y compris par `register()`) ; il peut se recharger lui-même ;
  * les textes d'un plugin peuvent être des libellés littéraux : `Field("port", "Port TCP")`
    fonctionne, `i18n.t` rend la clé telle quelle quand elle n'existe pas.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

from . import service

MANIFEST = "plugin.json"
# (nom du dossier, message d'erreur) — lu par l'application pour son journal.
LOAD_ERRORS: list[tuple[str, str]] = []

README = """\
Background Check — plugins utilisateur
=====================================

(Les plugins écrits pour HermesServ, l'ancien nom, marchent toujours : `import hermesserv`
reste valable ; le nom actuel du paquet est `bgc`.)

Un plugin = un dossier dans ce répertoire, avec deux fichiers :

  plugin.json
  {\n    "id": "mon-service",       <- identifiant unique du type (lettres, chiffres, tirets)\n    "name": "Mon service",       <- nom affiché dans « Ajouter un serveur »\n    "module": "plugin.py"        <- fichier Python à charger\n  }

  plugin.py
  from bgc.service import Controller, Field, ServerType

  class MonType(ServerType):
      id = "mon-service"
      label_key = "Mon service"          # un libellé littéral suffit
      desc_key = "Surveille mon service." # idem (pas besoin de traduction)
      fields = [Field("port", "Port TCP", "port", 8080)]

      def controller(self, server, log):
          return MonControleur(server, log)

  TYPE = MonType()   # <- c'est ce que le chargeur cherche

Règles :
  * un type peut réutiliser le type « command » s'il n'a rien de spécial à faire ;
  * ne pas réutiliser un identifiant déjà pris (le chargement est alors refusé) ;
  * un plugin exécute du code : n'installe que ce que tu as écrit ou vérifié toi-même.

Le bouton « Recharger les plugins » des réglages relit ce dossier sans redémarrer l'app.
"""


def user_dir() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", ""), "BackgroundCheck", "plugins")


def ensure_dir() -> str:
    """Crée le dossier (et son mode d'emploi) à la demande — jamais au démarrage."""
    root = user_dir()
    os.makedirs(root, exist_ok=True)
    readme = os.path.join(root, "README.txt")
    if not os.path.isfile(readme):
        with open(readme, "w", encoding="utf-8") as fh:
            fh.write(README)
    return root


def _manifests() -> list[tuple[str, str]]:
    root = user_dir()
    if not os.path.isdir(root):
        return []
    found = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    for name in names:
        path = os.path.join(root, name, MANIFEST)
        if os.path.isfile(path):
            found.append((name, path))
    return found


def load_all() -> list[tuple[str, str | None]]:
    """Charge chaque dossier de plugin ; renvoie [(nom, erreur ou None)]."""
    results: list[tuple[str, str | None]] = []
    for name, manifest_path in _manifests():
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            folder = os.path.dirname(manifest_path)
            module_name = str(meta.get("module") or "plugin.py")
            module_path = os.path.normpath(os.path.join(folder, module_name))
            if not module_path.startswith(os.path.normpath(folder) + os.sep) \
                    or not os.path.isfile(module_path):
                raise ValueError("module introuvable dans le dossier du plugin")
            spec = importlib.util.spec_from_file_location(f"bgc_plugin_{name}", module_path)
            if spec is None or spec.loader is None:
                raise ValueError("module illisible")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            # Tout `service.register` fait pendant le chargement est attribué à ce dossier :
            # c'est ce qui interdit d'écraser un type intégré, même via `register()`.
            service.CURRENT_OWNER = name
            try:
                spec.loader.exec_module(module)
                server_type = getattr(module, "TYPE", None) or getattr(module, "PLUGIN", None)
                if server_type is None:
                    register = getattr(module, "register", None)
                    if not callable(register):
                        raise ValueError("le module n'expose ni TYPE ni register()")
                    register()
                else:
                    if not isinstance(server_type, service.ServerType):
                        raise ValueError("TYPE n'est pas un ServerType")
                    service.register(server_type)
            finally:
                service.CURRENT_OWNER = None
            results.append((name, None))
        except Exception as exc:  # un plugin fautif ne casse jamais le démarrage
            message = f"{type(exc).__name__}: {exc}"[:200]
            LOAD_ERRORS.append((name, message))
            results.append((name, message))
    return results
