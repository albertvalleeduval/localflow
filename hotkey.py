# -*- coding: utf-8 -*-
"""Raccourci global, avec un hook clavier que l'on possède et que l'on
réinstalle périodiquement.

Pourquoi ne pas s'en remettre à la lib `keyboard` : Windows retire d'office un
hook bas niveau dont la fonction de rappel ne répond pas dans le délai
`LowLevelHooksTimeout` (300 ms par défaut). Au démarrage de la session, la
machine est saturée et le chargement du modèle occupe tous les cœurs : le hook
peut être décroché sans que rien ne le signale, et la lib ne le réinstalle
jamais. Le démon reste alors muet jusqu'au prochain redémarrage — panne
observée le 2026-08-01, invisible dans le journal.

La parade tient en deux points :

- le hook nous appartient, donc on peut le réinstaller quand on veut ;
- une minuterie Windows le réinstalle toutes les `REFRESH_S` secondes. Un
  `SetWindowsHookEx` coûte quelques microsecondes : le faire pour rien est sans
  effet mesurable, et cela borne une panne éventuelle à une minute au lieu de
  l'éternité.

La fonction de rappel se contente de comparer des codes de touches et d'appeler
les callbacks fournis, qui doivent eux-mêmes être immédiats (côté app.py, elles
postent une commande dans une file).
"""

import ctypes
import ctypes.wintypes as wintypes
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_QUIT, WM_TIMER = 0x0012, 0x0113
HC_ACTION = 0

# Délai entre deux réinstallations du hook.
REFRESH_S = 45
_TIMER_ID = 1


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC,
                                     wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
# Sans type de retour déclaré, ctypes ramène un entier 32 bits : le handle
# de module est tronqué et SetWindowsHookExW échoue en « module introuvable ».
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

# Modificateurs : le code générique sert à interroger l'état de la touche,
# les codes gauche et droite servent à reconnaître l'événement lui-même.
MODIFIERS = {
    "ctrl": (0x11, (0x11, 0xA2, 0xA3)),
    "control": (0x11, (0x11, 0xA2, 0xA3)),
    "alt": (0x12, (0x12, 0xA4, 0xA5)),
    "shift": (0x10, (0x10, 0xA0, 0xA1)),
    "win": (0x5B, (0x5B, 0x5C)),
    "windows": (0x5B, (0x5B, 0x5C)),
    "cmd": (0x5B, (0x5B, 0x5C)),
}

NAMED_KEYS = {
    "space": (0x20,), "enter": (0x0D,), "return": (0x0D,), "tab": (0x09,),
    "esc": (0x1B,), "escape": (0x1B,), "backspace": (0x08,),
    "insert": (0x2D,), "delete": (0x2E,), "home": (0x24,), "end": (0x23,),
    "pageup": (0x21,), "pagedown": (0x22,),
    "up": (0x26,), "down": (0x28,), "left": (0x25,), "right": (0x27,),
    "capslock": (0x14,), "printscreen": (0x2C,), "pause": (0x13,),
}
for _i in range(1, 25):
    NAMED_KEYS[f"f{_i}"] = (0x6F + _i,)


class HotkeyError(Exception):
    pass


def resolve(name):
    """Nom de touche -> codes de touches virtuelles acceptés."""
    name = name.strip().lower()
    if name in MODIFIERS:
        return MODIFIERS[name][1]
    if name in NAMED_KEYS:
        return NAMED_KEYS[name]
    if len(name) == 1:
        # Passe par la disposition courante : sur un clavier AZERTY, « a » est
        # bien la touche marquée A, pas celle de la position QWERTY.
        code = user32.VkKeyScanW(ctypes.c_wchar(name))
        if code != -1:
            return (code & 0xFF,)
    raise HotkeyError(f"touche inconnue : {name!r}")


