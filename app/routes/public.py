import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse

import markdown
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from markupsafe import Markup

from app.auth import COOKIE_NAME, create_session_cookie, create_takeover_action_token, decode_transfer_action_token, get_current_user
from app.config import BASE_URL, LinkStatus, RESERVED_CODES
from app.csrf import validate_csrf_token
from app.database import get_db
from app.deps import check_rate_limit, user_allows_any_domain, user_allows_external_urls
from app.ownership import move_twin_rows
from app.mail import (
    MailError,
    skicka_bulk_overlatelse_avbojd_agare,
    skicka_bulk_overlatelse_bekraftad_agare,
    skicka_bundle_overlatelse_notis_admin,
    skicka_overlatelse_avbojd_agare,
    skicka_overlatelse_bekraftad_agare,
    skicka_overlatelse_notis_admin,
    skicka_verifieringsmail,
)
from app.templating import templates
from app.validation import validate_code, validate_email, validate_target_url

router = APIRouter()


def _generate_code(db) -> str:
    while True:
        code = secrets.token_hex(3)  # 6 hex chars
        existing = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
        if not existing:
            return code


@router.get("/")
async def index(request: Request):
    user = get_current_user(request)
    with get_db() as db:
        link_featured = db.execute(
            """SELECT id, code, note, featured_title, featured_icon,
                      featured_sort AS sort_order, created_at
               FROM links
               WHERE is_featured=1 AND status=1""",
        ).fetchall()
        ext_featured = db.execute(
            """SELECT id, title, url, icon, sort_order, created_at
               FROM featured_external""",
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
    featured_intro_html = Markup(markdown.markdown(intro_md, extensions=["nl2br"])) if intro_md else None

    # Saknad rad → defaulttext. Sparat tomt värde → dölj raden helt.
    featured_heading = heading_row["value"] if heading_row is not None else "Snabblänkar"
    featured_subtitle = subtitle_row["value"] if subtitle_row is not None else "Ofta använda kortlänkar"

    # Slå ihop link-baserade och externa snabblänkar till en enad lista.
    featured: list[dict] = []
    for r in link_featured:
        featured.append({
            "external": False,
            "href": f"/{r['code']}",
            "title": r["featured_title"] or r["note"] or r["code"],
            "subtitle": f"svky.se/{r['code']}",
            "icon": r["featured_icon"],
            "sort_order": r["sort_order"] or 0,
            "created_at": r["created_at"],
        })
    for r in ext_featured:
        # Visa bara värdnamnet som undertext — fulla URL:en är ofta för
        # lång för att få plats i kortet. Tooltip visar full URL.
        try:
            host = urlparse(r["url"]).netloc or r["url"]
        except Exception:
            host = r["url"]
        featured.append({
            "external": True,
            "href": r["url"],
            "title": r["title"],
            "subtitle": host,
            "tooltip": r["url"],
            "icon": r["icon"],
            "sort_order": r["sort_order"] or 0,
            "created_at": r["created_at"],
        })
    featured.sort(key=lambda f: (f["sort_order"], f["created_at"] or ""))

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "user": user,
            "featured": featured,
            "featured_intro_html": featured_intro_html,
            "featured_heading": featured_heading,
            "featured_subtitle": featured_subtitle,
        },
    )


@router.get("/bestall")
async def bestall_form(request: Request):
    user = get_current_user(request)
    own_links = []
    if user:
        with get_db() as db:
            own_links = db.execute(
                """SELECT id, code, note FROM links
                   WHERE owner_id=? AND status=1
                   ORDER BY created_at DESC""",
                (user["id"],),
            ).fetchall()
        own_links = [dict(r) for r in own_links]
    active_tab = "bundle" if request.query_params.get("tab") == "bundle" else "link"
    return templates.TemplateResponse(
        "bestall.html",
        {"request": request, "user": user, "own_links": own_links,
         "active_tab": active_tab},
    )


