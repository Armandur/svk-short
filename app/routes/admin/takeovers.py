"""Admin-routes för överlåtelseförfrågningar (takeovers och transfers).

Inkluderar:
- Lista väntande förfrågningar för länkar och samlingar
- Godkänn/avvisa via admin-panel (POST-formulär)
- Godkänn/avvisa via signerad e-postlänk (GET visar bekräftelsesida, POST utför)
"""

from datetime import datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import decode_takeover_action_token
from app.config import BASE_URL, LinkStatus
from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.mail import MailError, skicka_overlatelse_avslagen, skicka_overlatelse_godkand
from app.templating import templates

from .helpers import pending_takeover_count

router = APIRouter()


@router.get("/takeover-requests")
async def admin_takeover_requests(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        link_requests = db.execute(
            """SELECT tr.id, tr.requester_email, tr.reason, tr.status,
                      tr.created_at, tr.resolved_at,
                      l.code, l.target_url, l.id AS link_id,
                      u.email AS owner_email
               FROM takeover_requests tr
               JOIN links l ON tr.link_id = l.id
               LEFT JOIN users u ON l.owner_id = u.id
               ORDER BY tr.status='pending' DESC, tr.created_at DESC""",
        ).fetchall()

        bundle_requests = db.execute(
            """SELECT btr.id, btr.requester_email, btr.reason, btr.status,
                      btr.created_at, btr.resolved_at,
                      b.code, b.name AS bundle_name, b.id AS bundle_id,
                      u.email AS owner_email
               FROM bundle_takeover_requests btr
               JOIN bundles b ON btr.bundle_id = b.id
               LEFT JOIN users u ON b.owner_id = u.id
               ORDER BY btr.status='pending' DESC, btr.created_at DESC""",
        ).fetchall()

        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/takeover_requests.html",
        {
            "request": request,
            "user": admin,
            "takeover_requests": [dict(r) for r in link_requests],
            "bundle_takeover_requests": [dict(r) for r in bundle_requests],
            "pending_takeovers": takeovers,
        },
    )


@router.post("/takeover-requests/{req_id}/approve")
async def admin_approve_takeover(
    request: Request, req_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            """SELECT tr.id, tr.link_id, tr.requester_email, tr.status, l.code
               FROM takeover_requests tr JOIN links l ON tr.link_id=l.id
               WHERE tr.id=?""",
            (req_id,),
        ).fetchone()

        if not row or row["status"] != "pending":
            raise HTTPException(status_code=404)

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (row["requester_email"],))
        new_user = db.execute(
            "SELECT id FROM users WHERE email=?", (row["requester_email"],)
        ).fetchone()
        old_owner = db.execute(
            "SELECT u.email FROM links l LEFT JOIN users u ON l.owner_id=u.id WHERE l.id=?",
            (row["link_id"],),
        ).fetchone()
        old_email = old_owner["email"] if old_owner and old_owner["email"] else "?"

        db.execute("UPDATE links SET owner_id=? WHERE id=?", (new_user["id"], row["link_id"]))
        db.execute(
            "UPDATE takeover_requests SET status='approved', resolved_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), req_id),
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
            (
                "takeover_approved",
                admin["id"],
                row["link_id"],
                f"överlåtelse godkänd: {old_email} → {row['requester_email']}",
            ),
        )

    try:
        skicka_overlatelse_godkand(row["requester_email"], row["code"], BASE_URL)
    except MailError:
        pass

    return RedirectResponse(url="/admin/takeover-requests", status_code=303)


@router.post("/takeover-requests/{req_id}/reject")
async def admin_reject_takeover(
    request: Request, req_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            """SELECT tr.id, tr.status, tr.requester_email, l.code
               FROM takeover_requests tr JOIN links l ON tr.link_id=l.id
               WHERE tr.id=?""",
            (req_id,),
        ).fetchone()

        if not row or row["status"] != "pending":
            raise HTTPException(status_code=404)

        db.execute(
            "UPDATE takeover_requests SET status='rejected', resolved_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), req_id),
        )

    try:
        skicka_overlatelse_avslagen(row["requester_email"], row["code"])
    except MailError:
        pass

    return RedirectResponse(url="/admin/takeover-requests", status_code=303)


