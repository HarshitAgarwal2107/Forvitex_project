"""Booking engine. Limits are checked inside the write transaction, so nothing the LLM or
concurrent callers do can take more than the spots a product allows."""
import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from typing import Protocol

from . import db, products
from .config import now


class BookingError(Exception):
    """Message is safe for the agent to explain to the caller."""


class BookingProvider(Protocol):
    """Implement this for Google Calendar, Cal.com, etc. The agent does not change."""
    def open_slots(self, product_id: str, item: str | None = None, date: str | None = None, limit: int = 6) -> list[dict]: ...
    def active(self, lead_id: int) -> dict | None: ...
    def book(self, lead_id: int, slot_id: int) -> dict: ...
    def reschedule(self, lead_id: int, slot_id: int) -> dict: ...
    def cancel(self, lead_id: int) -> dict: ...


# rep is the assigned team member's name, or empty while unassigned
_SEL = """SELECT b.id, b.status, b.slot_id, s.project, COALESCE(t.name, '') rep, b.member_id, s.start, b.lead_id, s.product_id
          FROM bookings b JOIN slots s ON s.id=b.slot_id LEFT JOIN team t ON t.id=b.member_id"""


def spoken(start: str) -> str:
    d = datetime.fromisoformat(start)
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}, {d.strftime('%I').lstrip('0')} {d.strftime('%p')}"


DAYS = {"monday": "mon सोम", "tuesday": "tue मंगल", "wednesday": "wed बुध", "thursday": "thu गुरु", "friday": "fri शुक्र",
        "saturday": "sat शनि shani", "sunday": "sun रवि itvaar संडे"}


def weekdays_in(text: str) -> list[str]:
    """Weekday names mentioned, English or Hindi."""
    t = text.lower()
    return [d for d, alias in DAYS.items()
            if re.search(rf"\b{d}\b|\b{alias.split()[0]}\b", t) or any(a in t for a in alias.split()[1:])]


