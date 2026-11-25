"""Times free models with the real sales prompt: first-sentence latency, prompt tokens, failures.
Run: python scripts/bench_models.py [rounds]"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "bench.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
from livekit.agents import llm
from livekit.agents.utils import http_context
from livekit.plugins import google, groq

from app import db, hedge
from app.agent import CallState, SalesAgent, director_note
from app.config import env
from app.core import instructions


async def main():
    http_context._new_session_ctx()
    db.init()
    async with httpx.AsyncClient(timeout=30) as h:
        g = (await h.get("https://api.groq.com/openai/v1/models", headers={"Authorization": "Bearer " + env("GROQ_API_KEY")})).json()
        print("groq:", sorted(m["id"] for m in g["data"]))
        m = (await h.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": env("GOOGLE_API_KEY")}, params={"pageSize": 200})).json()
        gem = sorted(x["name"].split("/")[-1] for x in m["models"] if "generateContent" in x.get("supportedGenerationMethods", []) and "flash" in x["name"])
        print("gemini flash:", gem)
    cid = db.execute("INSERT INTO calls(lead_id,mode,started_at,caller_id) VALUES(1,'text',?,'aanya')", db.iso())
    st = CallState(1, cid)
    agent = SalesAgent(st)
    note = director_note(st, "we want three bedrooms near Whitefield", "Skyline Greens")
    sys_text = instructions(st.product, st.caller)
    print("chars: core+product", len(sys_text), "note", len(note), "tools", len(str([t.info.description for t in agent._tools])))
    want = [("gemini", n) for n in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-flash-lite-latest") if n in gem]
    want += [("groq", x["id"]) for x in g["data"] if any(k in x["id"] for k in ("gpt-oss-120b", "gpt-oss-20b", "qwen", "llama", "kimi"))and "guard" not in x["id"] and "safeguard" not in x["id"]]
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    for kind, name in want:
        out = []
        for _ in range(rounds):
            model = google.LLM(model=name, temperature=0.7) if kind == "gemini" else (
                groq.LLM(model=name, temperature=0.7, reasoning_effort="low") if "gpt-oss" in name else
                groq.LLM(model=name, temperature=0.7, reasoning_effort="none") if "qwen" in name else groq.LLM(model=name, temperature=0.7))
            c = llm.ChatContext()
            for role, text in (("system", sys_text), ("user", "we just had a baby, our flat feels tiny"), ("assistant", "Oh, congratulations! Thinking of a bigger place?"),
                               ("user", "we want three bedrooms near Whitefield"), ("system", note)):
                c.add_message(role=role, content=text)
            t, first, sent, text, tok = time.monotonic(), None, None, "", None
            try:
                async with model.chat(chat_ctx=c, tools=agent._tools, conn_options=hedge.ONE_TRY) as s:
                    async for x in s:
                        if x.usage:
                            tok = x.usage.prompt_tokens
                        if x.delta and x.delta.content:
                            first = first or time.monotonic() - t
                            text += x.delta.content
                            if sent is None and any(p in text for p in ("! ", ". ", "? ", "।")):
                                sent = time.monotonic() - t
                total = time.monotonic() - t
                out.append(f"{first or total:.2f}/{sent or total:.2f}")
            except Exception as e:
                out.append(f"FAIL {getattr(e, 'status_code', '')}{type(e).__name__[:12]}")
            await asyncio.sleep(2.5)
        print(f"{name:32} first/sentence: {'  '.join(out)} | tokens {tok} | {text[:110]!r}")


asyncio.run(main())
