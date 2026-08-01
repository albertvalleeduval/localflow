# -*- coding: utf-8 -*-
"""Lancement automatique de localflow à l'ouverture de la session Windows.

Crée un raccourci vers `pythonw app.py` dans le dossier Démarrage
(`shell:startup`). `pythonw` plutôt que `python` : aucune fenêtre de console ne
s'ouvre, l'outil vit dans sa pastille d'état et dans `localflow.log`.

    python install_startup.py            installe (ou met à jour) le raccourci
    python install_startup.py --status    dit s'il est installé
    python install_startup.py --remove    le supprime
"""

import os
import sys

import win32com.client

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "app.py")
SHORTCUT_NAME = "localflow.lnk"


def startup_dir():
    shell = win32com.client.Dispatch("WScript.Shell")
    return shell.SpecialFolders("Startup")


def shortcut_path():
    return os.path.join(startup_dir(), SHORTCUT_NAME)


def pythonw():
    """Chemin de pythonw.exe correspondant à l'interpréteur courant."""
    exe = sys.executable
    folder, name = os.path.split(exe)
    if name.lower().startswith("pythonw"):
        return exe
    candidate = os.path.join(folder, name.lower().replace("python", "pythonw", 1))
    return candidate if os.path.exists(candidate) else exe


def install():
    target = pythonw()
    path = shortcut_path()
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(path)
    link.TargetPath = target
    link.Arguments = f'"{APP}"'
    # Répertoire de travail : le modèle, la config et le journal sont cherchés
    # à côté du script, mais autant que le processus démarre au bon endroit.
    link.WorkingDirectory = HERE
    link.Description = "localflow — dictée locale"
    link.IconLocation = f"{target},0"
    link.Save()
    print(f"Raccourci créé : {path}")
    print(f"  cible     : {target}")
    print(f"  arguments : \"{APP}\"")
    if os.path.basename(target).lower().startswith("python.exe"):
        print("  Attention : pythonw.exe est introuvable à côté de l'interpréteur "
              "courant, une fenêtre de console s'ouvrira au démarrage.")
    return 0


def remove():
    path = shortcut_path()
    if not os.path.exists(path):
        print("Rien à supprimer : le raccourci n'est pas installé.")
        return 0
    os.remove(path)
    print(f"Raccourci supprimé : {path}")
    return 0


def status():
    path = shortcut_path()
    if not os.path.exists(path):
        print(f"Non installé (rien dans {startup_dir()}).")
        return 1
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(path)
    print(f"Installé : {path}")
    print(f"  cible     : {link.TargetPath}")
    print(f"  arguments : {link.Arguments}")
    print(f"  dossier   : {link.WorkingDirectory}")
    return 0


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "--install"
    if action in ("--remove", "--uninstall"):
        return remove()
    if action == "--status":
        return status()
    if action != "--install":
        raise SystemExit(__doc__.strip())
    return install()


if __name__ == "__main__":
    sys.exit(main())
