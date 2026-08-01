# -*- coding: utf-8 -*-
"""PoC de latence : rejoue un enregistrement au rythme du temps réel dans le
moteur de flux et mesure la latence perçue (fin de parole -> texte complet),
comparée à l'approche naïve (tout transcrire à la fin).

Usage : python poc_latency.py <enregistrement.wav>

N'affiche aucun contenu de transcription : seulement des métriques.
Les textes sont écrits dans tmp/ pour inspection manuelle.
"""

import difflib
import os
import sys
import time
import wave

import numpy as np

from config import load as load_config
from engine import SAMPLE_RATE, StreamingTranscriber

HERE = os.path.dirname(os.path.abspath(__file__))


def load_wav_16k(path):
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        n_ch = w.getnchannels()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise SystemExit(f"Format non géré : {width * 8} bits")
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if n_ch > 1:
        audio = audio.reshape(-1, n_ch).mean(axis=1)
    if rate != SAMPLE_RATE:
        n_out = int(len(audio) * SAMPLE_RATE / rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n_out),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
    return audio


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip())
    wav = sys.argv[1]
    audio = load_wav_16k(wav)
    duration = len(audio) / SAMPLE_RATE
    print(f"Audio de test : {os.path.basename(wav)} — {duration:.1f}s", flush=True)

    # Le banc d'essai mesure le moteur tel qu'il tournera en vrai : mêmes
    # réglages de découpage et même backend que le démon.
    cfg = load_config()
    print("Chargement du modèle…", flush=True)
    eng = StreamingTranscriber(
        backend=cfg["backend"],
        model_name=cfg["model"],
        language=cfg["language"],
        vocabulary=cfg["vocabulary"],
        min_chunk_s=cfg["min_chunk_s"],
        max_chunk_s=cfg["max_chunk_s"],
        silence_ms=cfg["silence_ms"],
        log=lambda m: print(m, flush=True),
    )
    print(f"Modèle chargé en {eng.load_time:.1f}s, chauffe {eng.warmup_time:.1f}s",
          flush=True)

    # --- Référence naïve : tout transcrire d'un coup à la fin -------------
    t0 = time.perf_counter()
    naive_text = eng._transcribe(audio)
    naive_dt = time.perf_counter() - t0
    print(f"\nNAÏF : l'utilisateur attend {naive_dt:.2f}s "
          f"(RTF {duration / naive_dt:.1f}x)", flush=True)

    # --- Flux : alimentation au rythme du temps réel ----------------------
    print("\nFLUX : rejeu en temps réel (durée du test = durée de l'audio)…",
          flush=True)
    block = SAMPLE_RATE // 10  # blocs de 100 ms, comme un callback micro
    eng.start()
    t_start = time.perf_counter()
    for i in range(0, len(audio), block):
        target = t_start + i / SAMPLE_RATE
        pause = target - time.perf_counter()
        if pause > 0:
            time.sleep(pause)
        eng.feed(audio[i:i + block])
    t_release = time.perf_counter()          # « relâchement de la touche »
    stream_text = eng.stop()
    latency = time.perf_counter() - t_release

    stats = eng.chunk_stats
    busy = sum(dt for _, dt in stats)
    print(f"\nRésultats du flux :")
    print(f"  morceaux transcrits : {len(stats)}")
    print(f"  charge CPU pendant la dictée : {busy:.1f}s de calcul "
          f"pour {duration:.1f}s de parole ({100 * busy / duration:.0f}%)")
    print(f"  dernier morceau : {stats[-1][0]:.1f}s audio -> {stats[-1][1]:.2f}s")
    print(f"  >>> LATENCE PERÇUE après relâchement : {latency:.2f}s <<<")

    # --- Qualité : le flux dégrade-t-il le texte ? ------------------------
    ratio = difflib.SequenceMatcher(None, naive_text, stream_text).ratio()
    print(f"\nQualité : similarité naïf/flux {100 * ratio:.1f}% "
          f"({len(naive_text.split())} vs {len(stream_text.split())} mots)")

    tmp = os.path.join(HERE, "tmp")
    os.makedirs(tmp, exist_ok=True)
    for name, text in (("naif.txt", naive_text), ("flux.txt", stream_text)):
        with open(os.path.join(tmp, name), "w", encoding="utf-8") as f:
            f.write(text)
    print(f"Transcriptions écrites dans {tmp} pour inspection.", flush=True)


if __name__ == "__main__":
    main()
