# -*- coding: utf-8 -*-
"""Traductions du côté Python : menu de l'icône et portrait de voix.

Le gros des textes vit dans `web/locales/*.json`, appliqué par la page. Ne
restent ici que les chaînes produites hors du navigateur.

Ce qui n'est PAS traduit, à dessein : le journal (`localflow.log`) et les
messages d'erreur de configuration. Ce sont des artefacts techniques — les
garder dans une seule langue rend lisible par tout le monde le journal collé
dans un rapport de bug, ce qui compte plus pour un dépôt public que de les
avoir dans la langue de chacun. L'anglais s'impose donc là.

Deux langues sont à distinguer, et il ne faut jamais les confondre :

- `ui_language` : la langue de l'interface, gérée ici ;
- `language` : la langue que l'on dicte, qui commande le modèle et les
  données linguistiques de `stats.py` (mots vides, béquilles, élisions).

Quelqu'un peut très bien dicter en français dans une interface anglaise.
"""

import ctypes

LANGUAGES = ("fr", "en")
DEFAULT = "en"


def system_language():
    """Langue d'affichage de Windows, ramenée à une langue connue."""
    try:
        # Les 10 bits de poids faible portent la langue primaire ; 0x0C = français.
        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return "fr" if (lang_id & 0x3FF) == 0x0C else "en"
    except Exception:                                         # noqa: BLE001
        return DEFAULT


def resolve(ui_language):
    """« auto » -> langue de Windows ; sinon la langue demandée si connue."""
    if ui_language in LANGUAGES:
        return ui_language
    return system_language()


def t(key, lang=DEFAULT, **params):
    """Traduit une clé. Une clé absente se rend telle quelle, jamais en erreur."""
    table = CATALOG.get(lang) or CATALOG[DEFAULT]
    text = table.get(key) or CATALOG[DEFAULT].get(key, key)
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError):
        return text


CATALOG = {
    "fr": {
        # Menu de l'icône de notification
        "tray.open": "Ouvrir localflow",
        "tray.reload": "Recharger la configuration",
        "tray.log": "Ouvrir le journal",
        "tray.quit": "Quitter",

        # Pastille d'état
        "overlay.loading": "chargement…",
        "overlay.working": "transcription…",

        # Traits mesurés de la page Ma voix
        "voice.words_per_sentence": "Mots par phrase",
        "voice.vocabulary": "Vocabulaire distinct",
        "voice.questions": "Phrases interrogatives",
        "voice.fillers": "Hésitations",

        # Portrait : une branche par constat, choisie sur un seuil chiffré
        "voice.long_sentences":
            "Vos phrases sont longues — {n} mots en moyenne. Vous déroulez "
            "votre pensée d'un trait plutôt que de la découper.",
        "voice.short_sentences":
            "Vos phrases sont courtes — {n} mots en moyenne. Vous allez à "
            "l'essentiel.",
        "voice.medium_sentences":
            "Vos phrases font {n} mots en moyenne, un rythme d'oral posé.",
        "voice.rich_vocabulary":
            "Votre vocabulaire est varié : {n} % de mots distincts, vous vous "
            "répétez peu.",
        "voice.narrow_vocabulary":
            "Vous revenez souvent sur les mêmes mots ({n} % de mots "
            "distincts) — signe d'un sujet suivi plutôt que d'un vocabulaire "
            "pauvre.",
        "voice.many_questions":
            "Vous posez beaucoup de questions : vos dictées sont des échanges "
            "plus que des monologues.",
        "voice.thinking_aloud":
            "Vous pensez à voix haute — les béquilles de langage sont "
            "fréquentes, ce qui est normal quand on dicte sans script.",
        "voice.clean_speech":
            "Vous dictez d'une voix assez nette, avec peu d'hésitations.",
    },
    "en": {
        "tray.open": "Open localflow",
        "tray.reload": "Reload configuration",
        "tray.log": "Open the log",
        "tray.quit": "Quit",

        "overlay.loading": "loading…",
        "overlay.working": "transcribing…",

        "voice.words_per_sentence": "Words per sentence",
        "voice.vocabulary": "Distinct vocabulary",
        "voice.questions": "Questions",
        "voice.fillers": "Filler words",

        "voice.long_sentences":
            "Your sentences run long — {n} words on average. You unspool a "
            "thought in one go rather than breaking it up.",
        "voice.short_sentences":
            "Your sentences are short — {n} words on average. You get "
            "straight to the point.",
        "voice.medium_sentences":
            "Your sentences average {n} words, an even spoken rhythm.",
        "voice.rich_vocabulary":
            "Your vocabulary is varied: {n} % distinct words, you rarely "
            "repeat yourself.",
        "voice.narrow_vocabulary":
            "You come back to the same words often ({n} % distinct) — a sign "
            "of a sustained topic rather than a thin vocabulary.",
        "voice.many_questions":
            "You ask a lot of questions: your dictations are exchanges more "
            "than monologues.",
        "voice.thinking_aloud":
            "You think out loud — filler words are frequent, which is normal "
            "when dictating without a script.",
        "voice.clean_speech":
            "You dictate cleanly, with few hesitations.",
    },
}


if __name__ == "__main__":
    print(f"Langue de Windows : {system_language()}")
    missing = {lang: sorted(set(CATALOG[DEFAULT]) - set(table))
               for lang, table in CATALOG.items()}
    for lang, keys in missing.items():
        print(f"{lang} : {len(CATALOG[lang])} clés"
              + (f", MANQUANTES : {keys}" if keys else ", complet"))