@router.post("/bestall")
async def bestall_post(
    request: Request,
    email: str = Form(""),
    target_url: str = Form(...),
    code: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(...),
):
    """Beställ kortlänk via /bestall.

    Inloggad användare: länken skapas aktiv direkt utan e-postverifiering.
    Utloggad användare: vanligt pending-flöde med verifieringsmail.
    """
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)

    current_user = get_current_user(request)
    ip = request.client.host if request.client else "unknown"

    # ── Inloggad: hoppa över e-postverifiering ──────────────────────────────
    if current_user:
        errors = {}
        url_error = validate_target_url(
            target_url,
            allow_external=user_allows_external_urls(current_user["email"]),
        )
        if url_error:
            errors["target_url"] = url_error

        code = code.strip().lower()
        if code:
            code_error = validate_code(code)
            if code_error:
                errors["code"] = code_error

        if errors:
            return templates.TemplateResponse(
                "bestall.html",
                {
                    "request": request,
                    "user": current_user,
                    "errors": errors,
                    "values": {"target_url": target_url, "code": code, "note": note},
                },
                status_code=422,
            )

        with get_db() as db:
            if not check_rate_limit(db, ip, "request"):
                return templates.TemplateResponse(
                    "bestall.html",
                    {
                        "request": request,
                        "user": current_user,
                        "errors": {"general": "För många beställningar. Försök igen om en stund."},
                        "values": {"target_url": target_url, "code": code, "note": note},
                    },
                    status_code=429,
                )

            if not code:
                code = _generate_code(db)
            else:
                existing_link = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
                existing_bundle = db.execute(
                    "SELECT id FROM bundles WHERE code=? AND status=1", (code,)
                ).fetchone()
                if existing_link:
                    return templates.TemplateResponse(
                        "bestall.html",
                        {
                            "request": request,
                            "user": current_user,
                            "errors": {"code": f"Koden '{code}' är redan tagen. Välj en annan eller begär att få ta över den."},
                            "values": {"target_url": target_url, "code": code, "note": note},
                            "takeover_code": code,
                        },
                        status_code=422,
                    )
                elif existing_bundle:
                    return templates.TemplateResponse(
                        "bestall.html",
                        {
                            "request": request,
                            "user": current_user,
                            "errors": {"code": f"Koden '{code}' används för en samling. Välj en annan kod eller begär att få ta över den."},
                            "values": {"target_url": target_url, "code": code, "note": note},
                            "bundle_takeover_code": code,
                        },
                        status_code=422,
                    )

            db.execute(
                "INSERT INTO links (code, target_url, owner_id, status, note) VALUES (?,?,?,?,?)",
                (code, target_url, current_user["id"], LinkStatus.ACTIVE, note or None),
            )

        return RedirectResponse(
            url=f"/mina-lankar?flash=created:{code}", status_code=303
        )

    # ── Utloggad: vanligt pending-flöde med verifieringsmail ────────────────
    email = email.strip().lower()
    errors = {}

    email_error = validate_email(email, allow_any_domain=user_allows_any_domain(email))
    if email_error:
        errors["email"] = email_error

    url_error = validate_target_url(target_url, allow_external=user_allows_external_urls(email))
    if url_error:
        errors["target_url"] = url_error

    code = code.strip().lower()
    if code:
        code_error = validate_code(code)
        if code_error:
            errors["code"] = code_error

    if errors:
        return templates.TemplateResponse(
            "bestall.html",
            {
                "request": request,
                "user": None,
                "errors": errors,
                "values": {"email": email, "target_url": target_url, "code": code, "note": note},
            },
            status_code=422,
        )

    with get_db() as db:
        if not check_rate_limit(db, ip, "request"):
            return templates.TemplateResponse(
                "bestall.html",
                {
                    "request": request,
                    "user": None,
                    "errors": {"general": "För många beställningar. Försök igen om en stund."},
                    "values": {"email": email, "target_url": target_url, "code": code, "note": note},
                },
                status_code=429,
            )

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        user_row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        user_id = user_row["id"]

        if not code:
            code = _generate_code(db)
        else:
            existing_link = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
            existing_bundle = db.execute(
                "SELECT id FROM bundles WHERE code=? AND status=1", (code,)
            ).fetchone()
            if existing_link:
                return templates.TemplateResponse(
                    "bestall.html",
                    {
                        "request": request,
                        "user": None,
                        "errors": {"code": f"Koden '{code}' är redan tagen. Välj en annan eller begär att få ta över den."},
                        "values": {"email": email, "target_url": target_url, "code": code, "note": note},
                        "takeover_code": code,
                    },
                    status_code=422,
                )
            elif existing_bundle:
                return templates.TemplateResponse(
                    "bestall.html",
                    {
                        "request": request,
                        "user": None,
                        "errors": {"code": f"Koden '{code}' används för en samling. Välj en annan kod eller begär att få ta över den."},
                        "values": {"email": email, "target_url": target_url, "code": code, "note": note},
                        "bundle_takeover_code": code,
                    },
                    status_code=422,
                )

        db.execute(
            "INSERT INTO links (code, target_url, owner_id, status, note) VALUES (?,?,?,?,?)",
            (code, target_url, user_id, LinkStatus.PENDING, note or None),
        )
        link_row = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
        link_id = link_row["id"]

        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        db.execute(
            "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) VALUES (?,?,?,?,?)",
            (token, user_id, link_id, "verify", expires_at.isoformat()),
        )

    verify_url = f"{BASE_URL}/verify/{token}"
    mail_ok = True
    try:
        skicka_verifieringsmail(email, verify_url, code, target_url)
    except MailError:
        mail_ok = False

    return templates.TemplateResponse(
        "pending.html",
        {"request": request, "email": email, "code": code, "target_url": target_url,
         "mail_ok": mail_ok},
    )


