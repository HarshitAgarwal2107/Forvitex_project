"""Insights counted by code across customers. No model writes these: each is a count anyone can check by opening
the customers behind it. Nothing is shown until there are enough customers to count."""
import json
import re
from collections import defaultdict
from math import comb

ENOUGH = 10      # customers spoken with before anything is shown
GROUP = 5        # smallest group worth comparing
GAP = 0.25       # smallest difference in booking rate worth mentioning
THEMES = {       # different wordings of the same objection
    "Price": r"price|cost|expensive|budget|afford|fee|discount|high",
    "Not ready yet": r"brows|looking|later|not ready|timing|think|unsure|uncertain",
    "Too many calls": r"call|spam|disturb",
    "Location or distance": r"location|far|distance|area|commute",
    "Trust or guarantees": r"trust|guarantee|return|proof|risk|warranty",
}


def theme(label: str) -> str:
    return next((t for t, p in THEMES.items() if re.search(p, label, re.I)), label.strip().capitalize())


def chance(a: int, n: int, b: int, m: int) -> float:
    """Fisher exact test (two-sided): how often a gap this big would appear by luck alone."""
    k, total = a + b, n + m
    p = lambda x: comb(n, x) * comb(m, k - x) / comb(total, k)
    seen = p(a)
    return sum(p(x) for x in range(max(0, k - m), min(n, k) + 1) if p(x) <= seen * (1 + 1e-9))


def build(d: dict, product: dict | None = None) -> dict:
    spoke = {t["lead_id"] for t in d["turns"] if t["role"] == "user"}
    people = [l for l in d["leads"] if l["id"] in spoke]
    n = len(people)
    if n < ENOUGH:
        return {"ready": False, "have": n, "need": ENOUGH, "items": []}
    booked = {b["lead_id"] for b in d["books"]} & spoke
    facts = {l["id"]: json.loads(l["facts"]) for l in people}
    human = {e["lead_id"] for e in d["events"] if e["type"] == "human_requested"} & spoke
    moods = defaultdict(list)
    for t in d["turns"]:
        if t["sentiment"] is not None:
            moods[t["lead_id"]].append(t["sentiment"])
    everyone, items = {l["id"] for l in people}, []

    def add(kind, text, ids, weight):
        items.append({"kind": kind, "text": text, "weight": weight, "customers": sorted(
            ({"id": l["id"], "name": l["name"], "booked": l["id"] in booked} for l in people if l["id"] in ids), key=lambda x: x["name"])})

    # 1. what gets in the way
    themes = defaultdict(set)
    for i in everyone:
        for o in facts[i].get("objections", []):
            themes[theme(o)].add(i)
    for name, ids in themes.items():
        if len(ids) >= 3:
            won = len(ids & booked)
            add("Objection", f"{name} came up with {len(ids)} of {n} customers. {won} of those {len(ids)} booked.", ids, len(ids) / n)

    # 2. who books more or less often than everyone else
    groups = {"spoke Hindi or Hinglish": {l["id"] for l in people if l["language"] == "hi"},
              "sounded unhappy on average": {i for i in everyone if moods[i] and sum(moods[i]) / len(moods[i]) < -0.2},
              "asked for a person": human}
    for f in (product or {}).get("facts", []):
        known = {i: facts[i][f["key"]] for i in everyone if facts[i].get(f["key"]) is not None}
        rule = f["full_if"]
        if "min" in rule or "max" in rule:
            word, limit = ("at least", rule["min"]) if "min" in rule else ("at most", rule["max"])
            groups[f"gave {f['label']} of {word} {limit:g}"] = {i for i, v in known.items() if (v >= limit if "min" in rule else v <= limit)}
        elif f["type"] == "text":
            values = defaultdict(set)
            for i, v in known.items():
                values[str(v).strip().lower()].add(i)
            for v, ids in values.items():
                groups[f"said \"{v}\" for {f['label']}"] = ids
    for name, ids in groups.items():
        rest = everyone - ids
        if len(ids) < GROUP or len(rest) < GROUP:
            continue
        a, b = len(ids & booked), len(rest & booked)
        gap = a / len(ids) - b / len(rest)
        if abs(gap) >= GAP:
            luck = chance(a, len(ids), b, len(rest))
            sure = "Unlikely to be chance alone" if luck < 0.05 else "Early sign, could still be chance"
            add("Booking pattern", f"Customers who {name} booked {'more' if gap > 0 else 'less'} often: {a} of {len(ids)}, against {b} of {len(rest)} others. {sure}.",
                ids, 1 + abs(gap) - luck)

    # 3. what we keep failing to learn
    for f in (product or {}).get("facts", []):
        missing = {i for i in everyone if facts[i].get(f["key"]) is None}
        if len(missing) / n >= 0.6:
            add("Gap", f"{f['label']} was learned from only {n - len(missing)} of {n} customers.", missing, 0.3 * f["points"] / 100 + len(missing) / n / 10)

    # 4. what happens after booking
    held = [b for b in d["books"] if b["status"] in ("attended", "no_show")]
    absent = {b["lead_id"] for b in held if b["status"] == "no_show"}
    if len(held) >= GROUP and absent:
        add("Attendance", f"{sum(b['status'] == 'no_show' for b in held)} of {len(held)} held bookings were no-shows.", absent, len(absent) / len(held))
    if len(human) >= 3:
        add("Handover", f"{len(human)} of {n} customers asked for a person.", human, len(human) / n)
    items.sort(key=lambda x: -x["weight"])
    out, per = [], defaultdict(int)
    for x in items:  # a few of each kind, so one kind never fills the list
        per[x["kind"]] += 1
        if per[x["kind"]] <= 3:
            out.append({k: x[k] for k in ("kind", "text", "customers")})
    return {"ready": True, "have": n, "need": ENOUGH, "items": out[:8]}
