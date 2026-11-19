# -*- coding: utf-8 -*-
"""Moteur de transcription en flux pour la dictée locale.

Principe : pendant que l'utilisateur parle, l'audio est découpé en morceaux
sur les silences et transcrit en arrière-plan. Au moment où il relâche la
touche, il ne reste que la fin de phrase à transcrire — la latence perçue
est celle du dernier morceau, pas de la dictée entière.

Trois backends : « parakeet » et « whisper » tournent sur la machine,
« groq » envoie l'audio à l'API de Groq (voir groq_api.py) et ne charge
aucun modèle local.
"""

import os
import queue
import threading
import time

import numpy as np

from groq_api import GroqClient, GroqError, resolve_key

SAMPLE_RATE = 16000

# Découpage propre au backend groq. L'API compte les requêtes (par minute et
# par jour, quota partagé entre les applications d'un même compte) et facture
# une durée minimale par requête (10 s à ce jour) : les morceaux de 3 à 12 s du
# flux local coûteraient une requête toutes les quelques secondes, chacune
# comptée plus que sa durée. Groq répond vite, on garde donc de gros
# morceaux : une dictée de moins de 20 s part en une seule requête, au
# relâchement ; au-delà, coupe sur le premier silence passé 20 s, et d'office
# à 40 s pour que le dernier morceau, celui qu'on attend, reste court.
GROQ_MIN_CHUNK_S = 20.0
GROQ_MAX_CHUNK_S = 40.0

# Marqueurs d'hallucination classiques de Whisper sur du silence.
HALLUCINATIONS = (
    "sous-titr", "amara.org", "merci d'avoir regardé", "abonnez-vous",
    "à la prochaine", "merci de votre attention",
)


