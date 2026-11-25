"""Truth guard: checks each sentence before it is spoken. The agent may not claim an
action that did not happen. Prompts ask for honesty; this enforces the costly cases."""
import re

from .booking import weekdays_in
from .safety import asks_for_secret

_SENTENCE = re.compile(r"(?<=[.!?।])\s+")

RULES = [  # (pattern, actions that make it true, English replacement, Hindi replacement)
    (re.compile(r"(taken|took|removed|taking|removing) (you|your \w+) (off|from)"
                r"|(won't|will not) (hear from us|be (bothered|called|contacted))"
                # broad on purpose: a false alarm only turns a claim into an offer
                r"|^(?!.*\b(can|could|if you|want|would|shouldn't|rather)\b).*\b(stop|stopped|stopping|remov\w+|mark|marked|block\w*|cancelled|no more|won't|don't|never|nobody)\b"
                r".*\b(calls?|calling|contact\w*|list|bother\w*|your number|reach(ing)? out|hear from us|disturb\w*)\b|\bno more\b.*\bpromise\b"
                r"|(make|made) sure (we|you|nobody)\b.*\b(bother|call|contact)"
                r"|list से (हटा|निकाल) दिया", re.I),
     {"opted_out"}, "If you'd rather not get calls, just say so and I'll take you off the list.",
     "अगर आप calls नहीं चाहते, तो बस बता दीजिए, मैं list से हटा दूँगी।"),
    (re.compile(r"\b(i've|i have|i'll have|just) (now )?(booked|reserved|confirmed)"
                r"|\b(all|you're|it's|that's) (booked|confirmed)"
                r"|(book|confirm|cancel|बुक|कन्फर्म|कैंसिल) (हो गई|हो गया|कर दी|कर दिया)", re.I),
     {"visit_booked", "visit_rescheduled", "visit_cancelled", "HAS_VISIT"},
     "I haven't locked that in yet. Shall I?", "अभी book नहीं हुआ है। कर दूँ?"),
    (re.compile(r"\b(i've|i have|we've|just|it's been|all) (now )?(just )?(moved|rescheduled|shifted)", re.I), {"visit_rescheduled"},
     "I haven't moved it yet. Shall I?", "अभी बदला नहीं है। बदल दूँ?"),
    (re.compile(r"\b(i've|i have|we've|just|it's been) (now )?cancell?ed|cancel (हो गई|कर दी)", re.I), {"visit_cancelled"},
     "I haven't cancelled it yet. Shall I?", "अभी cancel नहीं हुई है। कर दूँ?"),
    (re.compile(r"\b(i've|i have) (asked|alerted|told|let|notified)\b.*\b(team|someone|manager|colleague)"
                r"|team को (बता|inform कर) दिया", re.I),
     {"human_requested"}, "Would you like someone from the team to call you back?",
     "क्या आप चाहेंगे कि team से कोई आपको call करे?"),
    (re.compile(r"\b(i'll|i will|i can|i've|we'll) (also |just )?(text|email|e-mail|whatsapp|message|send|sent|share)\b"
                r"|(भेज|send कर|share कर|शेयर कर) (दूँग[ीा]|दूंग[ीा]|देत[ीा] हूँ|सकत[ीा] हूँ)|भेजूँग[ीा]", re.I),
     set(), "", ""),
]


HOURS = {"ten": 10, "eleven": 11, "twelve": 12, "noon": 12, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "दस": 10, "ग्यारह": 11, "बारह": 12, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5}
_H = "|".join(HOURS) + r"|\d{1,2}"
_HOUR = re.compile(rf"\b(?:at|to|for) ({_H})\b(?! (?:lakh|crore|point|bed))|\b({_H})(?::00)? ?(?:am|pm|a\.m|p\.m)\b|({_H}) बजे", re.I)


def hours_in(text):
    found = [next(g for g in m if g).lower() for m in _HOUR.findall(text)]
    return {HOURS.get(h) or int(h) for h in found}


CONFIRMING = re.compile(r"\b(booked|confirmed|all set|set for|see you|moved|rescheduled|your (visit|session|ride|demo|appointment|consultation|call))\b|बुक|book (हो|कर)|visit .*(है|रहेगी)", re.I)


_ROMAN = set("hai hain ho hoon mein main aap aapko aapke mujhe kya nahi nahin ke ka ki se ko toh lekin aur kar sakti sakta rahe raha rahi agar chahiye "
             "wo ye kis kaun abhi bas bata dijiye hota hoti unka gayi gaya par".split())


def roman_hindi(s: str) -> bool:
    words = re.findall(r"[a-z]+", s.lower())
    return len(words) >= 4 and sum(w in _ROMAN for w in words) / len(words) >= 0.3


