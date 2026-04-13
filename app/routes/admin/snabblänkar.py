"""Admin-routes för snabblänkar (featured links) på startsidan."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.csrf import validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import templates

from .helpers import pending_takeover_count

router = APIRouter()


@router.get("/snabblänkar")
async def admin_snabblänkar(request: Request, q: str = ""):
    admin = get_admin_or_redirect(request)

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
        heading_row = db.execute(
            "SELECT value FROM site_settings WHERE key='snabblänkar_heading'"
        ).fetchone()
        subtitle_row = db.execute(
            "SELECT value FROM site_settings WHERE key='snabblänkar_subtitle'"
        ).fetchone()
        intro_md = intro_row["value"] if intro_row else ""
        # Förfyll med default om aldrig sparat, så admin ser nuvarande tillstånd.
        heading = heading_row["value"] if heading_row is not None else "Snabblänkar"
        subtitle = subtitle_row["value"] if subtitle_row is not None else "Ofta använda kortlänkar"
        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/snabblänkar.html",
        {
            "request": request,
            "user": admin,
            "featured": [dict(r) for r in featured],
            "search_results": [dict(r) for r in search_results],
            "q": q,
            "intro_md": intro_md,
            "heading": heading,
            "subtitle": subtitle,
            "saved": request.query_params.get("saved") == "1",
            "pending_takeovers": takeovers,
        },
    )


@router.post("/snabblänkar/update-intro")
async def admin_snabblänkar_update_intro(
    request: Request,
    intro_md: str = Form(""),
    heading: str = Form(""),
    subtitle: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO site_settings (key, value) VALUES ('snabblänkar_intro', ?)",
            (intro_md.strip(),),
        )
        db.execute(
            "INSERT OR REPLACE INTO site_settings (key, value) VALUES ('snabblänkar_heading', ?)",
            (heading.strip(),),
        )
        db.execute(
            "INSERT OR REPLACE INTO site_settings (key, value) VALUES ('snabblänkar_subtitle', ?)",
            (subtitle.strip(),),
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
    get_admin_or_redirect(request)

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
    get_admin_or_redirect(request)

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
    get_admin_or_redirect(request)

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
    request: Request,
    link_id: int,
    direction: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400)

    with get_db() as db:
        featured = [
            dict(r)
            for r in db.execute(
                "SELECT id, featured_sort FROM links WHERE is_featured=1 ORDER BY featured_sort, id"
            ).fetchall()
        ]

        idx = next((i for i, r in enumerate(featured) if r["id"] == link_id), None)
        if idx is None:
            raise HTTPException(status_code=404)

        swap_idx = idx - 1 if direction == "up" else idx + 1
        if swap_idx < 0 or swap_idx >= len(featured):
            return RedirectResponse(url="/admin/snabblänkar", status_code=303)

        a, b = featured[idx], featured[swap_idx]
        db.execute("UPDATE links SET featured_sort=? WHERE id=?", (swap_idx, a["id"]))
        db.execute("UPDATE links SET featured_sort=? WHERE id=?", (idx, b["id"]))

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)
