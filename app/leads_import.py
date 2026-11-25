"""Batch lead import: read CSV / Excel / pasted rows, guess columns, check every row, then save the good ones."""
import csv
import io
import re
import zipfile

from . import db, products, safety
from .crm import normalize_phone

MAX_BYTES, MAX_ROWS, MAX_COLS = 5_000_000, 5000, 40
GUESS = {
    "name": r"^(full |customer |lead |contact |client )?name$|^(customer|contact|lead|client)$|नाम",
    "phone": r"phone|mobile|contact ?(no|num)|whatsapp|^(tel|number|cell)|फोन|मोबाइल",
    "product": r"^(product|company|brand|campaign|interested in)",
    "notes": r"note|remark|comment|requirement|message|description|enquiry|inquiry|context",
    "source": r"source|channel|utm",
}


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():  # Excel stores phone numbers as 9900000001.0
        v = int(v)
    return safety.clean_text(v)[:1500]


def _text(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def _excel(data: bytes):
    import openpyxl
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > 60_000_000:  # small file that unpacks huge
                raise ValueError("That workbook is too large")
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except ValueError:
        raise
    except Exception:
        raise ValueError("That Excel file could not be opened. Save it as .xlsx or .csv and try again") from None
    try:
        for ws in wb.worksheets:  # first sheet that has anything in it
            rows = []
            for r in ws.iter_rows(values_only=True, max_col=MAX_COLS):
                if any(c not in (None, "") for c in r):
                    rows.append([_cell(c) for c in r])
                if len(rows) > MAX_ROWS + 1:
                    break
            if rows:
                return rows
        return []
    finally:
        wb.close()


def parse(data: bytes, filename: str = "") -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("Use a file under 5 MB")
    if filename.lower().endswith(".xls") and data[:2] != b"PK":
        raise ValueError("Old .xls files are not supported. Save it as .xlsx or .csv")
    if data[:2] == b"PK":
        rows = _excel(data)
    else:
        text = _text(data)
        if "\x00" in text:
            raise ValueError("That does not look like a CSV or Excel file")
        first = text.lstrip().split("\n", 1)[0]
        delim = max(",\t;|", key=first.count)
        rows = []
        try:
            for r in csv.reader(io.StringIO(text, newline=""), delimiter=delim):
                if any(c.strip() for c in r):
                    rows.append([_cell(c) for c in r[:MAX_COLS]])
                if len(rows) > MAX_ROWS + 1:
                    break
        except csv.Error:
            raise ValueError("That file could not be read as CSV") from None
    if not rows:
        raise ValueError("There are no rows in that file")
    # a heading row names columns; data pasted without headings has a phone number in its first row
    headed = not any(len(re.sub(r"\D", "", c)) >= 10 for c in rows[0])
    width = max(map(len, rows))
    headers = (rows[0] if headed else []) + [""] * width
    headers = [h or f"Column {i + 1}" for i, h in enumerate(headers[:width])]
    body = [r + [""] * (width - len(r)) for r in (rows[1:] if headed else rows)]
    if not body:
        raise ValueError("There is a heading row but no leads under it")
    if len(body) > MAX_ROWS:
        raise ValueError(f"Import at most {MAX_ROWS:,} leads at a time")
    return {"headers": headers, "rows": body, "mapping": guess(headers, body)}


def guess(headers, body) -> dict:
    out = {}
    for field, pattern in GUESS.items():
        for i, h in enumerate(headers):
            if i not in out.values() and re.search(pattern, h.strip(), re.I):
                out[field] = i
                break
    sample = body[:20]
    if "phone" not in out:  # no helpful heading: the column that looks like phone numbers
        score = [sum(10 <= len(re.sub(r"\D", "", r[i])) <= 13 for r in sample) for i in range(len(headers))]
        if max(score) >= len(sample) / 2:
            out["phone"] = score.index(max(score))
    if "name" not in out:
        for i in range(len(headers)):
            if i not in out.values() and sum(bool(re.search(r"[^\W\d]", r[i])) for r in sample) > len(sample) / 2:
                out["name"] = i
                break
    return out


def run(headers, rows, mapping, product_id, extra_notes=True, commit=False) -> dict:
    """Check every row; with commit, save the new ones in a single transaction."""
    if "name" not in mapping or "phone" not in mapping:
        raise ValueError("Choose which columns hold the name and the phone number")
    active = {p["id"]: p for p in products.all_products()}
    by_name = {k.lower(): i for i, p in active.items() for k in (i, p["company"])}
    if product_id and product_id not in active:
        raise ValueError("That product was deleted")
    used = set(mapping.values())
    out, seen, new = [], set(), []
    with db.tx() as con:
        have = {(r["phone"], r["product_id"]): r["deleted_at"] for r in con.execute("SELECT phone, product_id, deleted_at FROM leads")}
        blocked = {r["phone"] for r in con.execute("SELECT phone FROM leads WHERE do_not_call=1")}
        for n, r in enumerate(rows):
            get = lambda f: r[mapping[f]].strip() if f in mapping and mapping[f] < len(r) else ""
            name, raw, pid, status, why = get("name"), get("phone"), product_id, "new", ""
            phone = raw
            if get("product"):
                pid = by_name.get(get("product").lower())
            if re.fullmatch(r"\d(\.\d+)?e\+?\d+", raw, re.I):
                status, why = "problem", "Excel shortened this number. Format the column as text"
            elif not name:
                status, why = "problem", "No name"
            elif not pid:
                status, why = "problem", f"No product called \"{get('product')}\"" if get("product") else "No product chosen"
            else:
                try:
                    phone = normalize_phone(raw)
                    if (phone, pid) in seen:
                        status, why = "skip", "Repeated in this file"
                    elif phone in blocked:
                        status, why = "skip", "Asked not to be called"
                    elif (phone, pid) in have:
                        status, why = "skip", "Deleted earlier. Restore it under Deleted" if have[(phone, pid)] else "Already a lead"
                    seen.add((phone, pid))
                except ValueError:
                    status, why = "problem", "Phone number is missing or not valid"
            name, notes = safety.clean_line(name), get("notes")
            if extra_notes:
                more = [f"{headers[i]}: {v}" for i, v in enumerate(r) if i not in used and v and i < len(headers)]
                notes = ". ".join(x for x in [notes, *more] if x)
            if status == "new" and (bad := safety.check_rules({"Name": name, "Notes": notes, "Source": get("source")})):
                status, why = "problem", f"{bad[0]['title']}: \"{bad[0]['quote'][:60]}\""
            out.append({"row": n + 1, "name": name, "phone": phone, "product": active[pid]["company"] if pid else "", "product_id": pid if pid in active else "", "status": status, "why": why})
            if status == "new":
                new.append((name[:60], phone, safety.clean_line(get("source") or "Import")[:60], pid, notes[:1500], db.iso()))
        if commit:
            con.executemany("INSERT INTO leads(name,phone,source,product_id,context,created_at) VALUES(?,?,?,?,?,?)", new)
    return {"rows": out, "new": len(new), "skip": sum(x["status"] == "skip" for x in out),
            "problem": sum(x["status"] == "problem" for x in out), "saved": len(new) if commit else 0}
