"""Business numbers by product and by AI caller. Every figure is counted from stored records, never generated.
Attribution: a call belongs to the caller who made it; a booking to the caller whose call made it (none if staff made it)."""
import json
from collections import Counter
from datetime import datetime, timedelta

from . import callers, db, insights, products
from .config import now

ENOUGH = 20  # leads spoken with before a caller's rates are worth comparing


def _median(v):
    v = sorted(v)
    n = len(v)
    return None if not n else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _load(pid=None) -> dict:
    """All records for one product, or for every active product."""
    ids = [pid] if pid else [p["id"] for p in products.all_products()]
    q = ",".join("?" * len(ids)) or "''"
    return {
        "leads": db.rows(f"SELECT * FROM leads WHERE product_id IN ({q}) AND deleted_at IS NULL", *ids),
        "calls": db.rows(f"SELECT c.* FROM calls c JOIN leads l ON l.id=c.lead_id WHERE l.deleted_at IS NULL AND l.product_id IN ({q}) ORDER BY c.id", *ids),
        "turns": db.rows(f"""SELECT t.role, t.sentiment, t.metrics, t.call_id, c.lead_id, c.caller_id FROM turns t JOIN calls c ON c.id=t.call_id
                             JOIN leads l ON l.id=c.lead_id WHERE l.deleted_at IS NULL AND l.product_id IN ({q})""", *ids),
        "events": db.rows(f"""SELECT e.type, e.lead_id, e.call_id, c.caller_id FROM events e JOIN leads l ON l.id=e.lead_id LEFT JOIN calls c ON c.id=e.call_id
                              WHERE l.deleted_at IS NULL AND l.product_id IN ({q})""", *ids),
        "books": db.rows(f"""SELECT b.id, b.lead_id, b.status, b.caller_id, b.call_id, s.project, COALESCE(tm.name, '') rep, s.start, l.name
                             FROM bookings b JOIN slots s ON s.id=b.slot_id JOIN leads l ON l.id=b.lead_id LEFT JOIN team tm ON tm.id=b.member_id
                             WHERE l.deleted_at IS NULL AND l.product_id IN ({q}) ORDER BY s.start""", *ids),
    }


RESULTS = {"booked": "visit_booked", "moved": "visit_rescheduled", "cancelled": "visit_cancelled", "opted_out": "opted_out", "handover": "human_requested"}


def outcomes(d: dict) -> dict[int, str]:
    """One plain result per call, the most important thing that happened on it."""
    types, spoke = {}, {t["call_id"] for t in d["turns"] if t["role"] == "user"}
    for e in d["events"]:
        types.setdefault(e["call_id"], set()).add(e["type"])
    out = {}
    for c in d["calls"]:
        have = types.get(c["id"], set())
        out[c["id"]] = next((k for k in ("opted_out", "booked", "moved", "cancelled", "handover") if RESULTS[k] in have),
                            "talked" if c["id"] in spoke else "no_answer")
    return out


def select(d: dict, f: dict) -> dict:
    """Keep the calls matching every filter, and only what belongs to those calls."""
    f = {k: v for k, v in f.items() if v}
    if not f:
        return d
    people = {l["id"]: l for l in d["leads"]}
    result = outcomes(d) if "result" in f else {}
    since = (now() - timedelta(days=f["days"] - 1)).date().isoformat() if "days" in f else ""

    def keep(c):
        l = people[c["lead_id"]]
        return (("caller" not in f or c["caller_id"] == f["caller"]) and c["started_at"][:10] >= since and ("mode" not in f or c["mode"] == f["mode"])
                and ("language" not in f or l["language"] == f["language"]) and ("band" not in f or l["band"] == f["band"])
                and ("result" not in f or result[c["id"]] == f["result"]))

    calls = [c for c in d["calls"] if keep(c)]
    ids, who = {c["id"] for c in calls}, {c["lead_id"] for c in calls}
    if set(f) == {"caller"}:  # also people given to this caller but not reached, and credit recorded without a call
        who |= {l["id"] for l in d["leads"] if l["caller_id"] == f["caller"]}
        books = [b for b in d["books"] if b["call_id"] in ids or (b["call_id"] is None and b["caller_id"] == f["caller"])]
    else:
        books = [b for b in d["books"] if b["call_id"] in ids]
    return {"calls": calls, "turns": [t for t in d["turns"] if t["call_id"] in ids], "events": [e for e in d["events"] if e["call_id"] in ids],
            "books": books, "leads": [l for l in d["leads"] if l["id"] in who]}


