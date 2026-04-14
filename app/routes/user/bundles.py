import json
import logging
import secrets
import urllib.parse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.auth import COOKIE_NAME, create_session_cookie
from app.code_generator import generate_unique_code
from app.config import BASE_URL, RESERVED_CODES
from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_user_or_redirect
from app.mail import MailError, skicka_bundle_overlatelse
from app.ownership import move_twin_rows
from app.templating import templates
from app.validation import validate_code, validate_target_url

log = logging.getLogger(__name__)

router = APIRouter()


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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
            code = generate_unique_code(db)
        else:
            if code in RESERVED_CODES:
                errors["code"] = f"Koden '{code}' är reserverad."
            elif db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone():
                errors["code"] = f"Koden '{code}' är redan tagen av en kortlänk."
            elif db.execute("SELECT id FROM bundles WHERE code=? AND status=1", (code,)).fetchone():
                errors["code"] = f"Koden '{code}' är redan tagen av en annan samling."
                errors["_bundle_takeover_code"] = code

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
                    "bundle_takeover_code": errors.pop("_bundle_takeover_code", None),
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
    user = get_user_or_redirect(request)

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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)
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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        db.execute(
            "UPDATE bundles SET status=3, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (bundle_id,),
        )

    return RedirectResponse(url="/mina-lankar", status_code=303)


@router.post("/mina-samlingar/{bundle_id}/reactivate")
async def reaktivera_samling(
    request: Request, bundle_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT id, status FROM bundles WHERE id=? AND owner_id=?",
            (bundle_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        if row["status"] != 3:
            raise HTTPException(status_code=400)
        db.execute(
            "UPDATE bundles SET status=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
        url_error = validate_target_url(url, allow_external=bool(user["allow_external_urls"]))
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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
    user = get_user_or_redirect(request)

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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)
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


@router.post("/mina-samlingar/{bundle_id}/items/reorder")
async def reorder_items(request: Request, bundle_id: int):
    user = get_user_or_redirect(request)
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400) from e

    if not validate_csrf_token(data.get("csrf_token", ""), get_csrf_secret(request)):
        raise HTTPException(status_code=403)

    items = data.get("items", [])
    if not isinstance(items, list):
        raise HTTPException(status_code=400)

    with get_db() as db:
        _get_own_bundle(db, bundle_id, user["id"])
        for item in items:
            item_id = item.get("id")
            section_id = item.get("section_id")  # None or int
            sort_order = int(item.get("sort_order", 0))
            if item_id is None:
                continue
            db.execute(
                "UPDATE bundle_items SET section_id=?, sort_order=? WHERE id=? AND bundle_id=?",
                (section_id, sort_order, int(item_id), bundle_id),
            )
        db.execute(
            "UPDATE bundles SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (bundle_id,)
        )

    return JSONResponse({"ok": True})


@router.post("/mina-samlingar/{bundle_id}/sections")
async def ny_sektion(
    request: Request, bundle_id: int,
    name: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)
    to_email = to_email.strip().lower()

    # Collect optional link IDs to also transfer (checkboxes named transfer_link_<id>)
    form_data = await request.form()
    bundle_prefix = f"{BASE_URL}/"
    link_ids_to_transfer: list[int] = []
    with get_db() as db:
        bundle = _get_own_bundle(db, bundle_id, user["id"])
        # Validate each checked link actually belongs to this user and is in the bundle
        for key in form_data:
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
        log.exception("MailError")

    return RedirectResponse(
        url=f"/mina-samlingar/{bundle_id}?transfer_sent=1", status_code=303
    )


@router.post("/mina-samlingar/{bundle_id}/konvertera-till-lankar")
async def konvertera_samling_till_lankar(
    request: Request, bundle_id: int,
    target_url: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    user = get_user_or_redirect(request)

    target_url = target_url.strip()
    url_error = validate_target_url(target_url, allow_external=bool(user["allow_external_urls"]))
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
async def acceptera_overlatelse_confirm(request: Request, token: str):
    """Visar bekräftelsesida — förhindrar att e-postförhandsvisning auto-accepterar överlåtelsen."""
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
            "SELECT id, code, name FROM bundles WHERE id=?", (transfer["bundle_id"],)
        ).fetchone()
        if not bundle:
            raise HTTPException(status_code=404)

    link_count = 0
    if transfer.get("link_ids_to_transfer"):
        try:
            link_count = len(json.loads(transfer["link_ids_to_transfer"]))
        except (ValueError, TypeError):
            link_count = 0

    return templates.TemplateResponse(
        "bundle_transfer_confirm.html",
        {
            "request": request,
            "token": token,
            "bundle_name": bundle["name"],
            "bundle_code": bundle["code"],
            "to_email": transfer["to_email"],
            "link_count": link_count,
        },
    )


@router.post("/mina-samlingar/overlatelse/{token}")
async def acceptera_overlatelse_submit(
    request: Request, token: str, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)

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

        old_owner_id = bundle["owner_id"]

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
        # Dra med eventuell länk-skalrad med samma kod (rest från
        # konverter-till-samling) så den inte lämnas hos ursprunglig ägare.
        move_twin_rows(db, bundle["code"], old_owner_id, new_user["id"])
        # Transfer any shortlinks the sender opted to include
        if transfer.get("link_ids_to_transfer"):
            try:
                link_ids = json.loads(transfer["link_ids_to_transfer"])
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

    response = RedirectResponse(
        url="/mina-lankar?" + urllib.parse.urlencode({"flash": f"bundle_transfer_accepted:{bundle['code']}"}),
        status_code=303,
    )
    session = create_session_cookie(new_user["id"])
    response.set_cookie(
        COOKIE_NAME, session, httponly=True,
        secure=BASE_URL.startswith("https"), samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response
