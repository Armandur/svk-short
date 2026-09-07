"""Reserverade kortkoder.

En kod som beställts och tryckts på ett anslag går inte att ta tillbaka. Att
reservera i förväg kostar ingenting, att göra det i efterhand kostar en
användares affisch.
"""

import pytest

from app.config import RESERVED_CODES
from app.validation import validate_code


@pytest.mark.parametrize("kod", sorted(RESERVED_CODES))
def test_reserverade_koder_avvisas(kod):
    assert validate_code(kod) is not None, f"{kod} gick att beställa"


@pytest.mark.parametrize("kod", ["swish", "swishqr"])
def test_swishgeneratorns_adresser_ar_reserverade(kod):
    """Funktionen finns inte än (TASK-1673), men adresserna ska inte kunna
    tas under tiden."""
    assert kod in RESERVED_CODES


def test_en_vanlig_kod_slapps_igenom():
    """Utan det här provet hade en trasig validering sett ut som ett
    fungerande skydd - allt avvisas, alltså inga reserverade koder släpps."""
    assert validate_code("hsandkonf") is None