@router.post("/bundle-takeover-requests/{req_id}/approve")
async def admin_approve_bundle_takeover(
    request: Request, req_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            """SELECT btr.id, btr.bundle_id, btr.requester_email, btr.status,
                      b.code, b.name AS bundle_name
               FROM bundle_takeover_requests btr JOIN bundles b ON btr.bundle_id=b.id
               WHERE btr.id=?""",
            (req_id,),
        ).fetchone()

        if not row or row["status"] != "pending":
            raise HTTPException(status_code=404)

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (row["requester_email"],))
        new_user = db.execute(
            "SELECT id FROM users WHERE email=?", (row["requester_email"],)
        ).fetchone()
        db.execute(
            "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_user["id"], row["bundle_id"]),
        )
        db.execute(
            "UPDATE bundle_takeover_requests SET status='approved', resolved_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), req_id),
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            (
                "bundle_takeover_approved",
                admin["id"],
                f"bundle:{row['bundle_id']} överlåtet till {row['requester_email']}",
            ),
        )

    try:
        skicka_overlatelse_godkand(
            row["requester_email"], row["code"], BASE_URL, bundle_name=row["bundle_name"]
        )
    except MailError:
        pass

    return RedirectResponse(
        url=f"/admin/takeover-requests?action_done=approved&code={row['code']}",
        status_code=303,
    )


@router.post("/bundle-takeover-requests/{req_id}/reject")
async def admin_reject_bundle_takeover(
    request: Request, req_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            """SELECT btr.id, btr.status, btr.requester_email, b.code, b.name AS bundle_name
               FROM bundle_takeover_requests btr JOIN bundles b ON btr.bundle_id=b.id
               WHERE btr.id=?""",
            (req_id,),
        ).fetchone()

        if not row or row["status"] != "pending":
            raise HTTPException(status_code=404)

        db.execute(
            "UPDATE bundle_takeover_requests SET status='rejected', resolved_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), req_id),
        )

    try:
        skicka_overlatelse_avslagen(
            row["requester_email"], row["code"], bundle_name=row["bundle_name"]
        )
    except MailError:
        pass

    return RedirectResponse(
        url=f"/admin/takeover-requests?action_done=rejected&code={row['code']}",
        status_code=303,
    )


# ─── Signerade e-postlänkar för överlåtelsehantering ─────────────────────────

@router.get("/takeover-action/{token}")
async def takeover_action_confirm(request: Request, token: str):
    """Visar bekräftelsesida — förhindrar att e-postförhandsvisning auto-utför åtgärden."""
    admin = get_admin_or_redirect(request)
    data = decode_takeover_action_token(token)
    if not data:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "user": admin, "message": "Länken är ogiltig eller har gått ut (7 dagar)."},
            status_code=400,
        )

    req_id = data["req_id"]
    action = data["action"]
    kind = data.get("kind", "link")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400)

    if kind == "bundle":
        with get_db() as db:
            row = db.execute(
                """SELECT btr.status, btr.requester_email,
                          b.code, b.name AS bundle_name
                   FROM bundle_takeover_requests btr JOIN bundles b ON btr.bundle_id=b.id
                   WHERE btr.id=?""",
                (req_id,),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        if row["status"] != "pending":
            return RedirectResponse(
                url=f"/admin/takeover-requests?already_handled={req_id}", status_code=303
            )
        subject = f"samlingen {row['bundle_name']} (svky.se/{row['code']})"
    else:
        with get_db() as db:
            row = db.execute(
                """SELECT tr.status, tr.requester_email, l.code
                   FROM takeover_requests tr JOIN links l ON tr.link_id=l.id
                   WHERE tr.id=?""",
                (req_id,),
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        if row["status"] != "pending":
            return RedirectResponse(
                url=f"/admin/takeover-requests?already_handled={req_id}", status_code=303
            )
        subject = f"kortlänken svky.se/{row['code']}"

    return templates.TemplateResponse(
        "admin/takeover_action_confirm.html",
        {
            "request": request,
            "user": admin,
            "token": token,
            "action": action,
            "kind": kind,
            "subject": subject,
            "requester_email": row["requester_email"],
        },
    )


@router.post("/takeover-action/{token}")
async def takeover_action(request: Request, token: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)

    data = decode_takeover_action_token(token)
    if not data:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Länken är ogiltig eller har gått ut (7 dagar)."},
            status_code=400,
        )

    req_id = data["req_id"]
    action = data["action"]
    kind = data.get("kind", "link")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400)

    now = datetime.utcnow().isoformat()

    if kind == "bundle":
        code, requester_email, bundle_name = _handle_bundle_takeover_action(
            req_id, action, now
        )
        if action == "approve":
            try:
                skicka_overlatelse_godkand(requester_email, code, BASE_URL, bundle_name=bundle_name)
            except MailError:
                pass
        else:
            try:
                skicka_overlatelse_avslagen(requester_email, code, bundle_name=bundle_name)
            except MailError:
                pass
    else:
        code, requester_email = _handle_link_takeover_action(req_id, action, now)
        if action == "approve":
            try:
                skicka_overlatelse_godkand(requester_email, code, BASE_URL)
            except MailError:
                pass
        else:
            try:
                skicka_overlatelse_avslagen(requester_email, code)
            except MailError:
                pass

    outcome = "approved" if action == "approve" else "rejected"
    return RedirectResponse(
        url=f"/admin/takeover-requests?action_done={outcome}&code={code}",
        status_code=303,
    )


