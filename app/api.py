"""HTTP API, dashboard, text chat, and the post-call job loop.
Run: python -m uvicorn app.api:app --port 8000"""
import asyncio
import base64
import csv
import io
import json
import logging
from urllib.parse import urlsplit
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from livekit import api as lk
from livekit.agents import AgentSession
from pydantic import BaseModel, Field

from . import analysis, callers, db, leads_import, products, safety, stats, team
from .agent import CallState, SalesAgent, attach, build_llm, end_call_record
from .booking import BookingError, bookings, busy_members, spoken
from .config import AGENT_NAME, ROOT, env, now
from .crm import crm
from .rag import kb

log = logging.getLogger("api")
text_sessions: dict[int, AgentSession] = {}
MAX_ATTEMPTS = 3


async def job_loop():
    """Durable post-call queue: the calls table is the queue, so a crash loses nothing."""
    rolled = now().date()
    while True:
        try:
            if now().date() != rolled:
                rolled = now().date()
                for p in products.all_products():
                    db.sync_slots(p)
            stale = (now() - timedelta(minutes=15)).isoformat(timespec="seconds")
            db.execute("UPDATE calls SET status='ended', ended_at=? WHERE status='active' AND mode='voice' AND started_at<?", db.iso(), stale)
            for c in db.rows("SELECT id, attempts FROM calls WHERE status='ended' ORDER BY id LIMIT 5"):
                try:
                    await analysis.analyze_call(c["id"])
                except Exception as e:
                    n = c["attempts"] + 1
                    db.execute("UPDATE calls SET attempts=?, error=?, status=? WHERE id=?", n, str(e)[:200],
                               "failed" if n >= MAX_ATTEMPTS else "ended", c["id"])
                    await asyncio.sleep(3 * n)
        except Exception:
            log.exception("job loop")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_):
    db.init()
    # text sessions live in memory, so any left active by a restart are over
    db.execute("UPDATE calls SET status='ended', ended_at=? WHERE status='active' AND mode='text'", db.iso())
    asyncio.get_running_loop().run_in_executor(None, lambda: [kb(p) for p in products.all_products()])
    task = asyncio.create_task(job_loop())
    yield
    task.cancel()


