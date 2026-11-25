"""SQLite storage. One short-lived connection per operation; WAL lets the API and
voice worker processes share the file. Swap for Postgres by replacing this module."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, time, timedelta

from .config import DB_PATH, IST, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL, source TEXT DEFAULT 'web',
  stage TEXT DEFAULT 'New', score INTEGER DEFAULT 0, band TEXT DEFAULT 'Cold', score_parts TEXT DEFAULT '[]',
  facts TEXT DEFAULT '{}', sentiment REAL DEFAULT 0, language TEXT DEFAULT 'en',
  memory TEXT DEFAULT '', next_action TEXT DEFAULT '', do_not_call INTEGER DEFAULT 0, created_at TEXT,
  product_id TEXT NOT NULL DEFAULT 'skyline', context TEXT DEFAULT '', caller_id TEXT, deleted_at TEXT, UNIQUE(phone, product_id));
CREATE TABLE IF NOT EXISTS callers(id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT DEFAULT 'active', version INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS products(id TEXT PRIMARY KEY, config TEXT NOT NULL, source TEXT,
  status TEXT DEFAULT 'active', version INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS calls(
  id INTEGER PRIMARY KEY, lead_id INTEGER NOT NULL REFERENCES leads(id), mode TEXT, started_at TEXT, ended_at TEXT,
  status TEXT DEFAULT 'active', attempts INTEGER DEFAULT 0, analysis TEXT, error TEXT, caller_id TEXT);
CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY, call_id INTEGER NOT NULL REFERENCES calls(id), role TEXT, text TEXT,
  sentiment REAL, metrics TEXT DEFAULT '{}', created_at TEXT);
CREATE TABLE IF NOT EXISTS slots(
  id INTEGER PRIMARY KEY, project TEXT, start TEXT,
  product_id TEXT NOT NULL DEFAULT 'skyline', UNIQUE(product_id, project, start));
CREATE TABLE IF NOT EXISTS safety_log(id INTEGER PRIMARY KEY, at TEXT, kind TEXT, ref TEXT, hash TEXT, verdict TEXT, findings TEXT);
CREATE TABLE IF NOT EXISTS team(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT DEFAULT '', products TEXT DEFAULT '[]', deleted_at TEXT);
CREATE TABLE IF NOT EXISTS closures(id INTEGER PRIMARY KEY, product_id TEXT, day TEXT NOT NULL, hour INTEGER, reason TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS bookings(
  id TEXT PRIMARY KEY, slot_id INTEGER NOT NULL REFERENCES slots(id), lead_id INTEGER NOT NULL REFERENCES leads(id),
  status TEXT DEFAULT 'confirmed', created_at TEXT, member_id INTEGER, caller_id TEXT, call_id INTEGER);
DROP INDEX IF EXISTS one_active_booking_per_slot;
CREATE UNIQUE INDEX IF NOT EXISTS one_active_booking_per_lead ON bookings(lead_id) WHERE status='confirmed';
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, call_id INTEGER, lead_id INTEGER, type TEXT, detail TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS turns_by_call ON turns(call_id);
"""


