# -*- coding: utf-8 -*-
"""localflow : démon de dictée. Raccourci -> parole -> texte dans le champ actif.

Le modèle est chargé une fois au démarrage et reste en mémoire : une dictée ne
coûte que le temps de transcrire le dernier morceau (voir engine.py).

Découpage en threads, imposé par trois contraintes :

- les réactions au raccourci tournent dans le thread du hook clavier
  bas niveau. Y transcrire une dictée (jusqu'à une seconde) ferait geler le
  clavier de toutes les applications, et Windows désinstalle un hook trop lent.
  Ils se contentent donc de poster une commande dans une file ;
- le thread de travail exécute la séquence lente (arrêt du micro, transcription,
  injection), une dictée à la fois ;
- tkinter (la pastille d'état) exige le thread principal, qui ne fait donc que
  l'animer. Le chargement du modèle part lui aussi dans un thread, pour que la
  pastille s'affiche immédiatement au démarrage.
"""

import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
import time

import pywintypes
import win32api
import win32con
import win32event
import win32gui
import winerror

import config
import i18n
import runtime
from engine import StreamingTranscriber
from history import History
from hotkey import HotkeyError, HotkeyListener
from injector import Injector, foreground_app
from overlay import Overlay
from recorder import Recorder
from tray import Tray

LOG_PATH = os.path.join(runtime.DATA_DIR, "localflow.log")
# Préfixe Local\ : un verrou par session utilisateur, voulu — le hook clavier
# et le micro sont propres à la session, deux sessions peuvent cohabiter.
MUTEX_NAME = "Local\\localflow-single-instance"

_logger = logging.getLogger("localflow")


