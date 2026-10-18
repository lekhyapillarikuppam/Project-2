"""Attribution windows (SPEC §15.3): consecutive paragraphs of a chapter, up to a word budget, that
never split an utterance, plus the 2 previous paragraphs as read-only context without ids.

In the rendered text every span of the window's utterances is prefixed with its id: `[Q3]‘…’`
(numbered from Q1 in each window; a broken quote's spans carry the same id).
"""

from dataclasses import dataclass

from app.dialogue.segment import QuoteSpan

CONTEXT_PARAGRAPHS = 2


@dataclass(frozen=True)
class Utterance:
    id: str  # the segmenter's utterance id (U1…)
    parts: tuple[tuple[int, int, int], ...]  # (paragraph, start, end) per span, in text order

    @property
    def first_paragraph(self) -> int:
        return self.parts[0][0]

    @property
    def last_paragraph(self) -> int:
        return self.parts[-1][0]


def collect_utterances(spans: list[list[QuoteSpan]]) -> list[Utterance]:
    """Utterances in text order from the segmenter's spans per paragraph."""
    parts: dict[str, list[tuple[int, int, int]]] = {}
    for p, row in enumerate(spans):
        for q in row:
            parts.setdefault(q.utterance_id, []).append((p, q.start, q.end))
    return [Utterance(uid, tuple(ps)) for uid, ps in parts.items()]


@dataclass(frozen=True)
class Window:
    index: int
    context: tuple[int, ...]  # paragraph indices shown without ids
    paragraphs: tuple[int, ...]  # paragraph indices whose quotes are labelled
    utterance_ids: tuple[str, ...]  # Q1, Q2, … in this order


def _blocks(paragraph_count: int, utterances: list[Utterance]) -> list[tuple[int, int]]:
    """Paragraph ranges [first, last] that must stay together (an utterance over several paragraphs)."""
    joined_to_next = [False] * paragraph_count
    for u in utterances:
        for p in range(u.first_paragraph, u.last_paragraph):
            joined_to_next[p] = True
    blocks: list[tuple[int, int]] = []
    start = 0
    for p in range(paragraph_count):
        if not joined_to_next[p]:
            blocks.append((start, p))
            start = p + 1
    return blocks


def build_windows(paragraphs: list[str], utterances: list[Utterance], max_words: int) -> list[Window]:
    """Pack paragraph blocks into windows of at most `max_words` words (a single bigger block is a
    window of its own). Windows without utterances are dropped: they need no request."""
    words = [len(t.split()) for t in paragraphs]
    groups: list[list[int]] = []
    current: list[int] = []
    count = 0
    for first, last in _blocks(len(paragraphs), utterances):
        block = list(range(first, last + 1))
        size = sum(words[p] for p in block)
        if current and count + size > max_words:
            groups.append(current)
            current, count = [], 0
        current += block
        count += size
    if current:
        groups.append(current)

    windows: list[Window] = []
    for group in groups:
        inside = set(group)
        ids = tuple(u.id for u in utterances if u.first_paragraph in inside)
        if not ids:
            continue
        context = tuple(range(max(0, group[0] - CONTEXT_PARAGRAPHS), group[0]))
        windows.append(Window(len(windows), context, tuple(group), ids))
    return windows


def render_window(window: Window, paragraphs: list[str], utterances: list[Utterance]) -> tuple[str, dict[str, str]]:
    """The window's text with `[Qn]` before every span, and the map `Qn -> utterance id`."""
    by_id = {u.id: u for u in utterances}
    labels = {uid: f"Q{n}" for n, uid in enumerate(window.utterance_ids, 1)}
    marks: dict[int, list[tuple[int, str]]] = {}
    for uid, label in labels.items():
        for p, start, _ in by_id[uid].parts:
            marks.setdefault(p, []).append((start, label))
    rendered: list[str] = []
    for p in window.paragraphs:
        text = paragraphs[p]
        for start, label in sorted(marks.get(p, []), reverse=True):
            text = f"{text[:start]}[{label}]{text[start:]}"
        rendered.append(text)
    context = "\n\n".join(paragraphs[p] for p in window.context)
    body = "\n\n".join(rendered)
    text = f"CONTEXT (earlier text, no ids):\n{context}\n\nTEXT:\n{body}" if context else f"TEXT:\n{body}"
    return text, {label: uid for uid, label in labels.items()}
