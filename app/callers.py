"""AI callers: who speaks. Name, gender, voice, pace and personality, shared by any product.
A product uses one caller or splits new leads between several by percentage."""
import json
import re

from . import db
from .safety import clean_line

VOICES = {
    "female": ["ishita", "priya", "kavya", "ritu", "pooja", "simran", "shreya", "neha", "roopa", "suhani", "rupali", "tanya", "shruti", "kavitha"],
    "male": ["shubh", "rahul", "amit", "ratan", "rohan", "dev", "manan", "sumit", "aditya", "kabir", "varun", "aayan", "ashutosh", "advait"],
}
DEFAULT_ID = "aanya"
STARTERS = [
    {"id": "aanya", "name": "Aanya", "gender": "female", "voice": "ishita", "pace": 1.05,
     "personality": "Warm and sharp. Friendly, curious about people, quick to put them at ease."},
    {"id": "vikrant", "name": "Vikrant", "gender": "male", "voice": "shubh", "pace": 1.05,
     "personality": "Calm and direct. Gets to the point, gives straight answers, never hurries anyone."},
]


class Conflict(Exception):
    """Safe to show to the user."""


def problems(c: dict) -> list[str]:
    out, t = [], lambda k: str(c.get(k) or "").strip()
    if not re.fullmatch(r"[^\W\d_][^\d_]{1,29}", t("name")):
        out.append("Enter a name of 2 to 30 letters")
    if c.get("gender") not in VOICES:
        out.append("Choose a gender")
    elif c.get("voice") not in VOICES[c["gender"]]:
        out.append("Choose a voice that matches the gender")
    if not isinstance(c.get("pace"), (int, float)) or not 0.8 <= c["pace"] <= 1.3:
        out.append("Pace must be between 0.8 and 1.3")
    if not 10 <= len(t("personality")) <= 400:
        out.append("Describe the personality in 10 to 400 characters")
    return out


def _clean(c):
    return {"name": clean_line(c["name"]), "gender": c["gender"], "voice": c["voice"], "pace": round(float(c["pace"]), 2),
            "personality": clean_line(c["personality"])}


def _row(r):
    return r and {"id": r["id"], **json.loads(r["config"]), "status": r["status"], "version": r["version"]}


def get(cid: str) -> dict | None:
    return _row(db.one("SELECT * FROM callers WHERE id=?", cid))


def all_callers(status="active") -> list[dict]:
    return [_row(r) for r in db.rows("SELECT * FROM callers WHERE status=? ORDER BY rowid", status)]


def seed():
    if not db.one("SELECT 1 FROM callers"):  # first run only, so edits and deletions stay
        for c in STARTERS:
            db.execute("INSERT INTO callers(id,config) VALUES(?,?)", c["id"], json.dumps(_clean(c), ensure_ascii=False))


def create(c: dict) -> dict:
    base = re.sub(r"[^a-z0-9]+", "-", c["name"].lower()).strip("-") or "caller"
    cid, n = base, 2
    while get(cid):
        cid, n = f"{base}-{n}", n + 1
    db.execute("INSERT INTO callers(id,config) VALUES(?,?)", cid, json.dumps(_clean(c), ensure_ascii=False))
    return get(cid)


def update(cid: str, c: dict, version: int) -> dict:
    with db.tx() as con:
        if not con.execute("UPDATE callers SET config=?, version=version+1 WHERE id=? AND version=? AND status='active'",
                           (json.dumps(_clean(c), ensure_ascii=False), cid, version)).rowcount:
            raise Conflict("Someone else changed or deleted this caller. Reload before saving.")
    return get(cid)


def used_by(cid: str) -> list[dict]:
    from . import products
    return [{"id": p["id"], "company": p["company"], "share": x["share"]} for p in products.all_products() for x in p["callers"] if x["id"] == cid]


def delete(cid: str):
    if used := used_by(cid):
        raise Conflict("Still used by " + ", ".join(u["company"] for u in used) + ". Choose another caller there first.")
    if db.one("SELECT 1 FROM calls WHERE caller_id=? AND status='active'", cid):
        raise Conflict("This caller is on a call. Try again when it ends.")
    if len(all_callers()) == 1:
        raise Conflict("Keep at least one caller.")
    db.execute("UPDATE callers SET status='deleted', version=version+1 WHERE id=?", cid)


def restore(cid: str):
    db.execute("UPDATE callers SET status='active', version=version+1 WHERE id=?", cid)


def choose(con, lead_id: int, product: dict) -> str:
    """Who calls this lead. Runs inside the transaction that creates the call.
    A lead keeps the same caller, so relationships and results stay consistent. A new lead goes to whoever is
    furthest below their share, which hits the percentages far sooner than random picks would."""
    alive = {r["id"] for r in con.execute("SELECT id FROM callers WHERE status='active'")}
    split = [x for x in product["callers"] if x["id"] in alive and x["share"] > 0]
    if not split:
        first = DEFAULT_ID if DEFAULT_ID in alive else sorted(alive)[0]
        split = [{"id": first, "share": 100}]
    mine = con.execute("SELECT caller_id FROM leads WHERE id=?", (lead_id,)).fetchone()["caller_id"]
    if mine in [x["id"] for x in split]:
        return mine
    have = {r["caller_id"]: r["n"] for r in con.execute(
        "SELECT caller_id, COUNT(*) n FROM leads WHERE product_id=? AND caller_id IS NOT NULL AND id<>? GROUP BY caller_id", (product["id"], lead_id))}
    total, whole = sum(have.get(x["id"], 0) for x in split) + 1, sum(x["share"] for x in split)
    pick = max(split, key=lambda x: (x["share"] / whole * total - have.get(x["id"], 0), x["share"]))["id"]
    con.execute("UPDATE leads SET caller_id=? WHERE id=?", (pick, lead_id))
    return pick


def for_call(call_id: int) -> dict:
    r = db.one("SELECT caller_id FROM calls WHERE id=?", call_id)
    return (r and r["caller_id"] and get(r["caller_id"])) or get(DEFAULT_ID) or all_callers()[0]
