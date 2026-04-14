import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request

from app.auth import COOKIE_NAME, create_session_cookie, get_current_user
from app.code_generator import generate_unique_code
from app.config import BASE_URL, LinkStatus
from app.csrf import (
    get_anon_csrf_secret,
    get_csrf_secret,
    set_anon_csrf_cookie,
    validate_csrf_token,
)
from app.database import get_db
from app.deps import check_rate_limit, user_allows_any_domain, user_allows_external_urls
from app.mail import (
    MailError,
    skicka_verifieringsmail,
)
from app.templating import templates
from app.validation import validate_code, validate_email, validate_target_url

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/request/resend")
async def resend_verification(
    request: Request,
    code: str = Form(...),
    email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
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

        user_row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
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
            expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=24)
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
    from fastapi.responses import JSONResponse
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
    # Inloggad användare använder sessionscookiens csrf_secret; ej inloggad får anon-cookie.
    anon_secret, is_new = get_anon_csrf_secret(request)
    ctx: dict = {"request": request, "user": user, "own_links": own_links, "active_tab": active_tab}
    if not user:
        ctx["csrf_secret"] = anon_secret
    response = templates.TemplateResponse("bestall.html", ctx)
    if not user and is_new:
        set_anon_csrf_cookie(response, anon_secret)
    return response


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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
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
                code = generate_unique_code(db)
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
                            "errors": {
                                "code": f"Koden '{code}' är redan tagen. Välj en annan eller begär att få ta över den."
                            },
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
                            "errors": {
                                "code": f"Koden '{code}' används för en samling. Välj en annan kod eller begär att få ta över den."
                            },
                            "values": {"target_url": target_url, "code": code, "note": note},
                            "bundle_takeover_code": code,
                        },
                        status_code=422,
                    )

            db.execute(
                "INSERT INTO links (code, target_url, owner_id, status, note) VALUES (?,?,?,?,?)",
                (code, target_url, current_user["id"], LinkStatus.ACTIVE, note or None),
            )

        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/mina-lankar?flash=created:{code}", status_code=303)

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
                    "values": {
                        "email": email,
                        "target_url": target_url,
                        "code": code,
                        "note": note,
                    },
                },
                status_code=429,
            )

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        user_row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        user_id = user_row["id"]

        if not code:
            code = generate_unique_code(db)
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
                        "errors": {
                            "code": f"Koden '{code}' är redan tagen. Välj en annan eller begär att få ta över den."
                        },
                        "values": {
                            "email": email,
                            "target_url": target_url,
                            "code": code,
                            "note": note,
                        },
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
                        "errors": {
                            "code": f"Koden '{code}' används för en samling. Välj en annan kod eller begär att få ta över den."
                        },
                        "values": {
                            "email": email,
                            "target_url": target_url,
                            "code": code,
                            "note": note,
                        },
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
        expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=24)
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
        {
            "request": request,
            "email": email,
            "code": code,
            "target_url": target_url,
            "mail_ok": mail_ok,
        },
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

    if datetime.now(UTC).replace(tzinfo=None) > datetime.fromisoformat(row["expires_at"]):
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": "Länken har gått ut. Beställ en ny kortlänk."},
            status_code=400,
        )

    anon_secret, is_new = get_anon_csrf_secret(request)
    response = templates.TemplateResponse(
        "verify_confirm.html",
        {
            "request": request,
            "token": token,
            "code": row["code"],
            "target_url": row["target_url"],
            "csrf_secret": anon_secret,
        },
    )
    if is_new:
        set_anon_csrf_cookie(response, anon_secret)
    return response


@router.post("/verify/{token}")
async def verify_submit(request: Request, token: str, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
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
        if datetime.now(UTC).replace(tzinfo=None) > expires_at:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Länken har gått ut. Beställ en ny kortlänk."},
                status_code=400,
            )

        db.execute("UPDATE links SET status=? WHERE id=?", (LinkStatus.ACTIVE, row["link_id"]))
        db.execute(
            "UPDATE tokens SET used_at=? WHERE id=?",
            (datetime.now(UTC).replace(tzinfo=None).isoformat(), row["id"]),
        )
        db.execute(
            "UPDATE users SET last_login=? WHERE id=?",
            (datetime.now(UTC).replace(tzinfo=None).isoformat(), row["user_id"]),
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
