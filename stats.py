# -*- coding: utf-8 -*-
"""Statistiques d'usage et portrait de voix, calculés depuis l'historique.

Tout ici est de l'arithmétique et du comptage de mots : aucun modèle de
langue, aucun appel réseau. C'est une contrainte du projet, mais aussi un
choix — un décompte est vérifiable et reproductible, là où un portrait rédigé
par un modèle est surtout flatteur.

Le portrait de voix est donc composé de constats mesurés (longueur de phrase,
proportion de questions, hésitations, richesse du vocabulaire), chacun
comparé à des seuils explicites déclarés ici même.

    python stats.py        affiche les deux rapports en console
"""

import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta

import i18n

# Les données ci-dessous suivent la langue DICTÉE, pas celle de l'interface :
# quelqu'un qui dicte en français dans une interface anglaise a besoin de la
# liste française, sinon son mot le plus utilisé sera « de ».

# Mots vides : sans eux, le « mot le plus utilisé » serait « de » pour tout le
# monde. Liste volontairement courte et lisible plutôt qu'exhaustive.
STOPWORDS_FR = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "l", "et", "ou",
    "mais", "donc", "or", "ni", "car", "que", "qui", "quoi", "dont", "où",
    "à", "au", "aux", "en", "dans", "sur", "sous", "par", "pour", "avec",
    "sans", "vers", "chez", "je", "tu", "il", "elle", "on", "nous", "vous",
    "ils", "elles", "me", "te", "se", "moi", "toi", "lui", "leur", "y",
    "ce", "cet", "cette", "ces", "ça", "cela", "c", "s", "n", "j", "m", "t",
    "mon", "ma", "mes", "ton", "ta", "tes", "son", "sa", "ses", "notre",
    "nos", "votre", "vos", "leurs", "est", "sont", "était", "être", "suis",
    "es", "sommes", "êtes", "a", "ai", "as", "ont", "avait", "avoir", "avons",
    "avez", "fait", "faire", "peut", "peux", "pouvoir", "va", "vais", "aller",
    "plus", "moins", "très", "tout", "tous", "toute", "toutes", "bien",
    "pas", "ne", "non", "oui", "si", "y", "là", "ici", "alors", "aussi",
    "comme", "quand", "parce", "puis", "encore", "déjà", "toujours", "jamais",
}

STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "but", "so", "because", "that", "this",
    "these", "those", "which", "who", "whom", "whose", "what", "where",
    "when", "how", "why", "i", "you", "he", "she", "it", "we", "they", "me",
    "him", "her", "us", "them", "my", "your", "his", "its", "our", "their",
    "mine", "yours", "is", "are", "was", "were", "be", "been", "being", "am",
    "have", "has", "had", "do", "does", "did", "will", "would", "can",
    "could", "should", "may", "might", "must", "shall", "of", "in", "on",
    "at", "to", "for", "with", "by", "from", "about", "into", "over",
    "after", "before", "under", "between", "through", "up", "down", "out",
    "off", "as", "if", "then", "than", "there", "here", "not", "no", "yes",
    "all", "any", "some", "each", "every", "both", "more", "most", "very",
    "just", "only", "also", "too", "still", "already", "always", "never",
    "well", "like", "get", "got", "go", "going", "want", "make", "made",
}

# Hésitations et béquilles de langage, comptées à part.
FILLERS_FR = ("euh", "heu", "bah", "ben", "hein", "voilà", "en fait",
              "du coup", "tu vois", "genre", "quoi", "je veux dire",
              "c'est-à-dire", "disons", "enfin bref")

FILLERS_EN = ("uh", "um", "erm", "like", "you know", "i mean", "sort of",
              "kind of", "basically", "actually", "literally", "right",
              "so yeah", "or whatever", "stuff like that")

WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)

# Élisions à détacher : sans elles « c'est » et « j'aimerais » comptent comme
# des mots pleins et trustent le classement. Liste fermée, sinon
# « aujourd'hui » se ferait couper en deux.
ELISION_RE = re.compile(
    r"\b(c|d|j|l|m|n|s|t|qu|jusqu|lorsqu|puisqu|quoiqu)['’]", re.UNICODE)

# Table par langue dictée. Une langue absente dégrade proprement : pas de
# filtrage des mots vides ni de comptage d'hésitations, tout le reste des
# statistiques fonctionne normalement.
LINGUISTICS = {
    "fr": {"stopwords": STOPWORDS_FR, "fillers": FILLERS_FR,
           "elisions": ELISION_RE},
    "en": {"stopwords": STOPWORDS_EN, "fillers": FILLERS_EN,
           "elisions": None},
}
EMPTY_LANG = {"stopwords": frozenset(), "fillers": (), "elisions": None}


def linguistics(language):
    return LINGUISTICS.get((language or "").lower()[:2], EMPTY_LANG)

