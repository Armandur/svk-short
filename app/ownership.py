"""Hjälpare för ägarbyten av kortlänkar och samlingar.

Kortlänkar och samlingar delar kodnamnrymd — när en användare konverterar
en länk till samling (eller tvärtom) sätts "tvilling-raden" till status=3
(DISABLED_OWNER) som ett skal. Det betyder att varje överlåtelse måste
flytta både den aktiva raden och dess eventuella skal i den andra tabellen,
annars blir skalraden kvar hos ursprunglig ägare och syns som en död rest
i deras "Mina länkar".
"""


def move_twin_rows(db, code: str, from_user_id: int, new_owner_id: int) -> list[str]:
    """Flytta eventuell tvilling-rad (links↔bundles med samma code) från
    from_user_id till new_owner_id. Returnerar en lista med beskrivningar av
    vad som flyttades, användbart för audit-logg. Idempotent — om ingen
    tvilling finns eller den redan är flyttad händer inget.
    """
    moved: list[str] = []

    result = db.execute(
        "UPDATE links SET owner_id=? WHERE code=? AND owner_id=?",
        (new_owner_id, code, from_user_id),
    )
    if result.rowcount:
        moved.append(f"link:{code}")

    result = db.execute(
        "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE code=? AND owner_id=?",
        (new_owner_id, code, from_user_id),
    )
    if result.rowcount:
        moved.append(f"bundle:{code}")

    return moved
