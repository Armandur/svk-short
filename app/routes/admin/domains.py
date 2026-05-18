"""Admin-routes för att hantera tillåtna måldomäner för kortlänkar.

Tabellen allowed_domains styr vilka domäner vanliga användare får peka
kortlänkar mot. Se app/domains.py för normalisering och matchningslogik.
"""

from urllib.parse import quote, urlparse

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.domains import match_domain, normalize_domain, validate_domain
from app.templating import templates

from .helpers import pending_takeover_count

router = APIRouter()


def _redirect_error(msg: str) -> RedirectResponse:
    return RedirectResponse(url=f"/admin/domaner?error={quote(msg)}", status_code=303)


@router.get("/domaner")
async def admin_domains(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        domains = db.execute(
            """SELECT id, domain, include_subdomains, allow_free_url, note, created_at
               FROM allowed_domains ORDER BY domain"""
        ).fetchall()
        link_urls = db.execute("SELECT target_url FROM links").fetchall()
        takeovers = pending_takeover_count(db)

    # Räkna kortlänkar per domän. Varje länk tillskrivs den första domän som
    # matchar (samma ordning som validate_target_url använder).
    link_counts = {d["id"]: 0 for d in domains}
    for row in link_urls:
        host = urlparse(row["target_url"]).netloc.lower()
        matched = match_domain(host, domains)
        if matched is not None:
            link_counts[matched["id"]] += 1

    return templates.TemplateResponse(
        "admin/domains.html",
        {
            "request": request,
            "user": admin,
            "domains": domains,
            "link_counts": link_counts,
            "pending_takeovers": takeovers,
            "error": request.query_params.get("error") or "",
            "saved": request.query_params.get("saved") == "1",
        },
    )


@router.post("/domaner/add")
async def admin_domains_add(
    request: Request,
    domain: str = Form(...),
    include_subdomains: str = Form(""),
    allow_free_url: str = Form(""),
    note: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    normalized = normalize_domain(domain)
    err = validate_domain(normalized)
    if err:
        return _redirect_error(err)

    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM allowed_domains WHERE domain=?", (normalized,)
        ).fetchone()
        if existing:
            return _redirect_error(f"Domänen {normalized} finns redan i listan.")
        db.execute(
            """INSERT INTO allowed_domains (domain, include_subdomains, allow_free_url, note)
               VALUES (?, ?, ?, ?)""",
            (
                normalized,
                1 if include_subdomains else 0,
                1 if allow_free_url else 0,
                note.strip() or None,
            ),
        )

    return RedirectResponse(url="/admin/domaner?saved=1", status_code=303)


@router.post("/domaner/{domain_id}/delete")
async def admin_domains_delete(request: Request, domain_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM allowed_domains").fetchone()[0]
        if count <= 1:
            return _redirect_error(
                "Minst en domän måste finnas kvar - annars kan ingen skapa kortlänkar."
            )
        db.execute("DELETE FROM allowed_domains WHERE id=?", (domain_id,))

    return RedirectResponse(url="/admin/domaner", status_code=303)


@router.post("/domaner/{domain_id}/toggle-subdomains")
async def admin_domains_toggle_subdomains(
    request: Request, domain_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute(
            "UPDATE allowed_domains SET include_subdomains = 1 - include_subdomains WHERE id=?",
            (domain_id,),
        )

    return RedirectResponse(url="/admin/domaner", status_code=303)


@router.post("/domaner/{domain_id}/toggle-free-url")
async def admin_domains_toggle_free_url(
    request: Request, domain_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        db.execute(
            "UPDATE allowed_domains SET allow_free_url = 1 - allow_free_url WHERE id=?",
            (domain_id,),
        )

    return RedirectResponse(url="/admin/domaner", status_code=303)
