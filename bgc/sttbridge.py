"""Pont STT : API OpenAI (`/v1/audio/transcriptions`) → whisper.cpp (`/inference`).

Pourquoi ce fichier existe
--------------------------
Hermes transcrit avec **faster-whisper** (CTranslate2), qui ne sait faire du GPU qu'en CUDA
(NVIDIA). Sur un GPU AMD, il retombe sur le CPU : mesuré 11,2 s pour 8,7 s d'audio.
OpenWhispr, lui, installe et lance `whisper-server-win32-x64-vulkan.exe` (whisper.cpp + Vulkan)
avec le même modèle : 1,05 s pour le même extrait. Ce serveur ne parle que `/inference`, alors
que Hermes parle le dialecte OpenAI (`/v1/audio/transcriptions`). Ce module fait la traduction.

Ce n'est **pas** un fork : aucun code de Hermes ni d'OpenWhispr n'est modifié, rien n'est
déplacé. On lit le serveur amont, on relaie, on reformate.

Contrat exposé (ce que Hermes envoie)
-------------------------------------
POST /v1/audio/transcriptions   multipart/form-data : file, model, language, response_format
GET  /health                    état du pont + de l'amont (pour la sonde Background Check)

Le pont tourne dans un **fil** du process Background Check (pas de sous-processus) : un seul process à
suivre, rien qui survit à l'app, aucun problème de chemin d'interpréteur une fois empaqueté en
exe PyInstaller (`--onefile`).
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8770
DEFAULT_UPSTREAM = "http://127.0.0.1:8178"   # port par défaut du serveur lancé par OpenWhispr
UPSTREAM_TIMEOUT = 180.0

# Le serveur whisper.cpp traite les requêtes une par une (un seul GPU). Un verrou évite
# d'empiler deux transcriptions concurrentes et de faire grimper la latence en silence.
_GPU_LOCK = threading.Lock()


class Stats:
    """Compteurs affichés par la sonde Background Check (jamais de texte d'audio conservé)."""

    def __init__(self) -> None:
        self.requests = 0
        self.errors = 0
        self.conversions = 0
        self.last_seconds = 0.0
        self.last_chars = 0
        self.last_error = ""
        self.last_at = 0.0

    def snapshot(self) -> dict:
        return {"requests": self.requests, "errors": self.errors,
                "conversions": self.conversions,
                "last_seconds": round(self.last_seconds, 2), "last_chars": self.last_chars,
                "last_error": self.last_error, "last_at": self.last_at}


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    """Découpe un `multipart/form-data` en {champ: {data, filename, ctype}} — stdlib seule."""
    fields: dict[str, dict] = {}
    if not content_type or "boundary=" not in content_type:
        return fields
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1", "replace") + b"\r\n\r\n" + body)
    if not message.is_multipart():
        return fields
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fields[name] = {"data": part.get_payload(decode=True) or b"",
                        "filename": part.get_filename() or "audio",
                        "ctype": part.get_content_type()}
    return fields


def post_inference(upstream: str, audio: bytes, filename: str, ctype: str,
                   language: str = "") -> dict:
    """Envoie l'audio au serveur whisper.cpp et renvoie son JSON."""
    boundary = "----bgc" + uuid.uuid4().hex
    chunks: list[bytes] = []

    def add_field(name: str, value: str) -> None:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                      f"{value}\r\n".encode("utf-8"))

    add_field("response_format", "json")
    if language:
        add_field("language", language)
    chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                   f'filename="{filename}"\r\nContent-Type: {ctype or "application/octet-stream"}'
                   "\r\n\r\n").encode("utf-8"))
    chunks.append(audio)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(chunks)

    request = urllib.request.Request(
        f"{upstream.rstrip('/')}/inference", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Content-Length": str(len(body))})
    with urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def probe_upstream(upstream: str, timeout: float = 2.5) -> dict:
    """L'amont répond-il ? (`GET /` du serveur whisper.cpp renvoie 200.)"""
    if not upstream:
        return {"up": False, "code": None, "error": "adresse vide"}
    try:
        request = urllib.request.Request(upstream.rstrip("/") + "/", method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"up": True, "code": response.status, "error": ""}
    except urllib.error.HTTPError as exc:
        return {"up": exc.code < 500, "code": exc.code, "error": ""}
    except Exception as exc:                       # réseau, refus de connexion, DNS…
        return {"up": False, "code": None, "error": str(exc)}


def _silent_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    """Petit WAV de silence (16 bits mono) — sert à tester le VRAI chemin de transcription."""
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    header = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
              + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
              + b"data" + struct.pack("<I", len(data)))
    return header + data


