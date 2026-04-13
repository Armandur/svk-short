import secrets
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse
from datetime import datetime

from app.database import get_db
from app.auth import get_current_user, create_transfer_action_token, create_bulk_transfer_token
from app.validation import validate_target_url, validate_code, validate_email
from app.config import LinkStatus, BASE_URL, RESERVED_CODES
from app.csrf import validate_csrf_token
from app.mail import skicka_overdragelseforfragan, skicka_bulk_overdragelseforfragan, skicka_bundle_overlatelse, MailError
from app.templating import templates

router = APIRouter()


def _get_user_or_redirect(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


@router.get("/mina-lankar")
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
        bundles = db.execute(
            """SELECT b.id, b.code, b.name, b.description, b.theme, b.status,
                      b.created_at, b.updated_at,
                      (SELECT COUNT(*) FROM bundle_items WHERE bundle_id=b.id) AS item_count
               FROM bundles b
               WHERE b.owner_id=?
               ORDER BY b.created_at DESC""",
            (user["id"],),
        ).fetchall()

    return templates.TemplateResponse(
        "my_links.html",
        {
            "request": request,
            "user": user,
            "links": [dict(r) for r in links],
            "bundles": [dict(r) for r in bundles],
            "flash": flash,
        },
    )


@router.post("/mina-lankar/request-transfer-all")
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

    return RedirectResponse(url="/mina-lankar?flash=bulk_transfer_sent", status_code=303)


@router.get("/mina-lankar/{link_id}")
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


def _user_allow_external(user_id: int) -> bool:
    with get_db() as db:
        row = db.execute(
            "SELECT allow_external_urls FROM users WHERE id=?", (user_id,)
        ).fetchone()
    return bool(row["allow_external_urls"]) if row else False


@router.post("/mina-lankar/{link_id}/update")
async def update_link(request: Request, link_id: int, target_url: str = Form(...), csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    error = validate_target_url(target_url, allow_external=_user_allow_external(user["id"]))
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
        url=f"/mina-lankar?flash=transfer_sent:{code}",
        status_code=303,
    )


# ─── Samlingar (bundles) ─────────────────────────────────────────────────────

def _get_own_bundle(db, bundle_id: int, user_id: int):
    bundle = db.execute(
        "SELECT * FROM bundles WHERE id=? AND owner_id=?", (bundle_id, user_id)
    ).fetchone()
    if not bundle:
        raise HTTPException(status_code=404)
    return dict(bundle)


@router.post("/mina-samlingar")
async def skapa_samling(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    code: str = Form(""),
    theme: str = Form("rich"),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    name = name.strip()
    code = code.strip().lower()
    theme = theme if theme in ("rich", "compact") else "rich"
    errors = {}

    if not name:
        errors["name"] = "Namn krävs."

    if code:
        code_error = validate_code(code)
        if code_error:
            errors["code"] = code_error

    if errors:
        own_links = []
        with get_db() as db:
            own_links = [dict(r) for r in db.execute(
                "SELECT id, code, note FROM links WHERE owner_id=? AND status=1 ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()]
        return templates.TemplateResponse(
            "bestall.html",
            {
                "request": request, "user": user, "own_links": own_links,
                "bundle_errors": errors,
                "bundle_values": {"name": name, "description": description, "code": code, "theme": theme},
                "active_tab": "bundle",
            },
            status_code=422,
        )

    with get_db() as db:
        if not code:
            while True:
                code = secrets.token_hex(3)
                if not db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone():
                    if not db.execute("SELECT id FROM bundles WHERE code=?", (code,)).fetchone():
                        break
        else:
            if code in RESERVED_CODES:
                errors["code"] = f"Koden '{code}' är reserverad."
            elif db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone():
                errors["code"] = f"Koden '{code}' är redan tagen av en kortlänk."
            elif db.execute("SELECT id FROM bundles WHERE code=?", (code,)).fetchone():
                errors["code"] = f"Koden '{code}' är redan tagen av en annan samling."

        if errors:
            own_links = [dict(r) for r in db.execute(
                "SELECT id, code, note FROM links WHERE owner_id=? AND status=1 ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall()]
            return templates.TemplateResponse(
                "bestall.html",
                {
                    "request": request, "user": user, "own_links": own_links,
                    "bundle_errors": errors,
                    "bundle_values": {"name": name, "description": description, "code": code, "theme": theme},
                    "active_tab": "bundle",
                },
                status_code=422,
            )

        db.execute(
            "INSERT INTO bundles (code, name, description, theme, owner_id) VALUES (?,?,?,?,?)",
            (code, name, description.strip() or None, theme, user["id"]),
        )
        bundle_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.get("/mina-samlingar/{bundle_id}")
async def min_samling(request: Request, bundle_id: int):
    user = _get_user_or_redirect(request)

    with get_db() as db:
        bundle = _get_own_bundle(db, bundle_id, user["id"])
        sections = [dict(r) for r in db.execute(
            "SELECT * FROM bundle_sections WHERE bundle_id=? ORDER BY sort_order, id",
            (bundle_id,),
        ).fetchall()]
        items = [dict(r) for r in db.execute(
            "SELECT * FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id",
            (bundle_id,),
        ).fetchall()]
        own_links = [dict(r) for r in db.execute(
            "SELECT id, code, note FROM links WHERE owner_id=? AND status=1 ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()]
        for lnk in own_links:
            lnk["shortlink_url"] = f"{BASE_URL}/{lnk['code']}"

        # Find the owner's own shortlinks that are embedded in this bundle
        bundle_prefix = f"{BASE_URL}/"
        own_links_in_bundle = [dict(r) for r in db.execute(
            """SELECT DISTINCT l.id, l.code, l.note
               FROM links l
               INNER JOIN bundle_items bi
                 ON bi.bundle_id=? AND bi.url = (? || l.code)
               WHERE l.owner_id=? AND l.status=1""",
            (bundle_id, bundle_prefix, user["id"]),
        ).fetchall()]

    return templates.TemplateResponse(
        "mina_samlingar_detalj.html",
        {
            "request": request, "user": user,
            "bundle": bundle, "sections": sections, "items": items,
            "own_links": own_links,
            "own_links_in_bundle": own_links_in_bundle,
            "base_url": BASE_URL,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/mina-samlingar/{bundle_id}/update")
async def uppdatera_samling(
    request: Request, bundle_id: int,
    name: str = Form(...),
    description: str = Form(""),
    theme: str = Form("rich"),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)
    theme = theme if theme in ("rich", "compact") else "rich"

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        db.execute(
            """UPDATE bundles SET name=?, description=?, theme=?,
               updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (name.strip(), description.strip() or None, theme, bundle_id),
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}?saved=1", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/update-body")
async def uppdatera_samling_body(
    request: Request, bundle_id: int,
    body_md: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        db.execute(
            "UPDATE bundles SET body_md=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (body_md.strip() or None, bundle_id),
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}?saved=1", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/deactivate")
async def deaktivera_samling(
    request: Request, bundle_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        db.execute(
            "UPDATE bundles SET status=3, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (bundle_id,),
        )

    return RedirectResponse(url="/mina-lankar", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/items")
async def lagg_till_item(
    request: Request, bundle_id: int,
    title: str = Form(...),
    url: str = Form(""),
    icon: str = Form(""),
    description: str = Form(""),
    section_id: str = Form(""),
    shortcode: str = Form(""),
    own_link_code: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    url = url.strip()
    shortcode = shortcode.strip().lower()
    own_link_code = own_link_code.strip().lower()
    sec_id = int(section_id) if section_id.strip().isdigit() else None

    if own_link_code:
        # Lägg till en av användarens egna kortlänkar — konstruera URL server-side
        with get_db() as db:
            _get_own_bundle(db, bundle_id, user["id"])
            link_row = db.execute(
                "SELECT code FROM links WHERE code=? AND owner_id=? AND status=1",
                (own_link_code, user["id"]),
            ).fetchone()
            if not link_row:
                raise HTTPException(status_code=404)
            item_url = f"{BASE_URL}/{own_link_code}"
            max_sort = db.execute(
                "SELECT COALESCE(MAX(sort_order), 0) FROM bundle_items WHERE bundle_id=?",
                (bundle_id,),
            ).fetchone()[0]
            db.execute(
                """INSERT INTO bundle_items (bundle_id, section_id, title, url, icon, description, sort_order)
                   VALUES (?,?,?,?,?,?,?)""",
                (bundle_id, sec_id, title.strip(), item_url,
                 icon.strip() or None, description.strip() or None, max_sort + 1),
            )
            db.execute(
                "UPDATE bundles SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (bundle_id,)
            )
        return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)

    base = f"/mina-samlingar/{bundle_id}"
    if shortcode:
        code_error = validate_code(shortcode)
        if code_error:
            return RedirectResponse(url=f"{base}?item_error=invalid_code", status_code=303)
        if shortcode in RESERVED_CODES:
            return RedirectResponse(url=f"{base}?item_error=reserved_code", status_code=303)
        url_error = validate_target_url(url, allow_external=_user_allow_external(user["id"]))
        if url_error:
            return RedirectResponse(url=f"{base}?item_error=invalid_url", status_code=303)
    else:
        if not url.startswith("https://"):
            return RedirectResponse(url=f"{base}?item_error=invalid_url", status_code=303)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])

        if shortcode:
            conflict = db.execute(
                "SELECT 1 FROM links WHERE code=? AND status != 3 "
                "UNION SELECT 1 FROM bundles WHERE code=? AND status != 3",
                (shortcode, shortcode),
            ).fetchone()
            if conflict:
                import urllib.parse
                return RedirectResponse(
                    url=f"{base}?item_error=code_taken&ecode={urllib.parse.quote(shortcode)}",
                    status_code=303,
                )
            db.execute(
                "INSERT INTO links (code, target_url, owner_id, status) VALUES (?,?,?,1)",
                (shortcode, url, user["id"]),
            )
            url = f"{BASE_URL}/{shortcode}"

        max_sort = db.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM bundle_items WHERE bundle_id=?",
            (bundle_id,),
        ).fetchone()[0]
        db.execute(
            """INSERT INTO bundle_items (bundle_id, section_id, title, url, icon, description, sort_order)
               VALUES (?,?,?,?,?,?,?)""",
            (bundle_id, sec_id, title.strip(), url,
             icon.strip() or None, description.strip() or None, max_sort + 1),
        )
        db.execute(
            "UPDATE bundles SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (bundle_id,)
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/items/{item_id}/delete")
async def ta_bort_item(
    request: Request, bundle_id: int, item_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        db.execute(
            "DELETE FROM bundle_items WHERE id=? AND bundle_id=?", (item_id, bundle_id)
        )
        db.execute(
            "UPDATE bundles SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (bundle_id,)
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/items/{item_id}/update")
async def uppdatera_item(
    request: Request, bundle_id: int, item_id: int,
    title: str = Form(...),
    url: str = Form(...),
    icon: str = Form(""),
    description: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    url = url.strip()
    if not url.startswith("https://"):
        return RedirectResponse(
            url=f"/mina-samlingar/{bundle_id}?item_error=invalid_url",
            status_code=303,
        )

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        db.execute(
            """UPDATE bundle_items SET title=?, url=?, icon=?, description=?
               WHERE id=? AND bundle_id=?""",
            (title.strip(), url, icon.strip() or None, description.strip() or None,
             item_id, bundle_id),
        )
        db.execute(
            "UPDATE bundles SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (bundle_id,)
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.get("/mina-samlingar/{bundle_id}/stats")
async def bundle_statistik(request: Request, bundle_id: int):
    user = _get_user_or_redirect(request)

    with get_db() as db:
        bundle = db.execute(
            "SELECT id, code, name, status FROM bundles WHERE id=? AND owner_id=?",
            (bundle_id, user["id"]),
        ).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)

        view_stats = db.execute(
            """SELECT date(viewed_at) AS dag, COUNT(*) AS antal
               FROM bundle_views WHERE bundle_id=?
               GROUP BY dag ORDER BY dag DESC LIMIT 90""",
            (bundle_id,),
        ).fetchall()

        total_views = db.execute(
            "SELECT COUNT(*) FROM bundle_views WHERE bundle_id=?", (bundle_id,)
        ).fetchone()[0]

        views_7d = db.execute(
            """SELECT COUNT(*) FROM bundle_views WHERE bundle_id=?
               AND viewed_at >= datetime('now', '-7 days')""",
            (bundle_id,),
        ).fetchone()[0]

    return templates.TemplateResponse(
        "bundle_stats.html",
        {
            "request": request,
            "user": user,
            "bundle": dict(bundle),
            "view_stats": [dict(r) for r in view_stats],
            "total_views": total_views,
            "views_7d": views_7d,
        },
    )


@router.post("/mina-samlingar/{bundle_id}/items/{item_id}/move")
async def flytta_item(
    request: Request, bundle_id: int, item_id: int,
    direction: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        items = [dict(r) for r in db.execute(
            "SELECT id, sort_order FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id",
            (bundle_id,),
        ).fetchall()]
        idx = next((i for i, r in enumerate(items) if r["id"] == item_id), None)
        if idx is None:
            raise HTTPException(status_code=404)
        swap = idx - 1 if direction == "up" else idx + 1
        if 0 <= swap < len(items):
            db.execute("UPDATE bundle_items SET sort_order=? WHERE id=?", (swap, items[idx]["id"]))
            db.execute("UPDATE bundle_items SET sort_order=? WHERE id=?", (idx, items[swap]["id"]))

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/sections")
async def ny_sektion(
    request: Request, bundle_id: int,
    name: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        max_sort = db.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM bundle_sections WHERE bundle_id=?",
            (bundle_id,),
        ).fetchone()[0]
        db.execute(
            "INSERT INTO bundle_sections (bundle_id, name, sort_order) VALUES (?,?,?)",
            (bundle_id, name.strip(), max_sort + 1),
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/sections/{sec_id}/rename")
async def byt_namn_sektion(
    request: Request, bundle_id: int, sec_id: int,
    name: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        db.execute(
            "UPDATE bundle_sections SET name=? WHERE id=? AND bundle_id=?",
            (name.strip(), sec_id, bundle_id),
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/sections/{sec_id}/delete")
async def ta_bort_sektion(
    request: Request, bundle_id: int, sec_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        # Koppla loss items (section_id → NULL)
        db.execute(
            "UPDATE bundle_items SET section_id=NULL WHERE section_id=? AND bundle_id=?",
            (sec_id, bundle_id),
        )
        db.execute(
            "DELETE FROM bundle_sections WHERE id=? AND bundle_id=?", (sec_id, bundle_id)
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/request-transfer")
async def begar_overlatelse(
    request: Request, bundle_id: int,
    to_email: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)
    to_email = to_email.strip().lower()

    # Collect optional link IDs to also transfer (checkboxes named transfer_link_<id>)
    import json
    form_data = await request.form()
    bundle_prefix = f"{BASE_URL}/"
    link_ids_to_transfer: list[int] = []
    with get_db() as db:
        bundle = _get_own_bundle(db, bundle_id, user["id"])
        # Validate each checked link actually belongs to this user and is in the bundle
        for key in form_data.keys():
            if key.startswith("transfer_link_"):
                try:
                    lid = int(key[len("transfer_link_"):])
                except ValueError:
                    continue
                row = db.execute(
                    """SELECT 1 FROM links l
                       INNER JOIN bundle_items bi
                         ON bi.bundle_id=? AND bi.url = (? || l.code)
                       WHERE l.id=? AND l.owner_id=? AND l.status=1""",
                    (bundle_id, bundle_prefix, lid, user["id"]),
                ).fetchone()
                if row:
                    link_ids_to_transfer.append(lid)
        token = secrets.token_hex(32)
        link_ids_json = json.dumps(link_ids_to_transfer) if link_ids_to_transfer else None
        db.execute(
            "INSERT INTO bundle_transfers (bundle_id, to_email, token, link_ids_to_transfer) VALUES (?,?,?,?)",
            (bundle_id, to_email, token, link_ids_json),
        )

    transfer_url = f"{BASE_URL}/mina-samlingar/overlatelse/{token}"
    try:
        skicka_bundle_overlatelse(to_email, bundle["name"], bundle["code"], transfer_url)
    except MailError:
        pass

    return RedirectResponse(
        url=f"/mina-samlingar/{bundle_id}?transfer_sent=1", status_code=303
    )


@router.post("/mina-lankar/{link_id}/konvertera-till-samling")
async def konvertera_lankar_till_samling(
    request: Request, link_id: int,
    bundle_name: str = Form(...),
    bundle_theme: str = Form("rich"),
    keep_url: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

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

        db.execute(
            "UPDATE links SET status=3 WHERE id=?", (link_id,)
        )

    return RedirectResponse(url=f"/mina-samlingar/{bundle_id}", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/konvertera-till-lankar")
async def konvertera_samling_till_lankar(
    request: Request, bundle_id: int,
    target_url: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    user = _get_user_or_redirect(request)

    target_url = target_url.strip()
    url_error = validate_target_url(target_url, allow_external=_user_allow_external(user["id"]))
    if url_error:
        raise HTTPException(status_code=422, detail=url_error)

    with get_db() as db:
        bundle = _get_own_bundle(db, bundle_id, user["id"])
        code = bundle["code"]

        existing_link = db.execute(
            "SELECT id FROM links WHERE code=? AND status != 3", (code,)
        ).fetchone()
        if existing_link:
            raise HTTPException(status_code=409, detail="En kortlänk med den koden finns redan.")

        # The original link (status=3) may still exist — reactivate it if so,
        # otherwise insert a new one.
        old_link = db.execute(
            "SELECT id FROM links WHERE code=? AND status=3", (code,)
        ).fetchone()
        if old_link:
            db.execute(
                "UPDATE links SET target_url=?, owner_id=?, status=1 WHERE id=?",
                (target_url, user["id"], old_link["id"]),
            )
        else:
            db.execute(
                """INSERT INTO links (code, target_url, owner_id, status)
                   VALUES (?,?,?,1)""",
                (code, target_url, user["id"]),
            )
        db.execute(
            "UPDATE bundles SET status=3, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (bundle_id,),
        )

    return RedirectResponse(url="/mina-lankar", status_code=303)


@router.get("/mina-samlingar/overlatelse/{token}")
async def acceptera_overlatelse(request: Request, token: str):
    with get_db() as db:
        transfer = db.execute(
            "SELECT * FROM bundle_transfers WHERE token=? AND used_at IS NULL",
            (token,),
        ).fetchone()
        if not transfer:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Länken är ogiltig eller har redan använts."},
                status_code=400,
            )
        transfer = dict(transfer)
        bundle = db.execute(
            "SELECT * FROM bundles WHERE id=?", (transfer["bundle_id"],)
        ).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)

        db.execute(
            "INSERT OR IGNORE INTO users (email) VALUES (?)", (transfer["to_email"],)
        )
        new_user = db.execute(
            "SELECT id FROM users WHERE email=?", (transfer["to_email"],)
        ).fetchone()
        db.execute(
            "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_user["id"], transfer["bundle_id"]),
        )
        # Transfer any shortlinks the sender opted to include
        import json as _json
        if transfer.get("link_ids_to_transfer"):
            try:
                link_ids = _json.loads(transfer["link_ids_to_transfer"])
            except (ValueError, TypeError):
                link_ids = []
            for lid in link_ids:
                db.execute(
                    "UPDATE links SET owner_id=? WHERE id=?",
                    (new_user["id"], lid),
                )
        db.execute(
            "UPDATE bundle_transfers SET used_at=CURRENT_TIMESTAMP WHERE id=?",
            (transfer["id"],),
        )

    from app.auth import create_session_cookie, COOKIE_NAME
    import urllib.parse
    response = RedirectResponse(
        url="/mina-lankar?" + urllib.parse.urlencode({"flash": f"bundle_transfer_accepted:{bundle['code']}"}),
        status_code=303,
    )
    session = create_session_cookie(new_user["id"])
    from app.config import BASE_URL as _BASE_URL
    response.set_cookie(
        COOKIE_NAME, session, httponly=True,
        secure=_BASE_URL.startswith("https"), samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response