class StreamingTranscriber:
    def __init__(
        self,
        backend="parakeet",    # "parakeet", "whisper" ou "groq"
        model_name=None,       # modèle local ; None = défaut du backend
        language="fr",
        vocabulary="",         # whisper et groq (amorce du modèle)
        min_chunk_s=3.0,       # durée mini avant d'envisager une coupe
        max_chunk_s=12.0,      # coupe forcée même sans silence
        silence_ms=400,        # silence requis pour couper
        log=None,
        groq_api_key="",       # groq : vide = variable GROQ_API_KEY
        groq_model="whisper-large-v3-turbo",
    ):
        self.backend = backend
        if backend == "groq":
            min_chunk_s = max(min_chunk_s, GROQ_MIN_CHUNK_S)
            max_chunk_s = max(max_chunk_s, GROQ_MAX_CHUNK_S)
        self.language = language
        self.vocabulary = vocabulary
        self.min_chunk = int(min_chunk_s * SAMPLE_RATE)
        self.max_chunk = int(max_chunk_s * SAMPLE_RATE)
        self.silence_samples = int(silence_ms / 1000 * SAMPLE_RATE)
        self.log = log or (lambda *a: None)

        t0 = time.perf_counter()
        if backend == "groq":
            # Pas de modèle local : rien à télécharger, presque pas de mémoire.
            self.model = GroqClient(resolve_key(groq_api_key), groq_model,
                                    log=self.log)
            self.model.check()
        elif backend == "parakeet":
            import onnx_asr
            # fp32 obligatoire : l'int8 perd la ponctuation et les majuscules
            # (mesuré au PoC du 2026-07-31).
            self.model = onnx_asr.load_model(
                model_name or "nemo-parakeet-tdt-0.6b-v3")
        else:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(
                model_name or "large-v3-turbo",
                device="cpu",
                compute_type="int8",
                cpu_threads=os.cpu_count() or 8,
            )
        self.load_time = time.perf_counter() - t0

        # Passe de chauffe : la première inférence est toujours plus lente.
        # Pas avec groq, où elle coûterait une requête à chaque chargement.
        t0 = time.perf_counter()
        if backend != "groq":
            self._transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))
        self.warmup_time = time.perf_counter() - t0

        self._reset()

    # ------------------------------------------------------------------ état

    def _reset(self):
        self._buf = []
        self._buf_len = 0
        self._silence_run = 0
        self._peak_rms = 1e-4
        self._parts = []
        self._chunk_stats = []   # (durée_audio_s, durée_transcription_s)
        self._errors = []        # GroqError des morceaux non transcrits
        self._queue = queue.Queue()
        self._worker = None

    # ------------------------------------------------------- cycle de dictée

    def start(self):
        self._reset()
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

    def feed(self, block):
        """Reçoit un bloc float32 mono 16 kHz pendant l'enregistrement."""
        rms = float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0
        self._peak_rms = max(self._peak_rms, rms)
        gate = max(0.006, 0.10 * self._peak_rms)
        if rms < gate:
            self._silence_run += len(block)
        else:
            self._silence_run = 0

        self._buf.append(block)
        self._buf_len += len(block)

        cut_on_silence = (
            self._buf_len >= self.min_chunk
            and self._silence_run >= self.silence_samples
        )
        if cut_on_silence or self._buf_len >= self.max_chunk:
            self._cut()

    def stop(self):
        """Fin de la dictée : transcrit ce qui reste et rend le texte complet."""
        if self._buf_len:
            self._cut()
        self._queue.put(None)
        self._worker.join()
        return " ".join(p for p in self._parts if p).strip()

    @property
    def chunk_stats(self):
        return list(self._chunk_stats)

    @property
    def errors(self):
        """Échecs Groq de la dernière dictée, à lire après stop() : l'appelant
        prévient l'utilisateur que tout ou partie n'a pas été transcrit."""
        return list(self._errors)

    # ---------------------------------------------------------------- interne

    def _cut(self):
        chunk = np.concatenate(self._buf)
        self._buf = []
        self._buf_len = 0
        self._silence_run = 0
        self._queue.put(chunk)

    def _work(self):
        while True:
            chunk = self._queue.get()
            if chunk is None:
                return
            t0 = time.perf_counter()
            try:
                text = self._transcribe(chunk, tail=self._tail(), fast=True)
            except Exception as exc:                       # noqa: BLE001
                if isinstance(exc, GroqError):
                    self._errors.append(exc)
                # Un morceau qui échoue ne doit pas tuer le thread : le reste
                # de la dictée serait perdu sans le moindre signe.
                self.log(f"chunk could not be transcribed, skipped: {exc!r}")
                self._parts.append("")
                continue
            dt = time.perf_counter() - t0
            self._chunk_stats.append((len(chunk) / SAMPLE_RATE, dt))
            # Garde-fou : un morceau qui répète mot pour mot le précédent est
            # une boucle d'hallucination, pas une vraie redite. Comparé au
            # dernier morceau non vide : un blanc intercalé ne le déjoue pas.
            previous = next((p for p in reversed(self._parts) if p), "")
            if text and text == previous:
                text = ""
            self._parts.append(text)
            self.log(f"  chunk {len(self._chunk_stats)}: "
                     f"{len(chunk) / SAMPLE_RATE:.1f}s audio -> {dt:.2f}s compute")

    def _tail(self):
        """Fin du texte déjà transcrit, passée en amorce du morceau suivant."""
        done = " ".join(p for p in self._parts if p)
        return done[-200:] if done else ""

    def _transcribe(self, audio, tail="", fast=False):
        if self.backend == "parakeet":
            # Pas d'amorce de contexte ni d'hallucination sur silence avec un
            # modèle transducer : l'appel est direct.
            return self.model.recognize(
                audio, sample_rate=SAMPLE_RATE, language=self.language).strip()
        prompt = ", ".join(x for x in (self.vocabulary, tail) if x) or None
        if self.backend == "groq":
            return self._transcribe_groq(audio, prompt or "")
        opts = {}
        if fast:
            # Un morceau coupé en plein mot fait chuter le logprob et déclenche
            # les 5 reprises du fallback de température : jusqu'à 8s de calcul
            # pour 3s d'audio (mesuré au PoC). En flux on décode une seule
            # fois, sans conditionnement interne sur le texte précédent (la
            # continuité passe déjà par initial_prompt).
            opts = {
                "temperature": 0.0,
                "condition_on_previous_text": False,
            }
        segments, _ = self.model.transcribe(
            audio,
            language=self.language,
            vad_filter=True,
            initial_prompt=prompt,
            **opts,
        )
        return self._keep_speech(segments, fast)

    def _transcribe_groq(self, audio, prompt):
        """Groq fait tourner le même Whisper : mêmes filtres d'hallucination,
        appliqués aux segments de la réponse verbose_json."""
        result = self.model.transcribe(audio, SAMPLE_RATE, self.language,
                                       prompt=prompt)
        segments = result.get("segments")
        if segments is None:
            segments = [{"text": result.get("text") or ""}]
        return self._keep_speech([_Segment(s) for s in segments], fast=True)

    @staticmethod
    def _keep_speech(segments, fast):
        """Texte des segments, sans le silence pris pour de la parole ni les
        formules qu'invente Whisper sur un blanc."""
        out = []
        for seg in segments:
            text = seg.text.strip()
            if seg.no_speech_prob > 0.6 and seg.avg_logprob < -0.4:
                continue
            if any(h in text.lower() for h in HALLUCINATIONS) and (fast or seg.no_speech_prob > 0.3):
                # En flux, ces marqueurs n'apparaissent jamais dans une vraie
                # dictée : on les jette sans condition pour ne pas les propager
                # au morceau suivant via initial_prompt.
                continue
            out.append(text)
        return " ".join(out)


class _Segment:
    """Segment de réponse Groq, aux attributs des segments faster-whisper."""

    def __init__(self, raw):
        self.text = raw.get("text") or ""
        self.no_speech_prob = float(raw.get("no_speech_prob") or 0.0)
        self.avg_logprob = float(raw.get("avg_logprob") or 0.0)
