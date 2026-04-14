"""Gemensamma SQL-helpers för user-routes."""



def fetch_user_links(db, user_id: int) -> list[dict]:
    rows = db.execute(
        """SELECT l.id, l.code, l.target_url, l.status, l.note,
                  l.created_at, l.last_used_at,
                  (SELECT COUNT(*) FROM clicks WHERE link_id=l.id) AS click_count,
                  (SELECT b.id FROM bundles b WHERE b.code=l.code AND b.status=1 LIMIT 1) AS converted_bundle_id
             FROM links l
            WHERE l.owner_id=?
         ORDER BY l.created_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_user_bundles(db, user_id: int) -> list[dict]:
    rows = db.execute(
        """SELECT b.id, b.code, b.name, b.description, b.theme, b.status,
                  b.created_at, b.updated_at,
                  (SELECT COUNT(*) FROM bundle_items WHERE bundle_id=b.id) AS item_count,
                  (SELECT COUNT(*) FROM bundle_views WHERE bundle_id=b.id) AS view_count,
                  (SELECT MAX(viewed_at) FROM bundle_views WHERE bundle_id=b.id) AS last_viewed_at
             FROM bundles b
            WHERE b.owner_id=?
         ORDER BY b.created_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]
