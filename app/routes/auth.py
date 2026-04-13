"""Autentiseringsflöde: inloggning via magic link och utloggning."""

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import COOKIE_NAME, create_session_cookie, get_current_user
from app.config import BASE_URL
from app.csrf import validate_csrf_token
from app.database import get_db
from app.deps import check_rate_limit, user_allows_any_domain
from app.mail import MailError, skicka_loginmail
from app.templating import templates
from app.validation import validate_email

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/mina-lankar", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login_post(request: Request, email: str = Form(...), csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403)
    email = email.strip().lower()
    email_error = validate_email(email, allow_any_domain=user_allows_any_domain(email))
    if email_error:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": email_error},
            status_code=422,
        )

    ip = request.client.host if request.client else "unknown"

    with get_db() as db:
        if not check_rate_limit(db, ip, "login"):
            return templates.TemplateResponse(
                "login.html",
                {"request": request, "error": "För många försök. Vänta en stund och försök igen."},
                status_code=429,
            )

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        user_row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()

        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(hours=1)
        db.execute(
            "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) VALUES (?,?,NULL,?,?)",
            (token, user_row["id"], "login", expires_at.isoformat()),
        )

    login_url = f"{BASE_URL}/auth/{token}"
    mail_ok = True
    try:
        skicka_loginmail(email, login_url)
    except MailError:
        mail_ok = False

    return templates.TemplateResponse(
        "login_sent.html",
        {"request": request, "email": email, "mail_ok": mail_ok},
    )


@router.get("/auth/{token}")
async def auth_token(request: Request, token: str):
    with get_db() as db:
        row = db.execute(
            "SELECT id, user_id, expires_at, used_at FROM tokens WHERE token=? AND purpose='login'",
            (token,),
        ).fetchone()

        if not row:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "message": "Ogiltig inloggningslänk."},
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
                {"request": request, "message": "Inloggningslänken har gått ut. Begär en ny."},
                status_code=400,
            )

        db.execute(
            "UPDATE tokens SET used_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), row["id"]),
        )
        db.execute(
            "UPDATE users SET last_login=? WHERE id=?",
            (datetime.utcnow().isoformat(), row["user_id"]),
        )

    session_cookie = create_session_cookie(row["user_id"])
    response = RedirectResponse(url="/mina-lankar", status_code=302)
    response.set_cookie(
        COOKIE_NAME,
        session_cookie,
        httponly=True,
        secure=BASE_URL.startswith("https"),
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response
