# -*- coding: utf-8 -*-
"""Fenêtre de localflow : historique, réglages, état.

    python ui.py

Processus séparé du démon, à dessein : la fenêtre peut planter, être fermée ou
relancée sans jamais interrompre la dictée. Les deux ne se parlent pas
directement, ils partagent trois fichiers — `config.json`, `history.jsonl` et
`localflow.log`. Le démon surveille la date de `config.json` et se recharge
tout seul quand la fenêtre l'écrit : pas de tuyau à maintenir entre eux.

L'affichage passe par `pywebview`, qui rend du HTML dans le WebView2 déjà
installé avec Windows. Même moteur de rendu qu'Electron, sans embarquer un
second environnement d'exécution : le processus reste un simple processus
Python (arbitrage détaillé dans le README).
"""

import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

import webview

import config
import hotkey
import i18n
import install_startup
import runtime
from history import History
from injector import set_clipboard_text
import recorder
import stats

HERE = runtime.ASSETS_DIR
WEB = os.path.join(runtime.ASSETS_DIR, "web")
LOG_PATH = os.path.join(runtime.DATA_DIR, "localflow.log")

# Réglages exposés dans l'onglet Réglages. Le reste de config.py garde ses
# valeurs par défaut et s'édite à la main : ce sont des boutons de réglage fin
# (découpage des morceaux, cadence de frappe) qui n'ont pas leur place ici.
ADVANCED = ("min_chunk_s", "max_chunk_s", "silence_ms", "type_delay_ms")


def daemon_processes():
    """Démons localflow en cours : PID, mémoire et heure de démarrage.

    Passe par WMI plutôt que par un canal avec le démon : la fenêtre observe
    l'état depuis l'extérieur, sans rien lui demander, et reste donc juste
    même si le démon est bloqué.
    """
    import win32com.client

    # Dans les deux cas, le démon est reconnu par son chemin, pas par son
    # seul nom : un localflow.exe d'une autre installation, ou un clone du
    # dépôt dans un autre dossier, n'est pas notre démon — et le bouton
    # « Redémarrer » ne doit surtout pas le tuer.
    if runtime.FROZEN:
        where = "Name = 'localflow.exe'"
        ours = os.path.normcase(os.path.join(runtime.DATA_DIR, "localflow.exe"))
    else:
        where = "Name LIKE '%python%'"
        ours = os.path.normcase(HERE)
    wmi = win32com.client.GetObject("winmgmts:")
    found = []
    for proc in wmi.ExecQuery(
            "SELECT ProcessId, CommandLine, ExecutablePath, WorkingSetSize, "
            f"CreationDate FROM Win32_Process WHERE {where}"):
        if runtime.FROZEN:
            keep = os.path.normcase(str(proc.ExecutablePath or "")) == ours
        else:
            line = os.path.normcase(proc.CommandLine or "")
            keep = "app.py" in line and ours in line
        if keep:
            found.append({
                "pid": proc.ProcessId,
                "memory_mb": round(int(proc.WorkingSetSize or 0) / (1024 ** 2)),
                "started": _wmi_time(proc.CreationDate),
            })
    return found


def _wmi_time(stamp):
    """« 20260801230325.123456+120 » -> datetime, ou None."""
    try:
        return datetime.strptime(str(stamp)[:14], "%Y%m%d%H%M%S")
    except (ValueError, TypeError):
        return None


def daemon_pids():
    return [p["pid"] for p in daemon_processes()]


