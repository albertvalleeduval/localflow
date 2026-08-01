# -*- coding: utf-8 -*-
"""Capture micro : blocs de 100 ms vers le moteur de transcription.

Le callback de `sounddevice` tourne dans un thread audio temps réel : tout ce
qui s'y attarde produit un trou dans l'enregistrement, donc des mots perdus. Il
se contente donc de copier le bloc dans une file, et un thread ordinaire le
transmet au moteur.
"""

import queue
import threading
import time

import numpy as np
import sounddevice as sd

from engine import SAMPLE_RATE

BLOCK_MS = 100


class Recorder:
    def __init__(self, sink, device=None, block_ms=BLOCK_MS, log=None):
        """`sink` reçoit chaque bloc float32 mono (typiquement engine.feed)."""
        self.sink = sink
        self.device = device
        self.block_size = int(SAMPLE_RATE * block_ms / 1000)
        self.log = log or (lambda *a: None)

        self._queue = queue.Queue()
        self._stream = None
        self._pump = None
        self._level = 0.0
        self._overflows = 0
        self._blocks = 0

    # ------------------------------------------------------------ cycle de vie

    def start(self):
        if self._stream is not None:
            return
        self._queue = queue.Queue()
        self._level = 0.0
        self._overflows = 0
        self._blocks = 0
        self._pump = threading.Thread(target=self._drain, daemon=True)
        self._pump.start()
        stream = None
        try:
            stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=self.block_size,
                device=self.device,
                callback=self._callback,
            )
            stream.start()
        except Exception:
            # Micro inouvrable (débranché, occupé) : sans ce nettoyage, chaque
            # tentative abandonnerait un thread bloqué sur la file à jamais.
            if stream is not None:
                stream.close()
            self._queue.put(None)
            self._pump.join(timeout=1.0)
            self._pump = None
            raise
        self._stream = stream

    def stop(self):
        """Ferme le flux et garantit que tous les blocs ont atteint le moteur."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._queue.put(None)
        if self._pump is not None:
            self._pump.join(timeout=2.0)
            self._pump = None
        if self._overflows:
            self.log(f"microphone: {self._overflows} buffer overflow(s)")
        return self._blocks * self.block_size / SAMPLE_RATE

    @property
    def level(self):
        """Niveau sonore lissé (0..1), pour le visualiseur de l'overlay."""
        return self._level

    @property
    def recording(self):
        return self._stream is not None

    # ---------------------------------------------------------------- interne

    def _callback(self, indata, frames, time_info, status):
        if status:
            # input overflow : le tampon a débordé, de l'audio est perdu.
            self._overflows += 1
        # indata pointe sur un tampon réutilisé par PortAudio : il faut copier.
        self._queue.put(indata[:, 0].copy())

    def _drain(self):
        while True:
            block = self._queue.get()
            if block is None:
                return
            self._blocks += 1
            rms = float(np.sqrt(np.mean(block ** 2))) if len(block) else 0.0
            # Lissage asymétrique : montée immédiate, descente progressive, pour
            # un affichage qui suit la voix sans clignoter.
            self._level = max(rms, self._level * 0.7)
            try:
                self.sink(block)
            except Exception as exc:                      # noqa: BLE001
                # Une exception ici tuerait le thread et rendrait le micro muet
                # sans le moindre signe.
                self.log(f"error while processing an audio block: {exc!r}")


def list_devices():
    return sd.query_devices()


# ----------------------------------------------------------------- test isolé

def _selftest():
    """Dicte pendant N secondes et affiche le texte reconnu."""
    import sys

    from config import load as load_config
    from engine import StreamingTranscriber

    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    cfg = load_config()

    print(f"Périphérique d'entrée : {sd.query_devices(cfg['input_device'], 'input')['name']}")
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
    rec = Recorder(eng.feed, device=cfg["input_device"],
                   log=lambda m: print(m, flush=True))

    print(f"\nParlez pendant {seconds:.0f} s…", flush=True)
    eng.start()
    rec.start()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        bar = "#" * min(40, int(rec.level * 400))
        print(f"\r  niveau |{bar:<40}|", end="", flush=True)
        time.sleep(0.05)
    captured = rec.stop()
    print(f"\n{captured:.1f} s capturées, transcription du dernier morceau…",
          flush=True)
    t_release = time.perf_counter()
    text = eng.stop()
    print(f"latence après arrêt : {time.perf_counter() - t_release:.2f} s")
    print(f"\nTexte reconnu :\n{text or '(rien)'}")

    # Le contenu dépend de ce qui a été dit : le test vérifie la plomberie
    # (l'audio arrive bien du micro au moteur, sans trou), pas la transcription.
    complete = captured >= seconds * 0.9
    print(f"\n{'OK    ' if complete else 'ECHEC '}"
          f"audio capturé : {captured:.1f} s sur {seconds:.0f} s demandées")
    if not text:
        print("       (aucun texte : rien n'a été dit, ou le micro est muet)")
    return 0 if complete else 1


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
