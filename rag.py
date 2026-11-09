"""
RAG over Munshi's tax & compliance knowledge base (knowledge/*.md).

Retrieval: BM25 keyword ranking, written from scratch (no external services, deploys anywhere).
Each '## section' of a document is one chunk. Search returns the top chunks with a citation id
so the Tax advisor can ground its answer and cite where each rule came from.
"""
import math
import os
import re
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.join(HERE, "knowledge")
STOP = set("""a an the and or of to in on for is are be by with as at from that this it its if you your
can do does i my me we our not no yes what how when which who will would should may also than then
so up out into under over about per any all""".split())
SYNONYMS = {"44ada": "section58 presumptive", "44ad": "section58 presumptive business", "58": "section58",
            "87a": "rebate", "lut": "lut undertaking", "gst": "gst", "firc": "firc fira",
            "itr": "itr return", "slab": "slabs", "advance": "advance", "freelancer": "freelancer"}


def tokenize(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in STOP:
            continue
        w = w[:-1] if len(w) > 4 and w.endswith("s") else w   # light stemming
        out.append(w)
        if w in SYNONYMS:
            out.extend(SYNONYMS[w].split())
    return out


def load_chunks():
    chunks = []
    for fn in sorted(os.listdir(KB_DIR)):
        if not fn.endswith(".md"):
            continue
        text = open(os.path.join(KB_DIR, fn), encoding="utf-8").read()
        title = re.search(r"^# (.+)$", text, re.M).group(1).strip()
        sources = (re.search(r"^Sources: (.+)$", text, re.M) or [None, ""])[1].strip()
        for part in re.split(r"^## ", text, flags=re.M)[1:]:
            heading, _, body = part.partition("\n")
            body = body.strip()
            chunks.append({"id": f"{fn[:2]}.{len(chunks) + 1}", "doc": title, "section": heading.strip(),
                           "text": body, "sources": sources,
                           "tokens": tokenize(f"{title} {heading} {heading} {body}")})
    return chunks


class BM25:
    def __init__(self, chunks, k1=1.4, b=0.75):
        self.chunks, self.k1, self.b = chunks, k1, b
        self.tf = [Counter(c["tokens"]) for c in chunks]
        self.len = [len(c["tokens"]) for c in chunks]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter(t for c in chunks for t in set(c["tokens"]))
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query, k=3):
        q = tokenize(query)
        scores = []
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in q:
                if t in tf:
                    f = tf[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            scores.append((s, i))
        top = sorted(scores, reverse=True)[:k]
        return [{**{k_: v for k_, v in self.chunks[i].items() if k_ != "tokens"}, "score": round(s, 2)}
                for s, i in top if s > 0]


INDEX = BM25(load_chunks())


def search_tax_rules(query, k=3):
    hits = INDEX.search(query, k)
    if not hits:
        return {"results": [], "note": "No matching rule in the knowledge base. Say you don't know; suggest a CA."}
    return {"results": [{"cite": f"[{h['doc']} › {h['section']}]", "text": h["text"],
                         "sources": h["sources"], "score": h["score"]} for h in hits]}


if __name__ == "__main__":
    for q in ["Do I need GST registration for foreign clients?", "when is advance tax due presumptive",
              "is content writing a specified profession", "what is the 44ADA limit", "purpose code for web development"]:
        print(q, "->", [h["section"] for h in INDEX.search(q)])
