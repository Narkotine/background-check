"""Journal fichier de Background Check : `%LOCALAPPDATA%\\BackgroundCheck\\logs\\bgc.log`.

Avant (revue du 16 nov 2026), tout vivait en mémoire : journaux perdus à la fermeture, et
un plantage de l'exe (sans console) ne laissait **aucune trace**. Ici :
  * fichier tournant (1 Mo × 5) en UTF-8 ;
  * `sys.excepthook`, `threading.excepthook` et les erreurs de rappel Tk y sont écrits ;
  * le journal de l'app et celui de chaque tuile y sont recopiés (texte traduit).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import APP_ID

LOGGER = logging.getLogger("bgc")
LOG_NAME = "bgc.log"
_CONFIGURED = False


def log_dir() -> str:
    return os.path.join(os.environ.get("LOCALAPPDATA", "") or os.path.expanduser("~"),
                        APP_ID, "logs")


def log_path() -> str:
    return os.path.join(log_dir(), LOG_NAME)


def setup(directory: str | None = None) -> str:
    """Branche le fichier et les crochets d'exception (idempotent). Renvoie le chemin."""
    global _CONFIGURED
    folder = directory or log_dir()
    path = os.path.join(folder, LOG_NAME)
    if _CONFIGURED:
        return path
    try:
        os.makedirs(folder, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5,
                                      encoding="utf-8", delay=True)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s"))
        LOGGER.addHandler(handler)
    except OSError:
        pass  # disque plein, dossier interdit : l'app tourne quand même
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

    previous = sys.excepthook

    def excepthook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            LOGGER.critical("Exception non gérée", exc_info=(exc_type, exc, tb))
        previous(exc_type, exc, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread else "?"
        LOGGER.error("Exception dans le fil %s", name,
                     exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    _CONFIGURED = True
    return path


def tk_hook(root) -> None:
    """Les erreurs dans un rappel Tk (clic, after) vont au journal au lieu de stderr."""
    def report(exc_type, exc, tb):
        LOGGER.error("Exception dans un rappel Tk", exc_info=(exc_type, exc, tb))
    root.report_callback_exception = report
