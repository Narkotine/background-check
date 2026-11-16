"""Modèles rapides : un clic qui remplit une tuile (champs, nom, icône, boutons).

Rien n'est lancé ni exécuté : un preset **écrit des valeurs dans le formulaire**. Les chemins
sont détectés sur cette machine ; si un programme n'est pas là, le preset reste utilisable et
l'utilisateur corrige la ligne.

Les deux écrans (ajout d'un serveur et réglages d'une tuile existante) lisent la même table :
il n'y a donc qu'un endroit à corriger quand un service change de port ou de commande.
"""

from __future__ import annotations

import os

from . import i18n

KEYS = ("preset.watch", "preset.local", "preset.lmstudio", "preset.ollama", "preset.llamagui",
        "preset.llamacpp", "preset.vllm", "preset.textgen", "preset.koboldcpp", "preset.comfyui",
        "preset.sillytavern", "preset.openwebui", "preset.ha")

#: Ordre d'affichage **groupé** (un titre par groupe dans les deux écrans). Les moteurs LLM
#: locaux servent une API (et souvent leur propre interface web) ; les « interfaces web » sont
#: des frontends qui ont besoin d'un moteur à côté — c'est la distinction que l'utilisateur
#: demande de rendre lisible (10 nov 2026).
LAYOUT = (
    ("preset.group.watch", ("preset.watch", "preset.ha")),
    ("preset.group.engine", ("preset.local", "preset.llamagui", "preset.lmstudio",
                             "preset.ollama", "preset.llamacpp", "preset.vllm", "preset.textgen",
                             "preset.koboldcpp", "preset.comfyui")),
    ("preset.group.front", ("preset.sillytavern", "preset.openwebui")),
)

#: Port de l'UI LlamaGUI et port par défaut du llama-server qu'elle lance elle-même.
LLAMAGUI_PORT = 5240
LLAMA_SERVER_PORT = 9931

#: Ports par défaut des serveurs courants (vérifiés dans leur documentation, 10 nov 2026) :
#: llama.cpp `llama-server` 8080, text-generation-webui 7860, KoboldCpp 5001, vLLM 8000,
#: ComfyUI 8188, SillyTavern 8000, Open WebUI 3000, LM Studio 1234, Ollama 11434,
#: Home Assistant 8123. Aucun de ces presets ne lance quoi que ce soit : ils remplissent.
LLAMACPP_PORT = 8080
TEXTGEN_PORT = 7860
KOBOLDCPP_PORT = 5001
VLLM_PORT = 8000
COMFYUI_PORT = 8188
SILLYTAVERN_PORT = 8000
OPENWEBUI_PORT = 3000
HOME_ASSISTANT_PORT = 8123


def _first_file(*paths: str) -> str:
    for path in paths:
        if path and os.path.isfile(path):
            return path
    return ""


