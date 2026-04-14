"""Admin-routes för användarhantering: lista, skapa, rättigheter, massöverlåtelse."""

import secrets
import urllib.parse
from datetime import datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import BASE_URL, LinkStatus
from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import templates
from app.validation import validate_email

from .helpers import pending_takeover_count

router = APIRouter()


@router.get("/users")
async def admin_users(request: Request, q: str = ""):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        where = "WHERE u.email LIKE ?" if q else ""
        params = [f"%{q}%"] if q else []

        users = db.execute(
            f"""SELECT u.id, u.email, u.is_admin, u.allow_any_domain, u.allow_external_urls,
                       u.created_at, u.last_login,
                       (SELECT COUNT(*) FROM links WHERE owner_id=u.id) AS total_links,
                       (SELECT COUNT(*) FROM links WHERE owner_id=u.id AND status=1) AS active_links,
                       (SELECT COUNT(*) FROM links WHERE owner_id=u.id AND status=0) AS pending_links,
                       (SELECT COUNT(*) FROM links WHERE owner_id=u.id AND status IN (2,3)) AS disabled_links,
                       (SELECT COUNT(*) FROM bundles WHERE owner_id=u.id) AS total_bundles,
                       (SELECT COUNT(*) FROM bundles WHERE owner_id=u.id AND status=1) AS active_bundles,
                       (SELECT COUNT(*) FROM bundles WHERE owner_id=u.id AND status IN (2,3)) AS disabled_bundles
                FROM users u
                {where}
                ORDER BY u.created_at DESC""",
            params,
        ).fetchall()

        stats = db.execute(
            """SELECT COUNT(*) AS total_users,
                      SUM(is_admin) AS total_admins,
                      (SELECT COUNT(*) FROM links) AS total_links,
                      (SELECT COUNT(*) FROM bundles) AS total_bundles
               FROM users"""
        ).fetchone()

        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "user": admin,
            "users": [dict(r) for r in users],
            "stats": dict(stats),
            "q": q,
            "pending_takeovers": takeovers,
        },
    )


