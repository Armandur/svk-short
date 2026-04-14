"""Admin-routes för samlingshantering (bundles): lista, detalj, inaktivera, överlåt."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.ownership import move_twin_rows
from app.templating import templates
from app.validation import validate_target_url

from .helpers import pending_takeover_count

router = APIRouter()


@router.get("/bundles")
async def admin_bundles(request: Request, q: str = "", status_filter: str = ""):
    admin = get_admin_or_redirect(request)

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

        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/bundles.html",
        {
            "request": request,
            "user": admin,
            "bundles": [dict(r) for r in bundles],
            "stats": dict(stats),
            "q": q,
            "status_filter": status_filter,
            "pending_takeovers": takeovers,
        },
    )


@router.get("/bundles/{bundle_id}")
async def admin_bundle_detail(request: Request, bundle_id: int):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        bundle = db.execute(
            """SELECT b.*, u.email AS owner_email
               FROM bundles b LEFT JOIN users u ON b.owner_id=u.id
               WHERE b.id=?""",
            (bundle_id,),
        ).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)

        sections = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM bundle_sections WHERE bundle_id=? ORDER BY sort_order, id",
                (bundle_id,),
            ).fetchall()
        ]
        items = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id",
                (bundle_id,),
            ).fetchall()
        ]
        audit = [
            dict(r)
            for r in db.execute(
                """SELECT a.action, a.detail, a.created_at, u.email AS actor_email
                   FROM audit_log a LEFT JOIN users u ON a.actor_id=u.id
                   WHERE a.detail LIKE ?
                   ORDER BY a.created_at DESC LIMIT 50""",
                (f"%bundle:{bundle_id}%",),
            ).fetchall()
        ]
        assoc_link = db.execute(
            "SELECT id, status, target_url FROM links WHERE code=?",
            (bundle["code"],),
        ).fetchone()
        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/bundle_detail.html",
        {
            "request": request,
            "user": admin,
            "bundle": dict(bundle),
            "sections": sections,
            "items": items,
            "audit": audit,
            "assoc_link": dict(assoc_link) if assoc_link else None,
            "pending_takeovers": takeovers,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/bundles/{bundle_id}/update")
async def admin_update_bundle(
    request: Request,
    bundle_id: int,
    name: str = Form(...),
    description: str = Form(""),
    theme: str = Form("rich"),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)
    theme = theme if theme in ("rich", "compact") else "rich"

    with get_db() as db:
        db.execute(
            """UPDATE bundles SET name=?, description=?, theme=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (name.strip(), description.strip() or None, theme, bundle_id),
        )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            (
                "admin_bundle_update",
                admin["id"],
                f"bundle:{bundle_id} namn/beskrivning/tema uppdaterat",
            ),
        )

    return RedirectResponse(url=f"/admin/bundles/{bundle_id}?saved=1", status_code=303)


@router.post("/bundles/{bundle_id}/disable")
async def admin_disable_bundle(request: Request, bundle_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

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
        if new_status == 2:
            db.execute(
                "DELETE FROM bundle_takeover_requests WHERE bundle_id=? AND status='pending'",
                (bundle_id,),
            )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            (action, admin["id"], f"bundle:{bundle_id}"),
        )

    return RedirectResponse(url=f"/admin/bundles/{bundle_id}", status_code=303)


@router.post("/bundles/{bundle_id}/transfer")
async def admin_transfer_bundle(
    request: Request,
    bundle_id: int,
    new_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)
    new_email = new_email.strip().lower()

    with get_db() as db:
        bundle = db.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)
        old_owner_id = bundle["owner_id"]
        old_owner = db.execute("SELECT email FROM users WHERE id=?", (old_owner_id,)).fetchone()
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
        moved_twin = move_twin_rows(db, bundle["code"], old_owner_id, new_user["id"])
        if moved_twin:
            db.execute(
                "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
                (
                    "admin_bundle_transfer_twin",
                    admin["id"],
                    f"bundle:{bundle_id} tvilling flyttad: {', '.join(moved_twin)} från {old_email} till {new_email}",
                ),
            )

    return RedirectResponse(url=f"/admin/bundles/{bundle_id}", status_code=303)


@router.post("/bundles/{bundle_id}/konvertera-till-lankar")
async def admin_konvertera_bundle_till_lankar(
    request: Request,
    bundle_id: int,
    target_url: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

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
            raise HTTPException(
                status_code=409, detail="En aktiv kortlänk med den koden finns redan."
            )

        old_link = db.execute("SELECT id FROM links WHERE code=? AND status=3", (code,)).fetchone()
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
