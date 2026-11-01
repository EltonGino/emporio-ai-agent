"""Accent/case-insensitive fuzzy matching of catalog queries (rapidfuzz + unidecode).

A query is split into terms; each term must match a product term exactly or, for words of
5+ letters, with a small typo ("takamini" → "takamine"). Model codes match with or without
hyphens and spaces ("GD20", "gd-20", "gd 20").
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz
from unidecode import unidecode

from emporio_agent.core.retrieval.bm25 import STOPWORDS

TOKEN = re.compile(r"[a-z0-9]+")
INNER_JOINER = re.compile(r"(?<=[a-z0-9])[-'](?=[a-z0-9])")  # "gd-20" → "gd20"
CODE_PARTS = re.compile(r"[a-z]+|[0-9]+")  # "gd20" → "gd", "20"
MIN_SIMILARITY = 85  # rapidfuzz ratio; 85 accepts "yamha"/"takamini" but not "kala"/"kalani"
MIN_FUZZY_LENGTH = 5  # shorter words and numbers must match exactly


def _singular(word: str) -> str:
    """Rough Portuguese singular, applied to queries and products alike."""
    if not word.isalpha() or len(word) <= 3:
        return word
    if word.endswith(("oes", "aes")):
        return word[:-3] + "ao"  # violões → violao
    return word[:-1] if word.endswith("s") else word


def _words(text: str) -> list[str]:
    text = INNER_JOINER.sub("", unidecode(text).lower())
    return [_singular(w) for w in TOKEN.findall(text) if w not in STOPWORDS]


def query_terms(text: str) -> list[str]:
    return list(dict.fromkeys(_words(text)))


def document_terms(text: str) -> set[str]:
    terms = set()
    for word in _words(text):
        terms.add(word)
        parts = CODE_PARTS.findall(word)
        if len(parts) > 1:
            terms.update(parts)
    return terms


def term_matches(term: str, terms: set[str]) -> bool:
    if term in terms:
        return True
    if len(term) < MIN_FUZZY_LENGTH or not term.isalpha():
        return False
    return any(fuzz.ratio(term, t) >= MIN_SIMILARITY for t in terms if t.isalpha())


def match_score(terms: list[str], document: set[str]) -> float:
    """Fraction of query terms found in the document (1.0 = all of them)."""
    if not terms:
        return 1.0
    return sum(term_matches(t, document) for t in terms) / len(terms)
