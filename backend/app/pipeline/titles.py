"""Title normalization and matching (SPEC §13). Shared by structure.py and scripts/evaluate.py."""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

MATCH_THRESHOLD = 80  # SPEC §13: fuzz.ratio >= 80 makes a candidate pair

# Strict roman numeral (1-3999), so ordinary words like "did" or "mild" are not mistaken for numbers.
# The lookbehind makes sure at least one numeral letter was consumed (every part is optional).
ROMAN = r"(?:m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3}))(?<=[mdclxvi])"
_UNITS = "one|two|three|four|five|six|seven|eight|nine"
_TEENS_TENS = (
    "ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
)
NUMBER_WORD = rf"(?:(?:{_TEENS_TENS})(?:[- ](?:{_UNITS}))?|{_UNITS})"
LABEL_WORD = r"(?:chapter|part|book|section|unit|lecture)"
LABEL_NUMBER = rf"(?:\d+|{ROMAN}|{NUMBER_WORD})"

# A chapter label at the start of a title, plus the separator after it.
_LEADING_LABEL = re.compile(rf"^{LABEL_WORD}\s+{LABEL_NUMBER}\b\s*[:.\-–—]?\s*", re.IGNORECASE)
# A whole line that is only a chapter label ("Chapter I", "CHAPTER 3", "Part Two").
LABEL_ONLY = re.compile(rf"^{LABEL_WORD}\s+{LABEL_NUMBER}\s*[:.]?$", re.IGNORECASE)
# A chapter label followed by anything (strategy 4 text pattern).
LABEL_START = re.compile(rf"^{LABEL_WORD}\s+{LABEL_NUMBER}\b", re.IGNORECASE)
_LEADING_SECTION_NUMBER = re.compile(r"^\d+(?:\.\d+)*\.?\s+")
# A bare roman number with a period ("XVII. The Idiot"); arabic ones are covered above (SPEC §13, rev 4).
_LEADING_BARE_ROMAN = re.compile(rf"^{ROMAN}\.\s+", re.IGNORECASE)
_TRAILING_PART = re.compile(r"\s*\(part\s+\d+\)\s*$", re.IGNORECASE)
_WORD_SEPARATORS = re.compile(r"[-–—/_]")
_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def _strip_punctuation(t: str) -> str:
    t = _WORD_SEPARATORS.sub(" ", t)
    t = _PUNCTUATION.sub("", t)
    return _WHITESPACE.sub(" ", t).strip()


def _normalize(s: str) -> tuple[str, bool]:
    """(normalized title, whether the label-keeping fallback was used)."""
    t = s.lower().strip()
    t = _LEADING_LABEL.sub("", t)
    t = _LEADING_SECTION_NUMBER.sub("", t)
    t = _LEADING_BARE_ROMAN.sub("", t)
    t = _TRAILING_PART.sub("", t)
    t = _strip_punctuation(t)
    if t:
        return t, False
    return _strip_punctuation(s.lower()), True


def normalize_title(s: str) -> str:
    """Lowercase; strip leading chapter label and section number, trailing "(part N)", punctuation.

    If that leaves nothing (a heading that is only `Chapter 12`), the label is kept instead:
    lowercase, punctuation removed, whitespace collapsed (SPEC §13).
    """
    return _normalize(s)[0]


def title_score(a: str, b: str) -> float:
    """fuzz.ratio of the normalized titles (0-100). Empty titles never match.

    Label-only titles (`chapter 12`) differ from each other only in their number, which fuzz.ratio
    barely notices (`chapter 12` vs `chapter 13` scores 90), so they must be identical to match.
    """
    na, fallback_a = _normalize(a)
    nb, fallback_b = _normalize(b)
    if not na or not nb:
        return 0.0
    if fallback_a or fallback_b:
        return 100.0 if na == nb else 0.0
    return fuzz.ratio(na, nb)


def _greedy_pairs(left: list[str], right: list[str]) -> list[tuple[int, int, float]]:
    """One-to-one matching: repeatedly take the best remaining pair with score >= threshold."""
    candidates = [
        (score, i, j)
        for i, a in enumerate(left)
        for j, b in enumerate(right)
        if (score := title_score(a, b)) >= MATCH_THRESHOLD
    ]
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs = []
    for score, i, j in candidates:
        if i in used_left or j in used_right:
            continue
        used_left.add(i)
        used_right.add(j)
        pairs.append((i, j, score))
    return pairs


@dataclass
class MatchResult:
    tp: int
    detected: int  # detected titles counted (after removing optional matches)
    labeled: int
    precision: float
    recall: float
    f1: float
    order_ok: bool
    pairs: list[tuple[str, str, float]] = field(default_factory=list)  # (detected, labeled, score)
    unmatched_detected: list[str] = field(default_factory=list)
    unmatched_labeled: list[str] = field(default_factory=list)
    optional_matched: list[str] = field(default_factory=list)


def prf(tp: int, detected: int, labeled: int) -> tuple[float, float, float]:
    """Precision, recall, F1. With nothing detected and nothing labeled the result is perfect."""
    if detected == 0 and labeled == 0:
        return 1.0, 1.0, 1.0
    precision = tp / detected if detected else 0.0
    recall = tp / labeled if labeled else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def match_titles(detected: list[str], labeled: list[str], optional: list[str]) -> MatchResult:
    """Score detected chapter titles against labels (SPEC §13).

    Required labels are matched first; detected titles left unmatched that match an optional
    label are then removed, so an optional label can never take a match away from a required one.
    """
    pairs = _greedy_pairs(detected, labeled)
    matched_detected = {i for i, _, _ in pairs}
    leftover = [i for i in range(len(detected)) if i not in matched_detected]
    optional_pairs = _greedy_pairs([detected[i] for i in leftover], optional)
    optional_idx = {leftover[k] for k, _, _ in optional_pairs}

    counted = len(detected) - len(optional_idx)
    tp = len(pairs)
    precision, recall, f1 = prf(tp, counted, len(labeled))
    by_detected = sorted(pairs)
    label_order = [j for _, j, _ in by_detected]
    matched_labeled = {j for _, j, _ in pairs}
    return MatchResult(
        tp=tp,
        detected=counted,
        labeled=len(labeled),
        precision=precision,
        recall=recall,
        f1=f1,
        order_ok=label_order == sorted(label_order),
        pairs=[(detected[i], labeled[j], score) for i, j, score in by_detected],
        unmatched_detected=[
            detected[i] for i in leftover if i not in optional_idx
        ],
        unmatched_labeled=[t for j, t in enumerate(labeled) if j not in matched_labeled],
        optional_matched=[detected[i] for i in sorted(optional_idx)],
    )
