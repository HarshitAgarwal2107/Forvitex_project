"""Hedged LLM: start the primary; if it is slow or fails before its first words, race the
backup and stream whichever speaks first. Cuts worst-case silence while spending little backup quota."""
import asyncio
import re
import time
from collections import deque

from livekit.agents import APIConnectOptions, APITimeoutError, llm

ONE_TRY = APIConnectOptions(max_retry=0, timeout=12.0)


class Slot:
    """One model and what we know about its free quota."""
    def __init__(self, model, fast, per_minute=0, english=0, hindi=0):
        self.model, self.fast, self.per_minute = model, fast, per_minute
        self.rank = {"en": english, "hi": hindi}   # lower speaks first in that language
        self.used = deque()      # (time, tokens) in the last minute
        self.cool_until = 0.0    # after a 429 or repeated failure

    def recent(self):
        while self.used and time.monotonic() - self.used[0][0] > 60:
            self.used.popleft()
        return sum(t for _, t in self.used)


class HedgedLLM(llm.LLM):
    def __init__(self, slots: list):
        super().__init__()
        self.slots = [s if isinstance(s, Slot) else Slot(s, True) for s in slots]

    @property
    def models(self):
        return [s.model for s in self.order(0)]

    def order(self, tokens: int, language: str = "en") -> list[Slot]:
        """Best models for this language that have room (least used first among equals), then anything resting."""
        now = time.monotonic()
        ready = [s for s in self.slots if s.cool_until <= now and (not s.per_minute or s.recent() + tokens <= s.per_minute)]
        ready.sort(key=lambda s: (s.rank[language], not s.fast, s.recent()))
        return ready + sorted((s for s in self.slots if s not in ready), key=lambda s: s.cool_until)

    def chat(self, **kw):  # used only if something bypasses the agent's llm_node
        return self.models[0].chat(**kw)


def rest(slot: Slot, e: Exception):
    """Stop asking a model that said it is out of quota, for as long as it asked us to."""
    if getattr(e, "status_code", None) == 429:
        text = str(e)
        m = re.search(r"try again in (?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?", text)
        wait = sum(float(x or 0) * k for x, k in zip(m.groups(), (3600, 60, 1))) if m else 0
        slot.cool_until = time.monotonic() + min(max(wait, 5), 3600)
    elif getattr(e, "status_code", None) in (404, 401, 403):
        slot.cool_until = time.monotonic() + 600
    else:
        slot.cool_until = time.monotonic() + 8


async def stream(models, *, chat_ctx, tools, tool_choice, delay: float, hard_timeout: float = 12.0, tokens: int = 0):
    slots = [m if isinstance(m, Slot) else Slot(m, True) for m in models]
    q: asyncio.Queue = asyncio.Queue()
    tasks: list[asyncio.Task] = []

    async def pump(i):
        try:
            kw = {"tool_choice": tool_choice} if tool_choice else {}
            slots[i].used.append((time.monotonic(), tokens))
            async with slots[i].model.chat(chat_ctx=chat_ctx, tools=tools, conn_options=ONE_TRY, **kw) as s:
                async for chunk in s:
                    q.put_nowait((i, "chunk", chunk))
            q.put_nowait((i, "end", None))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            rest(slots[i], e)
            q.put_nowait((i, "error", e))

    def launch():
        tasks.append(asyncio.create_task(pump(len(tasks))))

    launch()
    winner, over, last_error = None, 0, None
    try:
        while True:
            waiting = winner is None and len(tasks) < len(models)
            try:
                i, kind, val = await asyncio.wait_for(q.get(), delay if waiting else hard_timeout)
            except asyncio.TimeoutError:
                if waiting:
                    launch()
                    continue
                raise APITimeoutError("no model answered in time") from None
            if winner is None:
                if kind != "chunk":  # failed or finished with nothing to say
                    over += 1
                    last_error = val or last_error
                    if len(tasks) < len(models):
                        launch()
                    elif over == len(tasks):
                        if last_error:
                            raise last_error
                        return
                    continue
                if not (val.delta and (val.delta.content or val.delta.tool_calls)):
                    continue
                winner = i
                for j, t in enumerate(tasks):
                    if j != i:
                        t.cancel()
                yield val
            elif i == winner:
                if kind == "error":
                    raise val
                if kind == "end":
                    return
                yield val
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
