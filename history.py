# -*- coding: utf-8 -*-
"""Historique des dictées.

Le texte est écrit ici *avant* d'être injecté : une dictée qui atterrit dans la
mauvaise fenêtre, ou dans un champ qui refuse le collage, reste récupérable.
C'est la seule seconde chance possible — le presse-papiers, lui, est restauré
juste après le collage (voir injector.py).

Format : un objet JSON par ligne dans `history.jsonl`, le plus récent en
dernier. Volontairement lisible et sans base de données : le fichier s'ouvre
dans n'importe quel éditeur si l'outil ne démarre plus.

`history.jsonl` contient tout ce qui a été dicté : il est gitignoré et ne doit
jamais partir dans le dépôt public.
"""

import json
import os
import threading

import runtime

HISTORY_PATH = os.path.join(runtime.DATA_DIR, "history.jsonl")


class History:
    """Journal des dictées, borné aux `size` dernières entrées."""

    def __init__(self, size=200, path=HISTORY_PATH, log=None):
        self.size = size
        self.path = path
        self.log = log or (lambda *a: None)
        # Le thread de travail écrit, l'interface lit : un verrou suffit, les
        # volumes en jeu se comptent en kilooctets.
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return self.size > 0

    def add(self, text, seconds=0.0, stamp="", app="", fixes=0):
        """Range une dictée. `app` et `fixes` alimentent les statistiques."""
        if not self.enabled or not text:
            return
        entry = {"time": stamp, "seconds": round(seconds, 1), "text": text}
        # Champs omis quand ils n'apprennent rien : le fichier reste lisible
        # à l'œil, et les entrées d'avant leur existence restent valides.
        if app:
            entry["app"] = app
        if fixes:
            entry["fixes"] = fixes
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._trim()
        except OSError as exc:
            # Une dictée réussie ne doit pas échouer parce que l'historique
            # est en lecture seule ou le disque plein.
            self.log(f"historique non écrit : {exc}")

    def entries(self, limit=None):
        """Les dictées, de la plus récente à la plus ancienne."""
        if not os.path.exists(self.path):
            return []
        out = []
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue        # ligne tronquée par un arrêt brutal
        out.reverse()
        return out[:limit] if limit else out

    def delete(self, index):
        """Supprime la `index`-ième dictée en partant de la plus récente."""
        with self._lock:
            if not os.path.exists(self.path):
                return False
            with open(self.path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f if ln.strip()]
            # `entries()` renvoie la liste inversée : l'entrée 0 est la
            # dernière ligne du fichier.
            pos = len(lines) - 1 - index
            if not 0 <= pos < len(lines):
                return False
            del lines[pos]
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return True

    def clear(self):
        with self._lock:
            if os.path.exists(self.path):
                os.remove(self.path)

    def _trim(self):
        """Réécrit le fichier quand il dépasse la taille voulue.

        Appelé sous verrou, et seulement une fois le dépassement franchi : on
        relit rarement, jamais à chaque dictée.
        """
        with open(self.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= self.size * 2:
            return
        with open(self.path, "w", encoding="utf-8") as f:
            f.writelines(lines[-self.size:])