def probe_inference(upstream: str, timeout: float = 30.0) -> dict:
    """Teste `POST /inference` pour de vrai (GET / ne prouve que « le serveur écoute »).

    Un GET / répond 200 même quand `/inference` refuse tout (400 « Invalid request »), par
    exemple quand le serveur n'a pas été lancé avec `--convert` et reçoit de l'opus.
    """
    if not upstream:
        return {"up": False, "code": None, "body": "adresse vide"}
    try:
        result = post_inference(upstream, _silent_wav(), "probe.wav", "audio/wav", "")
        text = result.get("text") if isinstance(result, dict) else None
        return {"up": True, "code": 200, "body": "", "text_ok": isinstance(text, str)}
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace").strip()[:160]
        except Exception:
            body = ""
        return {"up": False, "code": exc.code, "body": body or str(exc)}
    except Exception as exc:
        return {"up": False, "code": None, "body": str(exc)}


# ── conversion en WAV ────────────────────────────────────────────────────────────
# Le serveur du sidecar OpenWhispr est lancé SANS `--convert` : il n'accepte que du WAV
# (16 bits PCM). Une dictée du desktop arrive en **webm/opus** → réponse 400 « Invalid
# request » en 1,7 ms, sans même toucher le GPU. On convertit donc dans le pont.
_ffmpeg_path: str | None | bool = False    # False = pas encore cherché, None = introuvable


def find_ffmpeg() -> str | None:
    """Chemin d'un ffmpeg utilisable (résultat mis en cache pour la vie du process)."""
    global _ffmpeg_path
    if _ffmpeg_path is not False:
        return _ffmpeg_path
    found = None
    explicit = (os.environ.get("BGC_FFMPEG") or os.environ.get("HERMESSERV_FFMPEG") or "").strip()
    if explicit and os.path.isfile(explicit):
        found = explicit
    if found is None:
        found = shutil.which("ffmpeg")
    if found is None:
        local = os.environ.get("LOCALAPPDATA", "")
        for pattern in (rf"{local}\hermes\tools\ffmpeg-*\bin\ffmpeg.exe",
                        rf"{local}\Programs\OpenWhispr\resources\app.asar.unpacked"
                        rf"\node_modules\ffmpeg-static\ffmpeg.exe"):
            matches = sorted(glob.glob(pattern))
            if matches:
                found = matches[-1]
                break
    _ffmpeg_path = found
    return found


def is_wav(data: bytes) -> bool:
    """Vrai si les octets sont déjà un WAV (RIFF....WAVE)."""
    return len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def guess_suffix(filename: str, ctype: str = "") -> str:
    """Extension de fichier pour ffmpeg : celle du nom, sinon déduite du type MIME."""
    extension = os.path.splitext(filename or "")[1].lower()
    if extension and len(extension) <= 6:
        return extension
    kind = (ctype or "").lower()
    for needle, suffix in (("webm", ".webm"), ("ogg", ".ogg"), ("opus", ".ogg"),
                           ("mpeg", ".mp3"), ("mp4", ".m4a"), ("m4a", ".m4a"),
                           ("flac", ".flac"), ("wav", ".wav")):
        if needle in kind:
            return suffix
    return ".bin"


def to_wav_16k(data: bytes, suffix: str = ".bin") -> tuple[bytes | None, str]:
    """Convertit n'importe quel audio en WAV 16 kHz mono 16 bits. (None, message) si échec."""
    if is_wav(data):
        return data, ""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None, ("audio à convertir (ce n'est pas du WAV) et ffmpeg introuvable ; "
                      "définis BGC_FFMPEG ou installe ffmpeg")
    work = tempfile.mkdtemp(prefix="sttbridge-")
    source = os.path.join(work, "input" + suffix)
    target = os.path.join(work, "output.wav")
    try:
        with open(source, "wb") as handle:
            handle.write(data)
        proc = subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", source,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", target],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=60)
        if proc.returncode != 0 or not os.path.isfile(target):
            message = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
            return None, f"conversion en WAV impossible : {message or 'ffmpeg a échoué'}"
        with open(target, "rb") as handle:
            return handle.read(), ""
    except Exception as exc:                       # noqa: BLE001 - jamais fatal ici
        return None, f"conversion en WAV impossible : {exc}"
    finally:
        shutil.rmtree(work, ignore_errors=True)