def lmstudio_exe() -> str:
    return _first_file(
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "LM Studio", "LM Studio.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "LM Studio", "LM Studio.exe"))


def ollama_exe() -> str:
    return _first_file(
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
        os.path.join(os.environ.get("ProgramFiles", ""), "Ollama", "ollama.exe")) \
        or _which("ollama")


def _which(name: str) -> str:
    try:
        import shutil
        return shutil.which(name) or ""
    except Exception:
        return ""


def llamagui_root() -> str:
    """Dossier d'installation de LlamaGUI (celui qui contient `server.py`)."""
    home = os.environ.get("USERPROFILE", "")
    local = os.environ.get("LOCALAPPDATA", "")
    for path in (os.path.join(home, "LLama-GUI"), os.path.join(home, "Llama-GUI"),
                 os.path.join(home, "Documents", "LLama-GUI"),
                 os.path.join(local, "Programs", "Llama GUI"),
                 os.path.join(local, "Llama GUI"), r"C:\LLama-GUI", r"C:\Llama-GUI"):
        if path and os.path.isfile(os.path.join(path, "server.py")):
            return path
    return ""


def llamagui_python(root: str) -> str:
    """Python de l'environnement virtuel du projet, sinon `python` du PATH."""
    venv = os.path.join(root, ".venv", "Scripts", "python.exe")
    return venv if os.path.isfile(venv) else "python"


# ── détection des autres moteurs / frontends courants ─────────────────────────
# Chaque fonction rend un chemin réel ou "" — le preset reste utilisable quand rien n'est
# détecté (l'utilisateur corrige la ligne), il ne prétend jamais avoir trouvé.
def _project_root(*names: str, marker: str) -> str:
    """Premier dossier candidat qui contient `marker` (le script d'entrée du projet)."""
    home = os.environ.get("USERPROFILE", "")
    local = os.environ.get("LOCALAPPDATA", "")
    for name in names:
        for base in (home, os.path.join(home, "Documents"), local, "C:\\"):
            root = os.path.join(base, name) if base else ""
            if root and os.path.isfile(os.path.join(root, marker)):
                return root
    return ""


def llama_server_exe() -> str:
    return _first_file(_which("llama-server"), _which("llama-server.exe"),
                       os.path.join(os.environ.get("USERPROFILE", ""), "llama.cpp",
                                    "build", "bin", "llama-server.exe"))


def koboldcpp_exe() -> str:
    home = os.environ.get("USERPROFILE", "")
    return _first_file(_which("koboldcpp.exe"), _which("koboldcpp"),
                       os.path.join(home, "KoboldCpp", "koboldcpp.exe"),
                       os.path.join(home, "Documents", "KoboldCpp", "koboldcpp.exe"))


def textgen_root() -> str:
    return _project_root("text-generation-webui", "oobabooga", marker="server.py")


def comfyui_root() -> str:
    return _project_root("ComfyUI", marker="main.py")


def sillytavern_root() -> str:
    return _project_root("SillyTavern", marker="server.js")


def _python_in(root: str) -> str:
    """Interpréteur du projet : `.venv`, `venv`, `python_embedded`, sinon `python`."""
    for sub in (os.path.join(".venv", "Scripts", "python.exe"),
                os.path.join("venv", "Scripts", "python.exe"),
                os.path.join("python_embedded", "python.exe")):
        path = os.path.join(root, sub) if root else ""
        if path and os.path.isfile(path):
            return path
    return "python"


def _llamagui_preset() -> dict:
    root = llamagui_root()
    python = llamagui_python(root) if root else "python"
    command = f'"{python}" server.py' if os.path.isfile(python) else "python server.py"
    icon = _first_file(os.path.join(root, "assets", "Llama-GUI.ico") if root else "")
    actions = [
        {"label": i18n.t("preset.llamagui.open_gui"),
         "target": f"http://127.0.0.1:{LLAMAGUI_PORT}"},
        {"label": i18n.t("preset.llamagui.open_llama"),
         "target": f"http://127.0.0.1:{LLAMA_SERVER_PORT}"},
    ]
    return {
        "fields": {
            "host": "127.0.0.1",
            "port": str(LLAMAGUI_PORT),
            "check_url": "",
            "url": f"http://127.0.0.1:{LLAMAGUI_PORT}",
            "command": command,
            "stop_command": "",
            "workdir": root,
            "env": "",
            # Le llama-server lancé par LlamaGUI : surveillé en même temps que l'UI.
            "extra_checks": f"llama-server = {LLAMA_SERVER_PORT}",
        },
        "name": "LlamaGUI",
        "icon": icon,
        "actions": actions,
        "focus": "port",
    }


def values(key: str) -> dict:
    """Valeurs à poser pour un preset : champs, nom, icône, actions et champ à focaliser."""
    if key == "preset.watch":
        return {"fields": {"command": "", "stop_command": "", "workdir": "", "env": ""},
                "name": "", "icon": "", "actions": [], "focus": "port"}
    if key == "preset.local":
        return {"fields": {}, "name": "", "icon": "", "actions": [], "focus": "command"}
    if key == "preset.lmstudio":
        exe = lmstudio_exe()
        # Le serveur local de LM Studio n'expose AUCUNE interface web (seulement /v1) :
        # l'interface, c'est l'application elle-même — d'où ces deux boutons (10 nov 2026).
        # « Fermer » porte une sonde (`proc:`) : grisé quand l'application ne tourne pas.
        actions = ([{"label": i18n.t("preset.lmstudio.open_app"), "target": f'"{exe}"'},
                    {"label": i18n.t("preset.lmstudio.close_app"),
                     "target": 'taskkill /IM "LM Studio.exe"',
                     "check": "proc:LM Studio.exe"}]
                   if exe else [])
        return {
            "fields": {"host": "127.0.0.1", "port": "1234", "check_url": "/v1/models", "url": "",
                       "command": "lms server start", "stop_command": "lms server stop",
                       "workdir": "", "env": ""},
            "name": "LM Studio", "icon": exe, "actions": actions, "focus": "port",
        }
    if key == "preset.ollama":
        return {
            "fields": {"host": "127.0.0.1", "port": "11434", "check_url": "/api/tags", "url": "",
                       "command": "ollama serve", "stop_command": "", "workdir": "", "env": ""},
            "name": "Ollama", "icon": ollama_exe(), "actions": [], "focus": "port",
        }
    if key == "preset.llamagui":
        return _llamagui_preset()
    if key == "preset.llamacpp":
        exe = llama_server_exe()
        return {
            "fields": {"host": "127.0.0.1", "port": str(LLAMACPP_PORT), "check_url": "/health",
                       # llama-server sert sa propre interface web sur la racine.
                       "url": f"http://127.0.0.1:{LLAMACPP_PORT}",
                       "command": f'"{exe}" --port {LLAMACPP_PORT} -m modele.gguf' if exe
                       else f"llama-server --port {LLAMACPP_PORT} -m modele.gguf",
                       "stop_command": "", "workdir": "", "env": ""},
            "name": "llama.cpp", "icon": exe, "actions": [], "focus": "command",
        }
    if key == "preset.vllm":
        return {
            # vLLM ne sert qu'une API OpenAI : pas d'interface web, donc pas d'URL.
            "fields": {"host": "127.0.0.1", "port": str(VLLM_PORT), "check_url": "/v1/models",
                       "url": "", "command": "vllm serve modele", "stop_command": "",
                       "workdir": "", "env": ""},
            "name": "vLLM", "icon": "", "actions": [], "focus": "command",
        }
    if key == "preset.textgen":
        root = textgen_root()
        python = _python_in(root)
        command = f'"{python}" server.py' if root else "python server.py"
        return {
            "fields": {"host": "127.0.0.1", "port": str(TEXTGEN_PORT), "check_url": "/",
                       "url": f"http://127.0.0.1:{TEXTGEN_PORT}",
                       "command": command, "stop_command": "", "workdir": root, "env": ""},
            "name": "text-generation-webui", "icon": "", "actions": [], "focus": "port",
        }
    if key == "preset.koboldcpp":
        exe = koboldcpp_exe()
        return {
            "fields": {"host": "127.0.0.1", "port": str(KOBOLDCPP_PORT), "check_url": "/",
                       "url": f"http://127.0.0.1:{KOBOLDCPP_PORT}",
                       "command": f'"{exe}" --port {KOBOLDCPP_PORT} --model modele.gguf' if exe
                       else f"koboldcpp.exe --port {KOBOLDCPP_PORT} --model modele.gguf",
                       "stop_command": "", "workdir": "", "env": ""},
            "name": "KoboldCpp", "icon": exe, "actions": [], "focus": "command",
        }
    if key == "preset.comfyui":
        root = comfyui_root()
        python = _python_in(root)
        command = f'"{python}" main.py' if root else "python main.py"
        return {
            "fields": {"host": "127.0.0.1", "port": str(COMFYUI_PORT), "check_url": "/",
                       "url": f"http://127.0.0.1:{COMFYUI_PORT}",
                       "command": command, "stop_command": "", "workdir": root, "env": ""},
            "name": "ComfyUI", "icon": "", "actions": [], "focus": "port",
        }
    if key == "preset.sillytavern":
        root = sillytavern_root()
        return {
            # Frontend seul : il a besoin d'un moteur (LM Studio, Ollama…) à côté, qu'on
            # ajoute comme sonde supplémentaire si besoin.
            "fields": {"host": "127.0.0.1", "port": str(SILLYTAVERN_PORT), "check_url": "/",
                       "url": f"http://127.0.0.1:{SILLYTAVERN_PORT}",
                       "command": "node server.js" if root else "",
                       "stop_command": "", "workdir": root, "env": ""},
            "name": "SillyTavern", "icon": "", "actions": [], "focus": "port",
        }
    if key == "preset.openwebui":
        return {
            # Le plus souvent lancé par Docker : aucune commande à poser ici (observation).
            "fields": {"host": "127.0.0.1", "port": str(OPENWEBUI_PORT), "check_url": "/health",
                       "url": f"http://127.0.0.1:{OPENWEBUI_PORT}",
                       "command": "", "stop_command": "", "workdir": "", "env": ""},
            "name": "Open WebUI", "icon": "", "actions": [], "focus": "port",
        }
    if key == "preset.ha":
        return {
            # Machine du réseau : aucune commande, aucune sonde de processus — le port qui
            # répond est la seule preuve possible depuis ce PC.
            "fields": {"host": "192.168.1.50", "port": str(HOME_ASSISTANT_PORT), "check_url": "/",
                       "url": f"http://192.168.1.50:{HOME_ASSISTANT_PORT}",
                       "command": "", "stop_command": "", "workdir": "", "env": ""},
            "name": "Home Assistant", "icon": "", "actions": [], "focus": "port",
        }
    return {"fields": {}, "name": "", "icon": "", "actions": [], "focus": None}
