# -*- coding: utf-8 -*-
"""Génère `web/icon.ico`, l'icône de localflow.

Une tuile arrondie dans le langage visuel de Windows 11, dégradé chaud du
rouge de l'outil vers l'orange, et une onde sonore de cinq barres blanches.
Le motif est choisi pour rester lisible en 16 pixels dans la zone de
notification : cinq formes pleines et bien séparées, aucun détail fin.

Dessiné en pixels plutôt qu'importé d'un éditeur — le dépôt reste sans binaire
opaque et sans dépendance graphique de plus (ni Pillow ni fichier téléchargé).

    python make_icon.py
"""

import os
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "web", "icon.ico")
# Le même dessin en PNG, pour la barre latérale de la fenêtre : une page web
# ne sait pas afficher un .ico de façon fiable.
OUT_PNG = os.path.join(HERE, "web", "logo.png")

SIZES = (16, 20, 24, 32, 48, 64, 128, 256)

TOP = (0x5C, 0x6E, 0xFF)         # BGR de #ff6e5c
BOTTOM = (0x2E, 0x38, 0xE8)      # BGR de #e8382e — plus sombre en bas
WHITE = (0xFF, 0xFF, 0xFF)

# Hauteurs relatives des cinq barres : une onde qui monte puis retombe, sans
# symétrie parfaite — plus vivant qu'un simple chapeau.
BARS = (0.34, 0.68, 1.00, 0.78, 0.44)


def coverage(px, py, test, samples):
    """Anticrénelage : part du pixel couverte, échantillonnée en sous-grille."""
    hits = 0
    step = 1.0 / samples
    for i in range(samples):
        for j in range(samples):
            if test(px + (i + 0.5) * step, py + (j + 0.5) * step):
                hits += 1
    return hits / (samples * samples)


def rounded_rect(x0, y0, x1, y1, radius):
    """Test d'appartenance à un rectangle aux coins arrondis."""
    def test(x, y):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        cx = min(max(x, x0 + radius), x1 - radius)
        cy = min(max(y, y0 + radius), y1 - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2 + 1e-9
    return test


def draw(size):
    """Renvoie les pixels BGRA, de haut en bas."""
    samples = 4 if size <= 64 else 2      # les grandes tailles n'en ont pas besoin

    tile = rounded_rect(0, 0, size, size, size * 0.225)

    # Onde : cinq barres centrées, largeur et espacement proportionnels.
    n = len(BARS)
    span = size * 0.62                    # largeur totale de l'onde
    bar_w = span / (n * 2 - 1)            # barres et espaces de même largeur
    left = (size - span) / 2.0
    max_h = size * 0.52
    mid = size / 2.0

    bars = []
    for i, ratio in enumerate(BARS):
        x0 = left + i * bar_w * 2
        height = max_h * ratio
        bars.append(rounded_rect(x0, mid - height / 2, x0 + bar_w,
                                 mid + height / 2, bar_w / 2))

    pixels = []
    for y in range(size):
        # Dégradé vertical, calculé une fois par ligne.
        t = y / max(1, size - 1)
        base = tuple(round(TOP[c] * (1 - t) + BOTTOM[c] * t) for c in range(3))
        for x in range(size):
            alpha = coverage(x, y, tile, samples)
            if alpha <= 0:
                pixels.append((0, 0, 0, 0))
                continue
            mark = 0.0
            for bar in bars:
                mark = max(mark, coverage(x, y, bar, samples))
                if mark >= 1.0:
                    break
            mark = min(mark, alpha)
            pixels.append(tuple(
                round(base[c] * (1 - mark) + WHITE[c] * mark) for c in range(3)
            ) + (round(255 * alpha),))
    return pixels


def png_chunk(tag, data):
    import zlib
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def to_png(size, pixels):
    """Les grandes tailles vont en PNG dans le .ico, comme le veut l'usage."""
    import zlib

    raw = bytearray()
    for y in range(size):
        raw.append(0)                                  # filtre « aucun »
        for x in range(size):
            b, g, r, a = pixels[y * size + x]
            raw += bytes((r, g, b, a))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", header)
            + png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + png_chunk(b"IEND", b""))


def to_bmp(size, pixels):
    """Format historique : en-tête DIB, image retournée, puis masque AND."""
    head = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                       size * size * 4, 0, 0, 0, 0)
    body = bytearray()
    for y in range(size - 1, -1, -1):                  # de bas en haut
        for x in range(size):
            body += bytes(pixels[y * size + x])
    mask = bytes(((size + 31) // 32) * 4 * size)       # l'alpha fait le travail
    return head + bytes(body) + mask


def main():
    images = []
    for size in SIZES:
        pixels = draw(size)
        images.append((size, to_png(size, pixels) if size >= 256
                       else to_bmp(size, pixels)))

    offset = 6 + 16 * len(images)
    out = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    for size, data in images:
        # 0 dans l'en-tête signifie 256 : un octet ne va pas plus haut.
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                           len(data), offset)
        offset += len(data)
    for _, data in images:
        out += data

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(out)
    print(f"Icône écrite : {OUT} ({len(out)} octets, tailles {SIZES})")

    png = to_png(128, draw(128))
    with open(OUT_PNG, "wb") as f:
        f.write(png)
    print(f"Logo écrit : {OUT_PNG} ({len(png)} octets, 128 px)")


if __name__ == "__main__":
    main()
