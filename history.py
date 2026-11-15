# -*- coding: utf-8 -*-
"""Historique des dictées.

Le texte est écrit ici *avant* d'être injecté : une dictée qui atterrit dans la
mauvaise fenêtre, ou dans un champ qui refuse le collage, reste récupérable.
C'est la seule seconde chance possible — le presse-papiers, lui, est restauré
juste après le collage (voir injector.py).

Format : un objet JSON par ligne dans `history.jsonl`, le plus récent en
dernier. Volontairement lisible et sans base de données : le fichier s'ouvre
dans n'importe quel éditeur si l'outil ne démarre plus.

Le texte ne sert qu'à cette seconde chance : passé `days` jours (ou au-delà
des `size` dictées les plus récentes), il est effacé de la ligne, mais la
ligne reste — date, durée, nombre de mots, application. Les compteurs et les
statistiques couvrent donc tout l'usage, sans garder ce qui a été dit.

`history.jsonl` contient tout ce qui a été dicté : il est gitignoré et ne doit
jamais partir dans le dépôt public.
"""

import json
import msvcrt
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

import runtime

HISTORY_PATH = os.path.join(runtime.DATA_DIR, "history.jsonl")


def word_count(entry):
    """Mots d'une dictée, que son texte ait été effacé ou non."""
    if "words" in entry:
        return entry["words"]
    return len(entry.get("text", "").split())


class History:
    """Journal des dictées. Le texte n'est gardé que pour les `size` plus
    récentes et sur `days` jours (0 = sans limite de durée) ; au-delà, seules
    les métadonnées restent."""

    def __init__(self, size=200, days=7, path=HISTORY_PATH, log=None):
        self.size = size
        self.days = days
        self.path = path
        self.log = log or (lambda *a: None)
        self._lock = threading.Lock()

    @contextmanager
    def _locked(self):
        """Verrou de thread, doublé d'un verrou inter-processus.

        Le démon écrit, la fenêtre lit et supprime — depuis deux processus.
        Un `threading.Lock` seul laisserait une réécriture complète (delete,
        _prune) croiser un ajout venu d'en face et perdre une dictée. Le verrou
        vit dans un fichier à part : verrouiller `history.jsonl` lui-même
        empêcherait de le réécrire.
        """
        with self._lock:
            try:
                guard = open(self.path + ".lock", "a")
            except OSError:
                # Dossier en lecture seule : au moins le verrou de thread.
                yield
                return
            try:
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    guard.seek(0)
                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                guard.close()

    @property
    def enabled(self):
        return self.size > 0

    def add(self, text, seconds=0.0, stamp="", app="", fixes=0):
        """Range une dictée. `app` et `fixes` alimentent les statistiques."""
        if not self.enabled or not text:
            return
        entry = {"time": stamp, "seconds": round(seconds, 1),
                 "words": len(text.split()), "text": text}
        # Champs omis quand ils n'apprennent rien : le fichier reste lisible
        # à l'œil, et les entrées d'avant leur existence restent valides.
        if app:
            entry["app"] = app
        if fixes:
            entry["fixes"] = fixes
        try:
            with self._locked():
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._prune()
        except OSError as exc:
            # Une dictée réussie ne doit pas échouer parce que l'historique
            # est en lecture seule ou le disque plein.
            self.log(f"history not written: {exc}")

    def entries(self, limit=None):
        """Les dictées, de la plus récente à la plus ancienne."""
        if not os.path.exists(self.path):
            return []
        out = []
        with self._locked():
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

    def delete(self, index, expected_text=None):
        """Efface le texte de la `index`-ième dictée en partant de la plus
        récente. La ligne reste : elle compte toujours dans les statistiques.

        `expected_text` protège contre un décalage : une dictée ajoutée par
        le démon entre l'affichage et le clic déplace tous les index. Si le
        texte à cette position n'est pas celui attendu, la ligne visée est
        retrouvée par son contenu — et rien n'est supprimé en cas de doute.
        """
        with self._locked():
            if not os.path.exists(self.path):
                return False
            with open(self.path, "r", encoding="utf-8") as f:
                lines = [ln for ln in f if ln.strip()]
            # `entries()` renvoie la liste inversée : l'entrée 0 est la
            # dernière ligne du fichier.
            pos = len(lines) - 1 - index
            if expected_text is not None:
                if not (0 <= pos < len(lines)) \
                        or self._entry_text(lines[pos]) != expected_text:
                    matches = [i for i, ln in enumerate(lines)
                               if self._entry_text(ln) == expected_text]
                    if len(matches) != 1:
                        return False
                    pos = matches[0]
            elif not 0 <= pos < len(lines):
                return False
            lines[pos] = self._stripped(lines[pos])
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return True

    @staticmethod
    def _entry_text(line):
        try:
            return json.loads(line).get("text")
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _stripped(line):
        """La ligne sans son texte, le nombre de mots gardé à sa place."""
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return line
        if "text" not in entry:
            return line
        entry["words"] = word_count(entry)
        del entry["text"]
        return json.dumps(entry, ensure_ascii=False) + "\n"

    def clear(self):
        """Efface le texte de toutes les dictées ; les statistiques restent."""
        with self._locked():
            self._prune(strip_all=True)

    def prune(self):
        """Efface les textes expirés. Appelé par la fenêtre à l'affichage :
        sans dictée pendant des jours, le démon n'aurait pas l'occasion de
        le faire."""
        try:
            with self._locked():
                self._prune()
        except OSError as exc:
            self.log(f"history not pruned: {exc}")

    def _prune(self, strip_all=False):
        """Retire le texte des dictées trop anciennes ou au-delà des `size`
        plus récentes. Appelé sous verrou ; ne réécrit le fichier que si une
        ligne change."""
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            lines = [ln if ln.endswith("\n") else ln + "\n"
                     for ln in f if ln.strip()]
        cutoff = ""
        if self.days > 0:
            cutoff = (datetime.now() - timedelta(days=self.days)) \
                .strftime("%Y-%m-%d %H:%M:%S")
        kept = 0
        changed = False
        # Du plus récent au plus ancien : les `size` premiers textes restent.
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i]
            if '"text"' not in line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "text" not in entry:
                continue
            expired = (strip_all or kept >= self.size
                       or (cutoff and entry.get("time", "") < cutoff))
            if expired:
                lines[i] = self._stripped(line)
                changed = True
            else:
                kept += 1
        if changed:
            with open(self.path, "w", encoding="utf-8") as f:
                f.writelines(lines)
