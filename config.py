# -*- coding: utf-8 -*-
"""Chargement de la configuration.

Les valeurs par défaut vivent ici (et en copie lisible dans
`config.example.json`). `config.json` est gitignoré : il porte les réglages
personnels (raccourci, vocabulaire, corrections de noms propres) et n'a besoin
de contenir que les clés qui s'écartent du défaut.
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")

DEFAULTS = {
    "hotkey": "ctrl+alt+d",
    "mode": "hold",              # "hold" (maintien) ou "toggle" (bascule)
    "backend": "parakeet",       # "parakeet" ou "whisper"
    "model": None,               # None = modèle par défaut du backend
    "language": "fr",
    "replacements": {},          # corrections de noms propres appliquées au texte
    "vocabulary": "",            # backend whisper uniquement (initial_prompt)
    "paste_mode": "clipboard",   # "clipboard" (Ctrl+V) ou "type" (frappe)
    "type_delay_ms": 10,         # cadence de la frappe de secours
    "min_chunk_s": 3.0,
    "max_chunk_s": 12.0,
    "silence_ms": 400,
    "max_dictation_s": 300,      # arrêt d'office (micro oublié ouvert)
    "input_device": None,        # None = périphérique d'entrée par défaut
    "overlay": True,             # pastille d'état pendant la dictée
    "history_size": 200,         # dictées conservées ; 0 = pas d'historique
    # Langue de l'interface — à ne pas confondre avec `language` ci-dessus,
    # qui est la langue que l'on dicte. Anglais par défaut : le dépôt est
    # destiné à être public. "auto" suit la langue d'affichage de Windows.
    "ui_language": "en",
}

MODES = ("hold", "toggle")
BACKENDS = ("parakeet", "whisper")
PASTE_MODES = ("clipboard", "type")
UI_LANGUAGES = ("en", "fr", "auto")


class ConfigError(Exception):
    pass


def load(path=CONFIG_PATH):
    """Renvoie la config effective : défauts écrasés par le fichier utilisateur."""
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            try:
                user = json.load(f)
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{path} n'est pas un JSON valide : {exc}")
        if not isinstance(user, dict):
            raise ConfigError(f"{path} doit contenir un objet JSON.")
        unknown = sorted(set(user) - set(DEFAULTS))
        if unknown:
            raise ConfigError(
                f"Clés inconnues dans {os.path.basename(path)} : "
                f"{', '.join(unknown)}")
        cfg.update(user)
    _validate(cfg)
    return cfg


def _validate(cfg):
    if cfg["mode"] not in MODES:
        raise ConfigError(f"mode doit valoir {' ou '.join(MODES)}.")
    if cfg["backend"] not in BACKENDS:
        raise ConfigError(f"backend doit valoir {' ou '.join(BACKENDS)}.")
    if cfg["paste_mode"] not in PASTE_MODES:
        raise ConfigError(f"paste_mode doit valoir {' ou '.join(PASTE_MODES)}.")
    if cfg["ui_language"] not in UI_LANGUAGES:
        raise ConfigError(
            f"ui_language doit valoir {' ou '.join(UI_LANGUAGES)}.")
    if not isinstance(cfg["hotkey"], str) or not cfg["hotkey"].strip():
        raise ConfigError("hotkey doit être une chaîne non vide.")
    if not isinstance(cfg["language"], str) or not cfg["language"]:
        raise ConfigError("language doit être un code de langue (ex. \"fr\").")
    if not isinstance(cfg["vocabulary"], str):
        raise ConfigError("vocabulary doit être une chaîne.")
    if cfg["model"] is not None and not isinstance(cfg["model"], str):
        raise ConfigError("model doit être un nom de modèle, ou null.")
    device = cfg["input_device"]
    if device is not None and (not isinstance(device, int) or isinstance(device, bool)):
        raise ConfigError("input_device doit être un index entier, ou null.")
    if not isinstance(cfg["replacements"], dict):
        raise ConfigError("replacements doit être un objet {motif: remplacement}.")
    for pattern, target in cfg["replacements"].items():
        if not isinstance(pattern, str) or not isinstance(target, str):
            raise ConfigError(
                "replacements : motifs et remplacements doivent être des chaînes.")
    if not isinstance(cfg["overlay"], bool):
        raise ConfigError("overlay doit valoir true ou false.")
    for key in ("min_chunk_s", "max_chunk_s", "silence_ms", "max_dictation_s"):
        value = cfg[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ConfigError(f"{key} doit être un nombre positif.")
    size = cfg["history_size"]
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ConfigError("history_size doit être un entier positif ou nul.")
    delay = cfg["type_delay_ms"]
    if not isinstance(delay, (int, float)) or isinstance(delay, bool) or delay < 0:
        raise ConfigError("type_delay_ms doit être un nombre positif ou nul.")
    if cfg["max_chunk_s"] <= cfg["min_chunk_s"]:
        raise ConfigError("max_chunk_s doit être supérieur à min_chunk_s.")


if __name__ == "__main__":
    import sys

    try:
        cfg = load()
    except ConfigError as exc:
        sys.exit(f"Configuration invalide : {exc}")
    source = "config.json" if os.path.exists(CONFIG_PATH) else "défauts (pas de config.json)"
    print(f"Configuration chargée depuis : {source}")
    for key in DEFAULTS:
        flag = "" if cfg[key] == DEFAULTS[key] else "  <- personnalisé"
        print(f"  {key} = {cfg[key]!r}{flag}")
