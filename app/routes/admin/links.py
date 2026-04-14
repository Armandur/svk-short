"""Admin-routes för länkhantering: lista, skapa, visa, aktivera/deaktivera, uppdatera."""

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.code_generator import generate_unique_code
from app.config import BASE_URL, LinkStatus
from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.mail import MailError, skicka_verifieringsmail
from app.ownership import move_twin_rows
from app.templating import templates
from app.validation import validate_code, validate_target_url

from .helpers import pending_takeover_count

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/links")
async def admin_links(
    request: Request,
    q: str = "",
    status_filter: str = "",
    page: int = 1,
    error: str = "",
    code: str = "",
):
    admin = get_admin_or_redirect(request)
    per_page = 20
    offset = (page - 1) * per_page

    with get_db() as db:
        where_parts = []
        params: list = []

        if q:
            where_parts.append(
                "(l.code LIKE ? OR l.target_url LIKE ? OR u.email LIKE ?)"
            )
            like = f"%{q}%"
            params += [like, like, like]

        if status_filter in ("0", "1", "2", "3"):
            where_parts.append(f"l.status={int(status_filter)}")

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        total = db.execute(
            f"""SELECT COUNT(*) FROM links l
                LEFT JOIN users u ON l.owner_id=u.id {where}""",
            params,
        ).fetchone()[0]

        links = db.execute(
            f"""SELECT l.id, l.code, l.target_url, l.status, l.note,
                       l.created_at, l.last_used_at, u.email AS owner_email,
                       (SELECT COUNT(*) FROM clicks WHERE link_id=l.id) AS click_count,
                       (SELECT b.id FROM bundles b WHERE b.code=l.code AND b.status=1 LIMIT 1) AS active_bundle_id
                FROM links l LEFT JOIN users u ON l.owner_id=u.id
                {where}
                ORDER BY l.created_at DESC
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        ).fetchall()

        stats = db.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(status=1) AS active,
                SUM(status=0) AS pending,
                SUM(status IN (2,3)) AS disabled,
                (SELECT COUNT(*) FROM clicks) AS total_clicks
               FROM links"""
        ).fetchone()

        takeovers = pending_takeover_count(db)

    total_pages = max(1, (total + per_page - 1) // per_page)

    return templates.TemplateResponse(
        "admin/links.html",
        {
            "request": request,
            "user": admin,
            "links": [dict(r) for r in links],
            "stats": dict(stats),
            "q": q,
            "status_filter": status_filter,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "per_page": per_page,
            "offset": offset,
            "pending_takeovers": takeovers,
            "error": error,
            "error_code": code,
        },
    )


@router.get("/links/create")
async def admin_create_link_form(request: Request):
    admin = get_admin_or_redirect(request)
    with get_db() as db:
        takeovers = pending_takeover_count(db)
    return templates.TemplateResponse(
        "admin/create_link.html",
        {
            "request": request,
            "user": admin,
            "pending_takeovers": takeovers,
            "errors": {},
            "values": {},
        },
    )


@router.post("/links/create")
async def admin_create_link(
    request: Request,
    target_url: str = Form(...),
    code: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    errors = {}

    url_error = validate_target_url(target_url, allow_external=True)
    if url_error:
        errors["target_url"] = url_error

    code = code.strip().lower()
    if code:
        code_error = validate_code(code)
        if code_error:
            errors["code"] = code_error

    if errors:
        with get_db() as db:
            takeovers = pending_takeover_count(db)
        return templates.TemplateResponse(
            "admin/create_link.html",
            {
                "request": request,
                "user": admin,
                "pending_takeovers": takeovers,
                "errors": errors,
                "values": {"target_url": target_url, "code": code, "note": note},
            },
            status_code=422,
        )

    with get_db() as db:
        if not code:
            code = generate_unique_code(db)
        elif db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone():
            takeovers = pending_takeover_count(db)
            return templates.TemplateResponse(
                "admin/create_link.html",
                {
                    "request": request,
                    "user": admin,
                    "pending_takeovers": takeovers,
                    "errors": {"code": f"Koden '{code}' är redan tagen."},
                    "values": {"target_url": target_url, "code": code, "note": note},
                },
                status_code=422,
            )

        db.execute(
            "INSERT INTO links (code, target_url, owner_id, status, note) VALUES (?,?,?,?,?)",
            (code, target_url, admin["id"], LinkStatus.ACTIVE, note.strip() or None),
        )
        link_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        db.execute(
            "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
            ("admin_create", admin["id"], link_id, f"skapad av admin med mål: {target_url}"),
        )

    return RedirectResponse(url=f"/admin/links/{link_id}?created=1", status_code=303)


@router.get("/links/{link_id}")
async def admin_link_detail(request: Request, link_id: int):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        link = db.execute(
            """SELECT l.*, u.email AS owner_email
               FROM links l LEFT JOIN users u ON l.owner_id=u.id
               WHERE l.id=?""",
            (link_id,),
        ).fetchone()

        if not link:
            raise HTTPException(status_code=404)

        click_stats = db.execute(
            """SELECT date(clicked_at) AS dag, COUNT(*) AS antal
               FROM clicks WHERE link_id=?
               GROUP BY dag ORDER BY dag DESC LIMIT 90""",
            (link_id,),
        ).fetchall()

        total_clicks = db.execute(
            "SELECT COUNT(*) FROM clicks WHERE link_id=?", (link_id,)
        ).fetchone()[0]

        clicks_7d = db.execute(
            """SELECT COUNT(*) FROM clicks WHERE link_id=?
               AND clicked_at >= datetime('now', '-7 days')""",
            (link_id,),
        ).fetchone()[0]

        audit = db.execute(
            """SELECT a.action, a.detail, a.created_at, u.email AS actor_email
               FROM audit_log a LEFT JOIN users u ON a.actor_id=u.id
               WHERE a.link_id=?
               ORDER BY a.created_at DESC""",
            (link_id,),
        ).fetchall()

        link_takeovers = db.execute(
            """SELECT id, requester_email, reason, status, created_at
               FROM takeover_requests WHERE link_id=? ORDER BY created_at DESC""",
            (link_id,),
        ).fetchall()

        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/link_detail.html",
        {
            "request": request,
            "user": admin,
            "link": dict(link),
            "click_stats": [dict(r) for r in click_stats],
            "total_clicks": total_clicks,
            "clicks_7d": clicks_7d,
            "audit": [dict(r) for r in audit],
            "link_takeovers": [dict(r) for r in link_takeovers],
            "pending_takeovers": takeovers,
        },
    )


@router.post("/links/{link_id}/toggle")
async def admin_toggle_link(request: Request, link_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute("SELECT status, code FROM links WHERE id=?", (link_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)

        if row["status"] in (LinkStatus.ACTIVE, LinkStatus.PENDING):
            new_status = LinkStatus.DISABLED_ADMIN
            action = "admin_deactivate"
        else:
            # Blockera återaktivering om en aktiv samling med samma kod finns
            active_bundle = db.execute(
                "SELECT id FROM bundles WHERE code=? AND status=1",
                (row["code"],),
            ).fetchone()
            if active_bundle:
                return RedirectResponse(
                    url=f"/admin/links?error=converted_bundle&code={row['code']}",
                    status_code=303,
                )
            new_status = LinkStatus.ACTIVE
            action = "admin_reactivate"

        db.execute("UPDATE links SET status=? WHERE id=?", (new_status, link_id))
        db.execute(
            "INSERT INTO audit_log (action, actor_id, link_id) VALUES (?,?,?)",
            (action, admin["id"], link_id),
        )

    return RedirectResponse(url="/admin/links", status_code=303)


@router.post("/links/{link_id}/update")
async def admin_update_link(
    request: Request,
    link_id: int,
    target_url: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    error = validate_target_url(target_url, allow_external=True)
    if error:
        raise HTTPException(status_code=422, detail=error)

    with get_db() as db:
        db.execute("UPDATE links SET target_url=? WHERE id=?", (target_url, link_id))
        db.execute(
            "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
            ("admin_update_url", admin["id"], link_id, f"new url: {target_url}"),
        )

    return RedirectResponse(url=f"/admin/links/{link_id}", status_code=303)


@router.post("/links/{link_id}/resend-verification")
async def admin_resend_verification(request: Request, link_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        link = db.execute(
            """SELECT l.code, l.target_url, u.email AS owner_email
               FROM links l LEFT JOIN users u ON l.owner_id=u.id
               WHERE l.id=? AND l.status=0""",
            (link_id,),
        ).fetchone()
        if not link:
            raise HTTPException(status_code=404)

        existing = db.execute(
            """SELECT token FROM tokens
               WHERE link_id=? AND purpose='verify' AND used_at IS NULL
                 AND expires_at > datetime('now')
               ORDER BY expires_at DESC LIMIT 1""",
            (link_id,),
        ).fetchone()

        if existing:
            token = existing["token"]
        else:
            token = secrets.token_hex(32)
            expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=24)
            user_row = db.execute(
                "SELECT id FROM users WHERE email=?", (link["owner_email"],)
            ).fetchone()
            db.execute(
                "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) VALUES (?,?,?,?,?)",
                (token, user_row["id"], link_id, "verify", expires_at.isoformat()),
            )

    if link["owner_email"]:
        verify_url = f"{BASE_URL}/verify/{token}"
        try:
            skicka_verifieringsmail(
                link["owner_email"], verify_url, link["code"], link["target_url"]
            )
        except MailError:
            log.exception("MailError")

    return RedirectResponse(url=f"/admin/links/{link_id}?resent=1", status_code=303)


@router.post("/links/{link_id}/transfer")
async def admin_transfer_link(
    request: Request,
    link_id: int,
    new_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)
    new_email = new_email.strip().lower()

    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (new_email,))
        new_user = db.execute(
            "SELECT id FROM users WHERE email=?", (new_email,)
        ).fetchone()
        link_row = db.execute(
            """SELECT l.code, l.owner_id, u.email AS owner_email
               FROM links l LEFT JOIN users u ON l.owner_id=u.id
               WHERE l.id=?""",
            (link_id,),
        ).fetchone()
        if not link_row:
            raise HTTPException(status_code=404)
        old_email = link_row["owner_email"] or "?"
        old_owner_id = link_row["owner_id"]

        db.execute("UPDATE links SET owner_id=? WHERE id=?", (new_user["id"], link_id))
        db.execute(
            "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
            ("transfer", admin["id"], link_id, f"moved from {old_email} to {new_email}"),
        )
        moved_twin = move_twin_rows(db, link_row["code"], old_owner_id, new_user["id"])
        if moved_twin:
            db.execute(
                "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
                (
                    "transfer_twin",
                    admin["id"],
                    link_id,
                    f"tvilling flyttad: {', '.join(moved_twin)} från {old_email} till {new_email}",
                ),
            )

    return RedirectResponse(url=f"/admin/links/{link_id}", status_code=303)
