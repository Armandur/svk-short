"""Routes för takeover-förfrågningar: /request/takeover och /request/bundle-takeover."""

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import create_takeover_action_token, get_current_user
from app.config import BASE_URL
from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import check_rate_limit, user_allows_any_domain
from app.mail import (
    MailError,
    skicka_bundle_overlatelse_notis_admin,
    skicka_overlatelse_notis_admin,
)
from app.templating import templates
from app.validation import validate_email

log = logging.getLogger(__name__)
router = APIRouter()


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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
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
            {
                "request": request,
                "user": user,
                "code": code,
                "errors": errors,
                "values": {"email": email, "reason": reason},
            },
            status_code=422,
        )

    with get_db() as db:
        if not check_rate_limit(db, ip, "takeover"):
            user = get_current_user(request)
            return templates.TemplateResponse(
                "takeover_form.html",
                {
                    "request": request,
                    "user": user,
                    "code": code,
                    "errors": {"general": "För många begäranden. Försök igen om en stund."},
                    "values": {"email": email, "reason": reason},
                },
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
                {
                    "request": request,
                    "user": user,
                    "code": code,
                    "errors": {"code": f"Koden '{code}' finns inte eller är inte aktiv."},
                    "values": {"email": email, "reason": reason},
                },
                status_code=422,
            )

        db.execute(
            "INSERT INTO takeover_requests (link_id, requester_email, reason) VALUES (?,?,?)",
            (link_row["id"], email, reason.strip() or None),
        )
        req_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        admin_emails = [
            r["email"] for r in db.execute("SELECT email FROM users WHERE is_admin=1").fetchall()
        ]

    approve_url = (
        f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'approve')}"
    )
    reject_url = (
        f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'reject')}"
    )
    admin_url = f"{BASE_URL}/admin/takeover-requests"
    for admin_email in admin_emails:
        try:
            skicka_overlatelse_notis_admin(
                admin_email,
                code,
                email,
                reason.strip() or None,
                approve_url,
                reject_url,
                admin_url,
            )
        except MailError:
            log.exception("MailError")

    return templates.TemplateResponse(
        "takeover_sent.html",
        {"request": request, "code": code, "email": email},
    )


@router.get("/request/bundle-takeover")
async def bundle_takeover_form(request: Request, code: str = ""):
    code = code.lower()  # P4.1: case-insensitive lookup
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
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
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
            {
                "request": request,
                "user": user,
                "code": code,
                "kind": "bundle",
                "errors": errors,
                "values": {"email": email, "reason": reason},
            },
            status_code=422,
        )

    with get_db() as db:
        if not check_rate_limit(db, ip, "takeover"):
            user = get_current_user(request)
            return templates.TemplateResponse(
                "takeover_form.html",
                {
                    "request": request,
                    "user": user,
                    "code": code,
                    "kind": "bundle",
                    "errors": {"general": "För många begäranden. Försök igen om en stund."},
                    "values": {"email": email, "reason": reason},
                },
                status_code=429,
            )

        bundle_row = db.execute(
            "SELECT id, name FROM bundles WHERE code=? AND status=1", (code,)
        ).fetchone()

        if not bundle_row:
            user = get_current_user(request)
            return templates.TemplateResponse(
                "takeover_form.html",
                {
                    "request": request,
                    "user": user,
                    "code": code,
                    "kind": "bundle",
                    "errors": {
                        "code": f"Koden '{code}' finns inte eller är inte en aktiv samling."
                    },
                    "values": {"email": email, "reason": reason},
                },
                status_code=422,
            )

        db.execute(
            "INSERT INTO bundle_takeover_requests (bundle_id, requester_email, reason) VALUES (?,?,?)",
            (bundle_row["id"], email, reason.strip() or None),
        )
        req_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

        admin_emails = [
            r["email"] for r in db.execute("SELECT email FROM users WHERE is_admin=1").fetchall()
        ]

    bundle_name = bundle_row["name"]
    approve_url = f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'approve', kind='bundle')}"
    reject_url = f"{BASE_URL}/admin/takeover-action/{create_takeover_action_token(req_id, 'reject', kind='bundle')}"
    admin_url = f"{BASE_URL}/admin/takeover-requests"
    for admin_email in admin_emails:
        try:
            skicka_bundle_overlatelse_notis_admin(
                admin_email,
                code,
                bundle_name,
                email,
                reason.strip() or None,
                approve_url,
                reject_url,
                admin_url,
            )
        except MailError:
            log.exception("MailError")

    return templates.TemplateResponse(
        "takeover_sent.html",
        {
            "request": request,
            "code": code,
            "email": email,
            "kind": "bundle",
            "bundle_name": bundle_name,
        },
    )
