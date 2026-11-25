"""CRM: leads, facts, deterministic scoring. The LLM extracts facts; code decides the score."""
import json
import re
from typing import Protocol

from . import db, products, safety
from .booking import bookings

class CRMProvider(Protocol):
    """Implement this for HubSpot, Salesforce, etc. The agent does not change."""
    def get(self, lead_id: int) -> dict | None: ...
    def upsert(self, name: str, phone: str, product_id: str, source: str = "web", context: str = "") -> int: ...
    def merge_facts(self, lead_id: int, facts: dict, call_id: int | None = None) -> dict: ...


def normalize_phone(p: str) -> str:
    d = re.sub(r"\D", "", p)
    if d.startswith("00"):  # 0091...
        d = d[2:]
    elif len(d) == 11 and d[0] == "0":  # 09900000001
        d = d[1:]
    if len(d) == 10:
        d = "91" + d
    if not 11 <= len(d) <= 15:
        raise ValueError("Enter a valid phone number")
    return "+" + d


def score(product: dict, facts: dict, has_visit: bool) -> tuple[int, str, list]:
    """Explainable 0-100 score: 75 from the product's own qualifying facts, 25 from a booked appointment."""
    parts, total = [], sum(f["points"] for f in product["facts"]) or 1
    for f in product["facts"]:
        v, rule, top = facts.get(f["key"]), f["full_if"], f["points"] / total * 75
        if v is None:
            share, why = 0, "unknown"
        else:
            ok = (v >= rule["min"] if "min" in rule else v <= rule["max"] if "max" in rule else
                  any(str(a).lower() in str(v).lower() for a in rule["any_of"]) if "any_of" in rule else True)
            share, why = (1, str(v)) if ok else (0.35, f"{v}: partial fit")
        parts.append({"label": f["label"], "points": round(top * share), "max": round(top), "why": why})
    parts.append({"label": "Intent", "points": 25 if has_visit else 0, "max": 25,
                  "why": f"{product['appointment']} booked or attended" if has_visit else "nothing booked yet"})
    n = min(100, sum(p["points"] for p in parts))
    return n, "Hot" if n >= 70 else "Warm" if n >= 40 else "Cold", parts


class LocalCRM:
    def get(self, lead_id):
        lead = db.one("SELECT * FROM leads WHERE id=?", lead_id)
        if lead:
            lead["facts"] = json.loads(lead["facts"])
            lead["score_parts"] = json.loads(lead["score_parts"])
        return lead

    def upsert(self, name, phone, product_id, source="web", context=""):
        products.get(product_id)  # must exist
        phone = normalize_phone(phone)
        with db.tx() as con:
            con.execute("INSERT INTO leads(name,phone,source,product_id,context,created_at) VALUES(?,?,?,?,?,?) "
                        "ON CONFLICT(phone,product_id) DO UPDATE SET context=CASE WHEN excluded.context='' THEN context ELSE excluded.context END, deleted_at=NULL",
                        (safety.clean_line(name)[:60], phone, source, product_id, safety.clean_text(context)[:1500], db.iso()))
            return con.execute("SELECT id FROM leads WHERE phone=? AND product_id=?", (phone, product_id)).fetchone()["id"]

    def edit(self, lead_id, name, phone, context, do_not_call):
        phone = normalize_phone(phone)
        with db.tx() as con:
            cur = con.execute("SELECT * FROM leads WHERE id=? AND deleted_at IS NULL", (lead_id,)).fetchone()
            if not cur:
                raise KeyError(lead_id)
            if con.execute("SELECT 1 FROM leads WHERE phone=? AND product_id=? AND id<>?", (phone, cur["product_id"], lead_id)).fetchone():
                raise ValueError("Another lead for this product already has that phone number")
            # opting out sets the stage; opting back in returns them to where their history says they are
            stage = cur["stage"]
            if do_not_call:
                stage = "Do not call"
            elif cur["do_not_call"]:
                stage = "Contacted" if con.execute("SELECT 1 FROM calls WHERE lead_id=?", (lead_id,)).fetchone() else "New"
            con.execute("UPDATE leads SET name=?, phone=?, context=?, do_not_call=?, stage=? WHERE id=?",
                        (safety.clean_line(name)[:60], phone, safety.clean_text(context)[:1500], int(do_not_call), stage, lead_id))

    def delete(self, ids):
        """Hide leads, reversibly. Anyone mid-call or with an upcoming booking is left alone and reported."""
        done, kept = [], []
        with db.tx() as con:
            for i in dict.fromkeys(ids):
                lead = con.execute("SELECT name FROM leads WHERE id=? AND deleted_at IS NULL", (i,)).fetchone()
                if not lead:
                    continue
                if con.execute("SELECT 1 FROM calls WHERE lead_id=? AND status='active'", (i,)).fetchone():
                    kept.append({"id": i, "name": lead["name"], "why": "On a call"})
                elif con.execute("""SELECT 1 FROM bookings b JOIN slots s ON s.id=b.slot_id WHERE b.lead_id=? AND b.status='confirmed' AND s.start>?""",
                                 (i, db.now().isoformat())).fetchone():
                    kept.append({"id": i, "name": lead["name"], "why": "Has an upcoming booking"})
                else:
                    con.execute("UPDATE leads SET deleted_at=? WHERE id=?", (db.iso(), i))
                    done.append(i)
        return {"deleted": done, "kept": kept}

    def restore(self, ids):
        with db.tx() as con:
            con.executemany("UPDATE leads SET deleted_at=NULL WHERE id=?", [(i,) for i in ids])

    def merge_facts(self, lead_id, facts, call_id=None):
        visit, product = bookings.active(lead_id) is not None or bookings.attended(lead_id), products.for_lead(lead_id)
        py = {"number": (int, float), "text": str, "list": list}
        with db.tx() as con:
            cur = json.loads(con.execute("SELECT facts FROM leads WHERE id=?", (lead_id,)).fetchone()["facts"])
            for k, kind in products.fact_types(product).items():
                typ = py[kind]
                if kind == "list" and isinstance(v := (facts or {}).get(k), list):
                    facts = {**facts, k: [x for x in v if not safety.sensitive(x)]}
                elif safety.sensitive((facts or {}).get(k)):
                    continue  # health, religion, caste, politics: never stored
                v = (facts or {}).get(k)
                if v in (None, "", []) or isinstance(v, bool) or not isinstance(v, typ):
                    continue  # never overwrite knowledge with blanks or junk
                if kind == "list":
                    cur[k] = list(dict.fromkeys(cur.get(k, []) + [safety.clean_line(x)[:120] for x in v if x]))[-6:]
                elif kind == "text":
                    cur[k] = safety.clean_line(v)[:80]
                elif 0 <= v <= 100000:
                    cur[k] = v
            s, band, parts = score(product, cur, visit)
            con.execute("UPDATE leads SET facts=?,score=?,band=?,score_parts=? WHERE id=?",
                        (json.dumps(cur, ensure_ascii=False), s, band, json.dumps(parts, ensure_ascii=False), lead_id))
        return cur


crm = LocalCRM()