def setup_logging():
    """Journal fichier tournant, plus la console quand il y en a une.

    Lancé par `pythonw` (démarrage de Windows), le processus n'a pas de sortie
    standard : `sys.stdout` vaut None et un simple print planterait.
    """
    _logger.setLevel(logging.INFO)
    _logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
    handler.setFormatter(fmt)
    _logger.addHandler(handler)

    if sys.stdout is not None:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter("%(asctime)s  %(message)s",
                                               datefmt="%H:%M:%S"))
        _logger.addHandler(console)

    # Un démon qui meurt sans laisser de trace est indébogable, d'autant plus
    # sans console : les exceptions non rattrapées vont dans le journal.
    def on_exception(exc_type, exc, tb):
        _logger.error("uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = on_exception
    threading.excepthook = lambda args: _logger.error(
        f"uncaught exception in thread {args.thread.name}",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def log(message):
    _logger.info(message)


def claim_single_instance():
    """Verrou d'instance unique. Rend le mutex à garder, ou None si déjà lancé.

    Le démon est lancé au démarrage de Windows : sans verrou, un lancement
    manuel donnerait deux processus qui répondent au même raccourci et se
    battent pour le micro.
    """
    try:
        mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    except pywintypes.error:
        return None
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return None
    return mutex


class LocalFlow:
    def __init__(self, cfg, overlay=None):
        self.cfg = cfg
        self.overlay = overlay
        self.listener = None
        self.tray = None

        self.engine = None
        self.recorder = None
        self.history = History(size=cfg["history_size"], log=log)
        self.injector = Injector(
            paste_mode=cfg["paste_mode"],
            replacements=cfg["replacements"],
            type_delay_ms=cfg["type_delay_ms"],
            log=log,
        )

        self._commands = queue.Queue()
        self._lock = threading.Lock()
        self._recording = False      # une dictée est en cours de capture
        self._injecting = False      # on est en train de frapper : ignorer nos touches
        self._stopping = False
        self._armed = True           # mode bascule : touche relâchée depuis le dernier appui
        self._app = ""               # application visée, relevée au début de la dictée
        self._limit = None           # minuterie du garde-fou de durée

    # ------------------------------------------------------------- démarrage

    def load(self):
        """Charge le modèle (plusieurs secondes) et prépare la capture."""
        t0 = time.perf_counter()
        self._set_state("loading")
        engine = StreamingTranscriber(
            backend=self.cfg["backend"],
            model_name=self.cfg["model"],
            language=self.cfg["language"],
            vocabulary=self.cfg["vocabulary"],
            min_chunk_s=self.cfg["min_chunk_s"],
            max_chunk_s=self.cfg["max_chunk_s"],
            silence_ms=self.cfg["silence_ms"],
            log=lambda m: None,
        )
        self.recorder = Recorder(engine.feed,
                                 device=self.cfg["input_device"], log=log)
        # Publié en dernier : `engine` non nul est le signal « prêt à dicter »
        # lu par le thread du hook clavier.
        self.engine = engine
        self._set_state("hidden")
        log(f"{self.cfg['backend']} model ready in {time.perf_counter() - t0:.1f}s "
            f"(load {engine.load_time:.1f}s, "
            f"warm-up {engine.warmup_time:.1f}s)")

    def bind_hotkey(self):
        """Installe le raccourci dans le mode configuré."""
        self.listener = HotkeyListener(
            self.cfg["hotkey"], self._on_press, self._on_release, log=log)
        self.listener.start()
        if self.cfg["mode"] == "toggle":
            log(f"hotkey {self.cfg['hotkey']} in toggle mode: "
                f"one press starts, one press stops "
                f"(forced stop after {self.cfg['max_dictation_s']:.0f}s)")
        else:
            log(f"hotkey {self.cfg['hotkey']} in hold mode: "
                f"hold the key, speak, release")

    def start_threads(self):
        """Lance le thread de travail, le chargement du modèle et la veille
        sur la configuration."""
        threading.Thread(target=self._work, daemon=True).start()
        threading.Thread(target=self._load_quietly, daemon=True).start()
        threading.Thread(target=self._watch_config, daemon=True).start()

    # ------------------------------------------------- rechargement à chaud

    def _watch_config(self):
        """Applique les réglages dès que la fenêtre écrit `config.json`.

        Une surveillance de la date de modification plutôt qu'un tuyau entre
        les deux processus : rien à ouvrir, rien à sécuriser, et la config
        éditée à la main dans un éditeur est prise en compte de la même façon.
        """
        last = self._config_mtime()
        while not self._stopping:
            time.sleep(1.0)
            stamp = self._config_mtime()
            if stamp == last:
                continue
            try:
                new = config.load()
            except config.ConfigError as exc:
                log(f"configuration ignored (invalid): {exc}")
                last = stamp
                continue
            except OSError:
                # Lecture croisée avec l'écriture par la fenêtre : au prochain
                # tour, le fichier sera complet.
                continue
            # `last` n'avance que si la config a vraiment été prise : reportée
            # pendant une dictée, elle est retentée au tour suivant.
            if self._apply(new):
                last = stamp

    @staticmethod
    def _config_mtime():
        try:
            return os.path.getmtime(config.CONFIG_PATH)
        except OSError:
            return 0.0

    def _apply(self, new):
        """Prend en compte la nouvelle configuration, sans couper une dictée.

        Renvoie False quand une dictée est en cours : l'appelant retente."""
        with self._lock:
            if self._recording or self._injecting:
                return False
        old = self.cfg
        self.cfg = new

        changed = [k for k in new if new[k] != old.get(k)]
        if not changed:
            return True
        log(f"configuration reloaded: {', '.join(sorted(changed))}")

        if new["hotkey"] != old["hotkey"] or new["mode"] != old["mode"]:
            if self.listener is not None:
                self.listener.stop()
            try:
                self.bind_hotkey()
            except HotkeyError as exc:
                # Sans repli, un raccourci invalide laisserait le démon sourd.
                log(f"raccourci invalide ({exc}), "
                    f"retour à {old['hotkey']} en mode {old['mode']}")
                self.cfg = dict(new, hotkey=old["hotkey"], mode=old["mode"])
                try:
                    self.bind_hotkey()
                except HotkeyError as exc2:
                    log(f"could not reinstall the previous hotkey: {exc2}")

        if self.tray is not None:
            # Le menu est reconstruit à chaque clic droit : changer la langue
            # ici suffit, rien à réinstaller.
            self.tray.lang = i18n.resolve(new["ui_language"])
        if self.overlay is not None:
            self.overlay.set_lang(i18n.resolve(new["ui_language"]))
        self.history = History(size=new["history_size"], log=log)
        self.injector = Injector(
            paste_mode=new["paste_mode"], replacements=new["replacements"],
            type_delay_ms=new["type_delay_ms"], log=log)
        if self.recorder is not None:
            self.recorder.device = new["input_device"]

        # Le moteur coûte plusieurs secondes à recharger : seulement si ce qui
        # le définit a bougé.
        if any(new[k] != old[k] for k in
               ("backend", "model", "language", "vocabulary",
                "min_chunk_s", "max_chunk_s", "silence_ms")):
            self.engine = None
            threading.Thread(target=self._load_quietly, daemon=True).start()
        return True

    def _load_quietly(self):
        try:
            self.load()
        except Exception as exc:                           # noqa: BLE001
            log(f"could not load the model: {exc!r}")
            self._set_state("hidden")

    def wait(self):
        """Boucle d'attente quand la pastille est désactivée."""
        try:
            while not self._stopping:
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        self.shutdown()

    def shutdown(self):
        self._stopping = True
        self._cancel_limit()
        if self.recorder is not None and self.recorder.recording:
            self.recorder.stop()
        self._commands.put(None)
        if self.listener is not None:
            self.listener.stop()
        if self.tray is not None:
            self.tray.stop()
        if self.overlay is not None:
            self.overlay.quit()

    def _set_state(self, state):
        if self.overlay is not None:
            self.overlay.set_state(state)

    # ------------------------------------------------- zone de notification

    def start_tray(self):
        self.tray = Tray(
            on_open=self.open_window,
            on_quit=self.shutdown,
            on_restart=self.reload_config,
            on_log=lambda: os.startfile(LOG_PATH),
            tooltip=f"localflow — {self.cfg['hotkey']}",
            lang=i18n.resolve(self.cfg["ui_language"]),
            log=log)
        self.tray.start()

    def open_window(self):
        """Ouvre la fenêtre, ou la ramène au premier plan si elle est déjà là.

        Le clic simple sur l'icône déclenche aussi le premier temps d'un
        double-clic : sans cette vérification, un utilisateur pressé se
        retrouverait avec deux fenêtres.
        """
        hwnd = self._existing_window()
        if hwnd:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
            except pywintypes.error:
                # Windows refuse le passage au premier plan à un processus qui
                # n'a pas le focus : la fenêtre existe quand même, on s'arrête.
                pass
            return
        subprocess.Popen(runtime.launch_command("ui.py"), cwd=runtime.DATA_DIR)

    @staticmethod
    def _existing_window():
        """Fenêtre de l'interface, en écartant celle qui porte l'icône."""
        found = []

        def visit(hwnd, _):
            if (win32gui.IsWindowVisible(hwnd)
                    and win32gui.GetWindowText(hwnd) == "localflow"
                    and win32gui.GetClassName(hwnd) != "localflow_tray"):
                found.append(hwnd)

        try:
            win32gui.EnumWindows(visit, None)
        except pywintypes.error:
            return None
        return found[0] if found else None

    def reload_config(self):
        try:
            self._apply(config.load())
        except config.ConfigError as exc:
            log(f"invalid configuration: {exc}")

    # -------------------------------------------------- réactions aux touches
    #
    # Exécutées dans le thread du hook clavier : ne rien y faire de lent.

    def _on_press(self):
        if self.cfg["mode"] == "toggle":
            if not self._armed:
                # Une touche maintenue produit des appuis répétés : sans ce
                # désarmement, la bascule démarrerait et arrêterait en boucle.
                return
            self._armed = False
            if self._recording:
                self._request_stop("toggle")
            else:
                self._request_start()
            return
        self._request_start()

    def _on_release(self):
        self._armed = True
        if self.cfg["mode"] != "toggle":
            self._request_stop("release")

    # --------------------------------------------------------- machine à états
    #
    # Les deux demandes sont idempotentes : le relâchement de la touche et le
    # garde-fou de durée peuvent très bien arriver l'un après l'autre.

    def _request_start(self):
        if self.engine is None:
            # Dictée demandée avant la fin de la chauffe : la pastille le dit,
            # sinon l'utilisateur parlerait dans le vide sans le savoir.
            self._set_state("loading")
            log("model still loading, dictation ignored")
            return
        with self._lock:
            if self._injecting or self._recording:
                return
            self._recording = True
        self._commands.put(("start", ""))

    def _request_stop(self, reason=""):
        with self._lock:
            if not self._recording:
                return
            self._recording = False
        self._commands.put(("stop", reason))

    # ------------------------------------------------------ thread de travail

    def _work(self):
        while True:
            command = self._commands.get()
            if command is None:
                return
            action, reason = command
            try:
                if action == "start":
                    self._begin()
                elif action == "stop":
                    self._finish(reason)
            except Exception as exc:                       # noqa: BLE001
                # Le démon doit survivre à une dictée qui tourne mal.
                log(f"error during dictation: {exc!r}")
                with self._lock:
                    self._recording = False
                self._cancel_limit()
                # Sinon la pastille resterait affichée pour toujours.
                self._set_state("hidden")

    def _begin(self):
        # Relevé maintenant, pas à la fin : c'est l'application dans laquelle
        # on veut écrire, et le focus peut changer pendant qu'on parle.
        self._app = foreground_app()
        self.engine.start()
        try:
            self.recorder.start()
        except Exception:
            # Referme le cycle du moteur : son thread de travail attendrait
            # sinon indéfiniment des morceaux qui ne viendront jamais.
            self.engine.stop()
            raise
        self._set_state("listening")
        # Garde-fou : un micro laissé ouvert (raccourci resté coincé, mode
        # bascule oublié) finit par s'arrêter tout seul.
        self._limit = threading.Timer(
            self.cfg["max_dictation_s"],
            lambda: self._request_stop("maximum duration reached"))
        self._limit.daemon = True
        self._limit.start()
        log("listening…")

    def _cancel_limit(self):
        if self._limit is not None:
            self._limit.cancel()
            self._limit = None

    def _finish(self, reason=""):
        self._cancel_limit()
        self._set_state("working")
        captured = self.recorder.stop()
        t_release = time.perf_counter()
        text = self.engine.stop()
        latency = time.perf_counter() - t_release
        suffix = f" ({reason})" if reason and reason != "release" else ""
        if not text:
            self._set_state("hidden")
            log(f"{captured:.1f}s captured, nothing to write{suffix}")
            return
        # Corrections appliquées avant l'archivage : l'historique porte le
        # texte tel qu'il a été écrit, pas la sortie brute du modèle.
        text, fixes = self.injector.prepare(text)
        # Avant l'injection : si le collage part dans la mauvaise fenêtre, le
        # texte reste récupérable dans l'historique.
        self.history.add(text, seconds=captured,
                         stamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                         app=self._app, fixes=fixes)
        self._injecting = True
        try:
            # La pastille disparaît avant la frappe : elle est topmost, autant
            # qu'elle ne se superpose pas au texte qui arrive.
            self._set_state("hidden")
            written = self.injector.inject(text, prepared=True)
        finally:
            self._injecting = False
        log(f"{captured:.1f}s dictated, latency {latency:.2f}s, "
            f"{len(written.split())} words written{suffix}")


def main():
    setup_logging()
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        log(f"invalid configuration: {exc}")
        sys.exit(f"Configuration invalide : {exc}")

    # Gardé dans une variable locale de main : le verrou tient tant que le
    # processus vit, et Windows le libère à sa mort.
    lock = claim_single_instance()
    if lock is None:
        log("another localflow instance is already running, this one stops")
        return 0
    log(f"localflow starting (log: {LOG_PATH})")

    overlay = None
    if cfg["overlay"]:
        # Créée avant tout le reste : elle doit s'afficher pendant que le
        # modèle charge, et tkinter n'accepte que le thread principal.
        overlay = Overlay(lang=i18n.resolve(cfg["ui_language"]))

    app = LocalFlow(cfg, overlay=overlay)
    if overlay is not None:
        overlay.level_source = lambda: app.recorder.level if app.recorder else 0.0
    # Le raccourci est armé avant le chargement : une dictée lancée trop tôt est
    # refusée avec l'état « chargement » plutôt que silencieusement perdue.
    try:
        app.bind_hotkey()
    except HotkeyError as exc:
        log(f"invalid hotkey: {exc}")
        sys.exit(f"Raccourci invalide : {exc}")
    app.start_tray()
    app.start_threads()

    if overlay is not None:
        try:
            overlay.run()
        except KeyboardInterrupt:
            pass
        app.shutdown()
    else:
        app.wait()
    log("localflow stopping")
    del lock
    return 0


if __name__ == "__main__":
    sys.exit(main())