class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    bridge: "SttBridge" = None                     # injecté par SttBridge.start()

    # Pas de bruit dans la console de l'app : le journal, c'est Background Check.
    def log_message(self, fmt: str, *args) -> None:   # noqa: A003 - signature imposee
        return

    # --- réponses ---------------------------------------------------------
    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _text(self, code: int, payload: str) -> None:
        data = payload.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: int, message: str, kind: str = "invalid_request_error") -> None:
        self._json(code, {"error": {"message": message, "type": kind}})

    # --- routes -----------------------------------------------------------
    def do_GET(self) -> None:                      # noqa: N802 - nom impose par http.server
        if self.path.split("?")[0].rstrip("/") in ("/health", ""):
            bridge = self.bridge
            upstream = probe_upstream(bridge.upstream)
            self._json(200, {"status": "ok" if bridge.running else "stopped",
                             "port": bridge.port,
                             "upstream": bridge.upstream,
                             "upstream_up": upstream["up"],
                             **bridge.stats.snapshot()})
            return
        self._error(404, "route inconnue : " + self.path, "not_found")

    def do_POST(self) -> None:                     # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if not path.endswith("/audio/transcriptions"):
            self._error(404, "route inconnue : " + self.path, "not_found")
            return
        bridge = self.bridge
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                self._error(400, "corps de requête vide")
                return
            body = self.rfile.read(length)
            fields = parse_multipart(self.headers.get("Content-Type", ""), body)
            audio = fields.get("file")
            if not audio or not audio["data"]:
                self._error(400, "champ 'file' manquant")
                return

            def as_text(name: str, default: str = "") -> str:
                raw = fields.get(name)
                if not raw:
                    return default
                return raw["data"].decode("utf-8", "replace").strip() or default

            language = as_text("language")        # fr, en… ; vide = auto (réglage de l'amont)
            response_format = as_text("response_format", "json")

            # L'amont n'accepte que du WAV : une dictée du desktop arrive en webm/opus.
            audio_bytes, convert_error = to_wav_16k(audio["data"],
                                                    guess_suffix(audio["filename"], audio["ctype"]))
            if audio_bytes is None:
                bridge.stats.errors += 1
                bridge.stats.last_error = convert_error
                self._error(415, convert_error, "unsupported_audio")
                return
            if not is_wav(audio["data"]):
                bridge.stats.conversions += 1

            started = time.time()
            with _GPU_LOCK:
                result = post_inference(bridge.upstream, audio_bytes, "audio.wav",
                                        "audio/wav", language)
            elapsed = time.time() - started

            text = result.get("text")
            if not isinstance(text, str):
                raise ValueError("réponse amont sans champ 'text'")

            bridge.stats.requests += 1
            bridge.stats.last_seconds = elapsed
            bridge.stats.last_chars = len(text)
            bridge.stats.last_at = time.time()

            if response_format in ("text", "srt", "vtt"):
                self._text(200, text)
            else:
                self._json(200, {"text": text})

        except urllib.error.HTTPError as exc:
            # L'amont a RÉPONDU, mais a refusé : ce n'est pas « injoignable ».
            try:
                body = exc.read().decode("utf-8", "replace").strip()[:200]
            except Exception:
                body = ""
            detail = f"HTTP {exc.code}" + (f" — {body}" if body else "")
            bridge.stats.errors += 1
            bridge.stats.last_error = f"l'amont a refusé la requête : {detail}"
            self._error(502, f"whisper.cpp a refusé la requête : {detail}", "upstream_rejected")
        except urllib.error.URLError as exc:
            bridge.stats.errors += 1
            bridge.stats.last_error = f"amont injoignable : {exc}"
            self._error(502, f"serveur whisper.cpp injoignable ({bridge.upstream}) : {exc}",
                        "upstream_unavailable")
        except Exception as exc:                   # noqa: BLE001 - le pont ne doit jamais mourir
            bridge.stats.errors += 1
            bridge.stats.last_error = str(exc)
            self._error(500, f"échec de la transcription : {exc}", "internal_error")


class _BridgeServer(ThreadingHTTPServer):
    """Serveur du pont.

    `allow_reuse_address = False` est **obligatoire sous Windows** : avec SO_REUSEADDR, deux
    sockets peuvent se lier au même port et le second écoute dans le vide sans erreur (vérifié :
    le pont « démarrait » alors qu'un autre process tenait déjà 8770). On préfère un refus net.
    """

    allow_reuse_address = False
    daemon_threads = True


class SttBridge:
    """Le pont lui-même : un serveur HTTP dans un fil, démarrable et arrêtable."""

    def __init__(self) -> None:
        self.host = DEFAULT_HOST
        self.port = DEFAULT_PORT
        self.upstream = DEFAULT_UPSTREAM
        self.stats = Stats()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
              upstream: str = DEFAULT_UPSTREAM) -> tuple[bool, str]:
        """Démarre le pont. (False, message) si le port est déjà pris — on n'écrase personne."""
        if self.running:
            return True, "déjà démarré"
        self.host, self.port, self.upstream = host, int(port), upstream
        try:
            httpd = _BridgeServer((host, int(port)), _Handler)
        except OSError as exc:
            return False, f"port {host}:{port} indisponible ({exc})"
        _Handler.bridge = self
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="stt-bridge",
                                        kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()
        self.stats.last_error = ""
        return True, f"à l'écoute sur {host}:{port} → {upstream}"

    def stop(self) -> bool:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return False
        try:
            httpd.shutdown()          # doit être appelé depuis un AUTRE fil que serve_forever
        finally:
            httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        return True