class Api:
    """Méthodes appelables depuis le JavaScript de la page."""

    def __init__(self):
        self._history = History(size=10 ** 9)   # lecture seule ici : ne rogne pas

    # ------------------------------------------------------------------ état

    def _locale(self, lang):
        """Catalogue de traduction de la page.

        Servi par le pont plutôt que chargé en `fetch` : la page vient d'un
        fichier local, et un `fetch` depuis l'origine `file://` est refusé par
        le moteur de rendu.
        """
        for candidate in (lang, i18n.DEFAULT):
            path = os.path.join(WEB, "locales", f"{candidate}.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return {}

    def get_state(self):
        try:
            cfg = config.load()
            error = ""
        except config.ConfigError as exc:
            cfg, error = dict(config.DEFAULTS), str(exc)
        lang = i18n.resolve(cfg["ui_language"])
        return {
            "lang": lang,
            "strings": self._locale(lang),
            "config": cfg,
            "defaults": config.DEFAULTS,
            "advanced": list(ADVANCED),
            # La page masque ce qui n'existe pas dans la distribution en
            # exécutable (le backend whisper, non embarqué).
            "frozen": runtime.FROZEN,
            "configError": error,
            "devices": self._devices(),
            "startup": os.path.exists(install_startup.shortcut_path()),
            "running": bool(daemon_pids()),
            "stats": self._stats(),
            "log": self._log_tail(),
        }

    def _devices(self):
        # `index: null` = périphérique par défaut ; la page lui donne son nom
        # traduit, ce module ne renvoie que les noms venus du système.
        out = [{"index": None, "name": ""}]
        try:
            for i, dev in enumerate(recorder.list_devices()):
                if dev.get("max_input_channels", 0) > 0:
                    out.append({"index": i, "name": dev["name"]})
        except Exception as exc:                              # noqa: BLE001
            out.append({"index": None, "name": f"({exc})"})
        return out

    def _stats(self):
        entries = self._history.entries()
        words = sum(len(e.get("text", "").split()) for e in entries)
        seconds = sum(e.get("seconds", 0) for e in entries)
        return {
            "count": len(entries),
            "words": words,
            "seconds": round(seconds),
            "last": entries[0]["time"] if entries else "",
        }

    # ------------------------------------------------------------------ état

    def get_status(self):
        """Ce qu'il faut savoir quand on se demande « est-ce que ça marche ? ».

        Tout est déduit de l'extérieur — table des processus, journal,
        configuration, historique — donc rien à demander au démon, et une
        réponse juste même s'il ne répond plus.
        """
        try:
            cfg = config.load()
        except config.ConfigError:
            cfg = dict(config.DEFAULTS)

        processes = daemon_processes()
        daemon = processes[0] if processes else None
        started = daemon["started"] if daemon else None
        model = self._model_state()

        return {
            "running": bool(daemon),
            "pid": daemon["pid"] if daemon else None,
            "memoryMb": daemon["memory_mb"] if daemon else 0,
            "uptime": self._uptime(started),
            "startedAt": started.strftime("%H:%M") if started else "",
            "duplicates": max(0, len(processes) - 1),
            "model": model,
            "microphone": self._microphone(cfg["input_device"]),
            "hotkey": cfg["hotkey"],
            "mode": cfg["mode"],
            "last": self._last_dictation(),
            "log": self._log_tail(120),
            "logPath": LOG_PATH,
        }

    @staticmethod
    def _uptime(started):
        if not started:
            return ""
        seconds = max(0, int((datetime.now() - started).total_seconds()))
        hours, minutes = divmod(seconds // 60, 60)
        return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"

    def _model_state(self):
        """Lu dans le journal : le démon y annonce son modèle prêt.

        On ne regarde que depuis le dernier démarrage — un « prêt » d'une
        session précédente ne dit rien de celle en cours.
        """
        lines = self._log_lines()
        start = 0
        for i, line in enumerate(lines):
            if "localflow starting" in line or "localflow démarre" in line:
                start = i
        ready = re.compile(r"(\w[\w.-]*) (?:model ready|modèle .* prêt) "
                           r"(?:in|en) ([\d.]+)s")
        failed = re.compile(r"could not load the model|chargement du modèle")
        for line in reversed(lines[start:]):
            match = ready.search(line)
            if match:
                return {"state": "ready", "name": match.group(1),
                        "seconds": float(match.group(2))}
            if failed.search(line):
                return {"state": "failed", "name": "", "seconds": 0}
        return {"state": "loading" if daemon_pids() else "off",
                "name": "", "seconds": 0}

    @staticmethod
    def _microphone(index):
        """Nom du micro réellement utilisé, périphérique par défaut compris."""
        try:
            import sounddevice as sd
            info = sd.query_devices(index, "input") if index is not None \
                else sd.query_devices(kind="input")
            return info["name"]
        except Exception:                                     # noqa: BLE001
            return ""

    def _last_dictation(self):
        entries = self._history.entries(limit=1)
        if not entries:
            return None
        entry = entries[0]
        latency = None
        # La latence n'est pas dans l'historique : elle ne concerne que le
        # diagnostic, elle vit dans le journal.
        # [ye] couvre l'anglais du journal courant et le français des
        # journaux antérieurs.
        pattern = re.compile(r"latenc[ye] ([\d.]+)s")
        for line in reversed(self._log_lines()):
            match = pattern.search(line)
            if match:
                latency = float(match.group(1))
                break
        return {
            "time": entry.get("time", ""),
            "words": len(entry.get("text", "").split()),
            "seconds": entry.get("seconds", 0),
            "latency": latency,
        }

    def open_log(self):
        os.startfile(LOG_PATH)
        return {"ok": True}

    # ---------------------------------------------------------- statistiques

    def get_usage(self):
        return stats.usage(self._history.entries())

    def get_voice(self):
        """Le portrait suit deux langues : celle dictée pour l'analyse des
        mots, celle de l'interface pour les phrases rendues."""
        try:
            cfg = config.load()
        except config.ConfigError:
            cfg = dict(config.DEFAULTS)
        return stats.voice(self._history.entries(),
                           language=cfg["language"],
                           ui_language=i18n.resolve(cfg["ui_language"]))

    @staticmethod
    def _log_lines():
        if not os.path.exists(LOG_PATH):
            return []
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()

    def _log_tail(self, lines=60):
        return "".join(self._log_lines()[-lines:])

    # ------------------------------------------------------------ historique

    def get_history(self, query="", limit=300):
        needle = (query or "").lower()
        out = []
        # `index` est la position dans la liste complète, pas dans la liste
        # filtrée : c'est elle que `delete_entry` attend.
        for index, entry in enumerate(self._history.entries()):
            text = entry.get("text", "")
            if needle and needle not in text.lower():
                continue
            out.append({
                "index": index,
                "time": entry.get("time", ""),
                "seconds": entry.get("seconds", 0),
                "text": text,
                "words": len(text.split()),
            })
            if len(out) >= limit:
                break
        return out

    def copy_text(self, text):
        try:
            set_clipboard_text(text)
            return {"ok": True}
        except Exception as exc:                              # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def delete_entry(self, index, text=None):
        # Le texte accompagne l'index : si une dictée est arrivée entre
        # l'affichage et le clic, l'index seul viserait la mauvaise ligne.
        return {"ok": self._history.delete(int(index), expected_text=text)}

    def clear_history(self):
        self._history.clear()
        return {"ok": True}

    # -------------------------------------------------------------- réglages

    def save_config(self, incoming):
        """Écrit config.json avec les seules clés qui s'écartent du défaut."""
        # Le JavaScript peut envoyer n'importe quoi : les clés inconnues sont
        # écartées plutôt que d'écrire une config que load() refusera.
        merged = dict(config.DEFAULTS)
        merged.update({k: v for k, v in (incoming or {}).items()
                       if k in config.DEFAULTS})
        try:
            config._validate(merged)
        except config.ConfigError as exc:
            return {"ok": False, "error": str(exc)}
        try:
            # La capture côté page peut produire une touche que le démon ne
            # résout pas (AltGr, touche morte) : refusée ici, avant écriture.
            hotkey.parse(merged["hotkey"])
        except hotkey.HotkeyError as exc:
            return {"ok": False, "error": f"raccourci : {exc}"}
        # faster-whisper n'est pas embarqué dans le build PyInstaller : écrit
        # tel quel, ce réglage laisserait le démon sans moteur, avec pour seul
        # indice une ligne de journal. Refusé avant écriture.
        if runtime.FROZEN and merged["backend"] == "whisper":
            return {"ok": False, "error":
                    "The whisper backend is not bundled in the executable "
                    "build. Install localflow from source to use it."}

        trimmed = {k: v for k, v in merged.items() if v != config.DEFAULTS[k]}
        try:
            with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(trimmed, f, ensure_ascii=False, indent=2)
                f.write("\n")
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        # Le démon voit la date du fichier changer et se recharge seul.
        return {"ok": True}

    def set_startup(self, enabled):
        try:
            install_startup.install() if enabled else install_startup.remove()
            return {"ok": True}
        except Exception as exc:                              # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # --------------------------------------------------------------- démon

    def restart_daemon(self):
        try:
            for pid in daemon_pids():
                subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                               capture_output=True, check=False)
            # taskkill rend la main avant la mort effective du processus : le
            # nouveau démon perdrait sinon le verrou d'instance unique face au
            # mourant, et s'arrêterait aussitôt.
            deadline = time.monotonic() + 5.0
            while daemon_pids() and time.monotonic() < deadline:
                time.sleep(0.2)
            subprocess.Popen(runtime.launch_command("app.py"),
                             cwd=runtime.DATA_DIR)
            return {"ok": True}
        except Exception as exc:                              # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def open_folder(self):
        # Le dossier des fichiers de l'utilisateur : config, historique,
        # journal. Depuis les sources c'est aussi celui du code.
        os.startfile(runtime.DATA_DIR)
        return {"ok": True}


def set_window_icon():
    """Pose l'icône de localflow sur la fenêtre, une fois qu'elle existe.

    `pywebview` n'expose pas d'option pour ça : la fenêtre naît donc avec
    l'icône de l'interpréteur Python, visible dans sa barre de titre et dans
    la barre des tâches. On la remplace à la main par un message Windows.

    Le préfixe d'identité d'application est posé avant : sans lui, Windows
    range la fenêtre sous l'icône de Python dans la barre des tâches, quelle
    que soit celle de la fenêtre.
    """
    import win32con
    import win32gui

    icon = os.path.join(WEB, "icon.ico")
    if not os.path.exists(icon):
        return
    deadline = time.time() + 10
    while time.time() < deadline:
        hwnd = next((h for h in _windows_titled("localflow")), None)
        if hwnd:
            for size, which in ((16, win32con.ICON_SMALL),
                                (32, win32con.ICON_BIG)):
                handle = win32gui.LoadImage(
                    0, icon, win32con.IMAGE_ICON, size, size,
                    win32con.LR_LOADFROMFILE)
                win32gui.SendMessage(hwnd, win32con.WM_SETICON, which, handle)
            return
        time.sleep(0.2)


def _windows_titled(title):
    """Fenêtres visibles portant ce titre, hors fenêtre-relais de l'icône."""
    import win32gui

    found = []

    def visit(hwnd, _):
        if (win32gui.IsWindowVisible(hwnd)
                and win32gui.GetWindowText(hwnd) == title
                and win32gui.GetClassName(hwnd) != "localflow_tray"):
            found.append(hwnd)

    win32gui.EnumWindows(visit, None)
    return found


def window_size():
    """Taille qui tient sur l'écran, mise à l'échelle Windows comprise.

    En 200 % — courant sur un portable récent — une fenêtre demandée à 1040
    déborde largement : on part de l'écran plutôt que d'une valeur en dur.
    """
    try:
        screen = webview.screens[0]
        width = min(1040, int(screen.width * 0.78))
        height = min(720, int(screen.height * 0.84))
        return max(760, width), max(540, height)
    except Exception:                                         # noqa: BLE001
        return 960, 640


def main():
    # Identité d'application propre : sans elle, Windows regroupe la fenêtre
    # sous l'icône de python.exe dans la barre des tâches.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "localflow.window")
    except Exception:                                         # noqa: BLE001
        pass

    width, height = window_size()
    webview.create_window(
        "localflow",
        os.path.join(WEB, "index.html"),
        js_api=Api(),
        width=width,
        height=height,
        min_size=(760, 520),
        background_color="#141418",
    )
    # La fenêtre n'existe qu'une fois la boucle lancée : l'icône se pose donc
    # depuis un fil d'attente, pas avant.
    threading.Thread(target=set_window_icon, daemon=True).start()
    webview.start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