def parse(hotkey):
    """Décompose « ctrl+alt+d » en (codes modificateurs, codes déclencheurs).

    Lève HotkeyError si le raccourci ne se résout pas : la fenêtre de réglages
    s'en sert pour refuser un raccourci avant de l'écrire dans la config.
    """
    parts = [p.strip() for p in hotkey.lower().split("+") if p.strip()]
    if not parts:
        raise HotkeyError("raccourci vide.")
    unknown = [p for p in parts[:-1] if p not in MODIFIERS]
    if unknown:
        raise HotkeyError(
            f"« {unknown[0]} » n'est pas un modificateur "
            f"(attendus : ctrl, alt, shift, win).")
    modifiers = [MODIFIERS[p][0] for p in parts[:-1]]
    return modifiers, set(resolve(parts[-1]))


class HotkeyListener:
    """Écoute un raccourci et appelle `on_press` / `on_release`.

    `on_release` ne se déclenche qu'au relâchement de la dernière touche du
    raccourci (le « d » de ctrl+alt+d), même si les modificateurs restent
    enfoncés : c'est ce qui met fin à une dictée en mode maintien.
    """

    def __init__(self, hotkey, on_press, on_release, log=None):
        self.hotkey = hotkey
        self.on_press = on_press
        self.on_release = on_release
        self.log = log or (lambda *a: None)
        self._modifiers, self._trigger = parse(hotkey)

        self._thread = None
        self._thread_id = None
        self._hook = None
        self._proc = HOOKPROC(self._callback)   # référence gardée : sinon le
        self._down = False                      # ramasse-miettes la libère
        self._ready = threading.Event()

    # ------------------------------------------------------------ cycle de vie

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="hotkey")
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self):
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        # Attendre le décrochage effectif : sinon, pendant un changement de
        # raccourci, l'ancien et le nouveau hook vivent un instant ensemble et
        # un même appui déclencherait deux fois.
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    # ----------------------------------------------------------------- interne

    def _install(self):
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            self._install()
        except OSError as exc:
            self.log(f"could not install the keyboard hook: {exc}")
            self._ready.set()
            return
        # Minuterie de thread : les WM_TIMER arrivent dans cette boucle, donc
        # la réinstallation se fait sur le thread qui possède le hook. Avec
        # hwnd nul, Windows ignore l'identifiant demandé et en attribue un :
        # c'est la valeur de retour qu'il faudra détruire.
        timer = user32.SetTimer(None, _TIMER_ID, REFRESH_S * 1000, None)
        self._ready.set()

        msg = wintypes.MSG()
        # GetMessage bloque : le thread reste disponible pour la fonction de
        # rappel du hook, que Windows exécute pendant cette attente.
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_TIMER:
                try:
                    self._install()
                except OSError as exc:
                    self.log(f"could not reinstall the keyboard hook: {exc}")
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

        user32.KillTimer(None, timer)
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def _modifiers_down(self):
        return all(user32.GetAsyncKeyState(vk) & 0x8000
                   for vk in self._modifiers)

    def _callback(self, code, wparam, lparam):
        if code == HC_ACTION:
            vk = ctypes.cast(
                lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
            if vk in self._trigger:
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    # Une touche maintenue produit des appuis répétés : seul le
                    # premier compte.
                    if not self._down and self._modifiers_down():
                        self._down = True
                        self._safely(self.on_press)
                        return 1
                    if self._down:
                        return 1
                elif wparam in (WM_KEYUP, WM_SYSKEYUP) and self._down:
                    self._down = False
                    self._safely(self.on_release)
                    # La touche est consommée, appui comme relâchement : sans
                    # cela `ctrl+win` laisserait filer le relâchement de la
                    # touche Windows, qui ouvre le menu Démarrer, et
                    # `ctrl+alt+d` écrirait un « d » dans le champ actif.
                    return 1
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _safely(self, fn):
        # Une exception qui remonte dans la fonction de rappel traverse la
        # frontière ctypes et laisse le hook dans un état douteux.
        try:
            fn()
        except Exception as exc:                              # noqa: BLE001
            self.log(f"error in the hotkey callback: {exc!r}")
