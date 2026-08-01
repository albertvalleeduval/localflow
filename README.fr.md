# localflow

*[English version](README.md)*

Dictée système locale et gratuite pour Windows : on tient une touche, on parle,
on relâche, et le texte reconnu s'écrit dans le champ actif de n'importe quelle
application. Tout tourne en local sur le CPU — aucun compte, aucun quota,
aucun audio qui sort de la machine.

Alternative personnelle à Wispr Flow, avec la même exigence : la latence
perçue doit rester sous la seconde quelle que soit la durée dictée.

## Télécharger

**Prêt à l'emploi, sans Python** — récupérer `localflow-…-windows-x64.zip`
dans la [dernière release](https://github.com/albertvalleeduval/localflow/releases/latest),
l'extraire n'importe où, lancer `localflow.exe`. Le modèle (~2,5 Go) se
télécharge tout seul au premier lancement ; dès que l'info-bulle de l'icône de
notification affiche le raccourci, tenir `Ctrl+Alt+D` et parler. Un clic sur
l'icône (ou `localflow-ui.exe`) ouvre la fenêtre — raccourci, langue, micro,
historique, statistiques.

> **Avertissement SmartScreen.** Les exécutables ne sont pas signés (un
> certificat coûte cher, l'outil est gratuit) : Windows préviendra au premier
> lancement — cliquer *Informations complémentaires* → *Exécuter quand même*.
> Hésiter est un réflexe sain : l'outil installe un hook clavier, c'est ce que
> la dictée exige, mais c'est aussi ce qui mérite de la prudence avec un
> binaire téléchargé. Pour ne pas avoir à faire confiance à un zip,
> l'installation depuis les sources ci-dessous fait la même chose — le code
> est court et lisible, et le zip de release en est construit publiquement
> par [GitHub Actions](.github/workflows/release.yml).

**Depuis les sources** — voir [Installation](#installation).

## Comment ça marche

L'audio n'est pas transcrit d'un bloc à la fin : il est découpé sur les
silences pendant la parole, et chaque morceau part en transcription en
arrière-plan pendant qu'on continue de parler. Au relâchement de la touche il
ne reste qu'une fin de phrase à traiter, d'où une latence quasi constante.

Moteur : [`parakeet-tdt-0.6b-v3`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
(NVIDIA, 25 langues, ponctuation et majuscules natives) via `onnx-asr` en fp32
sur CPU. Un backend faster-whisper reste disponible en option.

## Latence mesurée

`poc_latency.py` est le banc d'essai : il rejoue un fichier audio au rythme du
temps réel dans le moteur (blocs de 100 ms, comme un callback micro) et mesure
le temps entre la fin de la parole et le texte complet.

Machine de mesure : CPU 16 threads, pas de GPU CUDA. Audio de test : 68 s de
parole dense sans pause, le pire cas pour le découpage sur silences.

| | Whisper `large-v3-turbo` int8 | Parakeet fp32 |
|---|---|---|
| Coût d'un morceau de 8 s | ~10 s | 0,65-0,9 s |
| Charge CPU pendant la dictée | 122 % (ne tient pas le temps réel) | ~8 % |
| **Latence après relâchement** | **23,1 s** | **0,11 s** |

Deux enseignements décisifs :

- **Whisper ne convient pas au flux sur CPU.** Il encode une fenêtre paddée de
  30 s à chaque appel, et l'encodeur de `turbo` est celui du grand modèle :
  chaque morceau coûte ~10 s quelle que soit sa taille. En parole dense la
  transcription prend du retard au lieu d'en rattraper.
- **Parakeet doit tourner en fp32.** La variante quantifiée int8 perd toute la
  ponctuation et les majuscules. Le fp32 coûte ~2,4 Go de RAM ; la qualité est
  au niveau de `large-v3-turbo` (96,5 % de similarité sur voix humaine réelle).

Le découpage en flux ne dégrade pas le texte : 98,3 % de similarité avec la
transcription du même audio en un seul bloc.

Borne haute : si le relâchement tombe juste avant une coupe, le dernier morceau
fait 12 s, soit ~0,9 s de calcul. La latence perçue reste donc entre 0,1 et 1 s
quelle que soit la durée dictée.

## Installation

Prérequis : Windows, Python 3.11+.

```
git clone https://github.com/albertvalleeduval/localflow.git
cd localflow
pip install -r requirements.txt
copy config.example.json config.json
```

`pywebview` sert uniquement à la fenêtre : le démon tourne sans lui.

Le modèle se télécharge tout seul au premier lancement (~2,5 Go en cache
HuggingFace).

Régler `language` dans `config.json` (ou plus tard dans l'onglet Réglages) sur
la langue que l'on dicte — `config.example.json` part sur `"en"`, mettre
`"fr"` pour dicter en français.

## Utilisation

```
python app.py
```

Le modèle se charge en quelques secondes, puis le démon attend le raccourci.
Tenir `Ctrl+Alt+D`, parler, relâcher : le texte s'écrit dans le champ actif.

Une seule instance peut tourner à la fois (verrou Windows) : un second
lancement s'arrête aussitôt en le signalant dans le journal. Tout ce que fait le
démon est consigné dans `localflow.log`, à côté du script — utile quand il est
lancé au démarrage, sans console.

Une icône apparaît dans la zone de notification. Clic : la fenêtre — ou son
retour au premier plan si elle est déjà ouverte, jamais un doublon. Clic
droit : ouvrir la fenêtre, recharger la configuration, ouvrir le journal,
quitter. Sous Windows 11, les nouvelles icônes atterrissent dans le
débordement — cliquer le chevron `^` de la barre des tâches et faire glisser
celle de localflow à côté de l'horloge pour la garder visible.

### La fenêtre

```
python ui.py
```

Cinq onglets. **Historique** : toutes les dictées, recherche, copie en un
clic. Le texte y est écrit *avant* d'être injecté, donc une dictée partie dans
la mauvaise fenêtre reste récupérable. **Réglages** : raccourci capturé en
appuyant sur les touches, mode, langue, micro, corrections, démarrage
automatique. **Statistiques** : débit, volume, régularité jour par jour,
applications et heures. **Ma voix** : portrait de style, tournure récurrente,
mots favoris. **État** : relevé de santé, journal, redémarrage du démon.

L'onglet État répond à une seule question — « est-ce que ça marche ? ». Une
ligne par chose à vérifier : service en marche et depuis combien de temps,
modèle chargé ou non, mémoire occupée, micro réellement utilisé, raccourci
actif, dernière dictée avec sa latence. Tout est déduit de l'extérieur (table
des processus, journal, configuration, historique), donc rien n'est demandé au
démon et la réponse reste juste même s'il ne répond plus.

### Deux langues, à ne pas confondre

- **`ui_language`** — la langue de la fenêtre et du menu de l'icône. Anglais
  par défaut, français disponible, `auto` pour suivre Windows. Se change dans
  Réglages, sans redémarrage.
- **`language`** — la langue que l'on **dicte**. Elle commande le modèle et
  l'analyse de texte de Ma voix : mots vides, béquilles de langage, élisions.

Les deux sont indépendantes. Quelqu'un qui dicte en français dans une
interface anglaise garde bien la liste de mots vides française, sinon son mot
le plus utilisé serait « de ». Les données linguistiques existent pour le
français et l'anglais ; pour une autre langue dictée, la dégradation est
propre — pas de filtrage des mots vides ni de comptage d'hésitations, tout le
reste des statistiques fonctionne.

Les traductions de la fenêtre vivent dans `web/locales/*.json`, celles du
menu de l'icône et du portrait dans `i18n.py`. **Le journal et les messages
d'erreur de configuration ne sont pas traduits** : ce sont des artefacts
techniques, et les garder en anglais rend lisible par tout le monde un
journal collé dans un rapport de bug.

### Statistiques et portrait de voix, sans modèle de langue

`stats.py` ne fait que de l'arithmétique et du comptage de mots : pas de LLM,
pas de réseau. C'est une contrainte du projet, mais aussi un choix — un
décompte est vérifiable et reproductible, là où un portrait rédigé par un
modèle est surtout flatteur.

Le portrait est donc composé de constats mesurés (longueur de phrase,
richesse du vocabulaire, part de questions, taux d'hésitations), chacun
comparé à un seuil déclaré en clair en tête du module. La tournure
récurrente est le n-gramme le plus fréquent, cherché du plus long au plus
court. Les élisions du français sont détachées avant comptage, sans quoi
« c'est » et « j'aimerais » trustent le classement des mots.

Deux champs alimentent ces pages depuis le 1<sup>er</sup> août :
l'application visée, relevée au début de la dictée (le focus peut changer
pendant qu'on parle), et le nombre de corrections appliquées. Comme le reste,
ils ne quittent jamais la machine.

Les réglages s'appliquent sans redémarrer : le démon surveille la date de
`config.json` et se recharge seul, qu'il soit modifié par la fenêtre ou à la
main dans un éditeur. Seul un changement de moteur ou de langue impose de
recharger le modèle, ce qui prend quelques secondes.

L'affichage passe par `pywebview`, c'est-à-dire le moteur WebView2 déjà
installé avec Windows. Même rendu qu'une application Electron, mais sans
embarquer un second environnement d'exécution : ~1 Mo de dépendance contre
~150 Mo, et un seul processus Python au lieu de deux plus un pont. Le cœur de
l'outil (capture micro, hook clavier, modèle ONNX, injection de frappes) doit
de toute façon rester en Python — Electron se serait ajouté, pas substitué.

La fenêtre tourne dans son propre processus : elle peut planter ou être fermée
sans jamais interrompre la dictée. Elle ne parle pas au démon, les deux
partagent trois fichiers (`config.json`, `history.jsonl`, `localflow.log`).

### Lancement au démarrage de Windows

```
python install_startup.py            installe le raccourci
python install_startup.py --status   vérifie
python install_startup.py --remove   désinstalle
```

Le raccourci pointe sur `pythonw app.py` : aucune fenêtre de console, l'outil
vit dans sa pastille d'état. Compter une douzaine de secondes après l'ouverture
de session pour le chargement du modèle (~2,5 Go de RAM occupés en permanence,
c'est le prix du fp32).

### Tests par brique

Chaque brique se teste séparément :

| Commande | Ce qu'elle vérifie |
|---|---|
| `python config.py` | La configuration effective, réglage par réglage. |
| `python injector.py` | Les deux modes d'injection et la restauration du presse-papiers. |
| `python recorder.py 10` | Le micro : vumètre, durée capturée, texte reconnu. |
| `python overlay.py` | La pastille d'état : défilement des quatre états. |
| `python tray.py` | L'icône de notification et son menu. |
| `python stats.py` | Les deux rapports, usage et voix, en console. |
| `python i18n.py` | La langue détectée et la symétrie des catalogues. |
| `python poc_latency.py <fichier.wav>` | La latence du moteur sur un enregistrement. |
| `python make_icon.py` | Régénère `web/icon.ico`. |

## Configuration

`config.json` est ignoré par git : il porte les réglages personnels. Il n'a
besoin de contenir que les clés qui s'écartent du défaut. `python config.py`
affiche la config effective.

| Clé | Défaut | Rôle |
|---|---|---|
| `hotkey` | `ctrl+alt+d` | Raccourci de dictée : modificateurs (`ctrl`, `alt`, `shift`, `win`) puis une touche, séparés par `+`. La touche finale est consommée par localflow : elle ne parvient pas à l'application active, donc `ctrl+win` n'ouvre pas le menu Démarrer. |
| `mode` | `hold` | `hold` : tenir la touche. `toggle` : un appui démarre, un appui arrête. |
| `backend` | `parakeet` | `parakeet` (CPU, recommandé) ou `whisper` (faster-whisper). |
| `model` | `null` | Nom du modèle ; `null` = défaut du backend. |
| `language` | `fr` | Langue forcée. |
| `replacements` | `{}` | Corrections appliquées au texte final, `{"motif": "remplacement"}`. |
| `vocabulary` | `""` | Noms propres passés en `initial_prompt` — backend `whisper` uniquement. |
| `paste_mode` | `clipboard` | `clipboard` : Ctrl+V (rapide). `type` : frappe caractère par caractère, pour les champs qui bloquent le collage. |
| `type_delay_ms` | `10` | Cadence de la frappe de secours. Les fenêtres Win32 classiques suivent à `0` ; certaines applications modernes exigent bien plus (voir les limites). |
| `min_chunk_s` | `3.0` | Durée mini d'un morceau avant d'envisager une coupe sur silence. |
| `max_chunk_s` | `12.0` | Coupe forcée, même sans silence. |
| `silence_ms` | `400` | Silence requis pour couper. |
| `max_dictation_s` | `300` | Arrêt d'office, garde-fou du micro oublié ouvert. |
| `input_device` | `null` | Périphérique d'entrée ; `null` = micro par défaut du système. |
| `overlay` | `true` | Pastille d'état en bas de l'écran : chargement, écoute avec vumètre, transcription. |

## Limites connues

- Windows uniquement (raccourci global, presse-papiers et injection sont
  spécifiques à la plateforme).
- Si l'application cible tourne en administrateur, l'injection échoue
  silencieusement : c'est une protection Windows (UIPI), pas un bug.
- Le mode `type` (frappe caractère par caractère) est un secours, pas un mode
  d'usage : le Bloc-notes de Windows 11 perd ou répète des caractères en dessous
  de ~50 ms par caractère, soit 20 caractères par seconde. Le collage n'a pas ce
  problème et reste instantané ; `type` ne sert que pour les champs qui refusent
  le collage.

## Licence

MIT.
