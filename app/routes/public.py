import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.database import get_db
from app.auth import get_current_user, create_session_cookie, COOKIE_NAME
from app.mail import skicka_verifieringsmail
from app.validation import validate_target_url, validate_code
from app.config import BASE_URL, RATE_LIMIT_PER_HOUR, LinkStatus, RESERVED_CODES

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@router.post("/request")
async def request_link(
    request: Request,
    email: str = Form(...),
    target_url: str = Form(...),
    code: str = Form(""),
    note: str = Form(""),
):
    ip = request.client.host if request.client else "unknown"

    errors = {}

    url_error = validate_target_url(target_url)
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
                        "errors": {"code": f"Koden '{code}' är redan tagen. Välj en annan."},
                        "values": {"email": email, "target_url": target_url, "code": code, "note": note},
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
    skicka_verifieringsmail(email, verify_url, code, target_url)

    return templates.TemplateResponse(
        "pending.html",
        {"request": request, "email": email, "code": code, "target_url": target_url},
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


@router.get("/{code}")
async def redirect_code(request: Request, code: str):
    if code in RESERVED_CODES:
        from fastapi import HTTPException
        raise HTTPException(status_code=404)

    referer = request.headers.get("referer")
    with get_db() as db:
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
