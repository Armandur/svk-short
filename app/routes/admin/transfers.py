"""Admin-vy för pågående överlåtelser (transfer_requests + bundle_transfers)."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.csrf import get_csrf_secret, validate_csrf_token
from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import templates

from .helpers import pending_takeover_count

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/transfers")
async def pending_transfers(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        takeovers = pending_takeover_count(db)

        link_transfers = [
            dict(r)
            for r in db.execute(
                """SELECT tr.id, tr.to_email, tr.created_at,
                          l.code, l.id AS link_id,
                          u.email AS from_email
                   FROM transfer_requests tr
                   JOIN links l ON tr.link_id=l.id
                   JOIN users u ON tr.from_user_id=u.id
                   WHERE tr.status='pending'
                   ORDER BY tr.created_at""",
            ).fetchall()
        ]

        bundle_transfers = [
            dict(r)
            for r in db.execute(
                """SELECT bt.id, bt.to_email, bt.created_at,
                          b.code, b.name AS bundle_name, b.id AS bundle_id,
                          u.email AS from_email
                   FROM bundle_transfers bt
                   JOIN bundles b ON bt.bundle_id=b.id
                   JOIN users u ON b.owner_id=u.id
                   WHERE bt.used_at IS NULL
                   ORDER BY bt.created_at""",
            ).fetchall()
        ]

    return templates.TemplateResponse(
        "admin/transfers.html",
        {
            "request": request,
            "user": admin,
            "link_transfers": link_transfers,
            "bundle_transfers": bundle_transfers,
            "pending_takeovers": takeovers,
        },
    )


@router.post("/transfers/link/{req_id}/cancel")
async def cancel_link_transfer(request: Request, req_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT id FROM transfer_requests WHERE id=? AND status='pending'", (req_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        now = datetime.now(UTC).replace(tzinfo=None).isoformat()
        db.execute(
            "UPDATE transfer_requests SET status='cancelled', resolved_at=? WHERE id=?",
            (now, req_id),
        )

    return RedirectResponse(url="/admin/transfers?cancelled=link", status_code=303)


@router.post("/transfers/bundle/{transfer_id}/cancel")
async def cancel_bundle_transfer(request: Request, transfer_id: int, csrf_token: str = Form(...)):
    if not validate_csrf_token(csrf_token, get_csrf_secret(request)):
        raise HTTPException(status_code=403)
    get_admin_or_redirect(request)

    with get_db() as db:
        row = db.execute(
            "SELECT id FROM bundle_transfers WHERE id=? AND used_at IS NULL", (transfer_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        db.execute("DELETE FROM bundle_transfers WHERE id=?", (transfer_id,))

    return RedirectResponse(url="/admin/transfers?cancelled=bundle", status_code=303)
