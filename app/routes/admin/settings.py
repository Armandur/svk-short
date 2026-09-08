"""Admin-routes för redigering av webbplatsinnehåll.

Om-sidan, integritetssidan och nyhetssidan. Alla tre är markdown i
site_settings och delar redigeraren i admin/om_edit.html - skillnaden är
vilken nyckel som läses och vart den publika länken pekar.
"""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import NOTISNIVAER, templates

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


@router.get("/nyheter")
async def admin_edit_nyheter(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute("SELECT value FROM site_settings WHERE key='changelog_content'").fetchone()
        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/om_edit.html",
        {
            "request": request,
            "user": admin,
            "content": row["value"] if row else "",
            "pending_takeovers": takeovers,
            "saved": request.query_params.get("saved") == "1",
            "page_title": "Nyheter",
            "admin_path": "/admin/nyheter",
            "public_path": "/nyheter",
        },
    )


@router.post("/nyheter")
async def admin_save_nyheter(
    request: Request, content: str = Form(...), csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute(
            """INSERT INTO site_settings (key, value) VALUES ('changelog_content', ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (content,),
        )

    return RedirectResponse(url="/admin/nyheter?saved=1", status_code=303)


@router.get("/notis")
async def admin_edit_notis(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        rader = db.execute(
            "SELECT key, value FROM site_settings WHERE key IN ('notice_content', 'notice_level')"
        ).fetchall()
        takeovers = pending_takeover_count(db)
    varden = {r["key"]: r["value"] for r in rader}

    return templates.TemplateResponse(
        "admin/notis_edit.html",
        {
            "request": request,
            "user": admin,
            "content": varden.get("notice_content", ""),
            "niva": varden.get("notice_level", "info"),
            "nivaer": NOTISNIVAER,
            "pending_takeovers": takeovers,
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/notis")
async def admin_save_notis(
    request: Request,
    content: str = Form(""),
    niva: str = Form("info"),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    # Nivån kommer från ett formulär och är därmed indata utifrån, även om
    # den ser ut som ett val mellan två knappar. Ett okänt värde blir info
    # och inte ett fel: bannern ska visas, det är hela poängen med den.
    if niva not in NOTISNIVAER:
        niva = "info"

    with get_db() as db:
        for nyckel, varde in (("notice_content", content), ("notice_level", niva)):
            db.execute(
                """INSERT INTO site_settings (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (nyckel, varde),
            )

    return RedirectResponse(url="/admin/notis?saved=1", status_code=303)


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
