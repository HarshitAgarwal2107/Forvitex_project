"""Real-model text conversations on a throwaway database. Uses live API quota.
Run: python scripts/try_talk.py [warm|annoyed|hinglish ...]"""
import asyncio, os, sys, tempfile, time
from pathlib import Path
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "talk.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livekit.agents import AgentSession
from app import analysis, db
from app.agent import CallState, SalesAgent, attach, build_llm, end_call_record
from app.booking import bookings
from app.crm import crm

TALKS = {
    "warm": (1, ["yeah sure, go ahead", "so we just had a baby and our flat feels tiny", "yeah three bedroom, somewhere near whitefield, my office is at ITPL",
                 "hmm what's it cost?", "that's a bit much honestly, we were thinking one crore max", "does it have a creche or play area?",
                 "ok can we come see it this saturday?", "morning is better", "eleven works", "great thanks, that's all"]),
    "course": (4, ["yeah hi", "I work in bank operations and honestly I'm bored, want to move into AI", "I know a little python from college", "how much is it and can I pay monthly?",
                   "will I get a job after this?", "what about the timings, I work till seven", "ok can I talk to an advisor on saturday?", "eleven works", "thanks bye"]),
    "annoyed": (2, ["you people keep calling me, third time this week!", "I only filled a form once and now it's non stop", "fine. what do you want",
                    "can you give me 20 percent discount", "are you even a real person?", "look I'm busy, let me talk to some human later", "ok bye"]),
    "hinglish": (3, ["haan boliye", "yaar abhi bas dekh raha hoon", "investment ke liye soch raha tha, rent kitna aayega", "मतलब पक्का नहीं बता सकते?",
                     "achha sarjapur wala kab ready hoga", "home loan mil jayega kya", "actually let's speak in English, do you have villas in Goa?"]),
}

async def talk(name):
    lead, lines = TALKS[name]
    cid = db.execute("INSERT INTO calls(lead_id,mode,started_at) VALUES(?,?,?)", lead, "text", db.iso())
    st = CallState(lead, cid)
    s = AgentSession(llm=build_llm(), vad=None, turn_handling={"turn_detection": None})
    attach(s, st)
    await s.start(SalesAgent(st))
    await asyncio.sleep(0.5)
    print(f"\n=== {name} ===")
    for line in lines:
        seen = db.one("SELECT COALESCE(MAX(id),0) n FROM turns")["n"]
        t = time.monotonic()
        try:
            await asyncio.wait_for(s.run(user_input=line), 40)
        except Exception as e:
            print("!!", type(e).__name__, e)
        dt = time.monotonic() - t
        for r in db.rows("SELECT role,text FROM turns WHERE call_id=? AND id>?", cid, seen):
            print(("  CALLER: " if r["role"] == "user" else f"  AANYA ({dt:.1f}s): ") + r["text"])
        if st.ended:
            break
        await asyncio.sleep(9)  # about the pace of a real call
    await asyncio.sleep(3)
    await s.aclose()
    end_call_record(cid)
    lead = crm.get(lead)
    print("  events:", [(e["type"], e["detail"]) for e in db.rows("SELECT type,detail FROM events WHERE call_id=? AND type!='lead_updated'", cid)])
    print("  facts:", lead["facts"], "score:", lead["score"], lead["band"], "mood:", lead["sentiment"])
    try:
        print("  analysis:", await analysis.analyze_call(cid))
    except Exception as e:
        print("  analysis failed:", e)

async def main():
    db.init()
    for n in sys.argv[1:] or TALKS:
        await talk(n)
asyncio.run(main())
