"""QR-koder till kortlänkarna.

Modulen ritar koden LOKALT. Inga externa anrop, inga designkrav från tredje
part - det gäller vanliga kortlänkar. Swish-koder hämtar sitt innehåll ur en
egen spec och får en symbol i mitten, se docs/swish-qr.md och TASK-1673.
Ritvägen här är den de ska dela, men inte felkorrigeringsnivån - se nedan.

Koden bär den PUBLIKA kortlänken, aldrig target_url. Byter länken mål
fortsätter en tryckt kod att fungera, och det är hela poängen med en
kortlänk.
"""

from __future__ import annotations

import io
import re

import qrcode
import qrcode.image.svg
from qrcode.constants import ERROR_CORRECT_H, ERROR_CORRECT_M

from app.config import BASE_URL

# Två nivåer, av två olika skäl.
#
# Kortlänkarna får M (~15 %), standardens normalfall. H gjorde koden onödigt
# tät: en autogenererad kod (https://svky.se/ + 7 tecken) blev 29x29 moduler
# mot 25x25 med M, och en längre egen kod 33x33. Redundansen skyddade inget
# eftersom mitten är tom - den bara krympte varje modul. Mätt över hela
# spannet av kodlängder ger M 25x25 där H gav 29x29 eller 33x33.
#
# Swish-koderna får H. Där täcker symbolen mitten, och då är felkorrigeringen
# inte en marginal utan det som gör koden läsbar över huvud taget.
FELKORRIGERING_LANK = ERROR_CORRECT_M
FELKORRIGERING_SWISH = ERROR_CORRECT_H
_MODULSTORLEK = 10

# Fyra moduler är standardens minimum för den tysta zonen. En läsare behöver
# tyst yta för att hitta kanten, och en tryckt kod utan den är oläsbar hur
# skarp den än är.
MARGINAL_TRYCK = 4


def lankadress(code: str) -> str:
    """Den publika adressen koden ska bära."""
    return f"{BASE_URL.rstrip('/')}/{code}"


def _kod(data: str, marginal: int,
         felkorrigering: int = FELKORRIGERING_LANK) -> qrcode.QRCode:
    kod = qrcode.QRCode(
        error_correction=felkorrigering,
        border=marginal,
        box_size=_MODULSTORLEK,
    )
    kod.add_data(data)
    kod.make(fit=True)
    return kod


def png(data: str, marginal: int = MARGINAL_TRYCK,
        felkorrigering: int = FELKORRIGERING_LANK) -> bytes:
    """QR-koden som PNG. Vit bakgrund, svart mönster."""
    bild = _kod(data, marginal, felkorrigering).make_image(
        fill_color="black", back_color="white")
    buffert = io.BytesIO()
    bild.convert("RGB").save(buffert, format="PNG")
    return buffert.getvalue()


def svg(data: str, marginal: int = MARGINAL_TRYCK,
        felkorrigering: int = FELKORRIGERING_LANK) -> bytes:
    """QR-koden som SVG, för tryck. Skalbar utan hackiga kanter.

    SvgPathImage ritar BARA den svarta banan - filen blir genomskinlig. På
    vitt papper syns det inte, men lagd på färgat underlag eller en mörk sida
    inverteras koden och blir oläsbar. Vi lägger därför in en vit rektangel
    under banan. Slöjda har inte gjort det, se docs/swish-qr.md.
    """
    kod = _kod(data, marginal, felkorrigering)
    buffert = io.BytesIO()
    kod.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffert)
    ut = buffert.getvalue()

    # SvgPathImage ritar i millimeter med en modul per enhet, så måtten
    # räknas ur antalet moduler och inte ur pixlar.
    moduler = len(kod.get_matrix())
    bakgrund = (f'<rect x="0" y="0" width="{moduler}" height="{moduler}" fill="#ffffff"/>').encode()
    return re.sub(rb"(<path)", bakgrund + rb"\1", ut, count=1)


def filnamn(code: str, andelse: str) -> str:
    """Namn som går att skilja åt i nedladdningsmappen.

    Den som laddar ner koder inför tryck hämtar flera i följd, och
    'qrcode.svg (3)' säger ingenting en vecka senare.
    """
    trygg = re.sub(r"[^A-Za-z0-9_-]", "", code) or "kortlank"
    return f"svky-{trygg}.{andelse}"
