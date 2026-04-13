import secrets
from datetime import datetime, timedelta

import markdown as md
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse

from app.database import get_db
from app.auth import get_current_user, create_session_cookie, COOKIE_NAME, create_takeover_action_token, decode_transfer_action_token
from app.mail import (
    skicka_verifieringsmail,
    skicka_overdragelse_notis_admin,
    skicka_overdragelse_bekraftad_agare,
    skicka_overdragelse_avbojd_agare,
    skicka_bulk_overdragelse_bekraftad_agare,
    skicka_bulk_overdragelse_avbojd_agare,
    MailError,
)
from app.validation import validate_target_url, validate_code, validate_email
from app.config import BASE_URL, RATE_LIMIT_PER_HOUR, LinkStatus, RESERVED_CODES
from app.csrf import validate_csrf_token
from app.templating import templates

router = APIRouter()


def _allow_any_domain(email: str) -> bool:
    """Returnerar True om användaren finns i DB med allow_any_domain=1."""
    with get_db() as db:
        row = db.execute(
            "SELECT allow_any_domain FROM users WHERE email=?", (email,)
        ).fetchone()
    return bool(row["allow_any_domain"]) if row else False


def _allow_external_urls(email: str) -> bool:
    """Returnerar True om användaren finns i DB med allow_external_urls=1."""
    with get_db() as db:
        row = db.execute(
            "SELECT allow_external_urls FROM users WHERE email=?", (email,)
        ).fetchone()
    return bool(row["allow_external_urls"]) if row else False


def _check_rate_limit(db, ip: str, action: str) -> bool:
    """Returns True if allowed, False if rate limited."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    count = db.execute(
        "SELECT COUNT(*) FROM rate_limits WHERE ip=? AND action=? AND created_at > ?",
        (ip, action, cutoff.isoformat()),
    ).fetchone()[0]
    if count >= RATE_LIMIT_PER_HOUR:
        return False
    db.execute(
        "INSERT INTO rate_limits (ip, action) VALUES (?, ?)", (ip, action)
    )
    return True


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
        featured = db.execute(
            """SELECT id, code, note, featured_title, featured_icon
               FROM links
               WHERE is_featured=1 AND status=1
               ORDER BY featured_sort, created_at""",
        ).fetchall()
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "user": user, "featured": [dict(r) for r in featured]},
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
    return templates.TemplateResponse(
        "bestall.html",
        {"request": request, "user": user, "own_links": own_links},
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
            allow_external=_allow_external_urls(current_user["email"]),
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
            if not _check_rate_limit(db, ip, "request"):
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
                existing = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
                if existing:
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

    email_error = validate_email(email, allow_any_domain=_allow_any_domain(email))
    if email_error:
        errors["email"] = email_error

    url_error = validate_target_url(target_url, allow_external=_allow_external_urls(email))
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
        if not _check_rate_limit(db, ip, "request"):
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
            existing = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
            if existing:
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
    content_html = md.markdown(row["value"] if row else "", extensions=["nl2br"])
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
    content_html = md.markdown(row["value"] if row else "", extensions=["nl2br"])
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
        if not _check_rate_limit(db, ip, "resend"):
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
        existing = db.execute("SELECT id FROM links WHERE code=?", (code,)).fetchone()
    if existing:
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

    email_error = validate_email(email, allow_any_domain=_allow_any_domain(email))
    if email_error:
        errors["email"] = email_error

    url_error = validate_target_url(target_url, allow_external=_allow_external_urls(email))
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
        if not _check_rate_limit(db, ip, "request"):
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
async def verify(request: Request, token: str):
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


@router.get("/transfer-action/{token}")
async def transfer_action(request: Request, token: str):
    data = decode_transfer_action_token(token)
    if not data:
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Länken är ogiltig eller har gått ut (7 dagar)."},
            status_code=400,
        )

    action = data.get("action")
    if action not in ("accept", "decline"):
        raise HTTPException(status_code=400)

    # Bulk-token kodar req_ids (lista), enstaka token kodar req_id (int)
    is_bulk = "req_ids" in data
    req_ids = data["req_ids"] if is_bulk else [data["req_id"]]

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

        if not rows:
            raise HTTPException(status_code=404)

        # Om alla redan är hanterade — visa resultatsidan direkt
        if all(r["status"] != "pending" for r in rows):
            return templates.TemplateResponse(
                "transfer_done.html",
                {
                    "request": request,
                    "codes": [r["code"] for r in rows],
                    "already_handled": True,
                    "accepted": rows[0]["status"] == "accepted",
                    "is_bulk": is_bulk,
                },
            )

        now = datetime.utcnow().isoformat()
        to_email = rows[0]["to_email"]
        from_email = rows[0]["from_email"]
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
                skicka_bulk_overdragelse_bekraftad_agare(from_email, codes, to_email, BASE_URL)
            except MailError:
                pass
        else:
            try:
                skicka_overdragelse_bekraftad_agare(from_email, codes[0], to_email, BASE_URL)
            except MailError:
                pass
    else:
        if is_bulk:
            try:
                skicka_bulk_overdragelse_avbojd_agare(from_email, codes, to_email)
            except MailError:
                pass
        else:
            try:
                skicka_overdragelse_avbojd_agare(from_email, codes[0], to_email)
            except MailError:
                pass

    return templates.TemplateResponse(
        "transfer_done.html",
        {
            "request": request,
            "codes": codes,
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
    referer = request.headers.get("referer")

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
            db.execute(
                "INSERT INTO bundle_views (bundle_id, referer) VALUES (?,?)",
                (bundle["id"], referer),
            )

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

        db.execute(
            "INSERT INTO clicks (link_id, referer) VALUES (?,?)",
            (row["id"], referer),
        )
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

    email_error = validate_email(email, allow_any_domain=_allow_any_domain(email))
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
        if not _check_rate_limit(db, ip, "takeover"):
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
            skicka_overdragelse_notis_admin(
                admin_email, code, email, reason.strip() or None,
                approve_url, reject_url, admin_url,
            )
        except MailError:
            pass

    return templates.TemplateResponse(
        "takeover_sent.html",
        {"request": request, "code": code, "email": email},
    )
