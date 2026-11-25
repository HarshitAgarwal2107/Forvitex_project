"""Checks every key with one tiny real request. Never prints a key. Run: python scripts/check_keys.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
from livekit import api as lk

from app.config import env


async def check(name, key, fn):
    if key and not env(key):
        return print(f"MISSING  {name}: add {key}")
    try:
        print(f"OK       {name}: {await fn()}")
    except httpx.HTTPStatusError as e:
        print(f"FAILED   {name}: HTTP {e.response.status_code} {e.response.text[:160]!r}")
    except Exception as e:
        print(f"FAILED   {name}: {type(e).__name__} {str(e)[:120]}")


async def main():
    async with httpx.AsyncClient(timeout=30) as h:
        async def livekit():
            async with lk.LiveKitAPI(env("LIVEKIT_URL"), env("LIVEKIT_API_KEY"), env("LIVEKIT_API_SECRET")) as a:
                await a.room.list_rooms(lk.ListRoomsRequest())
            return "connected"

        async def deepgram():
            r = await h.get("https://api.deepgram.com/v1/projects", headers={"Authorization": "Token " + env("DEEPGRAM_API_KEY")})
            r.raise_for_status()
            return "key accepted"

        async def gemini():
            m, head = env("LIVE_MODEL", "gemini-3.5-flash-lite"), {"x-goog-api-key": env("GOOGLE_API_KEY")}
            base = "https://generativelanguage.googleapis.com/v1beta/models"
            r = await h.post(f"{base}/{m}:generateContent", headers=head, json={"contents": [{"parts": [{"text": "Say hi"}]}]})
            if r.status_code == 404:
                names = [x["name"].split("/")[-1] for x in (await h.get(base, headers=head, params={"pageSize": 200})).json().get("models", [])]
                raise RuntimeError(f"{m} unavailable. Set LIVE_MODEL to one of: {[n for n in names if 'flash-lite' in n]}")
            r.raise_for_status()
            return f"{m} replied in {r.elapsed.total_seconds():.2f}s"

        def groq(model):
            async def f():
                r = await h.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": "Bearer " + env("GROQ_API_KEY")},
                                 json={"model": model, "max_completion_tokens": 60, "messages": [{"role": "user", "content": "Say hi"}]})
                r.raise_for_status()
                return f"{model} replied in {r.elapsed.total_seconds():.2f}s, {r.headers.get('x-ratelimit-limit-tokens', '?')} tokens/min"
            return f

        async def sarvam():
            r = await h.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": env("SARVAM_API_KEY")},
                             json={"text": "Hi", "target_language_code": "en-IN", "model": env("SARVAM_MODEL", "bulbul:v3"),
                                   "speaker": env("SARVAM_SPEAKER") or "ishita"})
            r.raise_for_status()
            return f"speech in {r.elapsed.total_seconds():.2f}s"

        await check("LiveKit", "LIVEKIT_URL", livekit)
        await check("Deepgram", "DEEPGRAM_API_KEY", deepgram)
        await check("Gemini", "GOOGLE_API_KEY", gemini)
        await check("Groq large", "GROQ_API_KEY", groq("openai/gpt-oss-120b"))
        await check("Groq fast", "GROQ_API_KEY", groq("qwen/qwen3.8-27b"))
        await check("Sarvam", "SARVAM_API_KEY", sarvam)


asyncio.run(main())
