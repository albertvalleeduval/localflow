# -*- coding: utf-8 -*-
"""Injection du texte reconnu dans le champ actif de l'application au premier plan.

Deux voies :

- `clipboard` (défaut) : on place le texte dans le presse-papiers, on simule
  Ctrl+V, puis on restaure l'ancien contenu. Instantané même sur un paragraphe
  entier, et marche dans la quasi-totalité des applications.
- `type` : frappe caractère par caractère, pour les rares champs qui ignorent
  le collage. Lent par construction, d'où le statut de secours.

Deux pièges Windows traités ici :

- Au moment où on relâche le raccourci de dictée, les modificateurs (Ctrl, Alt)
  sont souvent encore physiquement enfoncés. Un Ctrl+V envoyé à ce moment
  devient Ctrl+Alt+V et ne colle rien — d'où l'attente puis la libération
  défensive des modificateurs avant toute frappe simulée.
- Restaurer le presse-papiers trop tôt casse le collage : l'application lit le
  presse-papiers de façon asynchrone après le Ctrl+V.

Tout passe par SendInput en ctypes plutôt que par la lib `keyboard` : celle-ci
traduit chaque caractère en scancode selon la disposition clavier active et
produit du charabia sur AZERTY (mesuré). SendInput avec KEYEVENTF_UNICODE
envoie le point de code directement.
"""

import ctypes
import os
import re
import time
from ctypes import wintypes

import pywintypes
import win32clipboard
import win32con

# Attente après le Ctrl+V avant de restaurer le presse-papiers.
RESTORE_DELAY_S = 0.15
# Attente maximale du relâchement des modificateurs par l'utilisateur.
MODIFIER_TIMEOUT_S = 0.5

# Paires (gauche, droite) des modificateurs à neutraliser avant de frapper.
_MODIFIER_KEYS = (0xA0, 0xA1,   # VK_LSHIFT, VK_RSHIFT
                  0xA2, 0xA3,   # VK_LCONTROL, VK_RCONTROL
                  0xA4, 0xA5,   # VK_LMENU, VK_RMENU (Alt / AltGr)
                  0x5B, 0x5C)   # VK_LWIN, VK_RWIN


class InjectionError(Exception):
    pass


# ------------------------------------------------------- entrées clavier Win32

_ULONG_PTR = wintypes.WPARAM
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_INPUT_KEYBOARD = 1
_VK_RETURN = 0x0D
_VK_CONTROL = 0x11
_VK_V = 0x56


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", _ULONG_PTR)]


