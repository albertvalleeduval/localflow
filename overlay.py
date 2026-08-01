# -*- coding: utf-8 -*-
"""Pastille d'état : sans elle, rien ne dit si le démon écoute.

Petite pilule sans bordure, toujours au premier plan, en bas de l'écran :

- `loading` : le modèle se charge, une dictée lancée maintenant serait perdue ;
- `listening` : écoute en cours, avec le niveau du micro ;
- `working` : le dernier morceau se transcrit ;
- `hidden` : rien en cours.

Trois contraintes structurelles :

- tkinter doit vivre dans le thread principal, alors que le raccourci et le
  moteur tournent dans des threads. Les changements d'état passent donc par une
  file, relue par la boucle tkinter elle-même.
- la fenêtre ne doit jamais prendre le focus, sous peine de recevoir l'injection
  à la place de l'application visée : d'où WS_EX_NOACTIVATE. WS_EX_TRANSPARENT
  laisse en plus passer les clics de souris à travers.
- il faut déclarer le processus conscient du DPI avant de créer la fenêtre :
  sinon Windows étire son contenu (rendu flou sur un écran à 200 %). Tout est
  donc dessiné en pixels physiques, à l'échelle près.
"""

import ctypes
import math
import queue
import time
import tkinter as tk

import i18n

# Couleur rendue totalement transparente par Windows : elle découpe les coins
# arrondis de la pilule dans une fenêtre par ailleurs rectangulaire.
TRANSPARENT = "#ff00ff"
BG = "#1b1b1e"
FG = "#e8e8ea"
IDLE_BAR = "#3a3a40"
COLORS = {
    "loading": "#ffb340",
    "listening": "#ff4b4b",
    "working": "#4b9bff",
}

# Géométrie en unités de dessin, multipliée par l'échelle DPI.
WIDTH, HEIGHT = 184, 44
MARGIN_BOTTOM = 96          # au-dessus de la barre des tâches
DOT_X, DOT_R = 26, 7
CONTENT_X = 46
BARS = 12
TICK_MS = 40

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


def _declare_dpi_aware():
    """À appeler avant toute fenêtre. Sans effet sur les versions trop anciennes."""
    try:
        # -4 = DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(-4):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _dpi_scale():
    try:
        return ctypes.windll.user32.GetDpiForSystem() / 96.0
    except (AttributeError, OSError):
        return 1.0


