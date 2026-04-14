"""Admin-routes för snabblänkar (featured links) på startsidan."""

from urllib.parse import urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import templates

from .helpers import pending_takeover_count

router = APIRouter()


def _validate_external_url(url: str) -> str | None:
    """Tillåt vilken http(s)-URL som helst — externa snabblänkar används av admin.

    http tillåts medvetet (t.ex. interna system utan TLS). Vanliga kortlänkar
    kräver däremot alltid https (via validate_target_url i validation.py).
    """
    url = (url or "").strip()
    if not url:
        return "URL får inte vara tom."
    try:
        p = urlparse(url)
    except Exception:
        return "Ogiltig URL."
    if p.scheme not in ("http", "https"):
        return "URL:en måste börja med http:// eller https://."
    if not p.netloc:
        return "URL:en saknar domän."
    return None


def _next_sort_order(db) -> int:
    """Högsta sort_order + 1 över både link-baserade och externa snabblänkar."""
    a = db.execute(
        "SELECT COALESCE(MAX(featured_sort), 0) FROM links WHERE is_featured=1"
    ).fetchone()[0]
    b = db.execute("SELECT COALESCE(MAX(sort_order), 0) FROM featured_external").fetchone()[0]
    return max(a, b) + 1


def _combined_featured(db) -> list[dict]:
    """Sammanslagen, sorterad lista över både link-baserade och externa snabblänkar."""
    link_rows = db.execute(
        """SELECT l.id, l.code, l.note, l.status,
                  l.featured_title, l.featured_icon, l.featured_sort AS sort_order,
                  u.email AS owner_email
           FROM links l LEFT JOIN users u ON l.owner_id=u.id
           WHERE l.is_featured=1""",
    ).fetchall()
    ext_rows = db.execute(
        """SELECT id, title, url, icon, sort_order
           FROM featured_external"""
    ).fetchall()

    items: list[dict] = []
    for r in link_rows:
        items.append(
            {
                "kind": "link",
                "id": r["id"],
                "sort_order": r["sort_order"] or 0,
                "code": r["code"],
                "note": r["note"],
                "status": r["status"],
                "featured_title": r["featured_title"],
                "featured_icon": r["featured_icon"],
                "owner_email": r["owner_email"],
            }
        )
    for r in ext_rows:
        items.append(
            {
                "kind": "external",
                "id": r["id"],
                "sort_order": r["sort_order"] or 0,
                "title": r["title"],
                "url": r["url"],
                "icon": r["icon"],
            }
        )
    items.sort(key=lambda x: (x["sort_order"], x["kind"], x["id"]))
    return items


@router.get("/snabblänkar")
async def admin_snabblänkar(request: Request, q: str = ""):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        featured = _combined_featured(db)

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
            "featured": featured,
            "search_results": [dict(r) for r in search_results],
            "q": q,
            "intro_md": intro_md,
            "heading": heading,
            "subtitle": subtitle,
            "saved": request.query_params.get("saved") == "1",
            "error": request.query_params.get("error") or "",
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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        link = db.execute("SELECT id FROM links WHERE id=? AND status=1", (link_id,)).fetchone()
        if not link:
            raise HTTPException(status_code=404)

        next_sort = _next_sort_order(db)

        db.execute(
            """UPDATE links
               SET is_featured=1,
                   featured_title=CASE WHEN ?='' THEN NULL ELSE ? END,
                   featured_icon=CASE WHEN ?='' THEN NULL ELSE ? END,
                   featured_sort=?
               WHERE id=?""",
            (featured_title, featured_title, featured_icon, featured_icon, next_sort, link_id),
        )

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.post("/snabblänkar/add-external")
async def admin_snabblänkar_add_external(
    request: Request,
    title: str = Form(...),
    url: str = Form(...),
    icon: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    title = title.strip()
    url = url.strip()
    icon = icon.strip()

    if not title:
        return RedirectResponse(url="/admin/snabblänkar?error=Titel+kr%C3%A4vs.", status_code=303)
    err = _validate_external_url(url)
    if err:
        from urllib.parse import quote

        return RedirectResponse(url=f"/admin/snabblänkar?error={quote(err)}", status_code=303)

    with get_db() as db:
        next_sort = _next_sort_order(db)
        db.execute(
            """INSERT INTO featured_external (title, url, icon, sort_order)
               VALUES (?, ?, ?, ?)""",
            (title, url, icon or None, next_sort),
        )

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.post("/snabblänkar/{link_id}/remove")
async def admin_snabblänkar_remove(request: Request, link_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute("UPDATE links SET is_featured=0, featured_sort=0 WHERE id=?", (link_id,))

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.post("/snabblänkar/external/{item_id}/remove")
async def admin_snabblänkar_remove_external(
    request: Request, item_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute("DELETE FROM featured_external WHERE id=?", (item_id,))

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


@router.post("/snabblänkar/{link_id}/update-display")
async def admin_snabblänkar_update_display(
    request: Request,
    link_id: int,
    featured_title: str = Form(""),
    featured_icon: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
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


@router.post("/snabblänkar/external/{item_id}/update-display")
async def admin_snabblänkar_update_external(
    request: Request,
    item_id: int,
    title: str = Form(...),
    url: str = Form(...),
    icon: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    title = title.strip()
    url = url.strip()
    icon = icon.strip()

    if not title:
        return RedirectResponse(url="/admin/snabblänkar?error=Titel+kr%C3%A4vs.", status_code=303)
    err = _validate_external_url(url)
    if err:
        from urllib.parse import quote

        return RedirectResponse(url=f"/admin/snabblänkar?error={quote(err)}", status_code=303)

    with get_db() as db:
        db.execute(
            """UPDATE featured_external
               SET title=?, url=?, icon=CASE WHEN ?='' THEN NULL ELSE ? END
               WHERE id=?""",
            (title, url, icon, icon, item_id),
        )

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)


def _apply_sort(db, items: list[dict]) -> None:
    """Skriv ned sort_order för hela listan till respektive tabell."""
    for new_sort, it in enumerate(items, start=1):
        if it["kind"] == "link":
            db.execute(
                "UPDATE links SET featured_sort=? WHERE id=?",
                (new_sort, it["id"]),
            )
        else:
            db.execute(
                "UPDATE featured_external SET sort_order=? WHERE id=?",
                (new_sort, it["id"]),
            )
        it["sort_order"] = new_sort


@router.post("/snabblänkar/move")
async def admin_snabblänkar_move(
    request: Request,
    kind: str = Form(...),
    item_id: int = Form(...),
    direction: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400)
    if kind not in ("link", "external"):
        raise HTTPException(status_code=400)

    with get_db() as db:
        items = _combined_featured(db)

        idx = next(
            (i for i, it in enumerate(items) if it["kind"] == kind and it["id"] == item_id),
            None,
        )
        if idx is None:
            raise HTTPException(status_code=404)

        swap_idx = idx - 1 if direction == "up" else idx + 1
        if swap_idx < 0 or swap_idx >= len(items):
            return RedirectResponse(url="/admin/snabblänkar", status_code=303)

        items[idx], items[swap_idx] = items[swap_idx], items[idx]
        _apply_sort(db, items)

    return RedirectResponse(url="/admin/snabblänkar", status_code=303)