def resolve_day(day: str | None) -> str | None:
    """'saturday' / 'tomorrow' / ISO -> ISO date. Code does calendar maths, never the LLM."""
    d = (day or "").strip().lower()
    if not d or d in ("any", "weekend", "this week", "next week", "later"):
        return None
    today = now().date()
    if d in ("today", "आज", "aaj"):
        return today.isoformat()
    if "day after" in d or d in ("परसों", "parson"):
        return (today + timedelta(days=2)).isoformat()
    if "tomorrow" in d or d in ("कल", "kal"):
        return (today + timedelta(days=1)).isoformat()
    if found := weekdays_in(d):
        ahead = (list(DAYS).index(found[0]) - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).isoformat()
    try:
        return datetime.strptime(d, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise BookingError("Use a weekday name, today, tomorrow, or YYYY-MM-DD") from None


def resolve_item(product_id: str, name: str | None) -> str | None:
    if not name:
        return None
    items = products.get(product_id)["items"]
    for i in items:
        if i["name"].lower() in name.lower() or name.lower() in i["name"].lower() or any(a in name.lower() for a in i["aliases"]):
            return i["name"]
    raise BookingError(f"Unknown option. Choose from: {', '.join(i['name'] for i in items)}")


def closed_by(closures: list, product_id: str, start: str):
    """The closure covering this product and time, if any."""
    return next((c for c in closures if c["day"] == start[:10] and c["product_id"] in (None, "", product_id)
                 and c["hour"] in (None, int(start[11:13]))), None)


def overlapping(taken: list[str], start: str, minutes: int) -> int:
    t, span = datetime.fromisoformat(start), timedelta(minutes=minutes)
    return sum(abs(datetime.fromisoformat(x) - t) < span for x in taken)


def busy_members(con, product: dict, start: str, ignore: str = "") -> set[int]:
    """Team members whose bookings, for any product, overlap this time."""
    a = datetime.fromisoformat(start)
    a_end, out = a + timedelta(minutes=product["minutes"]), set()
    for r in con.execute("""SELECT b.member_id, s.start, s.product_id FROM bookings b JOIN slots s ON s.id=b.slot_id
                            WHERE b.status='confirmed' AND b.member_id IS NOT NULL AND b.id<>?""", (ignore,)):
        b = datetime.fromisoformat(r["start"])
        if b < a_end and a < b + timedelta(minutes=products.get(r["product_id"])["minutes"]):
            out.add(r["member_id"])
    return out


class LocalBooking:
    def times(self, product_id, item=None, date=None):
        """Every future slot inside the rules, with its state: open, soon (inside notice), full or closed."""
        prod, stamp = products.get(product_id), now()
        sql, args = "SELECT * FROM slots WHERE product_id=? AND start>? AND substr(start,1,10)<=?", [
            product_id, stamp.isoformat(), (stamp + timedelta(days=prod["horizon"])).date().isoformat()]
        if p := resolve_item(product_id, item):
            sql += " AND project=?"
            args.append(p)
        if date := resolve_day(date):
            sql += " AND substr(start,1,10)=?"
            args.append(date)
        taken = [b["start"] for b in db.rows(_SEL + " WHERE b.status='confirmed' AND s.product_id=?", product_id)]
        closures, names = db.rows("SELECT * FROM closures WHERE day>=?", stamp.date().isoformat()), {i["name"] for i in prod["items"]}
        earliest, out = (stamp + timedelta(hours=prod["notice"])).isoformat(), []
        for s in db.rows(sql + " ORDER BY start, id", *args):
            if s["project"] not in names or int(s["start"][11:13]) not in prod["hours"] or datetime.fromisoformat(s["start"]).weekday() not in prod["days"]:
                continue
            left = max(0, prod["spots"] - overlapping(taken, s["start"], prod["minutes"]))
            c = closed_by(closures, product_id, s["start"])
            s.update(left=left, spots=prod["spots"], closure=c["id"] if c else None,
                     state="closed" if c else "full" if not left else "soon" if s["start"] < earliest else "open")
            out.append(s)
        return out

    def open_slots(self, product_id, item=None, date=None, limit=6, spread=True, staff=False):
        """Times that can be offered. Staff may also use times inside the notice period."""
        prod = products.get(product_id)
        found = [s for s in self.times(product_id, item, date) if s["state"] in (("open", "soon") if staff else ("open",))]
        if not date and spread:  # offer a spread (one morning, one afternoon a day), not one morning's list
            am, pm = [h for h in prod["hours"] if h < 13], [h for h in prod["hours"] if h >= 13]
            pick = {h[len(h) // 2] for h in (am, pm) if h}
            found = [s for s in found if int(s["start"][11:13]) in pick]
        for s in found:
            s["when"] = spoken(s["start"])
        return found[:limit]

    def active(self, lead_id):
        b = db.one(_SEL + " WHERE b.lead_id=? AND b.status='confirmed' AND s.start>?", lead_id, now().isoformat())
        if b:
            b["when"] = spoken(b["start"])
        return b

    def _current(self, con, lead_id):
        # a visit whose time has passed is finished, so it must not block a new one
        con.execute("""UPDATE bookings SET status='completed' WHERE lead_id=? AND status='confirmed'
                       AND slot_id IN (SELECT id FROM slots WHERE start<=?)""", (lead_id, now().isoformat()))
        return con.execute("SELECT id, slot_id, caller_id, call_id, member_id FROM bookings WHERE lead_id=? AND status='confirmed'", (lead_id,)).fetchone()

    @staticmethod
    def _handles(con, member_id, product_id):
        r = con.execute("SELECT products FROM team WHERE id=? AND deleted_at IS NULL", (member_id,)).fetchone()
        return bool(r) and product_id in json.loads(r["products"])

    def _insert(self, con, lead_id, slot_id, caller_id=None, call_id=None, member_id=None):
        slot = con.execute("SELECT * FROM slots WHERE id=?", (slot_id,)).fetchone()
        lead = con.execute("SELECT product_id FROM leads WHERE id=?", (lead_id,)).fetchone()
        if products.get(lead["product_id"])["status"] != "active":
            raise BookingError("Bookings for this are closed.")
        if not slot or slot["product_id"] != lead["product_id"] or slot["start"] <= now().isoformat():
            raise BookingError("That slot does not exist or is in the past. Check slots again.")
        # checked inside the write transaction, so two people can never take the last spot
        prod = products.get(lead["product_id"])
        if closed_by([dict(c) for c in con.execute("SELECT * FROM closures WHERE day=?", (slot["start"][:10],))], prod["id"], slot["start"]):
            raise BookingError("That time is closed. Offer another one.")
        taken = [r["start"] for r in con.execute("SELECT s.start FROM bookings b JOIN slots s ON s.id=b.slot_id WHERE b.status='confirmed' AND s.product_id=?", (prod["id"],))]
        if overlapping(taken, slot["start"], prod["minutes"]) >= prod["spots"]:
            raise BookingError("That slot was just taken. Offer another one.")
        if member_id and (member_id in busy_members(con, prod, slot["start"]) or not self._handles(con, member_id, prod["id"])):
            member_id = None  # the person cannot do the new time, so it goes back to unassigned
        try:
            con.execute("INSERT INTO bookings(id,slot_id,lead_id,created_at,caller_id,call_id,member_id) VALUES(?,?,?,?,?,?,?)",
                        ("BK-" + secrets.token_hex(4).upper(), slot_id, lead_id, db.iso(), caller_id, call_id, member_id))
        except sqlite3.IntegrityError:
            raise BookingError("This person already has a booking.") from None
        con.execute("UPDATE leads SET stage='Visit Scheduled' WHERE id=?", (lead_id,))

    def book(self, lead_id, slot_id, caller_id=None, call_id=None):
        with db.tx() as con:
            cur = self._current(con, lead_id)
            if cur and cur["slot_id"] != slot_id:
                raise BookingError("Caller already has a visit booked. Use reschedule_visit to move it.")
            if not cur:  # same slot again = safe retry, returns the existing booking
                self._insert(con, lead_id, slot_id, caller_id, call_id)
        return self.active(lead_id)

    def reschedule(self, lead_id, slot_id):
        with db.tx() as con:  # release + reserve commit together or not at all
            cur = self._current(con, lead_id)
            if not cur:
                raise BookingError("No existing visit to reschedule. Use book_visit.")
            if cur["slot_id"] != slot_id:
                con.execute("UPDATE bookings SET status='rescheduled' WHERE id=?", (cur["id"],))
                self._insert(con, lead_id, slot_id, cur["caller_id"], cur["call_id"], cur["member_id"])  # credit stays with whoever won it
        return self.active(lead_id)

    def cancel(self, lead_id):
        cur = self.active(lead_id)
        if not cur:
            raise BookingError("No active visit to cancel.")
        with db.tx() as con:
            con.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (cur["id"],))
            con.execute("UPDATE leads SET stage='Contacted' WHERE id=?", (lead_id,))
        return cur

    # ---- used by the Calendar page ----
    def by_id(self, booking_id):
        b = db.one(_SEL + " WHERE b.id=?", booking_id)
        if not b:
            raise BookingError("That booking no longer exists.")
        return b

    def assign(self, booking_id, member_id):
        with db.tx() as con:
            b = con.execute(_SEL + " WHERE b.id=?", (booking_id,)).fetchone()
            if not b or b["status"] != "confirmed" or b["start"] <= now().isoformat():
                raise BookingError("Only an upcoming booking can be assigned.")
            if member_id:
                if not self._handles(con, member_id, b["product_id"]):
                    raise BookingError("Choose someone on your team who handles this product.")
                if member_id in busy_members(con, products.get(b["product_id"]), b["start"], booking_id):
                    raise BookingError("They already have a booking at that time.")
            con.execute("UPDATE bookings SET member_id=? WHERE id=?", (member_id or None, booking_id))

    def outcome(self, booking_id, status):
        with db.tx() as con:
            b = con.execute(_SEL + " WHERE b.id=?", (booking_id,)).fetchone()
            if not b or b["status"] not in ("confirmed", "completed", "attended", "no_show"):
                raise BookingError("That booking was cancelled or moved.")
            if b["start"] > now().isoformat():
                raise BookingError("Record the outcome after the appointment starts.")
            con.execute("UPDATE bookings SET status=? WHERE id=?", (status, booking_id))
            if not con.execute("SELECT 1 FROM bookings b JOIN slots s ON s.id=b.slot_id WHERE b.lead_id=? AND b.status='confirmed' AND s.start>?",
                               (b["lead_id"], now().isoformat())).fetchone():  # a newer booking keeps its own stage
                con.execute("UPDATE leads SET stage=? WHERE id=?", ("Attended" if status == "attended" else "No-show", b["lead_id"]))

    def attended(self, lead_id):
        return db.one("SELECT 1 FROM bookings WHERE lead_id=? AND status='attended'", lead_id) is not None

    def ics(self, booking_id):
        b = db.one(_SEL + " WHERE b.id=?", booking_id)
        if not b:
            return None
        product = products.for_lead(b["lead_id"])
        title = f"{product['company']} {product['appointment']}"
        start = datetime.fromisoformat(b["start"])
        utc = lambda d: (d - timedelta(hours=5, minutes=30)).strftime("%Y%m%dT%H%M%SZ")  # stored in IST
        return "\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sales Agent//EN", "BEGIN:VEVENT",
            f"UID:{b['id']}@sales-desk.example", f"DTSTAMP:{utc(now())}", f"DTSTART:{utc(start)}",
            f"DTEND:{utc(start + timedelta(minutes=product['minutes']))}", f"SUMMARY:{title}: {b['project']}" + (f" with {b['rep']}" if b["rep"] else ""),
            "END:VEVENT", "END:VCALENDAR", ""])


bookings = LocalBooking()
