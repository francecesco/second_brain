"""Converte il font della UI web nel TTF usato per l'immagine "Oggi" del device.

Il woff2 in `secondbrain/web/static` è variabile (asse wght 300–700): qui si fissa il
grassetto (wght=700) e si salva un TTF statico in `secondbrain/assets`, con la licenza OFL
accanto. Si lancia a mano (serve il gruppo dev, `fonttools[woff]`) solo se il woff2 cambia:

    cd backend && .venv/bin/python tools/make_digest_font.py
"""
import shutil
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

BACKEND = Path(__file__).resolve().parent.parent
STATIC = BACKEND / "secondbrain" / "web" / "static"
ASSETS = BACKEND / "secondbrain" / "assets"
WEIGHT = 700


def main() -> None:
    font = TTFont(STATIC / "space-grotesk.woff2")
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": WEIGHT},
                                                 updateFontNames=False)
    font.flavor = None
    ASSETS.mkdir(exist_ok=True)
    font.save(ASSETS / "SpaceGrotesk-Bold.ttf")
    shutil.copyfile(STATIC / "space-grotesk-OFL.txt", ASSETS / "space-grotesk-OFL.txt")
    print(f"scritto {ASSETS / 'SpaceGrotesk-Bold.ttf'}")


if __name__ == "__main__":
    main()