app = FastAPI(title="Sales Voice Agent", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _csp() -> str:
    lk_host = urlsplit(env("LIVEKIT_URL")).hostname or ""
    live = " ".join(f"{k}://{h}" for h in (["*.livekit.cloud"] + ([lk_host] if lk_host and not lk_host.endswith(".livekit.cloud") else [])) for k in ("https", "wss"))
    return ("default-src 'none'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; "
            f"font-src 'self'; connect-src 'self' {live}; worker-src 'self' blob:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


@app.middleware("http")
async def protect(request: Request, call_next):
    """Other websites may not act on this one, and the browser is told how to contain the page."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin, site = request.headers.get("origin"), request.headers.get("sec-fetch-site")
        if (origin and urlsplit(origin).netloc != request.headers.get("host")) or site in ("cross-site", "same-site"):
            return JSONResponse({"detail": "This action must come from the dashboard itself"}, 403)
    r = await call_next(request)
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    r.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    r.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=(), payment=()"
    r.headers["Content-Security-Policy"] = _csp()
    # pages and scripts are always checked for updates; customer data is never kept by the browser
    r.headers["Cache-Control"] = "no-cache" if request.url.path == "/" or request.url.path.startswith("/static/") else "no-store"
    return r


async def must_be_safe(kind: str, ref: str, fields: dict[str, str], confirm: bool):
    """Stop a save that would put harmful text into a prompt, and say exactly why."""
    if bad := await safety.review(kind, ref, fields, confirm):
        raise HTTPException(422 if bad["verdict"] == "block" else 409, bad)


def product_text(p: dict) -> dict[str, str]:
    return {"Company name": p["company"], "What you sell": p["topic"], "What to book": p["appointment"], "Key facts": p["catalogue"], "Full information": p["knowledge"],
            "Never promise or say": "\n".join(p["rules"]), "Offerings": "\n".join(str(i.get("name", "")) + " " + " ".join(map(str, i.get("aliases") or [])) for i in p["items"]),
            "Questions": "\n".join(f"{f.get('label', '')}. {f.get('ask', '')}. {f.get('hint', '')}" for f in p["facts"]),
            "Speech and search hints": "\n".join([*p["keyterms"], *(f"{k} {v}" for k, v in p["glossary"].items())])}


class NewCall(BaseModel):
    lead_id: int
    mode: str = Field("voice", pattern="^(voice|text)$")


class Brief(BaseModel):
    company: str = Field("", max_length=60)
    brief: str = Field(min_length=80, max_length=20000)


class ProductIn(BaseModel):
    """Full pack from the editor."""
    company: str = ""
    topic: str = ""
    callers: list[dict] = []
    color: str = ""
    appointment: str = ""
    catalogue: str = ""
    knowledge: str = ""
    rules: list[str] = []
    items: list[dict] = []
    facts: list[dict] = []
    hours: list[int] = []
    days: list[int] = []
    minutes: int = 45
    spots: int = 1
    horizon: int = 14
    notice: int = 2
    rules_set: bool = False
    confirm: bool = False
    keyterms: list[str] = []
    glossary: dict[str, str] = {}
    version: int | None = None


class NewLead(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    phone: str = Field(min_length=5, max_length=20)
    product_id: str = Field(max_length=40)
    context: str = Field("", max_length=1500)
    confirm: bool = False


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


def need(x, what):
    if x is None:
        raise HTTPException(404, f"{what} not found")
    return x


def product_or_404(pid):
    return need(products.row(pid), "Product")


def checked(b: ProductIn) -> dict:
    p = b.model_dump(exclude={"version", "confirm"})
    if bad := products.problems(p):
        raise HTTPException(422, ". ".join(bad) + ".")
    return p


@app.get("/api/products")
def list_products(deleted: bool = False):
    return [{k: p[k] for k in ("id", "company", "topic", "appointment", "version", "color")} | stats.counts(p["id"])
            for p in products.all_products("deleted" if deleted else "active")]


@app.post("/api/products/draft")
async def draft_product(b: Brief):
    """Suggest settings from a brief. Nothing is saved until a person reviews it."""
    try:
        return await products.draft(b.brief, b.company.strip())
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except RuntimeError:
        raise HTTPException(502, "The AI is busy right now. Try again, or continue and fill it in yourself.") from None


@app.post("/api/products")
async def create_product(b: ProductIn):
    p = checked(b)
    await must_be_safe("product", "new", product_text(p), b.confirm)
    p = products.create(p)
    await asyncio.to_thread(kb, p)
    return products.row(p["id"])


@app.get("/api/products/colors")
def product_colors():
    return {"palette": products.COLORS, "next": products.free_color()}


@app.get("/api/products/{pid}")
def get_product(pid: str):
    return product_or_404(pid)


@app.put("/api/products/{pid}")
async def edit_product(pid: str, b: ProductIn):
    product_or_404(pid)
    if b.version is None:
        raise HTTPException(422, "Missing version")
    p = checked(b)
    await must_be_safe("product", pid, product_text(p), b.confirm)
    try:
        p = products.update(pid, p, b.version)
    except products.Conflict as e:
        raise HTTPException(409, str(e)) from None
    await asyncio.to_thread(kb, p)
    return products.row(pid)


@app.delete("/api/products/{pid}")
def delete_product(pid: str):
    product_or_404(pid)
    try:
        products.delete(pid)
    except products.Conflict as e:
        raise HTTPException(409, str(e)) from None
    return {"ok": True}


@app.post("/api/products/{pid}/restore")
def restore_product(pid: str):
    product_or_404(pid)
    products.restore(pid)
    return {"ok": True}


def filters(caller: str = "", days: int = Query(0, ge=0, le=365), mode: str = Query("", pattern="^(voice|text)?$"),
            language: str = Query("", pattern="^(en|hi)?$"), band: str = Query("", pattern="^(Hot|Warm|Cold)?$"),
            result: str = Query("", pattern="^(booked|moved|cancelled|opted_out|handover|talked|no_answer)?$")) -> dict:
    if caller:
        need(callers.get(caller), "Caller")
    return {k: v for k, v in dict(caller=caller, days=days, mode=mode, language=language, band=band, result=result).items() if v}


@app.get("/api/products/{pid}/stats")
def product_stats(pid: str, f: dict = Depends(filters)):
    product_or_404(pid)
    return stats.for_product(pid, f)


@app.get("/api/products/{pid}/calls")
def product_calls(pid: str, f: dict = Depends(filters), q: str = "", sort: str = "newest", page: int = Query(0, ge=0)):
    product_or_404(pid)
    rows = stats.calls_report(pid, f, q.strip()[:80], sort)
    return {"total": len(rows), "counts": Counter(r["result"] for r in rows), "rows": rows[page * 50:page * 50 + 50]}


@app.get("/api/products/{pid}/calls.csv")
def product_calls_csv(pid: str, f: dict = Depends(filters), q: str = "", sort: str = "newest"):
    product_or_404(pid)
    cols = ["id", "started_at", "name", "phone", "caller", "mode", "result", "offering", "seconds", "said", "mood", "reply", "language", "score", "band", "objections", "summary"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Call", "Started", "Customer", "Phone", "AI caller", "Channel", "Result", "Booked", "Seconds", "Customer turns", "Average mood (-1 to 1)",
                "Median reply (s)", "Language", "Score", "Band", "Objections", "Summary"])
    for r in stats.calls_report(pid, f, q.strip()[:80], sort):
        line = []
        for c in cols:
            v = r[c]
            v = "; ".join(v) if isinstance(v, list) else "" if v is None else round(v, 2) if isinstance(v, float) else v
            # text a spreadsheet would run as a formula is made harmless; phone numbers keep their plus
            line.append("'" + v if isinstance(v, str) and v and v[0] in "=+-@\t\r" and c != "phone" else v)
        w.writerow(line)
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={pid}-calls.csv"})


@app.get("/api/products/{pid}/ask")
async def ask_product(pid: str, q: str = ""):
    """Show exactly which facts the agent would be given for a question."""
    p = product_or_404(pid)
    return await asyncio.to_thread(lambda: kb(p).search(q[:300]))


def live_lead(lead_id: int) -> dict:
    l = need(crm.get(lead_id), "Lead")
    if l["deleted_at"]:
        raise HTTPException(404, "That lead was deleted")
    return l


class LeadEdit(BaseModel):
    name: str = Field(max_length=60)
    phone: str = Field(max_length=20)
    context: str = Field("", max_length=1500)
    do_not_call: bool = False
    confirm: bool = False


class Ids(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=10000)


@app.get("/api/leads/deleted")
def deleted_leads():
    active = {p["id"]: p["company"] for p in products.all_products()}
    return [{**l, "company": active[l["product_id"]]} for l in
            db.rows("SELECT id, name, phone, product_id, deleted_at FROM leads WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC, name")
            if l["product_id"] in active]


@app.post("/api/leads/delete")
def delete_leads(b: Ids):
    return crm.delete(b.ids)


@app.post("/api/leads/restore")
def restore_leads(b: Ids):
    crm.restore(b.ids)
    return {"ok": True}


@app.put("/api/leads/{lead_id}")
async def edit_lead(lead_id: int, b: LeadEdit):
    if not b.name.strip():
        raise HTTPException(422, "Enter a name")
    await must_be_safe("lead", str(lead_id), {"Name": b.name, "Notes": b.context}, b.confirm)
    try:
        crm.edit(lead_id, b.name, b.phone, b.context, b.do_not_call)
    except KeyError:
        raise HTTPException(404, "That lead was deleted") from None
    except ValueError as e:
        raise HTTPException(409 if "Another" in str(e) else 422, str(e)) from None
    return {"ok": True}


class CallerIn(BaseModel):
    name: str = Field("", max_length=60)
    gender: str = ""
    voice: str = ""
    pace: float = 1.0
    personality: str = Field("", max_length=1000)
    version: int | None = None
    confirm: bool = False


class Preview(BaseModel):
    voice: str
    pace: float = Field(1.0, ge=0.8, le=1.3)
    language: str = Field("en", pattern="^(en|hi)$")


def caller_checked(b: CallerIn) -> dict:
    c = b.model_dump(exclude={"version", "confirm"})
    if bad := callers.problems(c):
        raise HTTPException(422, ". ".join(bad) + ".")
    return c


@app.get("/api/callers")
def list_callers():
    return {"active": [{**c, "used": callers.used_by(c["id"])} for c in callers.all_callers()],
            "deleted": callers.all_callers("deleted"), "voices": callers.VOICES}


@app.post("/api/callers")
async def create_caller(b: CallerIn):
    c = caller_checked(b)
    await must_be_safe("caller", "new", {"Name": c["name"], "Personality": c["personality"]}, b.confirm)
    return callers.create(c)


@app.post("/api/callers/preview")
async def preview_voice(b: Preview):
    """A short sample. Cached on disk, so each voice and pace costs credit once."""
    if not any(b.voice in v for v in callers.VOICES.values()):
        raise HTTPException(422, "Unknown voice")
    if not env("SARVAM_API_KEY"):
        raise HTTPException(503, "Add SARVAM_API_KEY to .env to hear voices")
    path = ROOT / "data" / "previews" / f"{b.voice}-{b.language}-{b.pace:.2f}.wav"
    if not path.exists():
        text = "नमस्ते! कैसे हैं आप? बताइए, मैं क्या help करूँ?" if b.language == "hi" else "Hi there! Good to meet you. So, what are you looking for?"
        async with httpx.AsyncClient(timeout=20) as http:
            try:
                r = await http.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": env("SARVAM_API_KEY")},
                                    json={"text": text, "target_language_code": b.language + "-IN", "model": "bulbul:v3", "speaker": b.voice, "pace": round(b.pace, 2)})
                r.raise_for_status()
                audio = base64.b64decode(r.json()["audios"][0])
            except (httpx.HTTPError, KeyError, ValueError):
                raise HTTPException(502, "The voice service did not answer. Check your Sarvam credit.") from None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(audio)
    return FileResponse(path, media_type="audio/wav")


@app.put("/api/callers/{cid}")
async def edit_caller(cid: str, b: CallerIn):
    need(callers.get(cid), "Caller")
    c = caller_checked(b)
    await must_be_safe("caller", cid, {"Name": c["name"], "Personality": c["personality"]}, b.confirm)
    try:
        return callers.update(cid, c, b.version or 0)
    except callers.Conflict as e:
        raise HTTPException(409, str(e)) from None


@app.delete("/api/callers/{cid}")
def delete_caller(cid: str):
    need(callers.get(cid), "Caller")
    try:
        callers.delete(cid)
    except callers.Conflict as e:
        raise HTTPException(409, str(e)) from None
    return {"ok": True}


@app.post("/api/callers/{cid}/restore")
def restore_caller(cid: str):
    need(callers.get(cid), "Caller")
    callers.restore(cid)
    return {"ok": True}


@app.get("/api/callers/{cid}/stats")
def caller_stats(cid: str):
    need(callers.get(cid), "Caller")
    return stats.for_caller(cid)


class ImportIn(BaseModel):
    headers: list[str] = Field(max_length=leads_import.MAX_COLS)
    rows: list[list[str]] = Field(max_length=leads_import.MAX_ROWS)
    mapping: dict[str, int]
    product_id: str = ""
    extra_notes: bool = True
    commit: bool = False


@app.post("/api/leads/import/read")
async def import_read(request: Request, filename: str = ""):
    data = bytearray()
    async for part in request.stream():
        data += part
        if len(data) > leads_import.MAX_BYTES:
            raise HTTPException(413, "Use a file under 5 MB")
    try:
        return await asyncio.to_thread(leads_import.parse, bytes(data), filename)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@app.post("/api/leads/import")
def import_leads(b: ImportIn):
    if any(not 0 <= i < len(b.headers) for i in b.mapping.values()):
        raise HTTPException(422, "A chosen column does not exist")
    try:
        return leads_import.run(b.headers, b.rows, b.mapping, b.product_id, b.extra_notes, b.commit)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None


@app.post("/api/leads")
async def new_lead(b: NewLead):
    if not b.name.strip():
        raise HTTPException(422, "Enter a name")
    await must_be_safe("lead", "new", {"Name": b.name, "Notes": b.context}, b.confirm)
    if product_or_404(b.product_id)["status"] != "active":
        raise HTTPException(409, "That product was deleted")
    try:
        return {"id": crm.upsert(b.name, b.phone, b.product_id, "Added by team", b.context)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except KeyError:
        raise HTTPException(404, "Product not found") from None


@app.post("/api/calls")
async def new_call(b: NewCall):
    lead = live_lead(b.lead_id)
    if products.get(lead["product_id"])["status"] != "active":
        raise HTTPException(409, "This lead's product was deleted")
    if lead["do_not_call"]:
        raise HTTPException(409, "This person asked not to be called")
    needed = {"voice": ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"], "text": []}[b.mode]
    if missing := [k for k in needed if not env(k)]:
        raise HTTPException(503, "Missing in .env: " + ", ".join(missing))
    model = None
    if b.mode == "text":
        try:
            model = build_llm()
        except RuntimeError as e:
            raise HTTPException(503, str(e)) from None

    with db.tx() as con:
        who = callers.choose(con, lead["id"], products.get(lead["product_id"]))
        call_id = con.execute("INSERT INTO calls(lead_id,mode,started_at,caller_id) VALUES(?,?,?,?)", (lead["id"], b.mode, db.iso(), who)).lastrowid
    out = {"call_id": call_id, "caller": callers.get(who)["name"], "lead_id": lead["id"], "mode": b.mode}
    if b.mode == "voice":
        room = f"call-{call_id}-{int(now().timestamp())}"
        out["url"] = env("LIVEKIT_URL")
        out["token"] = (lk.AccessToken(env("LIVEKIT_API_KEY"), env("LIVEKIT_API_SECRET"))
            .with_identity(f"lead-{lead['id']}").with_ttl(timedelta(minutes=15))
            .with_grants(lk.VideoGrants(room_join=True, room=room))
            .with_room_config(lk.RoomConfiguration(agents=[
                lk.RoomAgentDispatch(agent_name=AGENT_NAME, metadata=json.dumps({"call_id": call_id}))])).to_jwt())
    else:
        state = CallState(lead["id"], call_id)
        session = AgentSession(llm=model, vad=None, turn_handling={"turn_detection": None})
        attach(session, state)

        @session.on("close")
        def _(ev):
            end_call_record(call_id)
            text_sessions.pop(call_id, None)

        text_sessions[call_id] = session
        await session.start(SalesAgent(state))
    return out


@app.post("/api/calls/{call_id}/message")
async def message(call_id: int, b: Message):
    s = need(text_sessions.get(call_id), "Active text session")
    try:
        await asyncio.wait_for(s.run(user_input=b.text), 40)
    except RuntimeError as e:
        raise HTTPException(409, "The agent is still replying") from e
    except asyncio.TimeoutError:
        raise HTTPException(504, "The model took too long. Try again.") from None
    except Exception as e:
        log.exception("text turn failed")
        raise HTTPException(502, "The model could not reply: " + type(e).__name__) from e
    return {"ok": True}


@app.post("/api/calls/{call_id}/end")
async def end(call_id: int):
    need(db.one("SELECT id FROM calls WHERE id=?", call_id), "Call")
    if s := text_sessions.pop(call_id, None):
        await s.aclose()
    end_call_record(call_id)
    return {"ok": True}


@app.post("/api/calls/{call_id}/reanalyze")
async def reanalyze(call_id: int):
    c = need(db.one("SELECT status FROM calls WHERE id=?", call_id), "Call")
    if c["status"] == "active":
        raise HTTPException(409, "Call is still active")
    db.execute("UPDATE calls SET status='ended', attempts=0 WHERE id=?", call_id)
    return {"ok": True}


@app.get("/api/state")
def state():
    leads = db.rows("SELECT id,name,phone,source,stage,score,band,sentiment,next_action,do_not_call,product_id,created_at,(SELECT MAX(started_at) FROM calls c WHERE c.lead_id=leads.id) last_call FROM leads WHERE deleted_at IS NULL ORDER BY score DESC, id")
    companies = {p["id"]: p["company"] for p in products.all_products()}
    leads = [l for l in leads if l["product_id"] in companies]
    for l in leads:
        l["company"] = companies[l["product_id"]]
    ids = {l["id"]: l["name"] for l in leads}
    visits = db.rows("""SELECT b.id, b.lead_id, s.project, s.start FROM bookings b JOIN slots s ON s.id=b.slot_id
                        WHERE b.status='confirmed' AND s.start>? ORDER BY s.start""", now().isoformat())
    visits = [{**v, "lead": ids[v["lead_id"]], "when": spoken(v["start"])} for v in visits if v["lead_id"] in ids]
    spoke = {r["lead_id"] for r in db.rows("SELECT DISTINCT c.lead_id FROM calls c JOIN turns t ON t.call_id=c.id WHERE t.role='user'")} & set(ids)
    return {"leads": leads, "visits": visits,
            "stats": {"leads": len(leads), "calls": sum(r["lead_id"] in ids for r in db.rows("SELECT lead_id FROM calls")),
                      "spoke": len(spoke), "visits": len(visits), "hot": sum(l["band"] == "Hot" for l in leads)}}


@app.get("/api/leads/{lead_id}")
def lead(lead_id: int):
    l = live_lead(lead_id)
    l["visit"] = bookings.active(lead_id)
    p = products.get(l["product_id"])
    l["product"] = {"company": p["company"], "topic": p["topic"], "appointment": p["appointment"], "labels": {f["key"]: f["label"] for f in p["facts"]}}
    l["calls"] = db.rows("SELECT id,mode,started_at,ended_at,status,caller_id FROM calls WHERE lead_id=? ORDER BY id DESC", lead_id)
    names = {c["id"]: c["name"] for s in ("active", "deleted") for c in callers.all_callers(s)}
    for c in l["calls"]:
        c["caller"] = names.get(c["caller_id"], "")
    l["caller"] = names.get(l["caller_id"], "")
    return l


@app.get("/api/calls/{call_id}")
def call(call_id: int):
    c = need(db.one("SELECT * FROM calls WHERE id=?", call_id), "Call")
    c["analysis"] = json.loads(c["analysis"]) if c["analysis"] else None
    c["turns"] = db.rows("SELECT role,text,sentiment,metrics,created_at FROM turns WHERE call_id=? ORDER BY id", call_id)
    for t in c["turns"]:
        t["metrics"] = json.loads(t["metrics"])
    c["events"] = db.rows("SELECT type,detail,created_at FROM events WHERE call_id=? ORDER BY id", call_id)
    return c


@app.delete("/api/leads/{lead_id}/booking")
def cancel_booking(lead_id: int):
    need(crm.get(lead_id), "Lead")
    try:
        b = bookings.cancel(lead_id)
    except BookingError as e:
        raise HTTPException(409, str(e)) from None
    crm.merge_facts(lead_id, {})
    db.log_event("visit_cancelled", {"project": b["project"], "when": b["when"], "by": "team"}, lead_id)
    return {"ok": True}


class Act(BaseModel):
    lead_id: int = 0
    slot_id: int = 0
    member_id: int = 0
    status: str = Field("attended", pattern="^(attended|no_show)$")


class Rules(BaseModel):
    days: list[int]
    hours: list[int]
    minutes: int | None = None
    spots: int | None = None
    horizon: int | None = None
    notice: int | None = None
    version: int


class Closure(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    hour: int | None = Field(None, ge=0, le=23)
    product_id: str = ""
    reason: str = Field("", max_length=60)


class Member(BaseModel):
    name: str = Field(max_length=60)
    phone: str = Field("", max_length=20)
    products: list[str]
    confirm: bool = False


@app.put("/api/products/{pid}/rules")
def booking_rules(pid: str, b: Rules):
    product_or_404(pid)
    try:
        return products.set_rules(pid, b.model_dump(exclude={"version"}), b.version)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except products.Conflict as e:
        raise HTTPException(409, str(e)) from None


@app.post("/api/calendar/closures")
def close_time(b: Closure):
    try:
        datetime.strptime(b.day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "That is not a date") from None
    if b.day < now().date().isoformat():
        raise HTTPException(422, "Choose today or a later day")
    if b.product_id:
        product_or_404(b.product_id)
    pid = b.product_id or None
    if not db.one("SELECT 1 FROM closures WHERE day=? AND product_id IS ? AND hour IS ?", b.day, pid, b.hour):
        db.execute("INSERT INTO closures(product_id,day,hour,reason) VALUES(?,?,?,?)", pid, b.day, b.hour, b.reason.strip())
    return {"ok": True}


@app.delete("/api/calendar/closures/{cid}")
def reopen_time(cid: int):
    db.execute("DELETE FROM closures WHERE id=?", cid)
    return {"ok": True}


@app.post("/api/team")
@app.put("/api/team/{mid}")
async def save_member(b: Member, mid: int | None = None):
    await must_be_safe("team", str(mid or "new"), {"Name": b.name}, b.confirm)
    try:
        return team.save(b.model_dump(exclude={"confirm"}), mid)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except team.Conflict as e:
        raise HTTPException(409, str(e)) from None


@app.delete("/api/team/{mid}")
def remove_member(mid: int):
    need(team.get(mid), "Person")
    return {"unassigned": team.remove(mid)}


@app.get("/api/calendar")
def calendar(start: str, product: str = ""):
    try:
        first = datetime.strptime(start, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(422, "Use a date like 2026-11-26") from None
    end = (first + timedelta(days=7)).strftime("%Y-%m-%d")
    active = {p["id"]: p for p in products.all_products()}
    chosen = {k: p for k, p in active.items() if not product or k == product}
    people = [{"id": m["id"], "name": m["name"], "phone": m["phone"], "products": [x for x in m["products"] if x in active]} for m in team.members()]
    found = db.rows("""SELECT b.id, b.lead_id, b.status, s.project, COALESCE(t.name, '') rep, b.member_id, s.start, s.product_id, b.caller_id, l.name, l.phone, l.score, l.band,
                       (SELECT c.analysis FROM calls c WHERE c.lead_id=l.id AND c.analysis IS NOT NULL ORDER BY c.id DESC LIMIT 1) analysis
                       FROM bookings b JOIN slots s ON s.id=b.slot_id JOIN leads l ON l.id=b.lead_id LEFT JOIN team t ON t.id=b.member_id
                       WHERE l.deleted_at IS NULL AND s.start>=? AND s.start<? AND b.status IN ('confirmed','completed','attended','no_show') ORDER BY s.start""", start, end)
    out = []
    with db.tx() as con:
        for b in found:
            if p := chosen.get(b["product_id"]):
                a = b.pop("analysis")
                busy = busy_members(con, p, b["start"], b["id"])
                out.append({**b, "company": p["company"], "past": b["start"] <= now().isoformat(),
                            "people": [{**m, "busy": m["id"] in busy} for m in people if p["id"] in m["products"]],
                            "caller": (callers.get(b["caller_id"]) or {}).get("name", "") if b["caller_id"] else "",
                            "summary": (json.loads(a).get("summary") or "") if a else ""})
    times = {}
    for pid in chosen:
        for s in bookings.times(pid):
            if start <= s["start"] < end:
                t = times.setdefault((s["start"], pid), {"product_id": pid, "slots": [], **{k: s[k] for k in ("start", "left", "spots", "state", "closure")}})
                t["slots"].append({"id": s["id"], "project": s["project"]})
    closures = [c for c in db.rows("SELECT * FROM closures WHERE day>=? AND day<? ORDER BY day", start, end) if not c["product_id"] or c["product_id"] in chosen]
    return {"products": [{k: p[k] for k in ("id", "company", "color", "rules_set")} for p in active.values()], "team": people,
            "times": [times[k] for k in sorted(times)], "closures": closures, "bookings": out}


@app.get("/api/calendar/open")
def calendar_open(product: str):
    product_or_404(product)
    return bookings.open_slots(product, limit=600, spread=False, staff=True)


@app.get("/api/calendar/leads")
def calendar_leads(product: str):
    """Leads of this product who have nothing upcoming."""
    return db.rows("""SELECT id, name, phone FROM leads WHERE product_id=? AND deleted_at IS NULL AND id NOT IN
                      (SELECT b.lead_id FROM bookings b JOIN slots s ON s.id=b.slot_id WHERE b.status='confirmed' AND s.start>?)
                      ORDER BY score DESC, name LIMIT 500""", product, now().isoformat())


@app.post("/api/calendar/slots/{slot_id}/{action}")
def slot_action(slot_id: int, action: str, b: Act):
    try:
        if action == "book":
            live_lead(b.lead_id)
            if bookings.active(b.lead_id):
                raise BookingError("This lead already has a booking. Move that one instead.")
            v = bookings.book(b.lead_id, slot_id)
            crm.merge_facts(b.lead_id, {})
            db.log_event("visit_booked", {"project": v["project"], "when": v["when"], "by": "team"}, b.lead_id)
        else:
            raise HTTPException(404, "Unknown action")
    except BookingError as e:
        raise HTTPException(409, str(e)) from None
    return {"ok": True}


@app.post("/api/calendar/bookings/{booking_id}/{action}")
def booking_action(booking_id: str, action: str, b: Act):
    try:
        cur = bookings.by_id(booking_id)
        lead_id = cur["lead_id"]
        if action in ("move", "cancel"):  # act on exactly the booking on screen, never a newer one
            live = bookings.active(lead_id)
            if not live or live["id"] != booking_id:
                raise BookingError("This booking has changed. Reopen it.")
        if action == "assign":
            bookings.assign(booking_id, b.member_id)
            db.log_event("assigned", {"person": (team.get(b.member_id) or {}).get("name", "nobody")}, lead_id)
        elif action == "move":
            v = bookings.reschedule(lead_id, b.slot_id)
            db.log_event("visit_rescheduled", {"project": v["project"], "when": v["when"], "by": "team"}, lead_id)
        elif action == "cancel":
            bookings.cancel(lead_id)
            db.log_event("visit_cancelled", {"project": cur["project"], "when": spoken(cur["start"]), "by": "team"}, lead_id)
        elif action == "outcome":
            bookings.outcome(booking_id, b.status)
            db.log_event(b.status, {"project": cur["project"]}, lead_id)
        else:
            raise HTTPException(404, "Unknown action")
    except BookingError as e:
        raise HTTPException(409, str(e).replace(" Offer another one.", " Choose another.")) from None
    crm.merge_facts(lead_id, {})
    return {"ok": True}


@app.get("/api/bookings/{booking_id}.ics")
def ics(booking_id: str):
    body = need(bookings.ics(booking_id), "Booking")
    return Response(body, media_type="text/calendar", headers={"Content-Disposition": "attachment; filename=site-visit.ics"})


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
