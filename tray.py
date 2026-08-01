# -*- coding: utf-8 -*-
"""Icône de localflow dans la zone de notification.

Sans elle, un démon sans fenêtre est invisible : rien n'indique qu'il tourne,
et le seul moyen de l'arrêter est le gestionnaire des tâches.

Une icône de notification a besoin d'une fenêtre pour recevoir ses messages —
Windows n'a pas d'autre canal. On crée donc une fenêtre jamais affichée, dans
son propre thread avec sa boucle de messages, indépendante de la pastille
d'état (tkinter, thread principal) et du hook clavier.
"""

import ctypes
import os
import threading

import win32api
import win32con
import win32gui

import i18n

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "web", "icon.ico")

WM_TRAY = win32con.WM_USER + 20
ID_OPEN, ID_RESTART, ID_LOG, ID_QUIT = 1001, 1002, 1003, 1004


class Tray:
    def __init__(self, on_open, on_quit, on_restart=None, on_log=None,
                 tooltip="localflow", lang=i18n.DEFAULT, log=None):
        self.lang = lang
        self.on_open = on_open
        self.on_quit = on_quit
        self.on_restart = on_restart
        self.on_log = on_log
        self.tooltip = tooltip
        self.log = log or (lambda *a: None)
        self._hwnd = None
        self._icon = None
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="tray")
        self._thread.start()

    def stop(self):
        if self._hwnd:
            win32gui.PostMessage(self._hwnd, win32con.WM_CLOSE, 0, 0)

    def notify(self, title, message):
        """Bulle d'information, pour signaler ce qui ne se voit pas."""
        if not self._hwnd or self._icon is None:
            return
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, (
            self._hwnd, 0,
            win32gui.NIF_INFO, WM_TRAY, self._icon,
            self.tooltip, message, 200, title, win32gui.NIIF_NONE))

    # ----------------------------------------------------------------- interne

    def _run(self):
        message_map = {
            WM_TRAY: self._on_tray,
            win32con.WM_COMMAND: self._on_command,
            win32con.WM_DESTROY: self._on_destroy,
        }
        cls = win32gui.WNDCLASS()
        cls.hInstance = win32api.GetModuleHandle(None)
        cls.lpszClassName = "localflow_tray"
        cls.lpfnWndProc = message_map
        try:
            win32gui.RegisterClass(cls)
        except win32gui.error:
            pass                                   # déjà enregistrée

        self._hwnd = win32gui.CreateWindow(
            cls.lpszClassName, "localflow", win32con.WS_OVERLAPPED, 0, 0,
            0, 0, 0, 0, cls.hInstance, None)

        self._icon = self._load_icon()
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, (
            self._hwnd, 0,
            win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
            WM_TRAY, self._icon, self.tooltip))

        win32gui.PumpMessages()

    def _load_icon(self):
        if os.path.exists(ICON):
            try:
                return win32gui.LoadImage(
                    0, ICON, win32con.IMAGE_ICON, 0, 0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
            except Exception as exc:                          # noqa: BLE001
                self.log(f"icône illisible ({exc}), repli sur celle du système")
        return win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

    def _on_tray(self, hwnd, msg, wparam, lparam):
        # Clic simple : c'est ce qu'on tente d'instinct sur une icône de
        # notification. Le double-clic passe par WM_LBUTTONUP lui aussi, d'où
        # le garde-fou côté ouverture (une fenêtre déjà ouverte est ramenée au
        # premier plan au lieu d'être dupliquée).
        if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
            self._safely(self.on_open)
        elif lparam == win32con.WM_RBUTTONUP:
            self._menu()
        return True

    def _menu(self):
        menu = win32gui.CreatePopupMenu()
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_OPEN,
                            i18n.t("tray.open", self.lang))
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        if self.on_restart:
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_RESTART,
                                i18n.t("tray.reload", self.lang))
        if self.on_log:
            win32gui.AppendMenu(menu, win32con.MF_STRING, ID_LOG,
                                i18n.t("tray.log", self.lang))
        win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
        win32gui.AppendMenu(menu, win32con.MF_STRING, ID_QUIT,
                            i18n.t("tray.quit", self.lang))

        x, y = win32gui.GetCursorPos()
        # Sans premier plan, le menu ne se referme pas quand on clique ailleurs.
        win32gui.SetForegroundWindow(self._hwnd)
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN, x, y, 0,
                                self._hwnd, None)
        win32gui.PostMessage(self._hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    def _on_command(self, hwnd, msg, wparam, lparam):
        choice = win32api.LOWORD(wparam)
        if choice == ID_OPEN:
            self._safely(self.on_open)
        elif choice == ID_RESTART and self.on_restart:
            self._safely(self.on_restart)
        elif choice == ID_LOG and self.on_log:
            self._safely(self.on_log)
        elif choice == ID_QUIT:
            self._safely(self.on_quit)
        return True

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self._hwnd, 0))
        win32gui.PostQuitMessage(0)
        return True

    def _safely(self, fn):
        try:
            fn()
        except Exception as exc:                              # noqa: BLE001
            self.log(f"erreur depuis la zone de notification : {exc!r}")


if __name__ == "__main__":
    import time

    stop = threading.Event()
    tray = Tray(on_open=lambda: print("ouvrir"),
                on_quit=stop.set,
                on_restart=lambda: print("recharger"),
                on_log=lambda: print("journal"),
                log=print)
    tray.start()
    print("Icône posée. Clic droit dessus pour le menu, « Quitter » pour finir.")
    while not stop.wait(0.2):
        pass
    tray.stop()
    time.sleep(0.3)
