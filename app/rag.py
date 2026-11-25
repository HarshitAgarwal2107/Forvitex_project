"""Local hybrid RAG: multilingual embeddings + keyword overlap. Uses no API quota."""
import json
import re
from functools import lru_cache

import numpy as np

from .config import ROOT

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MIN_SCORE = 0.28

# Hindi / Hinglish sales terms useful for any product; packs add their own
GLOSSARY = {
    "price pricing fees starts": "kitne kitna kitni keemat kimat daam rate fees कीमत दाम कितने कितना कितनी प्राइस रेट फीस",
    "loan instalments banks": "lone karz emi kist लोन कर्ज ईएमआई किस्त बैंक", "discounts offers": "chhoot chhut kam negotiate छूट डिस्काउंट कम",
    "overview schedule date": "kab कब", "refund cancellation": "wapas vapas paisa वापस रिफंड कैंसिल",
    "amenities features": "suvidha facilities suvidhayen सुविधा सुविधाएं", "visit session booking": "dekhne dekhna visit देखने देखना विजिट",
    "guarantee warranty": "guarantee pakka पक्का गारंटी वारंटी", "documents": "kagaz papers कागज दस्तावेज",
}


def aliases(extra: dict) -> dict:
    return {w: en for g in (GLOSSARY, extra) for en, words in g.items() for w in words.split()}


def expand(query, table):
    extra = [table[w] for w in re.findall(r"[\w\u0900-\u097F]+", query.lower()) if w in table]
    return " ".join([query, *dict.fromkeys(extra)])


def chunks(text: str, name: str):
    """Headed documents: one chunk per '## section'. Plain pasted text: one chunk per paragraph."""
    out, doc, sec, buf = [], name, "", []

    def flush():
        if buf:
            out.append({"title": f"{doc} / {sec}" if sec else doc, "text": " ".join(buf)})
            buf.clear()

    for line in text.splitlines():
        if line.startswith("# "):
            flush()
            doc, sec = line[2:], ""
        elif line.startswith("## "):
            flush()
            sec = line[3:]
        elif line.strip():
            buf.append(line.strip())
            if sum(map(len, buf)) > 600:
                flush()
        elif not sec:
            flush()
    flush()
    return out


def _tokens(s):
    return {w for w in re.findall(r"\w+", s.lower()) if len(w) > 2}


@lru_cache(1)
def embedder():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL, cache_dir=str(ROOT / "data" / "models"))


class KB:
    def __init__(self, knowledge, name, glossary):
        self.table = aliases(glossary)
        self.docs = chunks(knowledge, name)
        self.vecs = self._embed([d["title"] + ". " + d["text"] for d in self.docs])
        self.toks = [_tokens(d["title"] + " " + d["text"]) for d in self.docs]

    def _embed(self, texts):
        v = np.array(list(embedder().embed(texts)))
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    def search(self, query, k=3):
        if not query.strip():
            return []
        query = expand(query, self.table)
        sims = self.vecs @ self._embed([query])[0]
        q = _tokens(query)
        hits = []
        for i, d in enumerate(self.docs):
            kw = len(q & self.toks[i]) / len(q) if q else 0
            hits.append({**d, "score": round(float(0.75 * sims[i] + 0.35 * kw), 3)})
        hits.sort(key=lambda h: -h["score"])
        return [h for h in hits[:k] if h["score"] >= MIN_SCORE]


@lru_cache(64)
def _kb(knowledge, name, glossary):
    return KB(knowledge, name, json.loads(glossary))


def kb(product: dict) -> KB:
    """One index per product, rebuilt only when its knowledge changes. Products never see each other's facts."""
    return _kb(product["knowledge"], product["company"], json.dumps(product["glossary"], ensure_ascii=False))
