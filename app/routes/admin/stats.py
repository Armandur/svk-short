"""Admin-route för statistik: klick, sidvisningar och samlingsvisningar."""

from fastapi import APIRouter, Request

from app.database import get_db
from app.deps import get_admin_or_redirect
from app.templating import templates

from .helpers import pending_takeover_count

router = APIRouter()


@router.get("/stats")
async def admin_stats(request: Request):
    admin = get_admin_or_redirect(request)

    with get_db() as db:
        click_stats = db.execute(
            """SELECT date(clicked_at) AS dag, COUNT(*) AS antal
               FROM clicks
               GROUP BY dag ORDER BY dag DESC LIMIT 90"""
        ).fetchall()

        totals = db.execute(
            """SELECT
                COUNT(*) AS total_clicks,
                SUM(clicked_at >= datetime('now', '-7 days')) AS clicks_7d,
                SUM(clicked_at >= datetime('now', '-30 days')) AS clicks_30d
               FROM clicks"""
        ).fetchone()

        top_links = db.execute(
            """SELECT l.id, l.code, COUNT(c.id) AS antal
               FROM clicks c JOIN links l ON c.link_id = l.id
               WHERE c.clicked_at >= datetime('now', '-30 days')
               GROUP BY l.id ORDER BY antal DESC LIMIT 10"""
        ).fetchall()

        pv_stats = db.execute(
            """SELECT date(viewed_at) AS dag, COUNT(*) AS antal
               FROM page_views
               GROUP BY dag ORDER BY dag DESC LIMIT 90"""
        ).fetchall()

        pv_totals = db.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(viewed_at >= datetime('now', '-7 days')) AS last_7d,
                SUM(viewed_at >= datetime('now', '-30 days')) AS last_30d
               FROM page_views"""
        ).fetchone()

        pv_by_path = db.execute(
            """SELECT path, COUNT(*) AS antal
               FROM page_views
               WHERE viewed_at >= datetime('now', '-30 days')
               GROUP BY path ORDER BY antal DESC"""
        ).fetchall()

        bv_stats = db.execute(
            """SELECT date(viewed_at) AS dag, COUNT(*) AS antal
               FROM bundle_views
               GROUP BY dag ORDER BY dag DESC LIMIT 90"""
        ).fetchall()

        bv_totals = db.execute(
            """SELECT
                COUNT(*) AS total,
                SUM(viewed_at >= datetime('now', '-7 days')) AS last_7d,
                SUM(viewed_at >= datetime('now', '-30 days')) AS last_30d
               FROM bundle_views"""
        ).fetchone()

        top_bundles = db.execute(
            """SELECT b.id, b.code, b.name, COUNT(bv.id) AS antal
               FROM bundle_views bv JOIN bundles b ON bv.bundle_id = b.id
               WHERE bv.viewed_at >= datetime('now', '-30 days')
               GROUP BY b.id ORDER BY antal DESC LIMIT 10"""
        ).fetchall()

        takeovers = pending_takeover_count(db)

    return templates.TemplateResponse(
        "admin/stats.html",
        {
            "request": request,
            "user": admin,
            "click_stats": [dict(r) for r in click_stats],
            "totals": dict(totals),
            "top_links": [dict(r) for r in top_links],
            "pv_stats": [dict(r) for r in pv_stats],
            "pv_totals": dict(pv_totals),
            "pv_by_path": [dict(r) for r in pv_by_path],
            "bv_stats": [dict(r) for r in bv_stats],
            "bv_totals": dict(bv_totals),
            "top_bundles": [dict(r) for r in top_bundles],
            "pending_takeovers": takeovers,
        },
    )