def migrate(con):
    """Pre-product databases: rebuild the two tables whose uniqueness changed, keeping every row."""
    cols = lambda t: [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
    stmts = {m.group(1): m.group(0) for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)\(.*?\);", SCHEMA, re.S)}
    if (old := cols("products")) and "status" not in old:
        con.executescript("ALTER TABLE products ADD COLUMN status TEXT DEFAULT 'active'; ALTER TABLE products ADD COLUMN version INTEGER DEFAULT 1;")
    for t, col in (("bookings", "member_id INTEGER"),
                   ("leads", "caller_id TEXT"), ("leads", "deleted_at TEXT"), ("calls", "caller_id TEXT"), ("bookings", "caller_id TEXT"), ("bookings", "call_id INTEGER")):
        if (old := cols(t)) and col.split()[0] not in old and (t not in ("slots", "leads") or "product_id" in old):
            con.execute(f"ALTER TABLE {t} ADD COLUMN {col}")
    if "rep" in (old := cols("slots")):  # one invented host per slot becomes one slot per offering and time
        pid = "product_id" if "product_id" in old else "'skyline'"
        has_books = bool(cols("bookings"))
        con.executescript("PRAGMA foreign_keys=OFF; BEGIN; DROP INDEX IF EXISTS one_active_booking_per_slot;" + (f"""
            UPDATE bookings SET slot_id=(SELECT MIN(k.id) FROM slots k, slots m WHERE m.id=bookings.slot_id AND k.project=m.project AND k.start=m.start
                                         AND k.{pid if pid[0] != "'" else 'project'}=m.{pid if pid[0] != "'" else 'project'});""" if has_books else "") +
            stmts["slots"].replace("slots(", "slots_new(", 1) +
            f"INSERT INTO slots_new(id,project,start,product_id) SELECT MIN(id),project,start,{pid} FROM slots GROUP BY {pid},project,start;"
            "DROP TABLE slots; ALTER TABLE slots_new RENAME TO slots; COMMIT;")
    for t in ("leads",):
        old = cols(t)
        if old and "product_id" not in old:
            names = ",".join(old)
            con.executescript(f"PRAGMA foreign_keys=OFF; BEGIN; {stmts[t].replace(t + '(', t + '_new(', 1)}"
                              f"INSERT INTO {t}_new({names}) SELECT {names} FROM {t}; DROP TABLE {t}; ALTER TABLE {t}_new RENAME TO {t}; COMMIT;")


def sync_slots(product):
    """One slot per offering and time, for as many days ahead as the product allows. Unused future slots that no
    longer match are removed; slots anyone ever booked are kept, so appointments survive rule changes."""
    names = [i["name"] for i in product["items"]]
    last = (now() + timedelta(days=product["horizon"])).date().isoformat()
    with tx() as c:
        for s in c.execute("SELECT * FROM slots WHERE product_id=? AND start>?", (product["id"], now().isoformat())).fetchall():
            d = datetime.fromisoformat(s["start"])
            fits = s["project"] in names and d.hour in product["hours"] and d.weekday() in product["days"] and s["start"][:10] <= last
            if not fits and not c.execute("SELECT 1 FROM bookings WHERE slot_id=?", (s["id"],)).fetchone():
                c.execute("DELETE FROM slots WHERE id=?", (s["id"],))
        for n in range(product["horizon"] + 1):
            day = (now() + timedelta(days=n)).date()
            if day.weekday() in product["days"]:
                c.executemany("INSERT OR IGNORE INTO slots(product_id,project,start) VALUES(?,?,?)",
                              [(product["id"], name, datetime.combine(day, time(hour), IST).isoformat()) for name in names for hour in product["hours"]])


@contextmanager
def tx():
    con = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def rows(sql, *args):
    """Read-only query; returns plain dicts."""
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def one(sql, *args):
    r = rows(sql, *args)
    return r[0] if r else None


def execute(sql, *args):
    with tx() as con:
        return con.execute(sql, args).lastrowid


def iso():
    return now().isoformat(timespec="seconds")


def log_event(type_, detail, lead_id=None, call_id=None):
    execute("INSERT INTO events(call_id,lead_id,type,detail,created_at) VALUES(?,?,?,?,?)",
            call_id, lead_id, type_, json.dumps(detail, ensure_ascii=False), iso())


def scrub():
    """Bring already stored data up to today's rules: one-line names, and no sensitive details. Safe to repeat."""
    from . import safety
    with tx() as c:
        for r in c.execute("SELECT id, name, facts, memory FROM leads").fetchall():
            facts = json.loads(r["facts"])
            kept = {}
            for k, v in facts.items():
                v = [x for x in v if not safety.sensitive(x)] if isinstance(v, list) else v
                if v != [] and not (isinstance(v, str) and safety.sensitive(v)):
                    kept[k] = v
            name, memory = safety.clean_line(r["name"]), safety.without_sensitive(r["memory"]) if safety.sensitive(r["memory"]) else r["memory"]
            if (name, kept, memory) != (r["name"], facts, r["memory"]):
                c.execute("UPDATE leads SET name=?, facts=?, memory=? WHERE id=?", (name, json.dumps(kept, ensure_ascii=False), memory, r["id"]))
        for r in c.execute("SELECT id, analysis FROM calls WHERE analysis IS NOT NULL").fetchall():
            clean = scrub_analysis(json.loads(r["analysis"]))
            if clean != json.loads(r["analysis"]):
                c.execute("UPDATE calls SET analysis=? WHERE id=?", (json.dumps(clean, ensure_ascii=False), r["id"]))


def scrub_analysis(a: dict) -> dict:
    from . import safety
    out = {}
    for k, v in a.items():
        if isinstance(v, list):
            v = [x for x in v if not safety.sensitive(x)]
        elif isinstance(v, str) and safety.sensitive(v):
            v = safety.without_sensitive(v)
        out[k] = v
    if not out.get("summary") and a.get("summary"):
        out["summary"] = "Summary hidden because it contained private details."
    return out


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    migrate(con)
    con.executescript(SCHEMA)
    con.close()
    from . import callers, products  # need the tables above
    # insights are counted across calls now, so old one-call AI opinions are dropped
    execute("UPDATE calls SET analysis=json_remove(analysis,'$.business_insight') WHERE analysis LIKE '%business_insight%'")
    callers.seed()
    with tx() as c:  # calls made before callers existed were all Aanya's
        c.execute("UPDATE calls SET caller_id='aanya' WHERE caller_id IS NULL")
        c.execute("""UPDATE bookings SET call_id=(SELECT e.call_id FROM events e WHERE e.type LIKE 'visit_%' AND e.call_id IS NOT NULL
                     AND json_extract(e.detail,'$.ref')=bookings.id LIMIT 1) WHERE call_id IS NULL""")
        c.execute("UPDATE leads SET caller_id='aanya' WHERE caller_id IS NULL AND id IN (SELECT lead_id FROM calls)")
        c.execute("""UPDATE bookings SET caller_id='aanya' WHERE caller_id IS NULL AND lead_id IN (SELECT lead_id FROM calls WHERE id IN
                     (SELECT call_id FROM events WHERE type='visit_booked')) AND id IN (SELECT json_extract(detail,'$.ref') FROM events WHERE type LIKE 'visit_%')""")
    products.load_files()
    scrub()
    with tx() as c:
        for name, phone, source, pid, ctx in [
                ("Rahul Sharma", "+919900000001", "Website form", "skyline", ""),
                ("Ananya Iyer", "+919900000002", "Portal enquiry", "skyline", ""),
                ("Vikram Singh", "+919900000003", "Referral", "skyline", ""),
                ("Meera Nair", "+919900000004", "Course page", "brightpath", "Downloaded the AI Engineer syllabus. Works in bank operations.")]:
            c.execute("INSERT OR IGNORE INTO leads(name,phone,source,product_id,context,created_at) VALUES(?,?,?,?,?,?)",
                      (name, phone, source, pid, ctx, iso()))