@router.get("/om")
async def about(request: Request):
    user = get_current_user(request)
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM site_settings WHERE key='about_content'"
        ).fetchone()
    content_html = markdown.markdown(row["value"] if row else "", extensions=["nl2br"])
    return templates.TemplateResponse(
        "about.html", {"request": request, "user": user, "content": content_html}
    )


@router.get("/integritet")
async def integritet(request: Request):
    user = get_current_user(request)
    with get_db() as db:
        row = db.execute(
            "SELECT value FROM site_settings WHERE key='integritet_content'"
        ).fetchone()
    content_html = markdown.markdown(row["value"] if row else "", extensions=["nl2br"])
    return templates.TemplateResponse(
        "integritet.html", {"request": request, "user": user, "content": content_html}
    )


@router.post("/request/resend")
async def resend_verification(
    request: Request,
    code: str = Form(...),
    email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)

    ip = request.client.host if request.client else "unknown"

    with get_db() as db:
        if not check_rate_limit(db, ip, "resend"):
            return templates.TemplateResponse(
                "pending.html",
                {
                    "request": request,
                    "email": email,
                    "code": code,
                    "target_url": "",
                    "mail_ok": True,
                    "resend_error": "För många försök. Vänta en stund och försök igen.",
                },
                status_code=429,
            )

        link_row = db.execute(
            "SELECT id, target_url FROM links WHERE code=? AND status=0", (code,)
        ).fetchone()
        if not link_row:
            raise HTTPException(status_code=404)

        user_row = db.execute(
            "SELECT id FROM users WHERE email=?", (email,)
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404)

        # Försök hitta ett giltigt befintligt token
        existing = db.execute(
            """SELECT token FROM tokens
               WHERE link_id=? AND user_id=? AND purpose='verify'
                 AND used_at IS NULL AND expires_at > datetime('now')
               ORDER BY expires_at DESC LIMIT 1""",
            (link_row["id"], user_row["id"]),
        ).fetchone()

        if existing:
            token = existing["token"]
        else:
            # Skapa nytt token om det gamla gått ut
            token = secrets.token_hex(32)
            expires_at = datetime.utcnow() + timedelta(hours=24)
            db.execute(
                "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) VALUES (?,?,?,?,?)",
                (token, user_row["id"], link_row["id"], "verify", expires_at.isoformat()),
            )

    verify_url = f"{BASE_URL}/verify/{token}"
    mail_ok = True
    try:
        skicka_verifieringsmail(email, verify_url, code, link_row["target_url"])
    except MailError:
        mail_ok = False

    return templates.TemplateResponse(
        "pending.html",
        {
            "request": request,
            "email": email,
            "code": code,
            "target_url": link_row["target_url"],
            "mail_ok": mail_ok,
            "resent": True,
        },
    )


