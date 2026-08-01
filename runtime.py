# -*- coding: utf-8 -*-
"""Où vit localflow sur le disque, depuis les sources comme en exécutable.

Deux familles de chemins, volontairement distinctes :

- les ressources en lecture seule (`web/`, `config.example.json`) voyagent
  avec le code : à côté des fichiers .py, ou dans le dossier `_internal` d'un
  build PyInstaller ;
- les fichiers de l'utilisateur (`config.json`, `history.jsonl`,
  `localflow.log`) restent à côté de ce qu'il a lancé : le script, ou l'exe.
  À côté de l'exe plutôt qu'enfouis dans `_internal`, pour rester faciles à
  trouver, à éditer et à sauvegarder.
"""

import os
import sys

FROZEN = getattr(sys, "frozen", False)

# Ressources embarquées avec le code (web/, config.example.json).
ASSETS_DIR = os.path.dirname(os.path.abspath(__file__))

# Fichiers que l'utilisateur lit ou édite (config, historique, journal).
DATA_DIR = os.path.dirname(sys.executable) if FROZEN else ASSETS_DIR

# Noms des exécutables du build PyInstaller (voir localflow.spec).
_EXE_NAMES = {"app.py": "localflow.exe", "ui.py": "localflow-ui.exe"}


def launch_command(script):
    """Commande qui lance `app.py` ou `ui.py` dans la distribution courante.

    Depuis les sources : `pythonw script.py` (pas de fenêtre de console).
    En exécutable : l'exe correspondant, à côté de celui qui tourne.
    """
    if FROZEN:
        return [os.path.join(DATA_DIR, _EXE_NAMES[script])]
    folder = os.path.dirname(sys.executable)
    exe = os.path.join(folder, "pythonw.exe")
    if not os.path.exists(exe):
        exe = sys.executable
    return [exe, os.path.join(ASSETS_DIR, script)]
