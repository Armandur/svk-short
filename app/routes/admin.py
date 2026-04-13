from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
import secrets
from datetime import datetime, timedelta

from app.database import get_db
from app.auth import get_current_user, decode_takeover_action_token
from app.validation import validate_target_url, validate_code
from app.config import LinkStatus, BASE_URL
from app.csrf import validate_csrf_token
from app.mail import skicka_verifieringsmail, skicka_overlatelse_godkand, skicka_overlatelse_avslagen, MailError
from app.templating import templates

router = APIRouter(prefix="/admin")


def _get_admin_or_403(request: Request):
    user = get_current_user(request)
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def _pending_takeover_count(db) -> int:
    links = db.execute(
        "SELECT COUNT(*) FROM takeover_requests WHERE status='pending'"
    ).fetchone()[0]
    bundles = db.execute(
        "SELECT COUNT(*) FROM bundle_takeover_requests WHERE status='pending'"
    ).fetchone()[0]
    return links + bundles


@router.get("/links")
async def admin_links(
    request: Request,
    q: str = "",
    status_filter: str = "",
    page: int = 1,
    error: str = "",
    code: str = "",
):
    admin = _get_admin_or_403(request)
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

        if status_filter == "0":
            where_parts.append("l.status=0")
        elif status_filter == "1":
            where_parts.append("l.status=1")
        elif status_filter == "2":
            where_parts.append("l.status=2")
        elif status_filter == "3":
            where_parts.append("l.status=3")

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

        pending_takeovers = _pending_takeover_count(db)

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
            "pending_takeovers": pending_takeovers,
            "error": error,
            "error_code": code,
        },
    )


