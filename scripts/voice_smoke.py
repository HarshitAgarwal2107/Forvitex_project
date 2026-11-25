"""Places a real voice call to the running app with a synthetic caller, then prints what happened.
Needs start.ps1 running. Uses a little Sarvam credit. The test lead and its call are removed afterwards."""
import asyncio
import base64
import io
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
import numpy as np
from livekit import rtc

from app import db
from app.config import env

API = "http://127.0.0.1:8000/api"
CALLS = [
    [("Yes sure. We just had a baby and our flat feels really tiny now.", "en-IN"),
     ("We want three bedrooms near Whitefield.", "en-IN"),
     ("अच्छा, और होम लोन मिल जाएगा क्या?", "hi-IN"),
     ("Okay, can I come and see Skyline Greens this Saturday?", "en-IN"),
     ("Eleven in the morning works for me. Please book it.", "en-IN")],
    [("अच्छा, और होम लोन मिल जाएगा क्या?", "hi-IN"), ("Hi, do you remember why we are looking for a bigger home?", "en-IN"),
     ("Can we move my visit to Sunday at eleven instead?", "en-IN"),
     ("Yes, please do that.", "en-IN")],
]


async def speech(h, text, lang):
    r = await h.post("https://api.sarvam.ai/text-to-speech", headers={"api-subscription-key": env("SARVAM_API_KEY")},
                     json={"text": text, "target_language_code": lang, "model": "bulbul:v3", "speaker": "shubh", "speech_sample_rate": 24000})
    r.raise_for_status()
    with wave.open(io.BytesIO(base64.b64decode(r.json()["audios"][0]))) as w:
        return w.readframes(w.getnframes()), w.getframerate()


async def main():
    async with httpx.AsyncClient(timeout=40) as h:
        for lines in ([CALLS[0][:1] + CALLS[0][3:]] if "short" in sys.argv else CALLS):
            lead = await one_call(h, lines)
        final = (await h.get(f"{API}/leads/{lead}")).json()
        print("FINAL visit:", final["visit"], "| score:", final["score"], final["band"], "| facts:", final["facts"])
        with db.tx() as con:
            for sql in ("DELETE FROM turns WHERE call_id IN (SELECT id FROM calls WHERE lead_id=?)", "DELETE FROM events WHERE lead_id=?",
                        "DELETE FROM bookings WHERE lead_id=?", "DELETE FROM calls WHERE lead_id=?", "DELETE FROM leads WHERE id=?"):
                con.execute(sql, (lead,))


async def one_call(h, lines):
        clips = [await speech(h, *x) for x in lines]
        rate = clips[0][1]
        r = await h.post(API + "/leads", json={"name": "Kiran Test", "phone": "9900009999", "product_id": "skyline"})
        r = await h.post(API + "/calls", json={"lead_id": r.json()["id"], "mode": "voice"})
        r.raise_for_status()
        d = r.json()
        room, source = rtc.Room(), rtc.AudioSource(rate, 1)
        heard = {"frames": 0, "loud": 0.0}

        @room.on("track_subscribed")
        def _(track, *a):
            async def listen():
                async for ev in rtc.AudioStream(track):
                    x = np.frombuffer(ev.frame.data, dtype=np.int16).astype(float)
                    if x.size and np.sqrt((x ** 2).mean()) > 200:
                        heard["frames"] += 1
                        heard["loud"] = time.monotonic()
            asyncio.create_task(listen())

        t0 = time.monotonic()
        await room.connect(d["url"], d["token"])
        await room.local_participant.publish_track(
            rtc.LocalAudioTrack.create_audio_track("mic", source), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

        async def say(pcm):
            n = rate // 50 * 2
            for i in range(0, len(pcm), n):
                c = pcm[i:i + n].ljust(n, b"\0")
                await source.capture_frame(rtc.AudioFrame(c, rate, 1, n // 2))

        async def quiet_after_agent(limit=30):
            """Keep the mic open with silence until the agent has spoken and gone quiet."""
            start, base = time.monotonic(), heard["frames"]
            while time.monotonic() - start < limit:
                await say(b"\0" * (rate // 50 * 2))
                if heard["frames"] > base and time.monotonic() - heard["loud"] > 1.6:
                    return True
            return False

        print("greeting heard:", await quiet_after_agent(), f"({time.monotonic() - t0:.1f}s after joining)")
        for (text, _), (pcm, _) in zip(lines, clips):
            await say(pcm)
            print("said:", text, "| reply heard:", await quiet_after_agent())
        await room.disconnect()
        await asyncio.sleep(12)

        c = (await h.get(f"{API}/calls/{d['call_id']}")).json()
        for t in c["turns"]:
            print(f"  {t['role']:9} {t['text']}  {t['metrics'] or ''}")
        print("events:", [(e["type"], e["detail"]) for e in c["events"]])
        print("status:", c["status"], "| error:", c["error"], "| summary:", (c["analysis"] or {}).get("summary"))
        return d["lead_id"]


asyncio.run(main())