@router.get("/request/check-code")
async def check_code(code: str = ""):
    code = code.strip().lower()
    if not code:
        return JSONResponse({"status": "empty"})
    error = validate_code(code)
    if error:
        return JSONResponse({"status": "invalid", "message": error})
    with get_db() as db:
        existing_link = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
        existing_bundle = db.execute(
            "SELECT id FROM bundles WHERE code=? AND status=1", (code,)
        ).fetchone()
    if existing_link or existing_bundle:
        return JSONResponse({"status": "taken"})
    return JSONResponse({"status": "available"})


@router.post("/request")
async def request_link(
    request: Request,
    email: str = Form(...),
    target_url: str = Form(...),
    code: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    email = email.strip().lower()
    ip = request.client.host if request.client else "unknown"

    errors = {}

    email_error = validate_email(email, allow_any_domain=user_allows_any_domain(email))
    if email_error:
        errors["email"] = email_error

    url_error = validate_target_url(target_url, allow_external=user_allows_external_urls(email))
    if url_error:
        errors["target_url"] = url_error

    code = code.strip().lower()
    if code:
        code_error = validate_code(code)
        if code_error:
            errors["code"] = code_error

    if errors:
        user = get_current_user(request)
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "user": user,
                "errors": errors,
                "values": {"email": email, "target_url": target_url, "code": code, "note": note},
            },
            status_code=422,
        )

    with get_db() as db:
        if not check_rate_limit(db, ip, "request"):
            user = get_current_user(request)
            return templates.TemplateResponse(
                "index.html",
                {
                    "request": request,
                    "user": user,
                    "errors": {"general": "För många beställningar. Försök igen om en stund."},
                    "values": {"email": email, "target_url": target_url, "code": code, "note": note},
                },
                status_code=429,
            )

        # Upsert user
        db.execute(
            "INSERT OR IGNORE INTO users (email) VALUES (?)", (email,)
        )
        user_row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        user_id = user_row["id"]

        # Generate or validate code
        if not code:
            code = _generate_code(db)
        else:
            existing = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
            if existing:
                current_user = get_current_user(request)
                return templates.TemplateResponse(
                    "index.html",
                    {
                        "request": request,
                        "user": current_user,
                        "errors": {"code": f"Koden '{code}' är redan tagen. Välj en annan eller begär att få ta över den."},
                        "values": {"email": email, "target_url": target_url, "code": code, "note": note},
                        "takeover_code": code,
                    },
                    status_code=422,
                )

        db.execute(
            "INSERT INTO links (code, target_url, owner_id, status, note) VALUES (?,?,?,?,?)",
            (code, target_url, user_id, LinkStatus.PENDING, note or None),
        )
        link_row = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
        link_id = link_row["id"]

        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        db.execute(
            "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) VALUES (?,?,?,?,?)",
            (token, user_id, link_id, "verify", expires_at.isoformat()),
        )

    verify_url = f"{BASE_URL}/verify/{token}"
    mail_ok = True
    try:
        skicka_verifieringsmail(email, verify_url, code, target_url)
    except MailError:
        mail_ok = False

    return templates.TemplateResponse(
        "pending.html",
        {"request": request, "email": email, "code": code, "target_url": target_url,
         "mail_ok": mail_ok},
    )


@router.get("/verify/{token}")
async def verify_confirm(request: Request, token: str):
    """Visar bekräftelsesida — förhindrar att e-postförhandsvisning auto-aktiverar länken."""
    with get_db() as db:
        row = db.execute(
            """SELECT t.expires_at, t.used_at, l.code, l.target_url
               FROM tokens t JOIN links l ON t.link_id = l.id
               WHERE t.token=? AND t.purpose='verify'""",
            (token,),
        ).fetchone()

    if not row:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Ogiltig eller okänd länk."},
            status_code=400,
        )

    if row["used_at"]:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Den här länken har redan använts."},
            status_code=400,
        )

    if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Länken har gått ut. Beställ en ny kortlänk."},
            status_code=400,
        )

    return templates.TemplateResponse(
        "verify_confirm.html",
        {
            "request": request,
            "token": token,
            "code": row["code"],
            "target_url": row["target_url"],
        },
    )


