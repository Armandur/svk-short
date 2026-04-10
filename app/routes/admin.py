from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime

from app.database import get_db
from app.auth import get_current_user
from app.validation import validate_target_url
from app.config import LinkStatus, BASE_URL
from app.csrf import validate_csrf_token
from app.mail import skicka_overdragelse_godkand, skicka_overdragelse_avslagen, MailError

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


def _get_admin_or_403(request: Request):
    user = get_current_user(request)
    if not user or not user["is_admin"]:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def _pending_takeover_count(db) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM takeover_requests WHERE status='pending'"
    ).fetchone()[0]


@router.get("/links")
async def admin_links(
    request: Request,
    q: str = "",
    status_filter: str = "",
    page: int = 1,
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
                       (SELECT COUNT(*) FROM clicks WHERE link_id=l.id) AS click_count
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
        },
    )


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
        row = db.execute("SELECT status FROM links WHERE id=?", (link_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404)

        if row["status"] in (LinkStatus.ACTIVE, LinkStatus.PENDING):
            new_status = LinkStatus.DISABLED_ADMIN
            action = "admin_deactivate"
        else:
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

    error = validate_target_url(target_url)
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


@router.post("/links/{link_id}/transfer")
async def admin_transfer_link(
    request: Request, link_id: int, new_email: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    admin = _get_admin_or_403(request)

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


@router.get("/users")
async def admin_users(request: Request, q: str = ""):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        where = "WHERE u.email LIKE ?" if q else ""
        params = [f"%{q}%"] if q else []

        users = db.execute(
            f"""SELECT u.id, u.email, u.is_admin, u.created_at, u.last_login,
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


@router.get("/takeover-requests")
async def admin_takeover_requests(request: Request):
    admin = _get_admin_or_403(request)

    with get_db() as db:
        requests_rows = db.execute(
            """SELECT tr.id, tr.requester_email, tr.reason, tr.status,
                      tr.created_at, tr.resolved_at,
                      l.code, l.target_url, l.id AS link_id,
                      u.email AS owner_email
               FROM takeover_requests tr
               JOIN links l ON tr.link_id = l.id
               LEFT JOIN users u ON l.owner_id = u.id
               ORDER BY tr.status='pending' DESC, tr.created_at DESC""",
        ).fetchall()

        pending_takeovers = _pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/takeover_requests.html",
        {
            "request": request,
            "user": admin,
            "takeover_requests": [dict(r) for r in requests_rows],
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
        skicka_overdragelse_godkand(row["requester_email"], row["code"], BASE_URL)
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
        skicka_overdragelse_avslagen(row["requester_email"], row["code"])
    except MailError:
        pass

    return RedirectResponse(url="/admin/takeover-requests", status_code=303)