@router.get("/links/create")
async def admin_create_link_form(request: Request):
    admin = _get_admin_or_403(request)
    with get_db() as db:
        pending_takeovers = _pending_takeover_count(db)
    return templates.TemplateResponse(
        "admin/create_link.html",
        {
            "request": request,
            "user": admin,
            "pending_takeovers": pending_takeovers,
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
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

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
            pending_takeovers = _pending_takeover_count(db)
        return templates.TemplateResponse(
            "admin/create_link.html",
            {
                "request": request,
                "user": admin,
                "pending_takeovers": pending_takeovers,
                "errors": errors,
                "values": {"target_url": target_url, "code": code, "note": note},
            },
            status_code=422,
        )

    with get_db() as db:
        if not code:
            while True:
                code = secrets.token_hex(3)
                if not db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone():
                    break
        elif db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone():
            pending_takeovers = _pending_takeover_count(db)
            return templates.TemplateResponse(
                "admin/create_link.html",
                {
                    "request": request,
                    "user": admin,
                    "pending_takeovers": pending_takeovers,
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
    admin = _get_admin_or_403(request)

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

        pending_takeovers = _pending_takeover_count(db)

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
            "pending_takeovers": pending_takeovers,
        },
    )


@router.post("/links/{link_id}/toggle")
async def admin_toggle_link(request: Request, link_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

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
    request: Request, link_id: int, target_url: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

    error = validate_target_url(target_url, allow_external=True)
    if error:
        raise HTTPException(status_code=422, detail=error)

    with get_db() as db:
        db.execute(
            "UPDATE links SET target_url=? WHERE id=?", (target_url, link_id)
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
            ("admin_update_url", admin["id"], link_id, f"new url: {target_url}"),
        )

    return RedirectResponse(url=f"/admin/links/{link_id}", status_code=303)


@router.post("/links/{link_id}/resend-verification")
async def admin_resend_verification(request: Request, link_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

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
            expires_at = datetime.utcnow() + timedelta(hours=24)
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
            skicka_verifieringsmail(link["owner_email"], verify_url, link["code"], link["target_url"])
        except MailError:
            pass

    return RedirectResponse(url=f"/admin/links/{link_id}?resent=1", status_code=303)


@router.post("/links/{link_id}/transfer")
async def admin_transfer_link(
    request: Request, link_id: int, new_email: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)
    new_email = new_email.strip().lower()

    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (new_email,))
        new_user = db.execute(
            "SELECT id FROM users WHERE email=?", (new_email,)
        ).fetchone()
        old_owner = db.execute(
            "SELECT u.email FROM links l JOIN users u ON l.owner_id=u.id WHERE l.id=?",
            (link_id,),
        ).fetchone()
        old_email = old_owner["email"] if old_owner else "?"

        db.execute(
            "UPDATE links SET owner_id=? WHERE id=?", (new_user["id"], link_id)
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
            (
                "transfer",
                admin["id"],
                link_id,
                f"moved from {old_email} to {new_email}",
            ),
        )

    return RedirectResponse(url=f"/admin/links/{link_id}", status_code=303)


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
    _get_admin_or_403(request)

    from app.validation import validate_email
    email = email.strip().lower()
    err = validate_email(email, allow_any_domain=True)
    if err:
        import urllib.parse
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
        # Om användaren redan fanns, uppdatera flaggorna om de skickades med
        if allow_domain or allow_ext:
            db.execute(
                "UPDATE users SET allow_any_domain=?, allow_external_urls=? WHERE email=?",
                (allow_domain, allow_ext, email),
            )

    import urllib.parse
    return RedirectResponse(
        url="/admin/users?" + urllib.parse.urlencode({"created": email}),
        status_code=303,
    )


@router.post("/users/{user_id}/toggle-domain")
async def admin_toggle_domain(
    request: Request, user_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        row = db.execute(
            "SELECT allow_any_domain FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        new_val = 0 if row["allow_any_domain"] else 1
        db.execute(
            "UPDATE users SET allow_any_domain=? WHERE id=?", (new_val, user_id)
        )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-external-urls")
async def admin_toggle_external_urls(
    request: Request, user_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        row = db.execute(
            "SELECT allow_external_urls FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        new_val = 0 if row["allow_external_urls"] else 1
        db.execute(
            "UPDATE users SET allow_external_urls=? WHERE id=?", (new_val, user_id)
        )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/users")
async def admin_users(request: Request, q: str = ""):
    admin = _get_admin_or_403(request)

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

        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "user": admin,
            "users": [dict(r) for r in users],
            "stats": dict(stats),
            "q": q,
            "pending_takeovers": pending_takeovers,
        },
    )


@router.post("/users/{user_id}/transfer-all")
async def admin_transfer_all(
    request: Request, user_id: int, new_email: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)
    new_email = new_email.strip().lower()

    with get_db() as db:
        old_user = db.execute(
            "SELECT email FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not old_user:
            raise HTTPException(status_code=404)

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (new_email,))
        new_user = db.execute(
            "SELECT id FROM users WHERE email=?", (new_email,)
        ).fetchone()

        link_ids = db.execute(
            "SELECT id FROM links WHERE owner_id=?", (user_id,)
        ).fetchall()

        db.execute(
            "UPDATE links SET owner_id=? WHERE owner_id=?",
            (new_user["id"], user_id),
        )

        for link in link_ids:
            db.execute(
                "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
                (
                    "transfer",
                    admin["id"],
                    link["id"],
                    f"bulk move from {old_user['email']} to {new_email}",
                ),
            )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/login-link")
async def admin_create_login_link(
    request: Request, user_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

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

    import urllib.parse
    params = urllib.parse.urlencode({
        "new_login_link": f"{BASE_URL}/auth/{token}",
        "new_login_for": user_row["email"],
    })
    return RedirectResponse(url=f"/admin/users?{params}", status_code=303)


@router.get("/takeover-requests")
async def admin_takeover_requests(request: Request):
    admin = _get_admin_or_403(request)

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

        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/takeover_requests.html",
        {
            "request": request,
            "user": admin,
            "takeover_requests": [dict(r) for r in link_requests],
            "bundle_takeover_requests": [dict(r) for r in bundle_requests],
            "pending_takeovers": pending_takeovers,
        },
    )


@router.post("/takeover-requests/{req_id}/approve")
async def admin_approve_takeover(request: Request, req_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

    with get_db() as db:
        row = db.execute(
            """SELECT tr.id, tr.link_id, tr.requester_email, tr.status,
                      l.code
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

        db.execute(
            "UPDATE links SET owner_id=? WHERE id=?", (new_user["id"], row["link_id"])
        )
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
async def admin_reject_takeover(request: Request, req_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

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


@router.get("/stats")
async def admin_stats(request: Request):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        click_stats = db.execute(
            """SELECT date(clicked_at) AS dag, COUNT(*) AS antal
               FROM clicks
               GROUP BY dag ORDER BY dag DESC LIMIT 90"""
        ).fetchall()

        totals = db.execute(
            """SELECT
                COUNT(*) AS total_clicks,
                SUM(clicked_at >= datetime('now', '-7 days')) AS clicks_7d,
                SUM(clicked_at >= datetime('now', '-30 days')) AS clicks_30d
               FROM clicks"""
        ).fetchone()

        top_links = db.execute(
            """SELECT l.id, l.code, COUNT(c.id) AS antal
               FROM clicks c JOIN links l ON c.link_id = l.id
               WHERE c.clicked_at >= datetime('now', '-30 days')
               GROUP BY l.id ORDER BY antal DESC LIMIT 10"""
        ).fetchall()

        pv_stats = db.execute(
            """SELECT date(viewed_at) AS dag, COUNT(*) AS antal
               FROM page_views
               GROUP BY dag ORDER BY dag DESC LIMIT 90"""
        ).fetchall()

        pv_totals = db.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(viewed_at >= datetime('now', '-7 days')) AS last_7d,
                SUM(viewed_at >= datetime('now', '-30 days')) AS last_30d
               FROM page_views"""
        ).fetchone()

        pv_by_path = db.execute(
            """SELECT path, COUNT(*) AS antal
               FROM page_views
               WHERE viewed_at >= datetime('now', '-30 days')
               GROUP BY path ORDER BY antal DESC"""
        ).fetchall()

        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/stats.html",
        {
            "request": request,
            "user": admin,
            "click_stats": [dict(r) for r in click_stats],
            "totals": dict(totals),
            "top_links": [dict(r) for r in top_links],
            "pv_stats": [dict(r) for r in pv_stats],
            "pv_totals": dict(pv_totals),
            "pv_by_path": [dict(r) for r in pv_by_path],
            "pending_takeovers": pending_takeovers,
        },
    )


@router.get("/om")
async def admin_edit_om(request: Request):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        row = db.execute(
            "SELECT value FROM site_settings WHERE key='about_content'"
        ).fetchone()
        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/om_edit.html",
        {
            "request": request,
            "user": admin,
            "content": row["value"] if row else "",
            "pending_takeovers": pending_takeovers,
            "saved": request.query_params.get("saved") == "1",
            "page_title": "Om-sidan",
            "admin_path": "/admin/om",
            "public_path": "/om",
        },
    )


@router.post("/om")
async def admin_save_om(request: Request, content: str = Form(...), csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        db.execute(
            """INSERT INTO site_settings (key, value) VALUES ('about_content', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (content,),
        )

    return RedirectResponse(url="/admin/om?saved=1", status_code=303)


@router.get("/integritet")
async def admin_edit_integritet(request: Request):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        row = db.execute(
            "SELECT value FROM site_settings WHERE key='integritet_content'"
        ).fetchone()
        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/om_edit.html",
        {
            "request": request,
            "user": admin,
            "content": row["value"] if row else "",
            "pending_takeovers": pending_takeovers,
            "saved": request.query_params.get("saved") == "1",
            "page_title": "Integritetssidan",
            "admin_path": "/admin/integritet",
            "public_path": "/integritet",
        },
    )


@router.post("/integritet")
async def admin_save_integritet(request: Request, content: str = Form(...), csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        db.execute(
            """INSERT INTO site_settings (key, value) VALUES ('integritet_content', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (content,),
        )

    return RedirectResponse(url="/admin/integritet?saved=1", status_code=303)


@router.get("/snabblänkar")
async def admin_snabblänkar(request: Request, q: str = ""):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        featured = db.execute(
            """SELECT l.id, l.code, l.note, l.status,
                      l.featured_title, l.featured_icon, l.featured_sort,
                      u.email AS owner_email
               FROM links l LEFT JOIN users u ON l.owner_id=u.id
               WHERE l.is_featured=1
               ORDER BY l.featured_sort, l.created_at""",
        ).fetchall()

        search_results = []
        if q:
            search_results = db.execute(
                """SELECT l.id, l.code, l.note, l.status, l.is_featured,
                          u.email AS owner_email
                   FROM links l LEFT JOIN users u ON l.owner_id=u.id
                   WHERE (l.code LIKE ? OR l.note LIKE ?) AND l.status=1
                   ORDER BY l.created_at DESC LIMIT 20""",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()

        intro_row = db.execute(
            "SELECT value FROM site_settings WHERE key='snabblänkar_intro'"
        ).fetchone()
        intro_md = intro_row["value"] if intro_row else ""
        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/snabblänkar.html",
        {
            "request": request,
            "user": admin,
            "featured": [dict(r) for r in featured],
            "search_results": [dict(r) for r in search_results],
            "q": q,
            "intro_md": intro_md,
            "saved": request.query_params.get("saved") == "1",
            "pending_takeovers": pending_takeovers,
        },
    )


@router.post("/snabblänkar/update-intro")
async def admin_snabblänkar_update_intro(
    request: Request,
    intro_md: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO site_settings (key, value) VALUES ('snabblänkar_intro', ?)",
            (intro_md.strip(),),
        )

    return RedirectResponse(url="/admin/snabblänkar?saved=1", status_code=303)


@router.post("/snabblänkar/add")
async def admin_snabblänkar_add(
    request: Request,
    link_id: int = Form(...),
    featured_title: str = Form(""),
    featured_icon: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        link = db.execute("SELECT id FROM links WHERE id=? AND status=1", (link_id,)).fetchone()
        if not link:
            raise HTTPException(status_code=404)

        max_sort = db.execute(
            "SELECT COALESCE(MAX(featured_sort), 0) FROM links WHERE is_featured=1"
        ).fetchone()[0]

        db.execute(
            """UPDATE links
               SET is_featured=1,
                   featured_title=CASE WHEN ?='' THEN NULL ELSE ? END,
                   featured_icon=CASE WHEN ?='' THEN NULL ELSE ? END,
                   featured_sort=?
               WHERE id=?""",
            (featured_title, featured_title, featured_icon, featured_icon, max_sort + 1, link_id),
        )

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.post("/snabblänkar/{link_id}/remove")
async def admin_snabblänkar_remove(
    request: Request, link_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        db.execute(
            "UPDATE links SET is_featured=0, featured_sort=0 WHERE id=?", (link_id,)
        )

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.post("/snabblänkar/{link_id}/update-display")
async def admin_snabblänkar_update_display(
    request: Request,
    link_id: int,
    featured_title: str = Form(""),
    featured_icon: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)

    with get_db() as db:
        db.execute(
            """UPDATE links
               SET featured_title=CASE WHEN ?='' THEN NULL ELSE ? END,
                   featured_icon=CASE WHEN ?='' THEN NULL ELSE ? END
               WHERE id=? AND is_featured=1""",
            (featured_title, featured_title, featured_icon, featured_icon, link_id),
        )

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.post("/snabblänkar/{link_id}/move")
async def admin_snabblänkar_move(
    request: Request, link_id: int, direction: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    _get_admin_or_403(request)
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400)

    with get_db() as db:
        featured = db.execute(
            "SELECT id, featured_sort FROM links WHERE is_featured=1 ORDER BY featured_sort, id"
        ).fetchall()
        featured = [dict(r) for r in featured]

        idx = next((i for i, r in enumerate(featured) if r["id"] == link_id), None)
        if idx is None:
            raise HTTPException(status_code=404)

        swap_idx = idx - 1 if direction == "up" else idx + 1
        if swap_idx < 0 or swap_idx >= len(featured):
            return RedirectResponse(url="/admin/snabblänkar", status_code=303)

        # Byt sort_order med grannen
        a, b = featured[idx], featured[swap_idx]
        new_sort_a = swap_idx
        new_sort_b = idx
        db.execute("UPDATE links SET featured_sort=? WHERE id=?", (new_sort_a, a["id"]))
        db.execute("UPDATE links SET featured_sort=? WHERE id=?", (new_sort_b, b["id"]))

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.get("/takeover-action/{token}")
async def takeover_action_confirm(request: Request, token: str):
    """Show a confirmation page — prevents email pre-fetch from auto-executing."""
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

    # Fetch just enough info to show the confirmation page
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
                url=f"/admin/takeover-requests?already_handled={req_id}",
                status_code=303,
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
                url=f"/admin/takeover-requests?already_handled={req_id}",
                status_code=303,
            )
        subject = f"kortlänken svky.se/{row['code']}"

    return templates.TemplateResponse(
        "admin/takeover_action_confirm.html",
        {
            "request": request,
            "token": token,
            "action": action,
            "kind": kind,
            "subject": subject,
            "requester_email": row["requester_email"],
        },
    )


@router.post("/takeover-action/{token}")
async def takeover_action(request: Request, token: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
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
                return RedirectResponse(
                    url=f"/admin/takeover-requests?already_handled={req_id}",
                    status_code=303,
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
                    ("bundle_takeover_approved", f"bundle:{row['bundle_id']} via mail-länk till {row['requester_email']}"),
                )
            else:
                db.execute(
                    "UPDATE bundle_takeover_requests SET status='rejected', resolved_at=? WHERE id=?",
                    (now, req_id),
                )

        code = row["code"]
        bundle_name = row["bundle_name"]
        if action == "approve":
            try:
                skicka_overlatelse_godkand(row["requester_email"], code, BASE_URL, bundle_name=bundle_name)
            except MailError:
                pass
        else:
            try:
                skicka_overlatelse_avslagen(row["requester_email"], code, bundle_name=bundle_name)
            except MailError:
                pass

    else:
        with get_db() as db:
            row = db.execute(
                """SELECT tr.id, tr.status, tr.requester_email, tr.link_id,
                          l.code
                   FROM takeover_requests tr JOIN links l ON tr.link_id=l.id
                   WHERE tr.id=?""",
                (req_id,),
            ).fetchone()

            if not row:
                raise HTTPException(status_code=404)

            if row["status"] != "pending":
                return RedirectResponse(
                    url=f"/admin/takeover-requests?already_handled={req_id}",
                    status_code=303,
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
                    ("takeover_approved", row["link_id"], f"via mail-länk till {row['requester_email']}"),
                )
            else:
                db.execute(
                    "UPDATE takeover_requests SET status='rejected', resolved_at=? WHERE id=?",
                    (now, req_id),
                )

        code = row["code"]
        if action == "approve":
            try:
                skicka_overlatelse_godkand(row["requester_email"], code, BASE_URL)
            except MailError:
                pass
        else:
            try:
                skicka_overlatelse_avslagen(row["requester_email"], code)
            except MailError:
                pass

    outcome = "approved" if action == "approve" else "rejected"
    return RedirectResponse(
        url=f"/admin/takeover-requests?action_done={outcome}&code={code}",
        status_code=303,
    )


@router.post("/bundle-takeover-requests/{req_id}/approve")
async def admin_approve_bundle_takeover(request: Request, req_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

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
            ("bundle_takeover_approved", admin["id"],
             f"bundle:{row['bundle_id']} överlåtet till {row['requester_email']}"),
        )

    try:
        skicka_overlatelse_godkand(row["requester_email"], row["code"], BASE_URL, bundle_name=row["bundle_name"])
    except MailError:
        pass

    return RedirectResponse(
        url=f"/admin/takeover-requests?action_done=approved&code={row['code']}",
        status_code=303,
    )


@router.post("/bundle-takeover-requests/{req_id}/reject")
async def admin_reject_bundle_takeover(request: Request, req_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

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
        skicka_overlatelse_avslagen(row["requester_email"], row["code"], bundle_name=row["bundle_name"])
    except MailError:
        pass

    return RedirectResponse(
        url=f"/admin/takeover-requests?action_done=rejected&code={row['code']}",
        status_code=303,
    )


# ─── Admin: Samlingar (bundles) ───────────────────────────────────────────────

@router.get("/bundles")
async def admin_bundles(request: Request, q: str = "", status_filter: str = ""):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        where_parts = []
        params: list = []

        if q:
            where_parts.append("(b.code LIKE ? OR b.name LIKE ? OR u.email LIKE ?)")
            like = f"%{q}%"
            params += [like, like, like]
        if status_filter == "1":
            where_parts.append("b.status=1")
        elif status_filter == "off":
            where_parts.append("b.status!=1")

        where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        bundles = db.execute(
            f"""SELECT b.id, b.code, b.name, b.description, b.theme, b.status,
                       b.created_at, b.updated_at, u.email AS owner_email,
                       (SELECT COUNT(*) FROM bundle_items WHERE bundle_id=b.id) AS item_count
                FROM bundles b LEFT JOIN users u ON b.owner_id=u.id
                {where}
                ORDER BY b.created_at DESC""",
            params,
        ).fetchall()

        stats = db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(status=1) AS active,
                      SUM(status!=1) AS disabled,
                      (SELECT COUNT(*) FROM bundle_items) AS total_items
               FROM bundles"""
        ).fetchone()

        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/bundles.html",
        {
            "request": request, "user": admin,
            "bundles": [dict(r) for r in bundles],
            "stats": dict(stats),
            "q": q, "status_filter": status_filter,
            "pending_takeovers": pending_takeovers,
        },
    )


@router.get("/bundles/{bundle_id}")
async def admin_bundle_detail(request: Request, bundle_id: int):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        bundle = db.execute(
            """SELECT b.*, u.email AS owner_email
               FROM bundles b LEFT JOIN users u ON b.owner_id=u.id
               WHERE b.id=?""",
            (bundle_id,),
        ).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)

        sections = [dict(r) for r in db.execute(
            "SELECT * FROM bundle_sections WHERE bundle_id=? ORDER BY sort_order, id",
            (bundle_id,),
        ).fetchall()]
        items = [dict(r) for r in db.execute(
            "SELECT * FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id",
            (bundle_id,),
        ).fetchall()]
        audit = [dict(r) for r in db.execute(
            """SELECT a.action, a.detail, a.created_at, u.email AS actor_email
               FROM audit_log a LEFT JOIN users u ON a.actor_id=u.id
               WHERE a.detail LIKE ?
               ORDER BY a.created_at DESC LIMIT 50""",
            (f"%bundle:{bundle_id}%",),
        ).fetchall()]
        # Fetch any shortlink with same code (typically the link that was converted)
        assoc_link = db.execute(
            "SELECT id, status, target_url FROM links WHERE code=?",
            (bundle["code"],),
        ).fetchone()
        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/bundle_detail.html",
        {
            "request": request, "user": admin,
            "bundle": dict(bundle),
            "sections": sections, "items": items, "audit": audit,
            "assoc_link": dict(assoc_link) if assoc_link else None,
            "pending_takeovers": pending_takeovers,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/bundles/{bundle_id}/update")
async def admin_update_bundle(
    request: Request, bundle_id: int,
    name: str = Form(...),
    description: str = Form(""),
    theme: str = Form("rich"),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)
    theme = theme if theme in ("rich", "compact") else "rich"

    with get_db() as db:
        db.execute(
            """UPDATE bundles SET name=?, description=?, theme=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (name.strip(), description.strip() or None, theme, bundle_id),
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            ("admin_bundle_update", admin["id"], f"bundle:{bundle_id} namn/beskrivning/tema uppdaterat"),
        )

    return RedirectResponse(url=f"/admin/bundles/{bundle_id}?saved=1", status_code=303)


@router.post("/bundles/{bundle_id}/disable")
async def admin_disable_bundle(
    request: Request, bundle_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

    with get_db() as db:
        row = db.execute("SELECT status FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        new_status = 1 if row["status"] != 1 else 2
        action = "admin_bundle_reactivate" if new_status == 1 else "admin_bundle_disable"
        db.execute(
            "UPDATE bundles SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_status, bundle_id),
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            (action, admin["id"], f"bundle:{bundle_id}"),
        )

    return RedirectResponse(url=f"/admin/bundles/{bundle_id}", status_code=303)


@router.post("/bundles/{bundle_id}/transfer")
async def admin_transfer_bundle(
    request: Request, bundle_id: int,
    new_email: str = Form(...),
    include_link: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)
    new_email = new_email.strip().lower()

    with get_db() as db:
        bundle = db.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)
        old_owner = db.execute(
            "SELECT email FROM users WHERE id=?", (bundle["owner_id"],)
        ).fetchone()
        old_email = old_owner["email"] if old_owner else "?"

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (new_email,))
        new_user = db.execute("SELECT id FROM users WHERE email=?", (new_email,)).fetchone()
        db.execute(
            "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_user["id"], bundle_id),
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            (
                "admin_bundle_transfer",
                admin["id"],
                f"bundle:{bundle_id} överflytt från {old_email} till {new_email}",
            ),
        )
        if include_link:
            old_link = db.execute(
                "SELECT id FROM links WHERE code=?", (bundle["code"],)
            ).fetchone()
            if old_link:
                db.execute(
                    "UPDATE links SET owner_id=? WHERE id=?",
                    (new_user["id"], old_link["id"]),
                )
                db.execute(
                    "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
                    (
                        "admin_link_transfer",
                        admin["id"],
                        f"link kod={bundle['code']} överflytt från {old_email} till {new_email} (med samling)",
                    ),
                )

    return RedirectResponse(url=f"/admin/bundles/{bundle_id}", status_code=303)


@router.post("/bundles/{bundle_id}/konvertera-till-lankar")
async def admin_konvertera_bundle_till_lankar(
    request: Request, bundle_id: int,
    target_url: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

    target_url = target_url.strip()
    url_error = validate_target_url(target_url, allow_external=True)
    if url_error:
        raise HTTPException(status_code=422, detail=url_error)

    with get_db() as db:
        bundle = db.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)
        code = bundle["code"]

        existing_active = db.execute(
            "SELECT id FROM links WHERE code=? AND status != 3", (code,)
        ).fetchone()
        if existing_active:
            raise HTTPException(status_code=409, detail="En aktiv kortlänk med den koden finns redan.")

        old_link = db.execute(
            "SELECT id FROM links WHERE code=? AND status=3", (code,)
        ).fetchone()
        if old_link:
            db.execute(
                "UPDATE links SET target_url=?, owner_id=?, status=1 WHERE id=?",
                (target_url, bundle["owner_id"], old_link["id"]),
            )
        else:
            db.execute(
                "INSERT INTO links (code, target_url, owner_id, status) VALUES (?,?,?,1)",
                (code, target_url, bundle["owner_id"]),
            )
        db.execute(
            "UPDATE bundles SET status=2, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (bundle_id,),
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            (
                "admin_bundle_to_link",
                admin["id"],
                f"bundle:{bundle_id} (kod={code}) konverterad till kortlänk → {target_url}",
            ),
        )

    return RedirectResponse(url=f"/admin/links?q={code}", status_code=303)