OPENER = re.compile(r"\s*(great|awesome|perfect|absolutely|wonderful|excellent|got it|i understand|certainly|sure thing)[.!,]+\s*", re.I)


_UNIT = {"thousand": 1e3, "k": 1e3, "हज़ार": 1e3, "हजार": 1e3, "hazaar": 1e3, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "लाख": 1e5,
         "crore": 1e7, "crores": 1e7, "करोड़": 1e7, "million": 1e6}
_AMOUNT = re.compile(rf"(\d[\d,]*(?:\.\d+)?)\s*({'|'.join(sorted(_UNIT, key=len, reverse=True))})?(?![\w\u0900-\u097F])", re.I)


def amounts(text: str) -> set[int]:
    """Money-sized figures in a text, as plain numbers: '1.3 lakh' -> 130000."""
    out = set()
    for num, unit in _AMOUNT.findall(str(text)):
        num = num.rstrip(",")
        v = float(num.replace(",", "")) * _UNIT.get((unit or "").lower(), 1)
        if v >= 1000 and not (not unit and "," not in num and 1990 <= v <= 2100):  # ignore years
            out.add(round(v))
    return out


def check(sentence: str, done: set[str], hindi: bool) -> tuple[str, bool]:
    """Returns (what to say, whether it was changed)."""
    plain = sentence.replace("’", "'")
    if plain.rstrip().endswith("?"):  # an offer or question is not a claim
        return sentence, False
    for pattern, proof, en, hi in RULES:
        if pattern.search(plain) and not (proof & done):
            return (hi if hindi else en), True
    return sentence, False


class SentenceGuard:
    def __init__(self, done, hindi, on_block=lambda s: None, visit=None, known_amounts=None, gender="female", limit=None):
        self.limit, self.count = limit, 0   # most sentences in one reply
        self.gender = gender
        self.known = known_amounts
        self.visit, self.first, self.wrong_language = visit, True, False
        self.done, self.hindi, self.on_block = done, hindi, on_block
        self.buf, self.said, self.replaced = "", False, set()

    def _check(self, s):
        if asks_for_secret(s.replace("’", "'")):  # whatever any prompt says
            return ("मैं card, bank या OTP की जानकारी कभी नहीं माँगती।" if self.hindi else "I'll never ask for card, bank or OTP details on a call."), True
        out, changed = check(s, self.done, self.hindi)
        # a figure the product data (or the caller) never mentioned is never spoken
        if not changed and self.known is not None and amounts(s) - self.known:
            out, changed = ("सही figure team से पूछना बेहतर रहेगा, मैं अंदाज़ा नहीं लगाना चाहती।" if self.hindi else
                            "I'm not certain of the exact figure, so that's one to check with the team."), True
        # a confirmation must name the day that is really booked
        if not changed and self.visit and CONFIRMING.search(s.replace("’", "'")) and not s.rstrip().endswith("?"):
            days, hours = weekdays_in(s), hours_in(s)
            real = hours_in(self.visit["when"].split(", ")[-1])
            if (days and days != weekdays_in(self.visit["when"])) or (hours and real and hours != real):
                out, changed = (f"आपकी booking {self.visit['when']} को है, {self.visit['project']}।" if self.hindi else
                                f"You're booked for {self.visit['when']}, {self.visit['project']}."), True
        return out, changed

    def _one(self, s):
        if not self.hindi and sum("\u0900" <= c <= "\u097f" for c in s) > 3:
            self.wrong_language = True
            return ""
        if self.hindi and not self.said and roman_hindi(s):
            self.wrong_language = True
            return ""
        if self.first and (m := OPENER.match(s)):  # stock openers make it sound like a bot
            s = s[m.end():]
            s = s[:1].upper() + s[1:]
            if not s.strip():
                return ""
        self.first = False
        if self.limit and self.count >= self.limit:
            return ""  # nobody likes a monologue on the phone
        if s.strip():
            self.count += 1
        out, changed = self._check(s)
        if changed and self.gender == "male":
            out = out.replace("दूँगी", "दूँगा").replace("चाहती", "चाहता").replace("माँगती", "माँगता")
        if changed:
            self.on_block(s.strip())
            if out in self.replaced:  # say a correction once per reply
                return ""
            self.replaced.add(out)
        self.said |= bool(out.strip())
        return out

    def push(self, text):
        self.buf += text
        parts = _SENTENCE.split(self.buf)
        self.buf = parts.pop()
        return "".join(o + " " for p in parts if (o := self._one(p)))

    def flush(self):
        rest, self.buf = self.buf, ""
        return self._one(rest) if rest.strip() else ""
