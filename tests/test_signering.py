"""Signeringen: att CI signerar och att servern kan avvisa det osignerade.

Identitetssträngen står på TVÅ ställen - i workflowen som signerar och i
skriptet som verifierar. Glider de isär faller varje deploy med "no matching
signatures", ett fel som pekar mot signaturen medan orsaken är en textrad.
Provet nedan binder ihop dem.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPOROT = Path(__file__).resolve().parents[1]
WORKFLOW = (REPOROT / ".github/workflows/docker.yml").read_text()
SKRIPT = (REPOROT / "drift/svky-verifiera.sh").read_text()


def test_ci_signerar_bara_pa_main():
    """En signatur på ett grenbygge hade gjort serverns kontroll meningslös:
    vilken gren som helst hade då kunnat passera den."""
    for steg in ("Install cosign", "Sign the image", "Verify the signature"):
        assert steg in WORKFLOW, f"steget {steg} saknas"
    villkor = WORKFLOW.count("github.ref == 'refs/heads/main'")
    assert villkor >= 3, "något signeringssteg saknar main-villkoret"


def test_ci_har_id_token_behorighet():
    """Utan id-token: write kan cosign inte hämta ett OIDC-token, och
    signeringen faller med ett fel om Fulcio i stället för om behörighet."""
    assert "id-token: write" in WORKFLOW


def test_signaturen_gors_pa_digest_inte_pa_tagg():
    """Signering av en tagg hade signerat vad taggen pekade på just då."""
    assert "steps.build.outputs.digest" in WORKFLOW
    assert "@${DIGEST}" in WORKFLOW


def _identitet_ur_workflow() -> str:
    m = re.search(r'"(https://github\.com/[^"]*docker\.yml@[^"]*)"', WORKFLOW)
    assert m, "hittade ingen certificate-identity i workflowen"
    return m.group(1)


def test_identiteten_ar_densamma_i_ci_och_pa_servern():
    ci = _identitet_ur_workflow()
    # Skriptet bygger sin ur tre variabler. Läs förvalen och sätt ihop dem
    # på samma sätt, i stället för att skriva av strängen för hand.
    repo = re.search(r'REPO=\$\{SVKY_REPO:-([^}]+)\}', SKRIPT).group(1)
    workflow = re.search(r'WORKFLOW=\$\{SVKY_WORKFLOW:-([^}]+)\}', SKRIPT).group(1)
    gren = re.search(r'GREN=\$\{SVKY_GREN:-([^}]+)\}', SKRIPT).group(1)
    fran_skript = f"https://github.com/{repo}/{workflow}@{gren}"

    # Workflowen använder ${{ github.repository }}, som expanderar till repot.
    assert fran_skript == ci.replace("${{ github.repository }}", repo), (
        f"identiteterna skiljer sig:\n  CI:      {ci}\n  skript:  {fran_skript}"
    )


def test_workflowfilen_heter_som_identiteten_pastar():
    """Döps workflowfilen om slutar varje verifiering fungera, och felet syns
    som en ogiltig signatur."""
    workflow = re.search(r'WORKFLOW=\$\{SVKY_WORKFLOW:-([^}]+)\}', SKRIPT).group(1)
    assert (REPOROT / workflow).exists(), f"{workflow} finns inte"


@pytest.mark.parametrize(
    "argument",
    [
        "ghcr.io/armandur/svky.se:latest",  # tagg, inte digest
        "ghcr.io/armandur/svky.se@sha256:kort",  # trasig digest
        "ghcr.io/armandur/svky.se",  # varken eller
    ],
)
def test_skriptet_avvisar_allt_som_inte_ar_en_digest(argument):
    """En tagg går inte att verifiera meningsfullt: kontrollen skulle gälla
    vad taggen pekar på nu, medan nästa pull hämtar vad den pekar på då."""
    r = subprocess.run(
        [str(REPOROT / "drift/svky-verifiera.sh"), argument],
        capture_output=True, text=True, cwd=REPOROT,
    )
    assert r.returncode == 2, f"accepterade {argument}"
    assert "digest" in r.stderr.lower()


def test_skriptet_sager_ifran_utan_argument_och_utan_env(tmp_path):
    r = subprocess.run(
        [str(REPOROT / "drift/svky-verifiera.sh")],
        capture_output=True, text=True, cwd=tmp_path,
    )
    assert r.returncode == 2
    assert "SVKY_IMAGE" in r.stderr
