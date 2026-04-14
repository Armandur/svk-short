import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import COOKIE_NAME, create_bulk_transfer_token
from app.config import BASE_URL, LinkStatus
from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import check_rate_limit, get_user_or_redirect
from app.mail import (
    MailError,
    skicka_bulk_overlatelseforfragan,
    skicka_radera_konto_bekraftelse,
)
from app.templating import templates
from app.validation import validate_email

from ._queries import fetch_user_bundles, fetch_user_links

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/mina-lankar/radera-konto")
async def begar_radera_konto(request: Request, csrf_token: str = Form(...)):
    """Steg 1: användaren begär kontoborttagning — mail med engångslänk skickas."""
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

    if user.get("is_admin"):
        return RedirectResponse(url="/mina-lankar?flash=delete_admin_blocked", status_code=303)

    ip = request.client.host if request.client else "unknown"
    with get_db() as db:
        if not check_rate_limit(db, ip, "delete_account"):
            return RedirectResponse(url="/mina-lankar?flash=rate_limited", status_code=303)
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)).isoformat()
        db.execute(
            """INSERT INTO tokens (token, user_id, link_id, purpose, expires_at)
               VALUES (?, ?, NULL, 'delete_account', ?)""",
            (token, user["id"], expires_at),
        )

    confirm_url = f"{BASE_URL}/mina-lankar/radera-konto/{token}"
    try:
        skicka_radera_konto_bekraftelse(user["email"], confirm_url)
    except MailError:
        log.exception("MailError")

    return RedirectResponse(url="/mina-lankar?flash=delete_sent", status_code=303)


def _load_delete_token(db, token: str):
    row = db.execute(
        """SELECT id, user_id, expires_at, used_at
           FROM tokens WHERE token=? AND purpose='delete_account'""",
        (token,),
    ).fetchone()
    if not row or row["used_at"]:
        return None
    if datetime.now(UTC).replace(tzinfo=None) > datetime.fromisoformat(row["expires_at"]):
        return None
    return dict(row)


@router.get("/mina-lankar/radera-konto/{token}")
async def radera_konto_confirm(request: Request, token: str):
    """Steg 2 (GET): bekräftelsesida som listar vad som kommer att hända."""
    with get_db() as db:
        t = _load_delete_token(db, token)
        if not t:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Länken är ogiltig eller har gått ut."},
                status_code=400,
            )
        u = db.execute(
            "SELECT id, email, is_admin FROM users WHERE id=?", (t["user_id"],)
        ).fetchone()
        if not u:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Kontot finns inte längre."},
                status_code=400,
            )
        if u["is_admin"]:
            return templates.TemplateResponse(
                "error.html",
                {
                    "request": request,
                    "message": (
                        "Admin-konton kan inte raderas via självservice. "
                        "Kontakta tjänstens administratör."
                    ),
                },
                status_code=400,
            )

        active_links = [
            dict(r)
            for r in db.execute(
                "SELECT code, target_url FROM links WHERE owner_id=? AND status=1 ORDER BY code",
                (u["id"],),
            ).fetchall()
        ]
        active_bundles = [
            dict(r)
            for r in db.execute(
                "SELECT code, name FROM bundles WHERE owner_id=? AND status=1 ORDER BY code",
                (u["id"],),
            ).fetchall()
        ]
        total_links = db.execute(
            "SELECT COUNT(*) FROM links WHERE owner_id=?", (u["id"],)
        ).fetchone()[0]
        total_bundles = db.execute(
            "SELECT COUNT(*) FROM bundles WHERE owner_id=?", (u["id"],)
        ).fetchone()[0]

    return templates.TemplateResponse(
        "delete_account_confirm.html",
        {
            "request": request,
            "token": token,
            "email": u["email"],
            "active_links": active_links,
            "active_bundles": active_bundles,
            "total_links": total_links,
            "total_bundles": total_bundles,
        },
    )