@router.post("/verify/{token}")
async def verify_submit(request: Request, token: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)

    with get_db() as db:
        row = db.execute(
            """SELECT t.id, t.user_id, t.link_id, t.expires_at, t.used_at,
                      l.code, l.target_url
               FROM tokens t JOIN links l ON t.link_id = l.id
               WHERE t.token=? AND t.purpose='verify'""",
            (token,),
        ).fetchone()

        if not row:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Ogiltig eller okänd länk."},
                status_code=400,
            )

        if row["used_at"]:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Den här länken har redan använts."},
                status_code=400,
            )

        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.utcnow() > expires_at:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Länken har gått ut. Beställ en ny kortlänk."},
                status_code=400,
            )

        db.execute("UPDATE links SET status=? WHERE id=?", (LinkStatus.ACTIVE, row["link_id"]))
        db.execute(
            "UPDATE tokens SET used_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), row["id"]),
        )
        db.execute(
            "UPDATE users SET last_login=? WHERE id=?",
            (datetime.utcnow().isoformat(), row["user_id"]),
        )

    session_cookie = create_session_cookie(row["user_id"])
    response = templates.TemplateResponse(
        "verify_ok.html",
        {
            "request": request,
            "code": row["code"],
            "target_url": row["target_url"],
            "base_url": BASE_URL,
        },
    )
    response.set_cookie(
        COOKIE_NAME,
        session_cookie,
        httponly=True,
        secure=BASE_URL.startswith("https"),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


def _load_transfer_action(token: str):
    """Avkoda transfer-action-token och slå upp rader. Returnerar tuple
    (error_response, data, rows, is_bulk, req_ids, bundle_ids, bundle_rows)
    där error_response är satt om något gått fel (ogiltig token, okänd
    action, inga rader) eller None om allt är OK."""
    data = decode_transfer_action_token(token)
    if not data:
        return (
            ("error", "Länken är ogiltig eller har gått ut (7 dagar).", 400),
            None, None, None, None, None, None,
        )

    action = data.get("action")
    if action not in ("accept", "decline"):
        return (("http", 400), None, None, None, None, None, None)

    is_bulk = "req_ids" in data
    req_ids = data["req_ids"] if is_bulk else [data["req_id"]]
    bundle_ids = data.get("bundle_ids", [])

    with get_db() as db:
        rows = db.execute(
            f"""SELECT tr.id, tr.status, tr.to_email, tr.from_user_id,
                       tr.link_id, l.code, l.target_url, u.email AS from_email
               FROM transfer_requests tr
               JOIN links l ON tr.link_id = l.id
               JOIN users u ON tr.from_user_id = u.id
               WHERE tr.id IN ({','.join('?' for _ in req_ids)})""",
            req_ids,
        ).fetchall()
        rows = [dict(r) for r in rows]

        bundle_rows: list[dict] = []
        if bundle_ids:
            br = db.execute(
                f"SELECT id, code, name, owner_id FROM bundles "
                f"WHERE id IN ({','.join('?' for _ in bundle_ids)})",
                bundle_ids,
            ).fetchall()
            bundle_rows = [dict(b) for b in br]

    if not rows and not bundle_rows:
        return (("http", 404), None, None, None, None, None, None)

    return (None, data, rows, is_bulk, req_ids, bundle_ids, bundle_rows)


@router.get("/transfer-action/{token}")
async def transfer_action_confirm(request: Request, token: str):
    """Visar bekräftelsesida — förhindrar att e-postförhandsvisning auto-utför överlåtelsen."""
    err, data, rows, is_bulk, _req_ids, _bundle_ids, bundle_rows = _load_transfer_action(token)
    if err:
        if err[0] == "error":
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": err[1]},
                status_code=err[2],
            )
        raise HTTPException(status_code=err[1])

    action = data["action"]
    mail_bundles = [{"code": b["code"], "name": b["name"]} for b in bundle_rows]

    # Om allt redan är hanterat — visa resultatsidan direkt (idempotent, ingen skrivning).
    if rows and all(r["status"] != "pending" for r in rows):
        return templates.TemplateResponse(
            "transfer_done.html",
            {
                "request": request,
                "codes": [r["code"] for r in rows],
                "bundles": mail_bundles,
                "already_handled": True,
                "accepted": rows[0]["status"] == "accepted",
                "is_bulk": is_bulk,
            },
        )

    pending = [r for r in rows if r["status"] == "pending"]
    return templates.TemplateResponse(
        "transfer_action_confirm.html",
        {
            "request": request,
            "token": token,
            "action": action,
            "is_bulk": is_bulk,
            "codes": [r["code"] for r in pending],
            "from_email": pending[0]["from_email"] if pending else None,
            "to_email": pending[0]["to_email"] if pending else None,
            "bundle_count": len(bundle_rows),
        },
    )


