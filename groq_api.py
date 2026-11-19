# -*- coding: utf-8 -*-
"""Transcription en ligne chez Groq (backend « groq » de engine.py).

Contrairement à Parakeet et faster-whisper, ce backend envoie l'audio hors de
la machine : il se choisit explicitement dans la config et n'a aucun repli
silencieux vers un modèle local. Un échec (clé absente ou refusée, quota
atteint, réseau coupé) remonte en `GroqError`, dont `kind` dit quoi montrer.

API compatible OpenAI : POST multipart sur /audio/transcriptions, fichier WAV
16 kHz mono 16 bits. Bibliothèque standard seulement (urllib) : pas de
dépendance de plus pour un backend optionnel. La clé n'est jamais journalisée
ni recopiée dans un message d'erreur.
"""

import http.client
import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid
import wave

import numpy as np

API_BASE = "https://api.groq.com/openai/v1"
TRANSCRIPTIONS_URL = f"{API_BASE}/audio/transcriptions"
MODELS_URL = f"{API_BASE}/models"
# Certains frontaux refusent l'agent par défaut d'urllib : un 403 serait alors
# pris pour une clé refusée.
USER_AGENT = "localflow"

# Attente maximale acceptée sur un 429 : au-delà, mieux vaut dire que le quota
# est atteint que de figer la dictée.
MAX_RETRY_WAIT_S = 6.0
RETRIES = 2
# L'amorce est plafonnée côté Groq (224 jetons) : on garde la fin, c'est-à-dire
# le texte qui précède immédiatement le morceau.
PROMPT_MAX_CHARS = 800

# Réseau coupé, délai dépassé, connexion fermée en cours de réponse.
NETWORK_ERRORS = (OSError, http.client.HTTPException)


class GroqError(Exception):
    """Échec d'un appel à Groq. `kind` vaut "key" (clé absente ou refusée),
    "quota" (limite de requêtes atteinte), "network" ou "server"."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


def resolve_key(configured):
    """Clé de config.json, sinon variable d'environnement GROQ_API_KEY."""
    return (configured or "").strip() or os.environ.get("GROQ_API_KEY", "").strip()


def to_wav(audio, sample_rate):
    """float32 [-1, 1] -> octets d'un WAV PCM 16 bits mono."""
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _multipart(fields, filename, payload, mime):
    """Corps multipart/form-data : champs texte puis un fichier."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
            f'\r\n\r\n{value}\r\n'.encode("utf-8"))
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode("utf-8"))
    parts.append(payload)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _error_message(raw):
    """Message lisible d'une réponse d'erreur, tronqué pour le journal."""
    try:
        return str(json.loads(raw)["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return raw[:200].decode("utf-8", errors="replace")


class GroqClient:
    def __init__(self, api_key, model, log=None):
        if not api_key:
            raise GroqError("key", "no API key (groq_api_key or GROQ_API_KEY)")
        self.model = model
        self.log = log or (lambda *a: None)
        self._headers = {"Authorization": f"Bearer {api_key}",
                         "User-Agent": USER_AGENT}

    def _call(self, url, timeout, body=None, content_type=None):
        """Rend (statut, en-têtes, corps). Lève OSError ou HTTPException si le
        réseau fait défaut ; un statut HTTP d'erreur est rendu, pas levé."""
        headers = dict(self._headers)
        if content_type:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.headers, exc.read()

    def check(self):
        """Vérifie la clé au chargement, par la liste des modèles : aucun audio
        consommé. Une clé refusée lève ; un réseau absent (démarrage de
        Windows, Wi-Fi pas encore monté) n'empêche pas de charger, il sera
        signalé à la première dictée."""
        try:
            status, _, _ = self._call(MODELS_URL, timeout=8)
        except NETWORK_ERRORS as exc:
            self.log(f"groq not reachable at load time: {type(exc).__name__}")
            return False
        if status in (401, 403):
            raise GroqError("key", f"HTTP {status}")
        return 200 <= status < 300

    def transcribe(self, audio, sample_rate, language, prompt=""):
        """Transcrit un morceau. Rend la réponse verbose_json (texte et
        segments, avec no_speech_prob et avg_logprob)."""
        fields = {
            "model": self.model,
            "language": language,
            "temperature": "0",
            "response_format": "verbose_json",
        }
        if prompt:
            fields["prompt"] = prompt[-PROMPT_MAX_CHARS:]
        body, content_type = _multipart(
            fields, "audio.wav", to_wav(audio, sample_rate), "audio/wav")
        attempt = 0
        while True:
            attempt += 1
            try:
                status, headers, raw = self._call(
                    TRANSCRIPTIONS_URL, timeout=30,
                    body=body, content_type=content_type)
            except NETWORK_ERRORS as exc:
                if attempt == 1:
                    time.sleep(0.5)
                    continue
                raise GroqError("network", type(exc).__name__) from None
            if 200 <= status < 300:
                try:
                    return json.loads(raw)
                except ValueError:
                    raise GroqError("server", "response is not JSON") from None
            if status in (401, 403):
                raise GroqError("key", f"HTTP {status}")
            if status == 429:
                wait = _retry_after(headers)
                if attempt <= RETRIES and wait <= MAX_RETRY_WAIT_S:
                    self.log(f"groq rate limited, retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                raise GroqError("quota", f"retry after {wait:.0f}s: "
                                         f"{_error_message(raw)}")
            if status >= 500 and attempt <= RETRIES:
                time.sleep(0.5 * attempt)
                continue
            raise GroqError("server", f"HTTP {status}: {_error_message(raw)}")


def _retry_after(headers):
    try:
        return max(0.5, float(headers.get("retry-after", "")))
    except ValueError:
        return 2.0