def _metrics(d: dict, f: dict | None = None, product: dict | None = None) -> dict:
    f = {k: v for k, v in (f or {}).items() if v}
    caller = bool(f)
    d = select(d, f)
    leads, calls, turns, books = d["leads"], d["calls"], d["turns"], d["books"]
    events = Counter(e["type"] for e in d["events"])
    analyses = [json.loads(c["analysis"]) for c in calls if c["analysis"]]
    spoke = {t["lead_id"] for t in turns if t["role"] == "user"}
    booked = {b["lead_id"] for b in books}
    scores = [l["score"] for l in leads if l["id"] in spoke]
    moods = [t["sentiment"] for t in turns if t["sentiment"] is not None]
    latency = [m["e2e_latency"] for t in turns if t["role"] == "assistant" and "e2e_latency" in (m := json.loads(t["metrics"]))]
    seconds = [s for c in calls if c["ended_at"] and c["mode"] == "voice"
               and (s := (datetime.fromisoformat(c["ended_at"]) - datetime.fromisoformat(c["started_at"])).total_seconds()) < 1200]
    stamp = now().isoformat()
    attended = sum(b["status"] == "attended" for b in books)
    held = attended + sum(b["status"] == "no_show" for b in books)
    if caller:  # what was said on the selected calls
        said = Counter(o.strip().lower() for a in analyses for o in (a.get("objections") or []) if isinstance(o, str))
    else:
        said = Counter(o.strip().lower() for l in leads for o in json.loads(l["facts"]).get("objections", []))
    span = max(7, min(f.get("days", 14), 60))
    days = [(now() - timedelta(days=n)).date().isoformat() for n in range(span - 1, -1, -1)]
    per_day = Counter(c["started_at"][:10] for c in calls)
    return {
        "funnel": [["Leads", len(leads)], ["Spoke with", len(spoke)], ["Booked", len(booked & spoke)]],
        "bands": {b: sum(l["band"] == b for l in leads) for b in ("Hot", "Warm", "Cold")},
        "spoke": len(spoke), "booked": len(booked & spoke), "enough": len(spoke) >= ENOUGH,
        "attendance": attended / held if held else None,
        "calls": len(calls), "booked_rate": len(booked & spoke) / len(spoke) if spoke else None,
        "avg_score": sum(scores) / len(scores) if scores else None,
        "avg_mood": sum(moods) / len(moods) if moods else None, "median_reply": _median(latency), "median_call_seconds": _median(seconds),
        "daily": [[x, per_day[x]] for x in days],
        "offerings": Counter(b["project"] for b in books if b["status"] in ("confirmed", "completed", "attended", "no_show")).most_common(),
        "upcoming": [b for b in books if b["status"] == "confirmed" and b["start"] > stamp], "objections": said.most_common(6),
        "handoffs": events["human_requested"], "claims_stopped": events["claim_blocked"],
        "opt_outs": events["opted_out"] if caller else sum(l["do_not_call"] for l in leads),
        "insights": insights.build(d, product),
        "leads": [{"id": l["id"], "name": l["name"], "stage": l["stage"], "score": l["score"], "band": l["band"]}
                  for l in sorted(leads, key=lambda l: -l["score"])],
    }


BRIEF = ("calls", "spoke", "booked", "booked_rate", "attendance", "avg_score", "avg_mood", "median_reply", "opt_outs", "enough")


def for_product(pid: str, f: dict | None = None) -> dict:
    f = f or {}
    product = products.get(pid)
    d, split = _load(pid), {x["id"]: x["share"] for x in product["callers"]}
    out = _metrics(d, f, product)
    ids = list(dict.fromkeys([*split, *(c["caller_id"] for c in d["calls"] if c["caller_id"])]))
    out["by_caller"] = []
    for i in ids:
        m = _metrics(d, {**f, "caller": i})
        out["by_caller"].append({"id": i, "name": (callers.get(i) or {}).get("name", i), "share": split.get(i), **{k: m[k] for k in BRIEF}})
    return out