class Overlay:
    def __init__(self, level_source=None, lang=i18n.DEFAULT):
        """`level_source` : fonction rendant le niveau micro (0..1), pour le vumètre."""
        self.level_source = level_source or (lambda: 0.0)
        self.set_lang(lang)
        self._events = queue.Queue()
        self._state = "hidden"
        self._visible = False
        self._t0 = time.perf_counter()
        self._quit = False

        _declare_dpi_aware()
        self.scale = _dpi_scale()
        self.w = self._px(WIDTH)
        self.h = self._px(HEIGHT)

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.attributes("-transparentcolor", TRANSPARENT)
        self.root.configure(bg=TRANSPARENT)
        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h,
                                bg=TRANSPARENT, highlightthickness=0)
        self.canvas.pack()
        self.font = ("Segoe UI", -self._px(12))    # taille négative = pixels
        self._place()
        self.root.update_idletasks()
        self._make_unfocusable()

    def _px(self, value):
        return max(1, int(round(value * self.scale)))

    # ----------------------------------------------------------------- fenêtre

    def _place(self):
        x = (self.root.winfo_screenwidth() - self.w) // 2
        y = self.root.winfo_screenheight() - self.h - self._px(MARGIN_BOTTOM)
        self.root.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _make_unfocusable(self):
        user32 = ctypes.windll.user32
        hwnd = self.root.winfo_id()
        # Selon la version de Tk, winfo_id rend la fenêtre elle-même ou un
        # enfant : c'est la fenêtre de plus haut niveau qui porte les styles.
        parent = user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, _GWL_EXSTYLE,
            style | _WS_EX_NOACTIVATE | _WS_EX_TOOLWINDOW | _WS_EX_TRANSPARENT)

    # -------------------------------------------------- interface pour threads

    def set_state(self, state):
        """Appelable depuis n'importe quel thread."""
        self._events.put(state)

    def set_lang(self, lang):
        """Appelable depuis n'importe quel thread : simple remplacement d'un
        dictionnaire, lu par le thread tkinter au dessin suivant."""
        self.labels = {"loading": i18n.t("overlay.loading", lang),
                       "working": i18n.t("overlay.working", lang)}

    def quit(self):
        self._events.put("__quit__")

    def run(self):
        """Boucle tkinter : à appeler dans le thread principal."""
        self.root.after(TICK_MS, self._tick)
        self.root.mainloop()

    # ------------------------------------------------------------- animation

    def _tick(self):
        while True:
            try:
                state = self._events.get_nowait()
            except queue.Empty:
                break
            if state == "__quit__":
                self._quit = True
            elif state != self._state:
                self._state = state
                self._t0 = time.perf_counter()

        if self._quit:
            self.root.destroy()
            return

        self._show(self._state != "hidden")
        if self._visible:
            self._draw()
        self.root.after(TICK_MS, self._tick)

    def _show(self, visible):
        if visible == self._visible:
            return
        self._visible = visible
        if visible:
            self.root.deiconify()
            # deiconify peut replacer la fenêtre derrière les autres.
            self.root.attributes("-topmost", True)
        else:
            self.root.withdraw()

    def _draw(self):
        self.canvas.delete("all")
        self._pill()
        phase = time.perf_counter() - self._t0
        color = COLORS.get(self._state, FG)
        cy = self.h // 2

        if self._state == "working":
            self._spinner(self._px(DOT_X), cy, self._px(DOT_R + 2), phase, color)
        else:
            # Pulsation : lente pendant le chargement, plus vive à l'écoute.
            speed = 1.4 if self._state == "loading" else 2.6
            grow = 1.0 + 0.22 * math.sin(phase * speed * math.pi)
            r = self._px(DOT_R) * grow
            self.canvas.create_oval(self._px(DOT_X) - r, cy - r,
                                    self._px(DOT_X) + r, cy + r,
                                    fill=color, outline="")

        if self._state == "listening":
            self._meter(self._px(CONTENT_X), cy, self.w - self._px(CONTENT_X + 18),
                        color)
        else:
            self.canvas.create_text(self._px(CONTENT_X), cy, anchor="w",
                                    fill=FG, font=self.font,
                                    text=self.labels.get(self._state, ""))

    def _pill(self):
        """Fond arrondi : deux disques aux extrémités et un rectangle au milieu."""
        r = self.h / 2
        self.canvas.create_oval(0, 0, 2 * r, self.h, fill=BG, outline="")
        self.canvas.create_oval(self.w - 2 * r, 0, self.w, self.h,
                                fill=BG, outline="")
        self.canvas.create_rectangle(r, 0, self.w - r, self.h, fill=BG, outline="")

    def _spinner(self, cx, cy, r, phase, color):
        start = (-phase * 320) % 360
        self.canvas.create_arc(cx - r, cy - r, cx + r, cy + r,
                               start=start, extent=100, style="arc",
                               outline=color, width=self._px(3))

    def _meter(self, x, cy, width, color):
        """Vumètre : la hauteur des barres suit le niveau du micro."""
        level = min(1.0, max(0.0, self.level_source() * 12))
        step = width / BARS
        bar_w = max(1, int(step * 0.38))
        top = self.h / 2 - self._px(13)
        for i in range(BARS):
            # Chaque barre s'allume à partir de son propre seuil, et garde une
            # hauteur minimale pour rester lisible dans le silence.
            share = max(0.0, min(1.0, (level - i / BARS) * BARS))
            half = max(self._px(1.2), share * top)
            bx = x + i * step
            self.canvas.create_rectangle(bx, cy - half, bx + bar_w, cy + half,
                                         fill=color if share else IDLE_BAR,
                                         outline="")


# ----------------------------------------------------------------- test isolé

def _selftest():
    """Fait défiler les états, avec un faux niveau micro."""
    import threading

    fake = {"level": 0.0}
    overlay = Overlay(level_source=lambda: fake["level"])

    def script():
        for state, seconds in (("loading", 2.5), ("listening", 4.0),
                               ("working", 2.0), ("hidden", 1.0)):
            print(f"  état : {state}")
            overlay.set_state(state)
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < seconds:
                # Faux niveau micro : une voix qui monte et descend.
                fake["level"] = 0.05 * abs(math.sin((time.perf_counter() - t0) * 2))
                time.sleep(0.03)
        overlay.quit()

    print("Défilement des états de la pastille (bas de l'écran)…")
    threading.Thread(target=script, daemon=True).start()
    overlay.run()
    print("Fin.")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