class _INPUT(ctypes.Structure):
    class _UNION(ctypes.Union):
        # Le remplissage porte l'union à la taille de MOUSEINPUT, la plus
        # grande des trois : SendInput vérifie cbSize et refuse tout sinon.
        _fields_ = [("ki", _KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


def _send(events):
    """Envoie une série d'événements (vk, scancode, flags) en un seul appel.

    Le scancode des touches nommées est rempli automatiquement : certaines
    applications (et les hooks bas niveau, dont celui de la lib `keyboard`)
    identifient les touches par scancode et ignorent un événement qui n'en a pas.
    """
    buf = (_INPUT * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        if vk and not scan:
            scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
        buf[i].type = _INPUT_KEYBOARD
        buf[i].ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags,
                                time=0, dwExtraInfo=0)
    sent = ctypes.windll.user32.SendInput(
        len(events), ctypes.byref(buf), ctypes.sizeof(_INPUT))
    if sent != len(events):
        raise InjectionError(
            f"SendInput refusé (erreur {ctypes.GetLastError()}) — "
            "la fenêtre cible tourne-t-elle en administrateur ?")


def _is_down(vk):
    # Bit de poids fort : touche actuellement enfoncée.
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _char_events(char):
    """Événements appui/relâchement pour un caractère."""
    if char in "\r\n":
        # Un saut de ligne envoyé en Unicode n'est pas interprété comme Entrée
        # par la plupart des applications : il faut la vraie touche.
        return [(_VK_RETURN, 0, 0), (_VK_RETURN, 0, _KEYEVENTF_KEYUP)]
    events = []
    # Un caractère hors du plan de base (emoji) tient sur deux unités UTF-16 :
    # elles s'envoient l'une après l'autre.
    encoded = char.encode("utf-16-le")
    for i in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[i:i + 2], "little")
        events.append((0, unit, _KEYEVENTF_UNICODE))
        events.append((0, unit, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP))
    return events


def type_text(text, delay_s=0.01):
    """Frappe le texte caractère par caractère, disposition clavier ignorée.

    `delay_s` est le prix à payer pour les applications lentes : les fenêtres
    Win32 classiques suivent sans aucun délai, mais certaines applications
    modernes (le Bloc-notes de Windows 11, par exemple) perdent ou répètent des
    caractères en dessous de ~50 ms. D'où le réglage `type_delay_ms`.
    """
    for char in text:
        _send(_char_events(char))
        if delay_s:
            time.sleep(delay_s)


def clear_modifiers(timeout=MODIFIER_TIMEOUT_S):
    """Attend le relâchement des modificateurs, puis force ce qui reste.

    L'attente couvre le cas normal (l'utilisateur lâche le raccourci en entier)
    et le relâchement forcé couvre le mode bascule, où il peut très bien garder
    Ctrl enfoncé pour autre chose.
    """
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not any(_is_down(vk) for vk in _MODIFIER_KEYS):
            return
        time.sleep(0.01)
    stuck = [(vk, 0, _KEYEVENTF_KEYUP) for vk in _MODIFIER_KEYS if _is_down(vk)]
    if stuck:
        _send(stuck)
        time.sleep(0.02)


def send_paste():
    _send([(_VK_CONTROL, 0, 0),
           (_VK_V, 0, 0),
           (_VK_V, 0, _KEYEVENTF_KEYUP),
           (_VK_CONTROL, 0, _KEYEVENTF_KEYUP)])


# ------------------------------------------------------------- presse-papiers

def _open_clipboard(attempts=20, delay=0.02):
    """Le presse-papiers est une ressource globale verrouillable : on réessaie."""
    for _ in range(attempts):
        try:
            win32clipboard.OpenClipboard()
            return
        except pywintypes.error:
            time.sleep(delay)
    raise InjectionError(
        "presse-papiers inaccessible (verrouillé par une autre application)")


def get_clipboard_text():
    """Contenu texte du presse-papiers, ou None s'il n'y a pas de texte."""
    _open_clipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return None
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_text(text):
    _open_clipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


# --------------------------------------------------------------- post-traitement

def replace_and_count(text, replacements):
    """Applique les corrections et rend (texte, nombre de substitutions).

    Sert de substitut à l'`initial_prompt` de Whisper, que Parakeet n'a pas :
    les noms propres récurrents mal reconnus sont corrigés après coup. Le
    décompte alimente les statistiques (`stats.py`).
    """
    fixes = 0
    for pattern, target in (replacements or {}).items():
        try:
            text, done = re.subn(pattern, target, text, flags=re.IGNORECASE)
        except re.error:
            # Motif ou remplacement invalide (un « \1 » orphelin lève aussi
            # re.error) : substitution littérale plutôt que perdre la dictée.
            # Le lambda soustrait la cible à l'analyse des échappements.
            text, done = re.subn(re.escape(pattern), lambda _m: target, text,
                                 flags=re.IGNORECASE)
        fixes += done
    return text, fixes


def apply_replacements(text, replacements):
    """Corrections de noms propres, sans le décompte."""
    return replace_and_count(text, replacements)[0]


def foreground_app():
    """Nom de l'exécutable de la fenêtre active, ou "" si indéterminable.

    Relevé au *début* de la dictée : c'est l'application dans laquelle on a
    l'intention d'écrire, et le focus peut très bien changer pendant qu'on
    parle. Sert aux statistiques d'usage, et ne quitte jamais la machine.
    """
    import win32gui
    import win32process

    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not pid:
            return ""
        # PROCESS_QUERY_LIMITED_INFORMATION : le seul droit accordé sans
        # élévation sur les processus d'une autre intégrité.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            size = ctypes.c_ulong(len(buf))
            if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buf, ctypes.byref(size)):
                return ""
            return os.path.basename(buf.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:                                         # noqa: BLE001
        return ""                       # une statistique ne casse pas une dictée


class Injector:
    def __init__(self, paste_mode="clipboard", replacements=None,
                 type_delay_ms=10, log=None):
        self.paste_mode = paste_mode
        self.replacements = replacements or {}
        self.type_delay_s = max(0.0, type_delay_ms / 1000.0)
        self.log = log or (lambda *a: None)

    def prepare(self, text):
        """Texte final, corrections comprises, et nombre de corrections.

        Séparé de l'écriture : le démon range ce texte dans l'historique avant
        de tenter de l'injecter, pour que l'historique porte la version
        corrigée et non la sortie brute du modèle.
        """
        return replace_and_count((text or "").strip(), self.replacements)

    def inject(self, text, prepared=False):
        """Écrit le texte dans le champ actif. Renvoie le texte réellement injecté."""
        if not prepared:
            text = apply_replacements((text or "").strip(), self.replacements)
        if not text:
            return ""
        clear_modifiers()
        if self.paste_mode == "type":
            type_text(text, self.type_delay_s)
        else:
            self._paste(text)
        return text

    def _paste(self, text):
        try:
            saved = get_clipboard_text()
        except (InjectionError, pywintypes.error) as exc:
            self.log(f"presse-papiers illisible ({exc}), repli sur la frappe")
            return type_text(text, self.type_delay_s)

        set_clipboard_text(text)
        send_paste()
        time.sleep(RESTORE_DELAY_S)
        if saved is not None:
            try:
                set_clipboard_text(saved)
            except InjectionError as exc:
                self.log(f"restauration du presse-papiers impossible : {exc}")


# ----------------------------------------------------------------- test isolé

def _find_window(title_part):
    """hwnd de la première fenêtre visible dont le titre contient title_part."""
    import win32gui

    found = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and title_part in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(visit, None)
    return found[0] if found else None


def _force_foreground(hwnd):
    """Tente de mettre une fenêtre au premier plan malgré le verrou de Windows.

    Un processus d'arrière-plan n'a pas le droit de voler le focus. Le
    contournement classique : s'attacher à la file d'entrée du thread qui a le
    focus, ce qui nous fait hériter de son droit.
    """
    import win32api
    import win32gui
    import win32process

    fg = win32gui.GetForegroundWindow()
    fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    our_tid = win32api.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != our_tid:
        try:
            win32process.AttachThreadInput(our_tid, fg_tid, True)
            attached = True
        except pywintypes.error:
            pass
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
    except pywintypes.error:
        pass
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(our_tid, fg_tid, False)
            except pywintypes.error:
                pass


def _await_foreground(title_part, timeout=20):
    """Attend qu'une fenêtre donnée soit au premier plan, en essayant de l'y mettre.

    Rien n'est injecté avant confirmation : une frappe simulée dans la mauvaise
    fenêtre est au mieux inutile, au pire destructrice.
    """
    import win32gui

    deadline = time.perf_counter() + timeout
    asked = False
    while time.perf_counter() < deadline:
        hwnd = _find_window(title_part)
        if hwnd:
            if win32gui.GetForegroundWindow() == hwnd:
                return hwnd
            _force_foreground(hwnd)
            if not asked:
                print("  (si la fenêtre ne passe pas devant d'elle-même, "
                      "cliquez dessus)")
                asked = True
        time.sleep(0.2)
    return None


PHRASE = "Test d'injection localflow : accents éàçù, ponctuation, majuscules."
SENTINEL = "presse-papiers à restaurer"


def _test_own_window():
    """Injecte dans une fenêtre tkinter à nous et relit le résultat.

    Cible Win32 classique, focus garanti, vérification directe : c'est le test
    de référence des deux modes d'injection.
    """
    import threading
    import tkinter as tk

    root = tk.Tk()
    root.title("localflow — test d'injection")
    root.geometry("760x220+180+180")
    widget = tk.Text(root, font=("Consolas", 10))
    widget.pack(fill="both", expand=True)
    widget.focus_force()
    root.lift()
    root.attributes("-topmost", True)

    got = {}

    def run():
        time.sleep(0.8)
        set_clipboard_text(SENTINEL)
        for mode in ("clipboard", "type"):
            widget.delete("1.0", "end")
            time.sleep(0.2)
            Injector(paste_mode=mode).inject(PHRASE)
            time.sleep(0.5)
            got[mode] = widget.get("1.0", "end-1c")
        got["clipboard_restored"] = get_clipboard_text()
        root.after(0, root.destroy)

    threading.Thread(target=run, daemon=True).start()
    root.mainloop()

    results = []
    for mode in ("clipboard", "type"):
        results.append((got.get(mode) == PHRASE,
                        f"fenêtre à nous, mode {mode} : texte identique",
                        f"fenêtre à nous, mode {mode} : reçu {got.get(mode)!r}"))
    restored = got.get("clipboard_restored")
    results.append((restored == SENTINEL,
                    "presse-papiers restauré après le collage",
                    f"presse-papiers non restauré : {restored!r}"))
    return results


def _test_notepad():
    """Vérifie le collage dans une application tierce réelle (le Bloc-notes).

    Seul le mode `clipboard` est testé : le Bloc-notes de Windows 11 perd des
    caractères sur une frappe simulée rapide, ce qui teste sa lenteur et non
    notre code (voir `type_text`).
    """
    import os
    import subprocess
    import tempfile

    # Le test se termine par un taskkill /f sur notepad.exe : on refuse de
    # tourner si une fenêtre du Bloc-notes est déjà ouverte, pour ne pas
    # détruire un travail non enregistré.
    listing = subprocess.run(["tasklist", "/fi", "imagename eq notepad.exe"],
                             capture_output=True, text=True).stdout
    if "notepad.exe" in listing.lower():
        return [(False, "", "Bloc-notes déjà ouvert : fermez-le et relancez "
                           "(le test se termine par un taskkill)")]

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="localflow_test_")
    os.close(fd)
    base = os.path.splitext(os.path.basename(path))[0]
    subprocess.Popen(["notepad.exe", path])
    print(f"  Bloc-notes ouvert sur {os.path.basename(path)}, attente du focus…")
    target = _await_foreground(base)

    written = ""
    aborted = None
    if not target:
        aborted = "le Bloc-notes n'a pas pris le focus (rien n'a été injecté)"
    else:
        import win32gui

        time.sleep(0.3)
        set_clipboard_text(SENTINEL)
        Injector(paste_mode="clipboard").inject(PHRASE)
        time.sleep(0.4)
        if win32gui.GetForegroundWindow() != target:
            aborted = "le focus a changé pendant le test"
        else:
            _send([(_VK_CONTROL, 0, 0), (ord("A"), 0, 0),
                   (ord("A"), 0, _KEYEVENTF_KEYUP),
                   (_VK_CONTROL, 0, _KEYEVENTF_KEYUP)])
            time.sleep(0.2)
            _send([(_VK_CONTROL, 0, 0), (ord("C"), 0, 0),
                   (ord("C"), 0, _KEYEVENTF_KEYUP),
                   (_VK_CONTROL, 0, _KEYEVENTF_KEYUP)])
            time.sleep(0.4)
            written = get_clipboard_text() or ""

    # taskkill plutôt qu'Alt+F4 : pas de boîte de dialogue « Enregistrer ? ».
    subprocess.run(["taskkill", "/f", "/im", "notepad.exe"], capture_output=True)
    time.sleep(0.5)
    try:
        os.remove(path)
    except OSError:
        pass

    if aborted:
        return [(False, "", f"Bloc-notes : {aborted}")]
    return [(PHRASE in written,
             "Bloc-notes (application tierce), mode clipboard : texte collé",
             f"Bloc-notes : collage introuvable, contenu relu {written!r}")]


def _selftest():
    print("Test d'injection")
    results = _test_own_window() + _test_notepad()
    for ok, good, bad in results:
        print(f"  {'OK    ' if ok else 'ECHEC '}{good if ok else bad}")
    all_ok = all(ok for ok, _, _ in results)
    print("Injection validée." if all_ok else "Injection en échec.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
