"""Serveurs liés (« dépendances ») d'une tuile : **surveillés**, jamais pilotés.

Exemple : le pont STT ne sert à rien sans le serveur whisper d'OpenWhispr ; LlamaGUI lance un
llama-server ; Hermes a son application Desktop. Ces processus ne sont pas « à nous », mais on
veut voir d'un coup d'œil s'ils tournent et ce qu'ils consomment.

Deux sources, fusionnées :

* **le plugin** : `controller.dependencies()` → ``[{id, label | label_key, target}]`` ou, quand
  il a déjà sondé, ``[{id, label_key, pids, up}]`` ;
* **la tuile** : champ « Serveurs liés » (`server["deps"]`), une ligne ``nom = cible`` par
  serveur, séparées par « ; » ou un retour à la ligne. Cible : un port (`8178`), `hôte:port`,
  une URL (`http://127.0.0.1:8178/health`) ou un nom d'exécutable (`ollama.exe`).

`probe()` rend pour chacune : en marche ?, PID (écouteur du port ou processus), durée de marche.
Les mesures CPU/RAM/VRAM sont faites par `bgc/core/metrics.py` (clé « tuile/dep:<id> »).
"""

from __future__ import annotations

import re
import time
from urllib.parse import urlparse

from bgc import sysinfo

MAX_DEPS = 8
LOCAL_HOSTS = {"", "127.0.0.1", "localhost", "::1", "0.0.0.0"}
PROCESS_TTL = 5.0       # balayer tous les processus coûte : 5 s de cache par cible
REMOTE_TTL = 5.0
_cache: dict[tuple, tuple[float, object]] = {}


def parse_target(target: str, default_host: str = "127.0.0.1") -> dict:
    """« 8178 » / « hôte:port » / URL / « ollama.exe » → {host, port, process, url}."""
    text = str(target or "").strip().strip('"')
    out = {"host": default_host, "port": 0, "process": "", "url": ""}
    if not text:
        return out
    if re.match(r"^https?://", text, re.IGNORECASE):
        parsed = urlparse(text)
        out["url"] = text
        out["host"] = parsed.hostname or default_host
        try:
            out["port"] = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError:
            out["port"] = 0
        return out
    if text.isdigit():
        out["port"] = int(text)
        return out
    match = re.match(r"^\[?([^\]\s]+?)\]?:(\d{1,5})$", text)
    if match:
        out["host"], out["port"] = match.group(1), int(match.group(2))
        return out
    out["process"] = text if "." in text else text + ".exe"
    return out


def parse_spec(text: str) -> list[dict]:
    """Champ « Serveurs liés » de la tuile → [{id, label, target}] (8 au maximum)."""
    deps = []
    for index, chunk in enumerate(re.split(r"[;\n]+", str(text or ""))):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, sep, target = chunk.partition("=")
        name, target = (name.strip(), target.strip()) if sep else (chunk, chunk)
        if target:
            deps.append({"id": f"user{index}", "label": name or target, "target": target,
                         "source": "user"})
    return deps[:MAX_DEPS]


def declared(runtime) -> list[dict]:
    """Dépendances du plugin puis de la tuile, sans doublon (même cible = une seule)."""
    found: list[dict] = []
    controller = getattr(runtime, "controller", None)
    getter = getattr(controller, "dependencies", None)
    if callable(getter):
        try:
            for dep in getter() or []:
                if isinstance(dep, dict) and dep.get("id"):
                    found.append({"source": "plugin", **dep})
        except Exception:
            pass
    found += parse_spec((getattr(runtime, "server", None) or {}).get("deps") or "")
    seen, out = set(), []
    for dep in found:
        spec = parse_target(dep.get("target", ""))
        key = (spec["port"], spec["process"].lower()) if dep.get("target") else ("id", dep["id"])
        if key in seen:
            continue
        seen.add(key)
        out.append(dep)
    return out[:MAX_DEPS]


def _cached(key: tuple, ttl: float, compute):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = compute()
    _cache[key] = (now, value)
    return value


def probe_one(dep: dict, default_host: str = "127.0.0.1") -> dict:
    spec = parse_target(dep.get("target", ""), default_host)
    pids = [int(p) for p in (dep.get("pids") or []) if p]
    up = bool(dep.get("up")) if "up" in dep else None
    if up is None:
        if spec["port"]:
            if spec["host"].lower() in LOCAL_HOSTS:
                pid = sysinfo.listener_pid(spec["port"])
                up = pid is not None
                if pid:
                    pids = [pid]
            else:
                up = bool(_cached(("tcp", spec["host"], spec["port"]), REMOTE_TTL,
                                  lambda: sysinfo.tcp_open(spec["host"], spec["port"], 0.4)))
        elif spec["process"]:
            pid = _cached(("proc", spec["process"].lower()), PROCESS_TTL,
                          lambda: sysinfo.process_running(spec["process"]))
            up = bool(pid)
            if pid:
                pids = [pid]
        else:
            up = False
    info = sysinfo.proc_info(pids[0]) if pids else {}
    return {
        "id": str(dep["id"]),
        "label": str(dep.get("label") or ""),
        "label_key": str(dep.get("label_key") or ""),
        "target": str(dep.get("target") or ""),
        "host": spec["host"], "port": spec["port"], "process": spec["process"],
        "up": bool(up), "pids": pids,
        "name": info.get("name", ""),
        "started": info.get("create_time"),
        "source": dep.get("source", "plugin"),
    }


def probe(runtime) -> list[dict]:
    host = str(((getattr(runtime, "server", None) or {}).get("params") or {}).get("host")
               or "127.0.0.1")
    out = []
    for dep in declared(runtime):
        try:
            out.append(probe_one(dep, host))
        except Exception:
            from bgc import applog
            applog.LOGGER.exception("serveur lié %r : sondage en échec", dep.get("id"))
    return out


def dep_key(dep_id: str) -> str:
    """Identifiant d'élément d'une dépendance (clé des mesures : « tuile/dep:<id> »)."""
    return f"dep:{dep_id}"
