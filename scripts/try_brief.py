"""Creates a brand-new product from pasted text and has a real text conversation about it.
Runs on a throwaway database. Run: python scripts/try_brief.py"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "brief.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livekit.agents import AgentSession

from app import db, products
from app.agent import CallState, SalesAgent, attach, build_llm
from app.crm import crm

BRIEF = """Voltride sells electric scooters in Pune.

The Volt S1 costs 95,000 rupees, has a real-world range of 110 km and charges in 5 hours from a normal socket. Top speed 75 km per hour.

The Volt Pro costs 1.3 lakh rupees, has a range of 160 km, fast charging to 80 percent in 1 hour, and a top speed of 95 km per hour.

Both have a 5 year or 60,000 km battery warranty. Service is free for the first year. EMI is available from partner lenders, subject to their approval.

Customers can take a free 30 minute test ride at the Baner showroom. We deliver in 10 days within Pune. We do not deliver outside Pune."""
LINES = ["haan hello, boliye", "main roz 60 km chalata hoon office ke liye", "S1 theek rahega kya?", "kitne ki hai S1?",
         "okay in English, what is the price of both, and how is it compared to the Ola one?", "can I test ride on Sunday?", "eleven works", "do you deliver to Mumbai?", "ok thanks bye"]


async def main():
    db.init()
    p = await products.from_brief(BRIEF)
    print({k: p[k] for k in ("company", "topic", "appointment", "items", "rules")})
    for f in p["facts"]:
        print("  learns:", f["key"], f["type"], f["points"], f["full_if"], "|", f["ask"])
    lead = crm.upsert("Sameer Joshi", "9933333333", p["id"], context="Commented on our Instagram ad.")
    cid = db.execute("INSERT INTO calls(lead_id,mode,started_at) VALUES(?,?,?)", lead, "text", db.iso())
    st = CallState(lead, cid)
    s = AgentSession(llm=build_llm(), vad=None, turn_handling={"turn_detection": None})
    attach(s, st)
    await s.start(SalesAgent(st))
    await asyncio.sleep(1)
    for line in LINES:
        if st.ended:
            break
        await asyncio.wait_for(s.run(user_input=line), 40)
        await asyncio.sleep(7)
    await asyncio.sleep(2)
    for t in db.rows("SELECT role,text FROM turns WHERE call_id=?", cid):
        print("  CALLER:" if t["role"] == "user" else "  AANYA: ", t["text"])
    print("events:", [(e["type"], e["detail"]) for e in db.rows("SELECT type,detail FROM events WHERE call_id=?", cid)])
    l = crm.get(lead)
    print("facts:", l["facts"], "score:", l["score"], l["band"])


asyncio.run(main())