@router.post("/transfer-action/{token}")
async def transfer_action_submit(request: Request, token: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)

    err, data, rows, is_bulk, _req_ids, _bundle_ids, bundle_rows = _load_transfer_action(token)
    if err:
        if err[0] == "error":
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": err[1]},
                status_code=err[2],
            )
        raise HTTPException(status_code=err[1])

    action = data["action"]
    mail_bundles = [{"code": b["code"], "name": b["name"]} for b in bundle_rows]

    with get_db() as db:
        # Om alla redan är hanterade — visa resultatsidan direkt
        if rows and all(r["status"] != "pending" for r in rows):
            return templates.TemplateResponse(
                "transfer_done.html",
                {
                    "request": request,
                    "codes": [r["code"] for r in rows],
                    "bundles": mail_bundles,
                    "already_handled": True,
                    "accepted": rows[0]["status"] == "accepted",
                    "is_bulk": is_bulk,
                },
            )

        now = datetime.utcnow().isoformat()
        to_email = rows[0]["to_email"] if rows else None
        from_email = rows[0]["from_email"] if rows else None
        pending = [r for r in rows if r["status"] == "pending"]

        if action == "accept":
            db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (to_email,))
            new_user = db.execute(
                "SELECT id FROM users WHERE email=?", (to_email,)
            ).fetchone()
            for r in pending:
                db.execute(
                    "UPDATE links SET owner_id=? WHERE id=?",
                    (new_user["id"], r["link_id"]),
                )
                db.execute(
                    "UPDATE transfer_requests SET status='accepted', resolved_at=? WHERE id=?",
                    (now, r["id"]),
                )
                db.execute(
                    "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
                    (
                        "transfer_accepted",
                        new_user["id"],
                        r["link_id"],
                        f"överlåtelse godkänd: {from_email} → {to_email}",
                    ),
                )
                # Dra med eventuell samling med samma kod (skalrad efter
                # konverter-till-samling) så den inte lämnas hos avsändaren.
                move_twin_rows(db, r["code"], r["from_user_id"], new_user["id"])
            # Överlåt samlingar och deras ev. länk-skalrader
            for b in bundle_rows:
                db.execute(
                    "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (new_user["id"], b["id"]),
                )
                move_twin_rows(db, b["code"], b["owner_id"], new_user["id"])
        else:
            for r in pending:
                db.execute(
                    "UPDATE transfer_requests SET status='declined', resolved_at=? WHERE id=?",
                    (now, r["id"]),
                )

    codes = [r["code"] for r in pending]

    if action == "accept":
        if is_bulk:
            try:
                skicka_bulk_overlatelse_bekraftad_agare(
                    from_email, codes, to_email, BASE_URL, bundles=mail_bundles
                )
            except MailError:
                pass
        else:
            try:
                skicka_overlatelse_bekraftad_agare(from_email, codes[0], to_email, BASE_URL)
            except MailError:
                pass
    else:
        if is_bulk:
            try:
                skicka_bulk_overlatelse_avbojd_agare(
                    from_email, codes, to_email, bundles=mail_bundles
                )
            except MailError:
                pass
        else:
            try:
                skicka_overlatelse_avbojd_agare(from_email, codes[0], to_email)
            except MailError:
                pass

    return templates.TemplateResponse(
        "transfer_done.html",
        {
            "request": request,
            "codes": codes,
            "bundles": mail_bundles,
            "accepted": action == "accept",
            "already_handled": False,
            "is_bulk": is_bulk,
        },
    )