SORTS = {"newest": lambda c: c["id"], "longest": lambda c: c["seconds"] or 0, "unhappiest": lambda c: -(c["mood"] if c["mood"] is not None else 9),
         "slowest": lambda c: c["reply"] or 0, "score": lambda c: c["score"]}


def calls_report(pid: str, f: dict | None = None, q: str = "", sort: str = "newest") -> list[dict]:
    """One row per call, for the Calls tab and the CSV export."""
    full = _load(pid)
    d, result = select(full, f or {}), outcomes(full)
    people, names = {l["id"]: l for l in full["leads"]}, {c["id"]: c["name"] for s in ("active", "deleted") for c in callers.all_callers(s)}
    turns = {}
    for t in d["turns"]:
        turns.setdefault(t["call_id"], []).append(t)
    won = {b["call_id"]: b for b in d["books"] if b["call_id"]}
    out = []
    for c in d["calls"]:
        l, ts, a = people[c["lead_id"]], turns.get(c["id"], []), json.loads(c["analysis"]) if c["analysis"] else {}
        if q and q.lower() not in (l["name"] + " " + l["phone"] + " " + str(a.get("summary") or "")).lower():
            continue
        moods = [t["sentiment"] for t in ts if t["sentiment"] is not None]
        replies = [m["e2e_latency"] for t in ts if t["role"] == "assistant" and "e2e_latency" in (m := json.loads(t["metrics"]))]
        seconds = (datetime.fromisoformat(c["ended_at"]) - datetime.fromisoformat(c["started_at"])).total_seconds() if c["ended_at"] else None
        out.append({"id": c["id"], "lead_id": l["id"], "name": l["name"], "phone": l["phone"], "caller": names.get(c["caller_id"], ""), "mode": c["mode"],
                    "started_at": c["started_at"], "seconds": seconds, "status": c["status"], "result": result[c["id"]],
                    "said": sum(t["role"] == "user" for t in ts), "mood": sum(moods) / len(moods) if moods else None, "reply": _median(replies),
                    "language": l["language"], "score": l["score"], "band": l["band"], "offering": won[c["id"]]["project"] if c["id"] in won else "",
                    "summary": a.get("summary") or "", "objections": [str(o) for o in a.get("objections") or []]})
    return sorted(out, key=SORTS.get(sort, SORTS["newest"]), reverse=True)


def for_caller(cid: str) -> dict:
    d = _load()
    out = _metrics(d, {"caller": cid})
    called = {c["lead_id"] for c in d["calls"] if c["caller_id"] == cid}
    pids = {l["product_id"] for l in d["leads"] if l["id"] in called or l["caller_id"] == cid} | {u["id"] for u in callers.used_by(cid)}
    out["by_product"] = []
    for p in products.all_products():
        if p["id"] in pids:
            m = _metrics(_load(p["id"]), {"caller": cid})
            share = next((x["share"] for x in p["callers"] if x["id"] == cid), None)
            out["by_product"].append({"id": p["id"], "name": p["company"], "share": share, **{k: m[k] for k in BRIEF}})
    return out


def counts(pid: str) -> dict:
    return db.one("""SELECT (SELECT COUNT(*) FROM leads WHERE product_id=? AND deleted_at IS NULL) leads,
                  (SELECT COUNT(*) FROM calls c JOIN leads l ON l.id=c.lead_id WHERE l.product_id=? AND l.deleted_at IS NULL) calls,
                  (SELECT COUNT(*) FROM calls c JOIN leads l ON l.id=c.lead_id WHERE l.product_id=? AND c.status='active') active_calls,
                  (SELECT COUNT(*) FROM bookings b JOIN leads l ON l.id=b.lead_id JOIN slots s ON s.id=b.slot_id
                   WHERE l.product_id=? AND b.status='confirmed' AND s.start>?) upcoming""", pid, pid, pid, pid, now().isoformat())
