"""Typography normalization for comparing model output with tool evidence."""

import unicodedata

HYPHENS = str.maketrans({char: "-" for char in "‐‑‒–—−﹘﹣－"})


def normalize_typography(text: str) -> str:
    """Use ASCII hyphens/spaces, including non-breaking and narrow spaces."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(HYPHENS).split())
