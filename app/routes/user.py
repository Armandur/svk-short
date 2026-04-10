from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse

from app.database import get_db
from app.auth import get_current_user
from app.validation import validate_target_url
from app.config import LinkStatus
from app.csrf import validate_csrf_token
from app.templating import templates

router = APIRouter()


def _get_user_or_redirect(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


@router.get("/my-links")
async def my_links(request: Request, flash: str = ""):
    user = _get_user_or_redirect(request)

    with get_db() as db:
        links = db.execute(
            """SELECT l.id, l.code, l.target_url, l.status, l.note,
                      l.created_at, l.last_used_at,
                      (SELECT COUNT(*) FROM clicks WHERE link_id=l.id) AS click_count
               FROM links l
               WHERE l.owner_id=?
               ORDER BY l.created_at DESC""",
            (user["id"],),
        ).fetchall()

    return templates.TemplateResponse(
        "my_links.html",
        {
            "request": request,
            "user": user,
            "links": [dict(r) for r in links],
            "flash": flash,
        },
    )


@router.get("/my-links/{link_id}")
async def my_link_detail(request: Request, link_id: int):
    user = _get_user_or_redirect(request)

    with get_db() as db:
        link = db.execute(
            """SELECT id, code, target_url, status, note, created_at, last_used_at
               FROM links WHERE id=? AND owner_id=?""",
            (link_id, user["id"]),
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

    return templates.TemplateResponse(
        "my_link_detail.html",
        {
            "request": request,
            "user": user,
            "link": dict(link),
            "click_stats": [dict(r) for r in click_stats],
            "total_clicks": total_clicks,
            "clicks_7d": clicks_7d,
        },
    )


@router.post("/my-links/{link_id}/update")
async def update_link(request: Request, link_id: int, target_url: str = Form(...), csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    error = validate_target_url(target_url)
    if error:
        with get_db() as db:
            links = db.execute(
                """SELECT l.id, l.code, l.target_url, l.status, l.note,
                          l.created_at, l.last_used_at,
                          (SELECT COUNT(*) FROM clicks WHERE link_id=l.id) AS click_count
                   FROM links l WHERE l.owner_id=? ORDER BY l.created_at DESC""",
                (user["id"],),
            ).fetchall()
        return templates.TemplateResponse(
            "my_links.html",
            {
                "request": request,
                "user": user,
                "links": [dict(r) for r in links],
                "error": error,
                "edit_id": link_id,
            },
            status_code=422,
        )

    with get_db() as db:
        row = db.execute(
            "SELECT code FROM links WHERE id=? AND owner_id=?", (link_id, user["id"])
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        db.execute(
            "UPDATE links SET target_url=? WHERE id=? AND owner_id=?",
            (target_url, link_id, user["id"]),
        )
        code = row["code"]

    return RedirectResponse(
        url=f"/my-links?flash=updated:{code}",
        status_code=303,
    )


@router.post("/my-links/{link_id}/deactivate")
async def deactivate_link(request: Request, link_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT code, status FROM links WHERE id=? AND owner_id=?",
            (link_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        if row["status"] != LinkStatus.ACTIVE:
            raise HTTPException(status_code=400)
        db.execute(
            "UPDATE links SET status=? WHERE id=? AND owner_id=?",
            (LinkStatus.DISABLED_OWNER, link_id, user["id"]),
        )
        code = row["code"]

    return RedirectResponse(
        url=f"/my-links?flash=deactivated:{code}",
        status_code=303,
    )
