"""Admin-routes för användarhantering: lista, skapa, rättigheter, massöverlåtelse."""

import secrets
import urllib.parse
from datetime import datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import BASE_URL
from app.csrf import validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import templates
from app.validation import validate_email

from .helpers import pending_takeover_count

router = APIRouter()


@router.get("/users")
async def admin_users(request: Request, q: str = ""):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        where = "WHERE u.email LIKE ?" if q else ""
        params = [f"%{q}%"] if q else []

        users = db.execute(
            f"""SELECT u.id, u.email, u.is_admin, u.allow_any_domain, u.allow_external_urls,
                       u.created_at, u.last_login,
                       COUNT(l.id) AS total_links,
                       SUM(l.status=1) AS active_links,
                       SUM(l.status=0) AS pending_links,
                       SUM(l.status IN (2,3)) AS disabled_links
                FROM users u LEFT JOIN links l ON l.owner_id=u.id
                {where}
                GROUP BY u.id ORDER BY u.created_at DESC""",
            params,
        ).fetchall()

        stats = db.execute(
            """SELECT COUNT(*) AS total_users,
                      SUM(is_admin) AS total_admins,
                      (SELECT COUNT(*) FROM links) AS total_links
               FROM users"""
        ).fetchone()

        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "user": admin,
            "users": [dict(r) for r in users],
            "stats": dict(stats),
            "q": q,
            "pending_takeovers": takeovers,
        },
    )


@router.post("/users/create")
async def admin_create_user(
    request: Request,
    email: str = Form(...),
    allow_any_domain: str = Form(""),
    allow_external_urls: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    email = email.strip().lower()
    err = validate_email(email, allow_any_domain=True)
    if err:
        return RedirectResponse(
            url="/admin/users?" + urllib.parse.urlencode({"create_error": err}),
            status_code=303,
        )

    allow_domain = 1 if allow_any_domain else 0
    allow_ext = 1 if allow_external_urls else 0
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO users (email, allow_any_domain, allow_external_urls) VALUES (?,?,?)",
            (email, allow_domain, allow_ext),
        )
        if allow_domain or allow_ext:
            db.execute(
                "UPDATE users SET allow_any_domain=?, allow_external_urls=? WHERE email=?",
                (allow_domain, allow_ext, email),
            )

    return RedirectResponse(
        url="/admin/users?" + urllib.parse.urlencode({"created": email}),
        status_code=303,
    )


@router.post("/users/{user_id}/toggle-domain")
async def admin_toggle_domain(request: Request, user_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT allow_any_domain FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        db.execute(
            "UPDATE users SET allow_any_domain=? WHERE id=?",
            (0 if row["allow_any_domain"] else 1, user_id),
        )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-external-urls")
async def admin_toggle_external_urls(
    request: Request, user_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT allow_external_urls FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        db.execute(
            "UPDATE users SET allow_external_urls=? WHERE id=?",
            (0 if row["allow_external_urls"] else 1, user_id),
        )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/transfer-all")
async def admin_transfer_all(
    request: Request,
    user_id: int,
    new_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)
    new_email = new_email.strip().lower()

    with get_db() as db:
        old_user = db.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
        if not old_user:
            raise HTTPException(status_code=404)

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (new_email,))
        new_user = db.execute("SELECT id FROM users WHERE email=?", (new_email,)).fetchone()

        link_rows = db.execute(
            "SELECT id FROM links WHERE owner_id=?", (user_id,)
        ).fetchall()
        bundle_rows = db.execute(
            "SELECT id, code FROM bundles WHERE owner_id=?", (user_id,)
        ).fetchall()

        db.execute(
            "UPDATE links SET owner_id=? WHERE owner_id=?", (new_user["id"], user_id)
        )
        db.execute(
            "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE owner_id=?",
            (new_user["id"], user_id),
        )

        for link in link_rows:
            db.execute(
                "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
                (
                    "transfer",
                    admin["id"],
                    link["id"],
                    f"bulk move from {old_user['email']} to {new_email}",
                ),
            )
        for bundle in bundle_rows:
            db.execute(
                "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
                (
                    "admin_bundle_transfer",
                    admin["id"],
                    f"bundle:{bundle['id']} (kod={bundle['code']}) bulk-överflytt från {old_user['email']} till {new_email}",
                ),
            )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/login-link")
async def admin_create_login_link(
    request: Request, user_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        user_row = db.execute(
            "SELECT id, email FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404)

        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        db.execute(
            "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) VALUES (?,?,NULL,?,?)",
            (token, user_row["id"], "login", expires_at.isoformat()),
        )

    params = urllib.parse.urlencode({
        "new_login_link": f"{BASE_URL}/auth/{token}",
        "new_login_for": user_row["email"],
    })
    return RedirectResponse(url=f"/admin/users?{params}", status_code=303)