@router.get("/{code}")
async def redirect_code(request: Request, code: str):
    if code in RESERVED_CODES:
        raise HTTPException(status_code=404)

    user = get_current_user(request)

    with get_db() as db:
        # Kolla bundles först
        bundle = db.execute(
            "SELECT * FROM bundles WHERE code=? AND status=1", (code,)
        ).fetchone()
        if bundle:
            bundle = dict(bundle)
            sections = db.execute(
                "SELECT * FROM bundle_sections WHERE bundle_id=? ORDER BY sort_order, id",
                (bundle["id"],),
            ).fetchall()
            sections = [dict(s) for s in sections]
            section_map = {s["id"]: s for s in sections}

            items = db.execute(
                "SELECT * FROM bundle_items WHERE bundle_id=? ORDER BY sort_order, id",
                (bundle["id"],),
            ).fetchall()
            items = [dict(i) for i in items]

            # Gruppera items per sektion
            from collections import defaultdict
            grouped: dict = defaultdict(list)
            unsectioned = []
            for item in items:
                if item["section_id"] and item["section_id"] in section_map:
                    grouped[item["section_id"]].append(item)
                else:
                    unsectioned.append(item)

            theme = bundle["theme"]
            kiosk = request.query_params.get("kiosk") == "1"
            db.execute("INSERT INTO bundle_views (bundle_id) VALUES (?)", (bundle["id"],))

            body_html = Markup(markdown.markdown(
                bundle.get("body_md") or "",
                extensions=["nl2br"],
            )) if bundle.get("body_md") else None

            return templates.TemplateResponse(
                "bundle.html",
                {
                    "request": request,
                    "user": user,
                    "bundle": bundle,
                    "sections": sections,
                    "grouped": dict(grouped),
                    "unsectioned": unsectioned,
                    "theme": theme,
                    "kiosk": kiosk,
                    "base_url": BASE_URL,
                    "body_html": body_html,
                },
            )

        # Sedan kortlänkar
        row = db.execute(
            "SELECT id, target_url FROM links WHERE code=? AND status=?",
            (code, LinkStatus.ACTIVE),
        ).fetchone()

        if not row:
            return templates.TemplateResponse(
                "404.html",
                {"request": request, "code": code},
                status_code=404,
            )

        db.execute("INSERT INTO clicks (link_id) VALUES (?)", (row["id"],))
        db.execute(
            "UPDATE links SET last_used_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],)
        )

    return RedirectResponse(url=row["target_url"], status_code=302)


@router.get("/request/takeover")
async def takeover_form(request: Request, code: str = ""):
    user = get_current_user(request)
    return templates.TemplateResponse(
        "takeover_form.html",
        {"request": request, "user": user, "code": code},
    )


@router.post("/request/takeover")
async def takeover_post(
    request: Request,
    code: str = Form(...),
    email: str = Form(...),
    reason: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    email = email.strip().lower()
    ip = request.client.host if request.client else "unknown"

    errors = {}

    email_error = validate_email(email, allow_any_domain=user_allows_any_domain(email))
    if email_error:
        errors["email"] = email_error

    code = code.strip().lower()
    if not code:
        errors["code"] = "Kod saknas."

    if errors:
        user = get_current_user(request)
        return templates.TemplateResponse(
            "takeover_form.html",
            {"request": request, "user": user, "code": code, "errors": errors,
             "values": {"email": email, "reason": reason}},
            status_code=422,
        )

    with get_db() as db:
        if not check_rate_limit(db, ip, "takeover"):
            user = get_current_user(request)
            return templates.TemplateResponse(
                "takeover_form.html",
                {"request": request, "user": user, "code": code,
                 "errors": {"general": "För många begäranden. Försök igen om en stund."},
                 "values": {"email": email, "reason": reason}},
                status_code=429,
            )

        link_row = db.execute(
            "SELECT id FROM links WHERE code=? AND status IN (1, 0)", (code,)
        ).fetchone()

        if not link_row:
            user = get_current_user(request)
            bundle_row = db.execute(
                "SELECT id FROM bundles WHERE code=? AND status=1", (code,)
            ).fetchone()
            if bundle_row:
                return RedirectResponse(
                    url=f"/request/bundle-takeover?code={code}",
                    status_code=303,
                )
            return templates.TemplateResponse(
                "takeover_form.html",
                {"request": request, "user": user, "code": code,
                 "errors": {"code": f"Koden '{code}' finns inte eller är inte aktiv."},
                 "values": {"email": email, "reason": reason}},
                status_code=422,
            )

        db.execute(
            "INSERT INTO takeover_requests (link_id, requester_email, reason) VALUES (?,?,?)",
            (link_row["id"], email, reason.strip() or None),
        )
        req_id = db.execute(
            "SELECT last_insert_rowid() AS id"
        ).fetchone()["id"]

        admin_emails = [
            r["email"]
            for r in db.execute("SELECT email FROM users WHERE is_admin=1").fetchall()
        ]

    approve_url = f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'approve')}"
    reject_url = f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'reject')}"
    admin_url = f"{BASE_URL}/admin/takeover-requests"
    for admin_email in admin_emails:
        try:
            skicka_overlatelse_notis_admin(
                admin_email, code, email, reason.strip() or None,
                approve_url, reject_url, admin_url,
            )
        except MailError:
            pass

    return templates.TemplateResponse(
        "takeover_sent.html",
        {"request": request, "code": code, "email": email},
    )


