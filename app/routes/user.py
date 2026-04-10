from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from datetime import datetime

from app.database import get_db
from app.auth import get_current_user, create_transfer_action_token, create_bulk_transfer_token
from app.validation import validate_target_url, validate_email
from app.config import LinkStatus, BASE_URL
from app.csrf import validate_csrf_token
from app.mail import skicka_overdragelseforfragan, skicka_bulk_overdragelseforfragan, MailError
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


@router.post("/my-links/request-transfer-all")
async def request_transfer_all(
    request: Request,
    to_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

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

        def _render_error(msg):
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
                    "bulk_transfer_error": msg,
                    "bulk_transfer_open": True,
                },
                status_code=422,
            )

        if email_error:
            return _render_error(email_error)

        if to_email == user["email"]:
            return _render_error("Du kan inte överlåta länkarna till dig själv.")

        if not active_links:
            return _render_error("Du har inga aktiva länkarna att överlåta.")

        link_ids = [lnk["id"] for lnk in active_links]

        existing = db.execute(
            f"""SELECT link_id FROM transfer_requests
               WHERE link_id IN ({','.join('?' for _ in link_ids)}) AND status='pending'""",
            link_ids,
        ).fetchone()
        if existing:
            return _render_error(
                "En eller flera av dina länkar har redan en väntande överlåtelseförfrågan. "
                "Vänta tills den besvarats eller avbryt den innan du begär en ny."
            )

        req_ids = []
        for lnk in active_links:
            db.execute(
                "INSERT INTO transfer_requests (link_id, from_user_id, to_email) VALUES (?,?,?)",
                (lnk["id"], user["id"], to_email),
            )
            req_ids.append(db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])

    accept_url = f"{BASE_URL}/transfer-action/{create_bulk_transfer_token(req_ids, 'accept')}"
    decline_url = f"{BASE_URL}/transfer-action/{create_bulk_transfer_token(req_ids, 'decline')}"

    try:
        skicka_bulk_overdragelseforfragan(
            to=to_email,
            from_email=user["email"],
            links=active_links,
            accept_url=accept_url,
            decline_url=decline_url,
        )
    except MailError:
        pass

    return RedirectResponse(url="/my-links?flash=bulk_transfer_sent", status_code=303)


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


@router.post("/my-links/{link_id}/request-transfer")
async def request_transfer(
    request: Request,
    link_id: int,
    to_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    to_email = to_email.strip().lower()
    email_error = validate_email(to_email)

    with get_db() as db:
        row = db.execute(
            "SELECT code, target_url, status FROM links WHERE id=? AND owner_id=?",
            (link_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        if row["status"] != LinkStatus.ACTIVE:
            raise HTTPException(status_code=400)

        if email_error:
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
                    "transfer_error": email_error,
                    "transfer_error_id": link_id,
                },
                status_code=422,
            )

        if to_email == user["email"]:
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
                    "transfer_error": "Du kan inte överlåta en länk till dig själv.",
                    "transfer_error_id": link_id,
                },
                status_code=422,
            )

        existing = db.execute(
            """SELECT id FROM transfer_requests
               WHERE link_id=? AND status='pending'""",
            (link_id,),
        ).fetchone()
        if existing:
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
                    "transfer_error": "Det finns redan en pågående överlåtelseförfrågan för denna länk.",
                    "transfer_error_id": link_id,
                },
                status_code=422,
            )

        db.execute(
            "INSERT INTO transfer_requests (link_id, from_user_id, to_email) VALUES (?,?,?)",
            (link_id, user["id"], to_email),
        )
        req_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        code = row["code"]
        target_url = row["target_url"]

    accept_url = f"{BASE_URL}/transfer-action/{create_transfer_action_token(req_id, 'accept')}"
    decline_url = f"{BASE_URL}/transfer-action/{create_transfer_action_token(req_id, 'decline')}"

    try:
        skicka_overdragelseforfragan(
            to=to_email,
            from_email=user["email"],
            code=code,
            target_url=target_url,
            accept_url=accept_url,
            decline_url=decline_url,
        )
    except MailError:
        pass

    return RedirectResponse(
        url=f"/my-links?flash=transfer_sent:{code}",
        status_code=303,
    )