# Seuils du portrait. Déclarés ici pour qu'un constat soit toujours traçable
# jusqu'à un nombre, jamais une impression.
LONG_SENTENCE = 22          # mots par phrase au-delà desquels on parle « long »
SHORT_SENTENCE = 11
RICH_VOCAB = 0.55           # mots distincts / mots totaux
QUESTION_RATE = 0.12        # part de phrases interrogatives
FILLER_RATE = 0.03          # part de mots qui sont des béquilles


def _words(text, elisions=ELISION_RE):
    if elisions is not None:
        text = elisions.sub(r"\1 ", text.lower())
    return WORD_RE.findall(text.lower())


def _strip_accents(word):
    return "".join(c for c in unicodedata.normalize("NFD", word)
                   if unicodedata.category(c) != "Mn")


def _parse_time(stamp):
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def app_label(exe):
    """« Code.exe » -> « Code ». Purement cosmétique."""
    name = (exe or "").rsplit(".", 1)[0]
    return name[:1].upper() + name[1:] if name else "Inconnue"


# ------------------------------------------------------------------- usage

def usage(entries, days=182):
    """Compteurs d'usage : volume, débit, régularité, applications."""
    total_words = 0
    total_seconds = 0.0
    total_fixes = 0
    per_day = Counter()
    per_app = Counter()
    per_hour = Counter()
    speeds = []

    for entry in entries:
        text = entry.get("text", "")
        words = len(text.split())
        seconds = entry.get("seconds", 0) or 0
        total_words += words
        total_seconds += seconds
        total_fixes += entry.get("fixes", 0) or 0
        per_app[entry.get("app", "")] += words

        # Un débit ne veut rien dire sur deux mots : on écarte les bribes.
        if seconds >= 5 and words >= 10:
            speeds.append(words / (seconds / 60.0))

        moment = _parse_time(entry.get("time", ""))
        if moment:
            per_day[moment.date()] += words
            per_hour[moment.hour] += words

    speeds.sort()
    # Médiane plutôt que moyenne : une seule dictée hachée fausserait la moyenne.
    median_wpm = speeds[len(speeds) // 2] if speeds else 0

    today = date.today()
    calendar = [{"date": (today - timedelta(days=i)).isoformat(),
                 "words": per_day.get(today - timedelta(days=i), 0)}
                for i in range(days - 1, -1, -1)]

    apps = [{"app": app_label(name), "words": count,
             "share": round(100 * count / total_words) if total_words else 0}
            for name, count in per_app.most_common(6) if name]

    return {
        "dictations": len(entries),
        "words": total_words,
        "minutes": round(total_seconds / 60),
        "fixes": total_fixes,
        "wpm": round(median_wpm),
        "streak": _streak(per_day, today),
        "best_streak": _best_streak(per_day),
        "calendar": calendar,
        "apps": apps,
        "hours": [per_hour.get(h, 0) for h in range(24)],
        "since": min((e["date"] for e in calendar if e["words"]), default=""),
    }


def _streak(per_day, today):
    """Jours consécutifs jusqu'à aujourd'hui. La veille compte : une série
    n'est pas rompue tant que la journée en cours n'est pas finie."""
    if not per_day:
        return 0
    start = today if per_day.get(today) else today - timedelta(days=1)
    if not per_day.get(start):
        return 0
    count = 0
    while per_day.get(start - timedelta(days=count)):
        count += 1
    return count


def _best_streak(per_day):
    days = sorted(d for d, words in per_day.items() if words)
    best = run = 0
    previous = None
    for day in days:
        run = run + 1 if previous and (day - previous).days == 1 else 1
        best = max(best, run)
        previous = day
    return best


# -------------------------------------------------------------------- voix

def voice(entries, language="fr", ui_language="en"):
    """Portrait mesuré : tics de langage, tournures, moment et lieu de pointe.

    `language` est la langue DICTÉE : elle commande les mots vides, les
    béquilles et les élisions. `ui_language` ne fait que choisir la langue des
    phrases rendues. Les deux sont indépendantes.
    """
    lang = linguistics(language)
    stopwords, fillers, elisions = (lang["stopwords"], lang["fillers"],
                                    lang["elisions"])
    words = []
    sentences = []
    questions = 0
    per_hour = Counter()
    per_app = Counter()
    filler_hits = Counter()
    corrected = Counter()

    for entry in entries:
        text = entry.get("text", "")
        words.extend(_words(text, elisions))
        for part in re.split(r"(?<=[.!?…])\s+", text):
            part = part.strip()
            if not part:
                continue
            sentences.append(part)
            if part.endswith("?"):
                questions += 1

        low = text.lower()
        for filler in fillers:
            hits = low.count(filler)
            if hits:
                filler_hits[filler] += hits

        moment = _parse_time(entry.get("time", ""))
        if moment:
            per_hour[moment.hour] += 1
        if entry.get("app"):
            per_app[entry["app"]] += 1
        if entry.get("fixes"):
            corrected[entry["app"] or ""] += entry["fixes"]

    if not words:
        return {"empty": True}

    meaningful = [w for w in words
                  if len(w) > 2 and _strip_accents(w) not in stopwords
                  and w not in stopwords]
    lengths = [len(s.split()) for s in sentences] or [0]
    avg_len = sum(lengths) / len(lengths)
    richness = len(set(words)) / len(words)
    q_rate = questions / len(sentences) if sentences else 0
    f_rate = sum(filler_hits.values()) / len(words)

    peak_hour = per_hour.most_common(1)[0][0] if per_hour else None
    peak_app = per_app.most_common(1)[0][0] if per_app else ""

    return {
        "empty": False,
        "profile": _profile(avg_len, richness, q_rate, f_rate, ui_language),
        "traits": [
            {"label": i18n.t("voice.words_per_sentence", ui_language),
             "value": f"{avg_len:.0f}"},
            {"label": i18n.t("voice.vocabulary", ui_language),
             "value": f"{100 * richness:.0f} %"},
            {"label": i18n.t("voice.questions", ui_language),
             "value": f"{100 * q_rate:.0f} %"},
            {"label": i18n.t("voice.fillers", ui_language),
             "value": f"{100 * f_rate:.1f} %"},
        ],
        "catchphrase": _catchphrase(entries, stopwords=stopwords,
                                    elisions=elisions),
        "top_words": [{"word": w, "count": c}
                      for w, c in Counter(meaningful).most_common(8)],
        "fillers": [{"word": w, "count": c}
                    for w, c in filler_hits.most_common(5)],
        "peak_hour": peak_hour,
        "peak_app": app_label(peak_app) if peak_app else "",
        "longest": max(lengths),
    }


def _profile(avg_len, richness, q_rate, f_rate, lang):
    """Trois constats, chacun adossé à un seuil déclaré en tête de ce module.

    Rien n'est rédigé à la volée : chaque branche pointe une phrase du
    catalogue, et le seuil franchi décide laquelle.
    """
    if avg_len >= LONG_SENTENCE:
        length_key = "voice.long_sentences"
    elif avg_len <= SHORT_SENTENCE:
        length_key = "voice.short_sentences"
    else:
        length_key = "voice.medium_sentences"

    vocab_key = ("voice.rich_vocabulary" if richness >= RICH_VOCAB
                 else "voice.narrow_vocabulary")

    if q_rate >= QUESTION_RATE:
        manner_key = "voice.many_questions"
    elif f_rate >= FILLER_RATE:
        manner_key = "voice.thinking_aloud"
    else:
        manner_key = "voice.clean_speech"

    return " ".join((
        i18n.t(length_key, lang, n=f"{avg_len:.0f}"),
        i18n.t(vocab_key, lang, n=f"{100 * richness:.0f}"),
        i18n.t(manner_key, lang),
    ))


def _catchphrase(entries, size=4, minimum=3, stopwords=frozenset(),
                 elisions=ELISION_RE):
    """Suite de mots la plus répétée : la tournure qui revient sans qu'on
    s'en aperçoive. Cherche du plus long au plus court, le premier groupe
    atteignant `minimum` occurrences gagne."""
    for length in range(6, size - 1, -1):
        grams = Counter()
        for entry in entries:
            words = _words(entry.get("text", ""), elisions)
            for i in range(len(words) - length + 1):
                gram = words[i:i + length]
                # Un n-gramme entièrement fait de mots vides ne dit rien.
                if stopwords and all(w in stopwords for w in gram):
                    continue
                grams[" ".join(gram)] += 1
        if grams:
            phrase, count = grams.most_common(1)[0]
            if count >= minimum:
                return {"text": phrase, "count": count}
    return None


if __name__ == "__main__":
    import config
    from history import History

    cfg = config.load()
    entries = History(size=10 ** 9).entries()
    if not entries:
        raise SystemExit("Historique vide : rien à analyser.")

    u = usage(entries)
    print(f"{u['dictations']} dictées, {u['words']} mots, {u['minutes']} min")
    print(f"débit médian {u['wpm']} mots/min, {u['fixes']} corrections")
    print(f"série en cours {u['streak']} j, record {u['best_streak']} j")
    print("applications :", ", ".join(
        f"{a['app']} {a['share']}%" for a in u["apps"]) or "aucune")

    v = voice(entries, language=cfg["language"],
              ui_language=i18n.resolve(cfg["ui_language"]))
    if v["empty"]:
        raise SystemExit("Pas assez de texte pour le portrait.")
    print("\n" + v["profile"])
    print("traits :", ", ".join(f"{t['label']} {t['value']}" for t in v["traits"]))
    if v["catchphrase"]:
        print(f"tournure : « {v['catchphrase']['text']} » "
              f"({v['catchphrase']['count']} fois)")
    print("mots :", ", ".join(f"{w['word']} ({w['count']})"
                              for w in v["top_words"]))
