"""The people who take appointments. Entered by the business; starts empty and nothing here is ever generated."""
import json

from . import db
from .config import now
from .crm import normalize_phone
from .safety import clean_line


class Conflict(Exception):
    """Safe to show to the user."""


def _row(r):
    return r and {**r, "products": json.loads(r["products"])}


def get(mid: int) -> dict | None:
    return _row(db.one("SELECT * FROM team WHERE id=?", mid))


def members() -> list[dict]:
    return [_row(r) for r in db.rows("SELECT * FROM team WHERE deleted_at IS NULL ORDER BY name COLLATE NOCASE")]


def save(m: dict, mid: int | None = None) -> dict:
    from . import products
    name, phone = clean_line(m["name"])[:60], m["phone"].strip()
    if not name:
        raise ValueError("Enter a name")
    phone = normalize_phone(phone) if phone else ""
    pids = [p["id"] for p in products.all_products() if p["id"] in m["products"]]
    if not pids:
        raise ValueError("Choose at least one product this person handles")
    with db.tx() as con:
        if con.execute("SELECT 1 FROM team WHERE deleted_at IS NULL AND name=? COLLATE NOCASE AND id<>?", (name, mid or 0)).fetchone():
            raise Conflict("Someone on the team already has that name")
        if mid:
            if not con.execute("UPDATE team SET name=?, phone=?, products=? WHERE id=? AND deleted_at IS NULL", (name, phone, json.dumps(pids), mid)).rowcount:
                raise Conflict("That person was removed")
            # upcoming bookings for a product they no longer handle go back to unassigned
            con.execute(f"""UPDATE bookings SET member_id=NULL WHERE member_id=? AND status='confirmed' AND slot_id IN
                            (SELECT id FROM slots WHERE start>? AND product_id NOT IN ({','.join('?' * len(pids))}))""", (mid, now().isoformat(), *pids))
        else:
            mid = con.execute("INSERT INTO team(name,phone,products) VALUES(?,?,?)", (name, phone, json.dumps(pids))).lastrowid
    return get(mid)


def remove(mid: int) -> int:
    """Upcoming bookings become unassigned; past ones keep the name. Returns how many were unassigned."""
    with db.tx() as con:
        con.execute("UPDATE team SET deleted_at=? WHERE id=?", (db.iso(), mid))
        return con.execute("UPDATE bookings SET member_id=NULL WHERE member_id=? AND status='confirmed' AND slot_id IN (SELECT id FROM slots WHERE start>?)",
                           (mid, now().isoformat())).rowcount