@router.post("/mina-lankar/radera-konto/{token}")
async def radera_konto_submit(request: Request, token: str, csrf_token: str = Form(...)):
    """Steg 3 (POST): utför raderingen efter användarens bekräftelse."""
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)

    with get_db() as db:
        t = _load_delete_token(db, token)
        if not t:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Länken är ogiltig eller har gått ut."},
                status_code=400,
            )
        user_id = t["user_id"]
        user_row = db.execute("SELECT email, is_admin FROM users WHERE id=?", (user_id,)).fetchone()
        if not user_row:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Kontot finns inte längre."},
                status_code=400,
            )
        if user_row["is_admin"]:
            return templates.TemplateResponse(
                "error.html",
                {
                    "request": request,
                    "message": "Admin-konton kan inte raderas via självservice.",
                },
                status_code=400,
            )
        email = user_row["email"]

        # Anonymisera länkar: koppla loss från ägaren och avaktivera aktiva.
        db.execute(
            """UPDATE links
                  SET owner_id = NULL,
                      status = CASE WHEN status = ? THEN ? ELSE status END
                WHERE owner_id = ?""",
            (LinkStatus.ACTIVE, LinkStatus.DISABLED_OWNER, user_id),
        )
        # Anonymisera samlingar (status 3 = DISABLED_OWNER för bundles).
        db.execute(
            """UPDATE bundles
                  SET owner_id = NULL,
                      status = CASE WHEN status = 1 THEN 3 ELSE status END,
                      updated_at = CURRENT_TIMESTAMP
                WHERE owner_id = ?""",
            (user_id,),
        )
        # Anonymisera åtgärdsloggen (behåll händelserna men ta bort ägarkopplingen).
        db.execute("UPDATE audit_log SET actor_id=NULL WHERE actor_id=?", (user_id,))
        # Radera alla tokens kopplade till kontot (inkl. denna delete-token).
        db.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
        # Rensa pågående överlåtelse- och övertagsförfrågningar kopplade till e-posten.
        db.execute("DELETE FROM transfer_requests WHERE from_user_id=?", (user_id,))
        db.execute(
            "DELETE FROM transfer_requests WHERE to_email=? AND status='pending'",
            (email,),
        )
        db.execute(
            "DELETE FROM takeover_requests WHERE requester_email=? AND status='pending'",
            (email,),
        )
        db.execute(
            "DELETE FROM bundle_takeover_requests WHERE requester_email=? AND status='pending'",
            (email,),
        )
        db.execute(
            "DELETE FROM bundle_transfers WHERE to_email=? AND used_at IS NULL",
            (email,),
        )
        # Radera själva användarraden.
        db.execute("DELETE FROM users WHERE id=?", (user_id,))

    response = templates.TemplateResponse(
        "delete_account_done.html",
        {"request": request},
    )
    response.delete_cookie(COOKIE_NAME)
    return response


@router.post("/mina-lankar/request-transfer-all")
async def request_transfer_all(
    request: Request,
    to_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

    to_email = to_email.strip().lower()
    email_error = validate_email(to_email)

    with get_db() as db:
        active_links = db.execute(
            """SELECT id, code, target_url FROM links
               WHERE owner_id=? AND status=?
               ORDER BY created_at""",
            (user["id"], LinkStatus.ACTIVE),
        ).fetchall()
        active_links = [dict(r) for r in active_links]

        active_bundles = db.execute(
            """SELECT id, code, name FROM bundles
               WHERE owner_id=? AND status=1
               ORDER BY created_at""",
            (user["id"],),
        ).fetchall()
        active_bundles = [dict(r) for r in active_bundles]

        def _render_error(msg):
            links = fetch_user_links(db, user["id"])
            bundles = fetch_user_bundles(db, user["id"])
            return templates.TemplateResponse(
                "my_links.html",
                {
                    "request": request,
                    "user": user,
                    "links": links,
                    "bundles": bundles,
                    "bulk_transfer_error": msg,
                    "bulk_transfer_open": True,
                },
                status_code=422,
            )

        if email_error:
            return _render_error(email_error)

        if to_email == user["email"]:
            return _render_error("Du kan inte överlåta till dig själv.")

        if not active_links and not active_bundles:
            return _render_error("Du har inga aktiva kortlänkar eller samlingar att överlåta.")

        req_ids = []
        if active_links:
            link_ids = [lnk["id"] for lnk in active_links]
            existing = db.execute(
                f"""SELECT link_id FROM transfer_requests
                   WHERE link_id IN ({",".join("?" for _ in link_ids)}) AND status='pending'""",
                link_ids,
            ).fetchone()
            if existing:
                return _render_error(
                    "En eller flera av dina kortlänkar har redan en väntande överlåtelseförfrågan. "
                    "Vänta tills den besvarats eller avbryt den innan du begär en ny."
                )
            for lnk in active_links:
                db.execute(
                    "INSERT INTO transfer_requests (link_id, from_user_id, to_email) VALUES (?,?,?)",
                    (lnk["id"], user["id"], to_email),
                )
                req_ids.append(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

        bundle_ids = [b["id"] for b in active_bundles]

    accept_url = f"{BASE_URL}/transfer-action/{create_bulk_transfer_token(req_ids, 'accept', bundle_ids or None)}"
    decline_url = f"{BASE_URL}/transfer-action/{create_bulk_transfer_token(req_ids, 'decline', bundle_ids or None)}"

    try:
        skicka_bulk_overlatelseforfragan(
            to=to_email,
            from_email=user["email"],
            links=active_links,
            accept_url=accept_url,
            decline_url=decline_url,
            bundles=active_bundles,
        )
    except MailError:
        log.exception("MailError")

    return RedirectResponse(url="/mina-lankar?flash=bulk_transfer_sent", status_code=303)