@router.post("/users/create")
async def admin_create_user(
    request: Request,
    email: str = Form(...),
    allow_any_domain: str = Form(""),
    allow_external_urls: str = Form(""),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    email = email.strip().lower()
    err = validate_email(email, allow_any_domain=True)
    if err:
        return RedirectResponse(
            url="/admin/users?" + urllib.parse.urlencode({"create_error": err}),
            status_code=303,
        )

    allow_domain = 1 if allow_any_domain else 0
    allow_ext = 1 if allow_external_urls else 0
    with get_db() as db:
        db.execute(
            "INSERT OR IGNORE INTO users (email, allow_any_domain, allow_external_urls) VALUES (?,?,?)",
            (email, allow_domain, allow_ext),
        )
        if allow_domain or allow_ext:
            db.execute(
                "UPDATE users SET allow_any_domain=?, allow_external_urls=? WHERE email=?",
                (allow_domain, allow_ext, email),
            )

    return RedirectResponse(
        url="/admin/users?" + urllib.parse.urlencode({"created": email}),
        status_code=303,
    )


@router.post("/users/{user_id}/toggle-domain")
async def admin_toggle_domain(request: Request, user_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT allow_any_domain FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        db.execute(
            "UPDATE users SET allow_any_domain=? WHERE id=?",
            (0 if row["allow_any_domain"] else 1, user_id),
        )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-external-urls")
async def admin_toggle_external_urls(
    request: Request, user_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT allow_external_urls FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        db.execute(
            "UPDATE users SET allow_external_urls=? WHERE id=?",
            (0 if row["allow_external_urls"] else 1, user_id),
        )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/transfer-all")
async def admin_transfer_all(
    request: Request,
    user_id: int,
    new_email: str = Form(...),
    csrf_token: str = Form(...),
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)
    new_email = new_email.strip().lower()

    with get_db() as db:
        old_user = db.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
        if not old_user:
            raise HTTPException(status_code=404)

        db.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (new_email,))
        new_user = db.execute("SELECT id FROM users WHERE email=?", (new_email,)).fetchone()

        link_rows = db.execute(
            "SELECT id FROM links WHERE owner_id=?", (user_id,)
        ).fetchall()
        bundle_rows = db.execute(
            "SELECT id, code FROM bundles WHERE owner_id=?", (user_id,)
        ).fetchall()

        db.execute(
            "UPDATE links SET owner_id=? WHERE owner_id=?", (new_user["id"], user_id)
        )
        db.execute(
            "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE owner_id=?",
            (new_user["id"], user_id),
        )

        for link in link_rows:
            db.execute(
                "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
                (
                    "transfer",
                    admin["id"],
                    link["id"],
                    f"bulk move from {old_user['email']} to {new_email}",
                ),
            )
        for bundle in bundle_rows:
            db.execute(
                "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
                (
                    "admin_bundle_transfer",
                    admin["id"],
                    f"bundle:{bundle['id']} (kod={bundle['code']}) bulk-överflytt från {old_user['email']} till {new_email}",
                ),
            )

    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def admin_delete_user(
    request: Request,
    user_id: int,
    confirm_email: str = Form(...),
    action: str = Form("anonymize"),
    transfer_email: str = Form(""),
    csrf_token: str = Form(...),
):
    """Radera en användare direkt (admin-action, utan användarens bekräftelse).

    Admin väljer vad som händer med länkar och samlingar via ``action``:

    - ``anonymize`` (default): owner_id nollställs och aktiva objekt markeras
      DISABLED_ADMIN. Motsvarar självservice-raderingen i
      ``app/routes/user.py:radera_konto_submit``.
    - ``transfer_admin``: länkar och samlingar flyttas till den inloggade
      administratören.
    - ``transfer_email``: länkar och samlingar flyttas till den e-postadress
      som anges i ``transfer_email`` (skapas som användare om den saknas).

    I samtliga fall rensas tokens och pågående överlåtelse-/övertagsförfrågningar
    och själva användarraden tas bort. Admin-konton kan inte raderas denna väg.
    """
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    admin = get_admin_or_redirect(request)

    def err_redirect(msg: str) -> RedirectResponse:
        return RedirectResponse(
            url="/admin/users?" + urllib.parse.urlencode({"delete_error": msg}),
            status_code=303,
        )

    with get_db() as db:
        user_row = db.execute(
            "SELECT id, email, is_admin FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404)

        email = user_row["email"]

        if user_row["is_admin"]:
            return err_redirect(
                f"{email} är admin. Ta bort adminrättigheter innan kontot kan raderas."
            )
        if user_row["id"] == admin["id"]:
            return err_redirect("Du kan inte radera ditt eget konto.")
        if confirm_email.strip().lower() != email.lower():
            return err_redirect(
                f"Bekräftelsen matchade inte {email} — ingen åtgärd utförd."
            )

        # Bestäm mottagare för ev. överlåtelse.
        new_owner_id: int | None = None
        new_owner_email: str | None = None
        if action == "anonymize":
            pass
        elif action == "transfer_admin":
            new_owner_id = admin["id"]
            new_owner_email = admin["email"]
        elif action == "transfer_email":
            target_email = transfer_email.strip().lower()
            if not target_email:
                return err_redirect(
                    "Ange en e-postadress att flytta länkar och samlingar till."
                )
            email_err = validate_email(target_email, allow_any_domain=True)
            if email_err:
                return err_redirect(email_err)
            if target_email == email.lower():
                return err_redirect(
                    "Mottagaren kan inte vara samma konto som ska raderas."
                )
            db.execute(
                "INSERT OR IGNORE INTO users (email) VALUES (?)", (target_email,)
            )
            target_row = db.execute(
                "SELECT id, email FROM users WHERE email=?", (target_email,)
            ).fetchone()
            new_owner_id = target_row["id"]
            new_owner_email = target_row["email"]
        else:
            return err_redirect("Ogiltigt val för hantering av länkar och samlingar.")

        # Logga raderingen (actor_id = admin, rad bevaras efter användaren tas bort).
        if new_owner_id is not None:
            detail_suffix = (
                f"; länkar och samlingar flyttades till {new_owner_email}"
            )
        else:
            detail_suffix = (
                "; länkar och samlingar anonymiserades och avaktiverades"
            )
        db.execute(
            "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
            (
                "admin_delete_user",
                admin["id"],
                f"raderade användaren {email} (id={user_id}){detail_suffix}",
            ),
        )

        if new_owner_id is not None:
            # Hämta rader före överlåtelsen så vi kan logga per länk/samling
            # (matchar beteendet i admin_transfer_all).
            link_rows = db.execute(
                "SELECT id FROM links WHERE owner_id=?", (user_id,)
            ).fetchall()
            bundle_rows = db.execute(
                "SELECT id, code FROM bundles WHERE owner_id=?", (user_id,)
            ).fetchall()

            db.execute(
                "UPDATE links SET owner_id=? WHERE owner_id=?",
                (new_owner_id, user_id),
            )
            db.execute(
                "UPDATE bundles SET owner_id=?, updated_at=CURRENT_TIMESTAMP WHERE owner_id=?",
                (new_owner_id, user_id),
            )

            for link in link_rows:
                db.execute(
                    "INSERT INTO audit_log (action, actor_id, link_id, detail) VALUES (?,?,?,?)",
                    (
                        "transfer",
                        admin["id"],
                        link["id"],
                        f"bulk move from {email} to {new_owner_email} (kontoradering)",
                    ),
                )
            for bundle in bundle_rows:
                db.execute(
                    "INSERT INTO audit_log (action, actor_id, detail) VALUES (?,?,?)",
                    (
                        "admin_bundle_transfer",
                        admin["id"],
                        (
                            f"bundle:{bundle['id']} (kod={bundle['code']}) "
                            f"bulk-överflytt från {email} till {new_owner_email} (kontoradering)"
                        ),
                    ),
                )
        else:
            # Anonymisera länkar: koppla loss från ägaren och avaktivera aktiva.
            db.execute(
                """UPDATE links
                      SET owner_id = NULL,
                          status = CASE WHEN status = ? THEN ? ELSE status END
                    WHERE owner_id = ?""",
                (LinkStatus.ACTIVE, LinkStatus.DISABLED_ADMIN, user_id),
            )
            # Anonymisera samlingar (status 2 = DISABLED_ADMIN för bundles).
            db.execute(
                """UPDATE bundles
                      SET owner_id = NULL,
                          status = CASE WHEN status = 1 THEN 2 ELSE status END,
                          updated_at = CURRENT_TIMESTAMP
                    WHERE owner_id = ?""",
                (user_id,),
            )

        # Anonymisera actor_id i åtgärdsloggen — behåll händelserna.
        db.execute(
            "UPDATE audit_log SET actor_id=NULL WHERE actor_id=?", (user_id,)
        )
        # Rensa tokens och pågående överlåtelse-/övertagsförfrågningar.
        db.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
        db.execute(
            "DELETE FROM transfer_requests WHERE from_user_id=?", (user_id,)
        )
        db.execute(
            "DELETE FROM transfer_requests WHERE to_email=? AND status='pending'",
            (email,),
        )
        db.execute(
            "DELETE FROM takeover_requests WHERE requester_email=? AND status='pending'",
            (email,),
        )
        db.execute(
            "DELETE FROM bundle_takeover_requests WHERE requester_email=? AND status='pending'",
            (email,),
        )
        db.execute(
            "DELETE FROM bundle_transfers WHERE to_email=? AND used_at IS NULL",
            (email,),
        )
        # Radera själva användarraden.
        db.execute("DELETE FROM users WHERE id=?", (user_id,))

    params = {"deleted": email}
    if new_owner_email:
        params["deleted_transferred_to"] = new_owner_email
    return RedirectResponse(
        url="/admin/users?" + urllib.parse.urlencode(params),
        status_code=303,
    )


@router.post("/users/{user_id}/login-link")
async def admin_create_login_link(
    request: Request, user_id: int, csrf_token: str = Form(...)
):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        user_row = db.execute(
            "SELECT id, email FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404)

        token = secrets.token_hex(32)
        expires_at = datetime.utcnow() + timedelta(hours=24)
        db.execute(
            "INSERT INTO tokens (token, user_id, link_id, purpose, expires_at) VALUES (?,?,NULL,?,?)",
            (token, user_row["id"], "login", expires_at.isoformat()),
        )

    params = urllib.parse.urlencode({
        "new_login_link": f"{BASE_URL}/auth/{token}",
        "new_login_for": user_row["email"],
    })
    return RedirectResponse(url=f"/admin/users?{params}", status_code=303)
