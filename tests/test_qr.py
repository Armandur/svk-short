"""QR-koder till kortlänkarna.

Proven avkodar bilderna. Att en route svarar 200 med image/png säger bara att
något kom ut - inte att det går att skanna, och inte att det bär rätt adress.
"""

import io

import cv2
import numpy as np
import pytest
import qrcode.constants
from PIL import Image

from app import qr
from app.database import get_db


def _avkoda(png: bytes) -> str:
    arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    text, *_ = cv2.QRCodeDetector().detectAndDecode(arr)
    return text


def _svg_till_png(svgdata: bytes) -> bytes:
    """Renderar UTAN att komponera mot vitt. En genomskinlig SVG blir då
    svart bakgrund och inverterad kod - vilket är precis felet vi vill
    fånga."""
    import cairosvg

    buf = io.BytesIO()
    cairosvg.svg2png(bytestring=svgdata, write_to=buf, output_width=900)
    im = Image.open(io.BytesIO(buf.getvalue())).convert("RGB")
    ut = io.BytesIO()
    im.save(ut, format="PNG")
    return ut.getvalue()


def _skapa_lank(agare: int, code: str = "hsandkonf") -> int:
    with get_db() as db:
        db.execute(
            "INSERT INTO links (code, target_url, owner_id, status) VALUES (?,?,?,1)",
            (code, "https://www.svenskakyrkan.se/harnosand", agare),
        )
        return db.execute("SELECT last_insert_rowid()").fetchone()[0]


# --- modulen -------------------------------------------------------------

def test_koden_bar_kortlanken_inte_maladressen():
    """Byter länken mål ska en tryckt kod fortsätta fungera. Det är hela
    poängen med en kortlänk, och en kod på target_url hade förstört den."""
    adress = qr.lankadress("hsandkonf")
    assert adress.endswith("/hsandkonf")
    assert _avkoda(qr.png(adress)) == adress


def test_svg_har_vit_bakgrund():
    """SvgPathImage ritar bara banan, så filen blir genomskinlig. På färgat
    underlag inverteras koden och blir oläsbar. Provet renderar UTAN att
    komponera mot vitt - utan bakgrundsrektangeln avkodas den inte."""
    adress = qr.lankadress("hsandkonf")
    assert _avkoda(_svg_till_png(qr.svg(adress))) == adress


def test_marginalen_ar_minst_standardens_fyra():
    """En läsare behöver tyst yta för att hitta kanten. En tryckt kod utan
    den är oläsbar hur skarp den än är."""
    assert qr.MARGINAL_TRYCK >= 4


@pytest.mark.parametrize(
    ("code", "vantat"),
    [("hsandkonf", "svky-hsandkonf.svg"), ("../../etc/passwd", "svky-etcpasswd.svg"),
     ("!!!", "svky-kortlank.svg")],
)
def test_filnamnet_ar_ofarligt(code, vantat):
    assert qr.filnamn(code, "svg") == vantat


# --- routen --------------------------------------------------------------

@pytest.mark.parametrize("andelse", ["png", "svg"])
def test_agaren_far_hamta_sin_kod(client, inloggad_anvandare, andelse):
    lank = _skapa_lank(inloggad_anvandare["id"])

    svar = client.get(f"/mina-lankar/{lank}/qr.{andelse}")

    assert svar.status_code == 200
    assert svar.headers["content-type"].startswith(
        "image/png" if andelse == "png" else "image/svg+xml")
    assert "attachment" in svar.headers["content-disposition"]
    assert "svky-hsandkonf" in svar.headers["content-disposition"]

    png = svar.content if andelse == "png" else _svg_till_png(svar.content)
    assert _avkoda(png) == qr.lankadress("hsandkonf")


# Provet som bär behörigheten. Utan det räcker det att gissa ett id.
def test_annans_lank_ger_404(client, inloggad_anvandare):
    with get_db() as db:
        db.execute("INSERT INTO users (email) VALUES ('annan@svenskakyrkan.se')")
        annan = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    lank = _skapa_lank(annan, "annans")

    assert client.get(f"/mina-lankar/{lank}/qr.png").status_code == 404


def test_utloggad_skickas_till_login(client):
    assert client.get("/mina-lankar/1/qr.png").status_code == 303


def test_okand_andelse_ger_404(client, inloggad_anvandare):
    lank = _skapa_lank(inloggad_anvandare["id"])
    assert client.get(f"/mina-lankar/{lank}/qr.gif").status_code == 404


def test_admin_far_hamta_alla(client, admin):
    with get_db() as db:
        db.execute("INSERT INTO users (email) VALUES ('nagon@svenskakyrkan.se')")
        nagon = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    lank = _skapa_lank(nagon)

    svar = client.get(f"/admin/links/{lank}/qr.png")

    assert svar.status_code == 200
    assert _avkoda(svar.content) == qr.lankadress("hsandkonf")


def test_vanlig_anvandare_nekas_adminroutens_kod(client, inloggad_anvandare):
    lank = _skapa_lank(inloggad_anvandare["id"])
    assert client.get(f"/admin/links/{lank}/qr.png").status_code == 303


def _moduler(adress: str, felkorrigering: int) -> int:
    kod = qr._kod(adress, qr.MARGINAL_TRYCK, felkorrigering)
    return len(kod.get_matrix()) - 2 * qr.MARGINAL_TRYCK


def test_kortlank_ryms_i_25_moduler():
    """En autogenererad kod ska inte kosta ett versionssteg i onödan.

    Adressen skrivs ut med produktionens bas, inte lankadress() - provmiljön
    kör en längre BASE_URL, och det är den skarpa längden frågan gäller.
    H gav 29x29 moduler för samma adress. Skillnaden syns direkt på skärmen
    och i tryck: varje modul blir mindre, och koden ser tätare ut än den
    behöver vara när mitten ändå är tom.
    """
    adress = "https://svky.se/abcdefg"

    moduler = _moduler(adress, qr.FELKORRIGERING_LANK)

    assert moduler <= 25, f"{moduler}x{moduler} - kortlänken tog ett versionssteg extra"
    assert moduler < _moduler(adress, qr.FELKORRIGERING_SWISH)


def test_swish_behaller_hog_felkorrigering():
    """Symbolen i mitten täcker moduler. Utan H blir koden oläsbar."""
    assert qr.FELKORRIGERING_SWISH == qrcode.constants.ERROR_CORRECT_H
    assert qr.FELKORRIGERING_LANK != qr.FELKORRIGERING_SWISH