@router.get("/request/bundle-takeover")
async def bundle_takeover_form(request: Request, code: str = ""):
    user = get_current_user(request)
    bundle = None
    if code:
        with get_db() as db:
            row = db.execute(
                "SELECT id, name FROM bundles WHERE code=? AND status=1", (code,)
            ).fetchone()
            if row:
                bundle = dict(row)
    return templates.TemplateResponse(
        "takeover_form.html",
        {"request": request, "user": user, "code": code, "kind": "bundle", "bundle": bundle},
    )


@router.post("/request/bundle-takeover")
async def bundle_takeover_post(
    request: Request,
    code: str = Form(...),
    email: str = Form(...),
    reason: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)

    ip = request.client.host if request.client else "unknown"
    errors: dict = {}

    email = email.strip().lower()
    email_err = validate_email(email, allow_any_domain=user_allows_any_domain(email))
    if email_err:
        errors["email"] = email_err

    code = code.strip().lower()
    if not code:
        errors["code"] = "Ange en samlingskod."

    if errors:
        user = get_current_user(request)
        return templates.TemplateResponse(
            "takeover_form.html",
            {"request": request, "user": user, "code": code, "kind": "bundle",
             "errors": errors, "values": {"email": email, "reason": reason}},
            status_code=422,
        )

    with get_db() as db:
        if not check_rate_limit(db, ip, "takeover"):
            user = get_current_user(request)
            return templates.TemplateResponse(
                "takeover_form.html",
                {"request": request, "user": user, "code": code, "kind": "bundle",
                 "errors": {"general": "För många begäranden. Försök igen om en stund."},
                 "values": {"email": email, "reason": reason}},
                status_code=429,
            )

        bundle_row = db.execute(
            "SELECT id, name FROM bundles WHERE code=? AND status=1", (code,)
        ).fetchone()

        if not bundle_row:
            user = get_current_user(request)
            return templates.TemplateResponse(
                "takeover_form.html",
                {"request": request, "user": user, "code": code, "kind": "bundle",
                 "errors": {"code": f"Koden '{code}' finns inte eller är inte en aktiv samling."},
                 "values": {"email": email, "reason": reason}},
                status_code=422,
            )

        db.execute(
            "INSERT INTO bundle_takeover_requests (bundle_id, requester_email, reason) VALUES (?,?,?)",
            (bundle_row["id"], email, reason.strip() or None),
        )
        req_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        admin_emails = [
            r["email"]
            for r in db.execute("SELECT email FROM users WHERE is_admin=1").fetchall()
        ]

    bundle_name = bundle_row["name"]
    approve_url = f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'approve', kind='bundle')}"
    reject_url = f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'reject', kind='bundle')}"
    admin_url = f"{BASE_URL}/admin/takeover-requests"
    for admin_email in admin_emails:
        try:
            skicka_bundle_overlatelse_notis_admin(
                admin_email, code, bundle_name, email, reason.strip() or None,
                approve_url, reject_url, admin_url,
            )
        except MailError:
            pass

    return templates.TemplateResponse(
        "takeover_sent.html",
        {"request": request, "code": code, "email": email, "kind": "bundle", "bundle_name": bundle_name},
    )
