import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import create_transfer_action_token
from app.config import BASE_URL, LinkStatus
from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_user_or_redirect
from app.mail import MailError, skicka_overlatelseforfragan
from app.templating import templates
from app.validation import validate_email, validate_target_url

from ._queries import fetch_user_bundles, fetch_user_links

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/mina-lankar")
async def my_links(request: Request, flash: str = ""):
    user = get_user_or_redirect(request)

    with get_db() as db:
        links = fetch_user_links(db, user["id"])
        bundles = fetch_user_bundles(db, user["id"])

    return templates.TemplateResponse(
        "my_links.html",
        {
            "request": request,
            "user": user,
            "links": links,
            "bundles": bundles,
            "flash": flash,
        },
    )


@router.get("/mina-lankar/export")
async def export_my_data(request: Request):
    """Returnerar användarens samlade data som en JSON-fil (GDPR, art. 15 & 20)."""
    user = get_user_or_redirect(request)

    with get_db() as db:
        user_row = db.execute(
            """SELECT id, email, created_at, last_login,
                      allow_any_domain, allow_external_urls, is_admin
               FROM users WHERE id=?""",
            (user["id"],),
        ).fetchone()

        links = [
            dict(r)
            for r in db.execute(
                """SELECT id, code, target_url, status, note, created_at, last_used_at,
                      is_featured, featured_title, featured_icon, featured_sort
               FROM links WHERE owner_id=? ORDER BY created_at""",
                (user["id"],),
            ).fetchall()
        ]

        for lnk in links:
            clicks = db.execute(
                "SELECT clicked_at FROM clicks WHERE link_id=? ORDER BY clicked_at",
                (lnk["id"],),
            ).fetchall()
            lnk["clicks"] = [dict(c) for c in clicks]

        bundles = [
            dict(r)
            for r in db.execute(
                """SELECT id, code, name, description, theme, status,
                      created_at, updated_at, body_md
               FROM bundles WHERE owner_id=? ORDER BY created_at""",
                (user["id"],),
            ).fetchall()
        ]

        for bundle in bundles:
            bundle["sections"] = [
                dict(r)
                for r in db.execute(
                    "SELECT id, name, sort_order FROM bundle_sections WHERE bundle_id=? ORDER BY sort_order, id",
                    (bundle["id"],),
                ).fetchall()
            ]
            bundle["items"] = [
                dict(r)
                for r in db.execute(
                    """SELECT id, section_id, title, url, icon, description, sort_order, created_at
                   FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id""",
                    (bundle["id"],),
                ).fetchall()
            ]
            views = db.execute(
                "SELECT viewed_at FROM bundle_views WHERE bundle_id=? ORDER BY viewed_at",
                (bundle["id"],),
            ).fetchall()
            bundle["views"] = [dict(v) for v in views]

        transfer_requests_out = [
            dict(r)
            for r in db.execute(
                """SELECT id, link_id, to_email, status, created_at, resolved_at
               FROM transfer_requests WHERE from_user_id=? ORDER BY created_at""",
                (user["id"],),
            ).fetchall()
        ]

        takeover_requests_out = [
            dict(r)
            for r in db.execute(
                """SELECT id, link_id, reason, status, created_at, resolved_at
               FROM takeover_requests WHERE requester_email=? ORDER BY created_at""",
                (user["email"],),
            ).fetchall()
        ]

        bundle_takeover_out = [
            dict(r)
            for r in db.execute(
                """SELECT id, bundle_id, reason, status, created_at, resolved_at
               FROM bundle_takeover_requests WHERE requester_email=? ORDER BY created_at""",
                (user["email"],),
            ).fetchall()
        ]

    payload = {
        "exported_at": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        "user": dict(user_row),
        "links": links,
        "bundles": bundles,
        "transfer_requests": transfer_requests_out,
        "takeover_requests": takeover_requests_out,
        "bundle_takeover_requests": bundle_takeover_out,
    }

    filename = f"svky-export-{user['email']}-{datetime.now(UTC).replace(tzinfo=None).strftime('%Y%m%d')}.json"
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/mina-lankar/{link_id}")
async def my_link_detail(request: Request, link_id: int):
    user = get_user_or_redirect(request)

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


@router.post("/mina-lankar/{link_id}/update")
async def update_link(
    request: Request, link_id: int, target_url: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

    error = validate_target_url(target_url, allow_external=bool(user["allow_external_urls"]))
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
        url=f"/mina-lankar?flash=updated:{code}",
        status_code=303,
    )


@router.post("/mina-lankar/{link_id}/deactivate")
async def deactivate_link(request: Request, link_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
        url=f"/mina-lankar?flash=deactivated:{code}",
        status_code=303,
    )


@router.post("/mina-lankar/{link_id}/request-transfer")
async def request_transfer(
    request: Request,
    link_id: int,
    to_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
        skicka_overlatelseforfragan(
            to=to_email,
            from_email=user["email"],
            code=code,
            target_url=target_url,
            accept_url=accept_url,
            decline_url=decline_url,
        )
    except MailError:
        log.exception("MailError")

    return RedirectResponse(
        url=f"/mina-lankar?flash=transfer_sent:{code}",
        status_code=303,
    )


@router.post("/mina-lankar/{link_id}/konvertera-till-samling")
async def konvertera_lankar_till_samling(
    request: Request,
    link_id: int,
    bundle_name: str = Form(...),
    bundle_theme: str = Form("rich"),
    keep_url: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

    with get_db() as db:
        link = db.execute(
            "SELECT * FROM links WHERE id=? AND owner_id=? AND status=1",
            (link_id, user["id"]),
        ).fetchone()
        if not link:
            raise HTTPException(status_code=404)
        link = dict(link)

        code = link["code"]
        existing_bundle = db.execute(
            "SELECT id FROM bundles WHERE code=? AND status != 3", (code,)
        ).fetchone()
        if existing_bundle:
            raise HTTPException(status_code=409, detail="En samling med den koden finns redan.")

        # A status=3 bundle may still exist from a previous conversion — reactivate it.
        old_bundle = db.execute(
            "SELECT id FROM bundles WHERE code=? AND status=3", (code,)
        ).fetchone()
        if old_bundle:
            bundle_id = old_bundle["id"]
            db.execute(
                """UPDATE bundles SET name=?, theme=?, owner_id=?, status=1,
                   updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (bundle_name.strip() or code, bundle_theme, user["id"], bundle_id),
            )
        else:
            cur = db.execute(
                """INSERT INTO bundles (code, name, theme, owner_id, status)
                   VALUES (?,?,?,?,1)""",
                (code, bundle_name.strip() or code, bundle_theme, user["id"]),
            )
            bundle_id = cur.lastrowid

        if keep_url:
            # Only add the item if the bundle doesn't already have items
            existing_items = db.execute(
                "SELECT COUNT(*) FROM bundle_items WHERE bundle_id=?", (bundle_id,)
            ).fetchone()[0]
            if not existing_items:
                db.execute(
                    """INSERT INTO bundle_items (bundle_id, title, url, sort_order)
                       VALUES (?,?,?,1)""",
                    (bundle_id, link.get("note") or code, link["target_url"]),
                )

        db.execute("UPDATE links SET status=3 WHERE id=?", (link_id,))

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)
