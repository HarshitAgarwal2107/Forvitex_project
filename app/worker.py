"""LiveKit voice worker. Run: python -m app.worker dev"""
import asyncio
import json
import logging
import time

from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli, inference, tts
from livekit.plugins import cartesia, deepgram, sarvam

from . import db, products
from .agent import CallState, SalesAgent, clean_script, attach, build_llm, end_call_record
from .config import AGENT_NAME, env
from .crm import crm
from .rag import kb

log = logging.getLogger("worker")


_make = logging.Logger.makeRecord


def _private(self, name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None):
    """Customers' words never reach the log, at any level. LiveKit marks such fields 'lk.pii.*'."""
    if extra:
        extra = {k: "[hidden]" if k.startswith("lk.pii") or k in ("transcript", "text", "user_transcript", "config") else v for k, v in extra.items()}
    return _make(self, name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)


logging.Logger.makeRecord = _private
MAX_CALL_SECONDS = 600


def prewarm(proc: JobProcess):
    db.init()
    for p in products.all_products():
        kb(p)  # build indexes before the first call, not during it


server = AgentServer(setup_fnc=prewarm, initialize_process_timeout=90, num_idle_processes=1)


class VoiceAgent(SalesAgent):
    """Adds per-turn voice language switching."""
    def __init__(self, state, sarvam_tts, cartesia_tts):
        super().__init__(state)
        self._sarvam, self._cartesia = sarvam_tts, cartesia_tts

    async def stt_node(self, audio, model_settings):
        async for ev in SalesAgent.default.stt_node(self, audio, model_settings):
            for alt in getattr(ev, "alternatives", None) or []:
                alt.text = clean_script(alt.text)
            yield ev

    async def tts_node(self, text, model_settings):
        if self._sarvam:
            self._sarvam.update_options(target_language_code="hi-IN" if self.st.language == "hi" else "en-IN")
        if self._cartesia:
            self._cartesia.update_options(language=self.st.language)
        async for frame in SalesAgent.default.tts_node(self, text, model_settings):
            yield frame


def build_tts(lang, caller):
    s = c = None
    if env("SARVAM_API_KEY"):
        model = env("SARVAM_MODEL", "bulbul:v3")
        speaker = caller["voice"] if model == "bulbul:v3" else env("SARVAM_SPEAKER") or None
        s = sarvam.TTS(model=model, speaker=speaker, target_language_code="hi-IN" if lang == "hi" else "en-IN",
                       pace=caller["pace"], min_buffer_size=30)
    if env("CARTESIA_API_KEY"):
        c = cartesia.TTS(language=lang)
    options = [x for x in (s, c) if x]
    if not options:
        raise RuntimeError("Add SARVAM_API_KEY or CARTESIA_API_KEY to .env")
    return (options[0] if len(options) == 1 else tts.FallbackAdapter(options)), s, c


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext):
    call_id = json.loads(ctx.job.metadata)["call_id"]
    call = db.one("SELECT * FROM calls WHERE id=?", call_id)
    state = CallState(call["lead_id"], call_id)
    voice, s, c = build_tts(state.language, state.caller)

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi", language_hint=["en", "hi"], endpointing_ms=100, punctuate=True,
                         keyterm=state.product["keyterms"] or [state.product["company"]]),
        llm=build_llm(),
        tts=voice,
        turn_handling={
            # local model judges whether the thought is finished, so pauses mid-sentence don't trigger a reply
            "turn_detection": inference.TurnDetector(version="v1-mini"),
            "endpointing": {"min_delay": 0.45, "max_delay": 1.6},
            "interruption": {"min_duration": 0.5, "resume_false_interruption": True, "false_interruption_timeout": 1.5},
            # start thinking while the turn is being confirmed; capped to protect the free quota
            "preemptive_generation": {"enabled": env("PREEMPTIVE", "0") == "1", "max_retries": 1},
        },
        user_away_timeout=12.0,
    )
    attach(session, state)
    last_heard = time.monotonic()
    tasks = set()

    def spawn(coro):
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    def goodbye(en, hi):
        if not state.ended:
            state.ended = True
            session.say(hi if state.language == "hi" else en, allow_interruptions=False)
            session.shutdown(drain=True)

    async def check_silence():
        mark = last_heard
        session.say("हैलो, आप सुन पा रहे हैं?" if state.language == "hi" else "Hey, are you still there?")
        await asyncio.sleep(14)
        if last_heard == mark:
            goodbye("Looks like I've lost you. I'll let you go for now. Take care!", "लगता है आवाज़ कट गई। फिर बात करते हैं, ध्यान रखिए!")

    @session.on("user_state_changed")
    def _(ev):
        nonlocal last_heard
        if ev.new_state == "speaking":
            last_heard = time.monotonic()
        elif ev.new_state == "away" and not state.ended:
            spawn(check_silence())

    @session.on("error")
    def _(ev):
        db.log_event("pipeline_error", {"error": str(getattr(ev.error, "error", ev.error))[:200]}, state.lead_id, call_id)

    @session.on("close")
    def _(ev):
        end_call_record(call_id)
        ctx.shutdown(reason="call ended")

    async def cleanup():
        end_call_record(call_id)  # analysis runs in the API's job loop, so hang-up is instant
        for t in list(tasks):
            t.cancel()
        try:
            await ctx.delete_room()
        except Exception as e:
            log.warning("room cleanup: %s", e)

    ctx.add_shutdown_callback(cleanup)

    async def cap():
        await asyncio.sleep(MAX_CALL_SECONDS)
        goodbye("I've kept you a while, so I'll have the team follow up with you. Thanks for chatting!",
                "काफ़ी देर बात हो गई, आगे team आपसे बात करेगी। धन्यवाद!")

    await session.start(agent=VoiceAgent(state, s, c), room=ctx.room)
    spawn(cap())


if __name__ == "__main__":
    cli.run_app(server)
