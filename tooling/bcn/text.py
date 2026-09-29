"""Text normalisation for script-to-SRT alignment.

Both sides go through exactly the same normalisation: lowercase, punctuation
stripped, contractions expanded, numerals spelled out. Each token keeps the index
of the surface word it came from, so divergences can be reported in the
original wording.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}

FILLERS = {"um", "uh", "erm", "er", "ah", "hmm", "mm"}
_S_IS = {"it", "that", "what", "there", "here", "he", "she", "who", "where", "how", "this", "everything", "nothing", "something"}
_CONTRACTIONS = {
    "won't": "will not", "can't": "can not", "cannot": "can not", "shan't": "shall not", "ain't": "is not",
    "let's": "let us", "y'all": "you all",
}
_SUFFIXES = [("n't", " not"), ("'re", " are"), ("'ve", " have"), ("'ll", " will"), ("'m", " am"), ("'d", " would")]

WORD_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*(?:st|nd|rd|th|%)?|[^\W\d_]+(?:'[^\W\d_]+)*", re.UNICODE)


def number_words(n: int) -> list[str]:
    if n < 0:
        return ["minus"] + number_words(-n)
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        t, o = divmod(n, 10)
        return [_TENS[t]] + ([_ONES[o]] if o else [])
    if n < 1000:
        h, r = divmod(n, 100)
        return [_ONES[h], "hundred"] + (number_words(r) if r else [])
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= size:
            q, r = divmod(n, size)
            return number_words(q) + [name] + (number_words(r) if r else [])
    return [str(n)]


def _ordinal(words: list[str]) -> list[str]:
    last = words[-1]
    if last in _ORD:
        last = _ORD[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last = last + "th"
    return words[:-1] + [last]


def _expand_number(tok: str) -> list[str]:
    pct = tok.endswith("%")
    if pct:
        tok = tok[:-1]
    m = re.match(r"^(\d+)(st|nd|rd|th)$", tok)
    if m:
        return _ordinal(number_words(int(m.group(1))))
    tok = tok.replace(",", "") if re.match(r"^\d{1,3}(,\d{3})+$", tok) else tok
    if "." in tok or "," in tok:
        whole, _, frac = re.split(r"([.,])", tok, maxsplit=1)
        out = number_words(int(whole)) + ["point"] + [_ONES[int(d)] for d in frac if d.isdigit()]
    else:
        out = number_words(int(tok)) if tok.isdigit() else [tok]
    return out + (["percent"] if pct else [])


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = re.sub(r"[‐‑‒–—―/-]", " ", text)
    text = re.sub(r"\*\*|__|\[|\]\([^)]*\)", " ", text)
    return text.lower()


def tokens(text: str) -> tuple[list[str], list[int], list[str]]:
    """Return (normalised tokens, surface-word index per token, surface words)."""
    norm: list[str] = []
    owner: list[int] = []
    surface: list[str] = []
    raw_words = re.findall(r"\S+", text)
    for wi, raw in enumerate(raw_words):
        surface.append(raw)
        for w in WORD_RE.findall(_clean(raw)):
            if w in _CONTRACTIONS:
                parts = _CONTRACTIONS[w].split()
            elif w[0].isdigit():
                parts = _expand_number(w)
            else:
                parts = [w]
                for suf, rep in _SUFFIXES:
                    if w.endswith(suf) and len(w) > len(suf):
                        parts = (w[: -len(suf)] + rep).split()
                        break
                else:
                    if w.endswith("'s"):
                        base = w[:-2]
                        parts = [base, "is"] if base in _S_IS else [base]
                    elif "'" in w:
                        parts = [w.replace("'", "")]
            for p in parts:
                if p and p not in FILLERS:
                    norm.append(p)
                    owner.append(wi)
    return norm, owner, surface


def surface_span(surface: list[str], owner: list[int], a: int, b: int) -> str:
    """Original words covering tokens [a, b)."""
    if a >= b or not owner:
        return ""
    lo, hi = owner[a], owner[b - 1]
    return " ".join(surface[lo: hi + 1])


# -- phonetic similarity, for telling a mishearing from a paraphrase ------------------

def phonetic(word: str) -> str:
    """A small Metaphone-like key. Good enough to say 'like it' sounds like 'Likert'."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    for a, b in (("^kn", "n"), ("^gn", "n"), ("^pn", "n"), ("^wr", "r"), ("^x", "s"), ("^wh", "w"),
                 ("mb$", "m"), ("ph", "f"), ("ck", "k"), ("sch", "sk"), ("tch", "ch"), ("dg", "j"),
                 ("gh(?=[^aeiou]|$)", ""), ("c(?=[iey])", "s"), ("c", "k"), ("q", "k"), ("x", "ks"),
                 ("z", "s"), ("v", "f"), ("th", "0"), ("sh", "x"), ("ch", "x"), ("tio", "xo"), ("tia", "xa"),
                 ("d", "t"), ("b", "p"), ("g", "k")):
        w = re.sub(a, b, w)
    first, rest = w[0], w[1:]
    rest = re.sub(r"(?<=[^aeiou])h", "", rest)
    rest = re.sub(r"[wy](?![aeiou])", "", rest)
    rest = re.sub(r"[aeiouy]", "", rest)
    key = ("a" if first in "aeiou" else first) + rest
    return re.sub(r"(.)\1+", r"\1", key)


def sounds_alike(a: list[str], b: list[str]) -> float:
    """Similarity of two token spans, the better of phonetic and spelling similarity."""
    ja, jb = "".join(a), "".join(b)
    pa, pb = "".join(phonetic(x) for x in a), "".join(phonetic(x) for x in b)
    return max(SequenceMatcher(None, pa, pb).ratio() if pa and pb else 0.0,
               SequenceMatcher(None, ja, jb).ratio() if ja and jb else 0.0)
