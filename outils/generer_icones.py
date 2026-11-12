"""Génère les icônes de l'appli (PWA) aux couleurs de la maquette. À relancer si le logo change.

Usage :  python outils/generer_icones.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

NAVY, TEAL, MINT = (23, 52, 72), (8, 127, 140), (127, 208, 214)
DOSSIER = Path(__file__).resolve().parent.parent / "app" / "web" / "icones"


def police(taille: int) -> ImageFont.ImageFont:
    for nom in ("DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"):
        try:
            return ImageFont.truetype(nom, taille)
        except OSError:
            continue
    return ImageFont.load_default()


def icone(taille: int, marge: float) -> Image.Image:
    """`marge` : zone de sécurité des icônes « maskable » d'Android (le système rogne les bords)."""
    img = Image.new("RGB", (taille, taille), NAVY)
    d = ImageDraw.Draw(img)
    m = int(taille * marge)
    d.rounded_rectangle([m, m, taille - m, taille - m], radius=int(taille * 0.12), fill=NAVY)
    f = police(int((taille - 2 * m) * 0.52))
    texte = "EM"
    l, h = d.textbbox((0, 0), texte, font=f)[2:]
    d.text(((taille - l) / 2, (taille - h) / 2 - taille * 0.06), texte, font=f, fill=(255, 255, 255))
    y = taille / 2 + h / 2 + taille * 0.02
    d.rounded_rectangle([taille * 0.34, y, taille * 0.66, y + taille * 0.045], radius=int(taille * 0.02), fill=MINT)
    return img


if __name__ == "__main__":
    DOSSIER.mkdir(parents=True, exist_ok=True)
    for t in (192, 512):
        icone(t, 0.0).save(DOSSIER / f"icone-{t}.png")
        icone(t, 0.1).save(DOSSIER / f"icone-{t}-maskable.png")
    icone(180, 0.0).save(DOSSIER / "apple-touch-icon.png")
    icone(32, 0.0).save(DOSSIER / "favicon.png")
    print("Icônes générées dans", DOSSIER)