def _handle_bundle_takeover_action(
    req_id: int, action: str, now: str
) -> tuple[str, str, str]:
    """Utför DB-ändringarna för en samlings-överlåtelse. Returnerar (code, email, bundle_name)."""
    with get_db() as db:
        row = db.execute(
            """SELECT btr.id, btr.status, btr.requester_email, btr.bundle_id,
                      b.code, b.name AS bundle_name
               FROM bundle_takeover_requests btr JOIN bundles b ON btr.bundle_id=b.id
               WHERE btr.id=?""",
            (req_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404)
        if row["status"] != "pending":
            raise HTTPException(
                status_code=303,
                headers={"Location": f"/admin/takeover-requests?already_handled={req_id}"},
            )

        if action == "approve":
            db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (row["requester_email"],))
            new_user = db.execute(
                "SELECT id FROM users WHERE email=?", (row["requester_email"],)
            ).fetchone()
            db.execute(
                "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (new_user["id"], row["bundle_id"]),
            )
            db.execute(
                "UPDATE bundle_takeover_requests SET status='approved', resolved_at=? WHERE id=?",
                (now, req_id),
            )
            db.execute(
                "INSERT INTO audit_log (action, detail) VALUES (?,?)",
                (
                    "bundle_takeover_approved",
                    f"bundle:{row['bundle_id']} via mail-länk till {row['requester_email']}",
                ),
            )
        else:
            db.execute(
                "UPDATE bundle_takeover_requests SET status='rejected', resolved_at=? WHERE id=?",
                (now, req_id),
            )

    return row["code"], row["requester_email"], row["bundle_name"]


def _handle_link_takeover_action(
    req_id: int, action: str, now: str
) -> tuple[str, str]:
    """Utför DB-ändringarna för en länk-överlåtelse. Returnerar (code, email)."""
    with get_db() as db:
        row = db.execute(
            """SELECT tr.id, tr.status, tr.requester_email, tr.link_id, l.code
               FROM takeover_requests tr JOIN links l ON tr.link_id=l.id
               WHERE tr.id=?""",
            (req_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404)
        if row["status"] != "pending":
            raise HTTPException(
                status_code=303,
                headers={"Location": f"/admin/takeover-requests?already_handled={req_id}"},
            )

        if action == "approve":
            db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (row["requester_email"],))
            new_user = db.execute(
                "SELECT id FROM users WHERE email=?", (row["requester_email"],)
            ).fetchone()
            db.execute(
                "UPDATE links SET owner_id=?, status=? WHERE id=?",
                (new_user["id"], LinkStatus.ACTIVE, row["link_id"]),
            )
            db.execute(
                "UPDATE takeover_requests SET status='approved', resolved_at=? WHERE id=?",
                (now, req_id),
            )
            db.execute(
                "INSERT INTO audit_log (action, link_id, detail) VALUES (?,?,?)",
                (
                    "takeover_approved",
                    row["link_id"],
                    f"via mail-länk till {row['requester_email']}",
                ),
            )
        else:
            db.execute(
                "UPDATE takeover_requests SET status='rejected', resolved_at=? WHERE id=?",
                (now, req_id),
            )

    return row["code"], row["requester_email"]
