"""Admin-routes för redigering av webbplatsinnehåll: om-sidan och integritetssidan."""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import templates

from .helpers import pending_takeover_count

router = APIRouter()


@router.get("/om")
async def admin_edit_om(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute("SELECT value FROM site_settings WHERE key='about_content'").fetchone()
        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/om_edit.html",
        {
            "request": request,
            "user": admin,
            "content": row["value"] if row else "",
            "pending_takeovers": takeovers,
            "saved": request.query_params.get("saved") == "1",
            "page_title": "Om-sidan",
            "admin_path": "/admin/om",
            "public_path": "/om",
        },
    )


@router.post("/om")
async def admin_save_om(request: Request, content: str = Form(...), csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute(
            """INSERT INTO site_settings (key, value) VALUES ('about_content', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (content,),
        )

    return RedirectResponse(url="/admin/om?saved=1", status_code=303)


@router.get("/integritet")
async def admin_edit_integritet(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT value FROM site_settings WHERE key='integritet_content'"
        ).fetchone()
        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/om_edit.html",
        {
            "request": request,
            "user": admin,
            "content": row["value"] if row else "",
            "pending_takeovers": takeovers,
            "saved": request.query_params.get("saved") == "1",
            "page_title": "Integritetssidan",
            "admin_path": "/admin/integritet",
            "public_path": "/integritet",
        },
    )


@router.post("/integritet")
async def admin_save_integritet(
    request: Request, content: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute(
            """INSERT INTO site_settings (key, value) VALUES ('integritet_content', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (content,),
        )

    return RedirectResponse(url="/admin/integritet?saved=1", status_code=303)
