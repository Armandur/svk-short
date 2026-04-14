"""Routes för överlåtelsebekräftelse: /transfer-action/<token> GET+POST."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request

from app.auth import decode_transfer_action_token
from app.config import BASE_URL
from app.csrf import (
    get_anon_csrf_secret,
    get_csrf_secret,
    set_anon_csrf_cookie,
    validate_csrf_token,
)
from app.database import get_db
from app.mail import (
    MailError,
    skicka_bulk_overlatelse_avbojd_agare,
    skicka_bulk_overlatelse_bekraftad_agare,
    skicka_overlatelse_avbojd_agare,
    skicka_overlatelse_bekraftad_agare,
)
from app.ownership import move_twin_rows
from app.templating import templates

log = logging.getLogger(__name__)
router = APIRouter()


def _load_transfer_action(token: str):
    """Avkoda transfer-action-token och slå upp rader. Returnerar tuple
    (error_response, data, rows, is_bulk, req_ids, bundle_ids, bundle_rows)
    där error_response är satt om något gått fel (ogiltig token, okänd
    action, inga rader) eller None om allt är OK."""
    data = decode_transfer_action_token(token)
    if not data:
        return (
            ("error", "Länken är ogiltig eller har gått ut (7 dagar).", 400),
            None,
            None,
            None,
            None,
            None,
            None,
        )

    action = data.get("action")
    if action not in ("accept", "decline"):
        return (("http", 400), None, None, None, None, None, None)

    is_bulk = "req_ids" in data
    req_ids = data["req_ids"] if is_bulk else [data["req_id"]]
    bundle_ids = data.get("bundle_ids", [])

    with get_db() as db:
        rows = db.execute(
            f"""SELECT tr.id, tr.status, tr.to_email, tr.from_user_id,
                       tr.link_id, l.code, l.target_url, u.email AS from_email
               FROM transfer_requests tr
               JOIN links l ON tr.link_id = l.id
               JOIN users u ON tr.from_user_id = u.id
               WHERE tr.id IN ({",".join("?" for _ in req_ids)})""",
            req_ids,
        ).fetchall()
        rows = [dict(r) for r in rows]

        bundle_rows: list[dict] = []
        if bundle_ids:
            br = db.execute(
                f"SELECT id, code, name, owner_id FROM bundles "
                f"WHERE id IN ({','.join('?' for _ in bundle_ids)})",
                bundle_ids,
            ).fetchall()
            bundle_rows = [dict(b) for b in br]

    if not rows and not bundle_rows:
        return (("http", 404), None, None, None, None, None, None)

    return (None, data, rows, is_bulk, req_ids, bundle_ids, bundle_rows)


@router.get("/transfer-action/{token}")
async def transfer_action_confirm(request: Request, token: str):
    """Visar bekräftelsesida — förhindrar att e-postförhandsvisning auto-utför överlåtelsen."""
    err, data, rows, is_bulk, _req_ids, _bundle_ids, bundle_rows = _load_transfer_action(token)
    if err:
        if err[0] == "error":
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": err[1]},
                status_code=err[2],
            )
        raise HTTPException(status_code=err[1])

    action = data["action"]
    mail_bundles = [{"code": b["code"], "name": b["name"]} for b in bundle_rows]

    # Om allt redan är hanterat — visa resultatsidan direkt (idempotent, ingen skrivning).
    if rows and all(r["status"] != "pending" for r in rows):
        return templates.TemplateResponse(
            "transfer_done.html",
            {
                "request": request,
                "codes": [r["code"] for r in rows],
                "bundles": mail_bundles,
                "already_handled": True,
                "accepted": rows[0]["status"] == "accepted",
                "is_bulk": is_bulk,
            },
        )

    pending = [r for r in rows if r["status"] == "pending"]
    from app.auth import get_current_user

    logged_in = get_current_user(request) is not None
    ctx: dict = {
        "request": request,
        "token": token,
        "action": action,
        "is_bulk": is_bulk,
        "codes": [r["code"] for r in pending],
        "from_email": pending[0]["from_email"] if pending else None,
        "to_email": pending[0]["to_email"] if pending else None,
        "bundle_count": len(bundle_rows),
    }
    anon_secret, is_new = get_anon_csrf_secret(request)
    if not logged_in:
        ctx["csrf_secret"] = anon_secret
    response = templates.TemplateResponse("transfer_action_confirm.html", ctx)
    if not logged_in and is_new:
        set_anon_csrf_cookie(response, anon_secret)
    return response


@router.post("/transfer-action/{token}")
async def transfer_action_submit(request: Request, token: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)

    err, data, rows, is_bulk, _req_ids, _bundle_ids, bundle_rows = _load_transfer_action(token)
    if err:
        if err[0] == "error":
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": err[1]},
                status_code=err[2],
            )
        raise HTTPException(status_code=err[1])

    action = data["action"]
    mail_bundles = [{"code": b["code"], "name": b["name"]} for b in bundle_rows]

    with get_db() as db:
        # Om alla redan är hanterade — visa resultatsidan direkt
        if rows and all(r["status"] != "pending" for r in rows):
            return templates.TemplateResponse(
                "transfer_done.html",
                {
                    "request": request,
                    "codes": [r["code"] for r in rows],
                    "bundles": mail_bundles,
                    "already_handled": True,
                    "accepted": rows[0]["status"] == "accepted",
                    "is_bulk": is_bulk,
                },
            )

        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        to_email = rows[0]["to_email"] if rows else None
        from_email = rows[0]["from_email"] if rows else None
        pending = [r for r in rows if r["status"] == "pending"]

        if action == "accept":
            db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (to_email,))
            new_user = db.execute("SELECT id FROM users WHERE email=?", (to_email,)).fetchone()
            for r in pending:
                db.execute(
                    "UPDATE links SET owner_id=? WHERE id=?",
                    (new_user["id"], r["link_id"]),
                )
                db.execute(
                    "UPDATE transfer_requests SET status='accepted', resolved_at=? WHERE id=?",
                    (now, r["id"]),
                )
                db.execute(
                    "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
                    (
                        "transfer_accepted",
                        new_user["id"],
                        r["link_id"],
                        f"överlåtelse godkänd: {from_email} → {to_email}",
                    ),
                )
                # Dra med eventuell samling med samma kod (skalrad efter
                # konverter-till-samling) så den inte lämnas hos avsändaren.
                move_twin_rows(db, r["code"], r["from_user_id"], new_user["id"])
            # Överlåt samlingar och deras ev. länk-skalrader
            for b in bundle_rows:
                db.execute(
                    "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (new_user["id"], b["id"]),
                )
                move_twin_rows(db, b["code"], b["owner_id"], new_user["id"])
        else:
            for r in pending:
                db.execute(
                    "UPDATE transfer_requests SET status='declined', resolved_at=? WHERE id=?",
                    (now, r["id"]),
                )

    codes = [r["code"] for r in pending]

    if action == "accept":
        if is_bulk:
            try:
                skicka_bulk_overlatelse_bekraftad_agare(
                    from_email, codes, to_email, BASE_URL, bundles=mail_bundles
                )
            except MailError:
                log.exception("MailError")
        else:
            try:
                skicka_overlatelse_bekraftad_agare(from_email, codes[0], to_email, BASE_URL)
            except MailError:
                log.exception("MailError")
    else:
        if is_bulk:
            try:
                skicka_bulk_overlatelse_avbojd_agare(
                    from_email, codes, to_email, bundles=mail_bundles
                )
            except MailError:
                log.exception("MailError")
        else:
            try:
                skicka_overlatelse_avbojd_agare(from_email, codes[0], to_email)
            except MailError:
                log.exception("MailError")

    return templates.TemplateResponse(
        "transfer_done.html",
        {
            "request": request,
            "codes": codes,
            "bundles": mail_bundles,
            "accepted": action == "accept",
            "already_handled": False,
            "is_bulk": is_bulk,
        },
    )
