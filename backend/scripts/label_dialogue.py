"""Local labelling tool for dialogue attribution gold labels (SPEC §15.9, Phase 6.0).

    python scripts\\label_dialogue.py ..\\testset_dialogue\\text\\alice_ch07.json [--port 8765] [--no-browser]

Starts a small server on http://localhost:8765 (standard library only) serving
scripts/label_dialogue.html, and opens the browser. Every change is saved at once to
testset_dialogue/labels/:
    <name>.jsonl       gold utterances, one per line: id, paragraph, start, end, text, speaker,
                       explicit, utterance (paragraph = 0-based position in the text file's list,
                       start/end = character offsets in that paragraph, end exclusive)
    <name>.cast.json   {canonical name: {"aliases": [...], "gender": "female|male|unknown"}}
    <name>.state.json  the tool's working state (every candidate and decision), for resuming

Candidate quotes come from a deliberately crude, high-recall finder in this file. It is
independent of app/dialogue/segment.py on purpose: the labels must not come from the code
that is evaluated against them.
"""

import argparse
import json
import logging
import re
import sys
import threading
import webbrowser
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPTS_DIR.parent.parent
LABELS_DIR = REPO_DIR / "testset_dialogue" / "labels"
PAGE = SCRIPTS_DIR / "label_dialogue.html"

UNKNOWN = "UNKNOWN"  # gold speaker when the text does not say who speaks
GENDERS = ("female", "male", "unknown")

logger = logging.getLogger("label_dialogue")

# --- candidate finder ---------------------------------------------------------------------------

STYLES = {
    # style: (opening marks, closing marks)
    "curly_double": ("“", "”"),
    "straight_double": ('"', '"'),
    "curly_single": ("‘", "’"),
    "straight_single": ("'", "'"),
}
_OPEN_BEFORE = set(" \t\n([{—–-\"“")


def _is_apostrophe(text: str, i: int) -> bool:
    """Crude filter: a single mark between two letters (don't, Alice's, doesn‘t)."""
    return 0 < i < len(text) - 1 and text[i - 1].isalpha() and text[i + 1].isalpha()


def _opener_position(text: str, i: int) -> bool:
    before_ok = i == 0 or text[i - 1] in _OPEN_BEFORE
    after_ok = i + 1 < len(text) and not text[i + 1].isspace()
    return before_ok and after_ok


def dominant_style(paragraphs: list[str]) -> str:
    """The quote style with the most opening marks (straight marks counted at opener positions)."""
    counts = {name: 0 for name in STYLES}
    for text in paragraphs:
        for i, ch in enumerate(text):
            if ch == "“":
                counts["curly_double"] += 1
            elif ch == "‘" and not _is_apostrophe(text, i):
                counts["curly_single"] += 1
            elif ch == '"' and _opener_position(text, i):
                counts["straight_double"] += 1
            elif ch == "'" and _opener_position(text, i) and not _is_apostrophe(text, i):
                counts["straight_single"] += 1
    return max(counts, key=lambda name: counts[name])


def find_candidates(text: str, style: str) -> list[tuple[int, int]]:
    """Spans (start, end) from an opening to a closing mark, marks included. An unclosed quote
    runs to the end of the paragraph. Single-mark styles skip marks between two letters.

    Opening vs closing is decided by position, not by glyph: PDFs sometimes swap the curly
    marks (`table,‘ said Alice; ’it‘s`)."""
    marks = set(STYLES[style][0] + STYLES[style][1])
    single = style.endswith("single")
    spans: list[tuple[int, int]] = []
    open_at: int | None = None
    for i, ch in enumerate(text):
        if ch not in marks or (single and _is_apostrophe(text, i)):
            continue
        if open_at is None:
            if _opener_position(text, i):
                open_at = i
        elif i > open_at + 1 and not text[i - 1].isspace():
            spans.append((open_at, i + 1))
            open_at = None
        elif not single and _opener_position(text, i):  # a new double opener: close the unclosed quote
            end = len(text[:i].rstrip())
            if end > open_at + 1:
                spans.append((open_at, end))
            open_at = i
        # a single opener inside an open single quote is a nested quote: ignore it
    if open_at is not None:
        end = len(text.rstrip())
        if end > open_at + 1:
            spans.append((open_at, end))
    return spans


_VERBS = (
    r"said|says|asked|cried|replied|shouted|whispered|answered|exclaimed|added|went on|continued|"
    r"remarked|muttered|began|called|thought|repeated|interrupted|observed|explained|growled|"
    r"murmured|returned|retorted|roared|screamed|sighed|said again|put in"
)
_NAME = r"(?:the\s+)?[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*){0,3}"
_TAG_AFTER = re.compile(rf"^[\s,.!?;:—–-]*(?:(?:{_VERBS})\s+{_NAME}|{_NAME}\s+(?:{_VERBS})\b)")
_TAG_BEFORE = re.compile(rf"{_NAME}\s+(?:{_VERBS})(?:\s+\w+){{0,3}}[,:]?\s*$")


def explicit_hint(text: str, start: int, end: int) -> bool:
    """A `said Alice` / `the Hatter said` / `Alice said,` tag right next to the span."""
    return bool(_TAG_AFTER.match(text[end : end + 60]) or _TAG_BEFORE.search(text[max(0, start - 60) : start]))


def initial_items(paragraphs: list[str]) -> tuple[str, list[dict[str, Any]]]:
    style = dominant_style(paragraphs)
    items = []
    for p, text in enumerate(paragraphs):
        for s, e in find_candidates(text, style):
            hint = explicit_hint(text, s, e)
            items.append(
                {
                    "key": f"c{p}:{s}:{e}", "p": p, "s": s, "e": e, "origin": "candidate",
                    "decision": None, "speaker": None, "explicit": hint, "hint": hint, "same": False,
                }
            )  # fmt: skip
    return style, items


# --- state, export, validation ------------------------------------------------------------------


def sorted_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda it: (it["p"], it["s"], it["e"]))


def cast_lookup(cast: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Casefolded name or alias -> canonical name."""
    table: dict[str, str] = {}
    for name, info in cast.items():
        table[name.casefold()] = name
        for alias in info.get("aliases", []):
            table.setdefault(alias.casefold(), name)
    return table


def gold_lines(paragraphs: list[str], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The §15.9 label lines: decided quotes in text order; `same` joins the previous utterance."""
    lines: list[dict[str, Any]] = []
    utterance = 0
    for it in sorted_items(items):
        if it["decision"] != "quote":
            continue
        joined = it["same"] and lines
        if not joined:
            utterance += 1
        speaker = lines[-1]["speaker"] if joined else it["speaker"]
        lines.append(
            {
                "id": f"Q{len(lines) + 1}", "paragraph": it["p"], "start": it["s"], "end": it["e"],
                "text": paragraphs[it["p"]][it["s"] : it["e"]], "speaker": speaker,
                "explicit": bool(it["explicit"]), "utterance": f"U{utterance}",
            }
        )  # fmt: skip
    return lines


def validate(paragraphs: list[str], items: list[dict[str, Any]], cast: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for name, info in cast.items():
        if not name.strip():
            errors.append("a character has an empty name")
        if info.get("gender") not in GENDERS:
            errors.append(f"{name}: gender must be one of {', '.join(GENDERS)}")
    seen_quote = False
    for n, it in enumerate(sorted_items(items), start=1):
        where = f"#{n} (paragraph {it['p']})"
        if it["decision"] is None:
            errors.append(f"{where}: not decided")
            continue
        if not (0 <= it["s"] < it["e"] <= len(paragraphs[it["p"]])):
            errors.append(f"{where}: span out of range")
        if it["decision"] != "quote":
            continue
        if it["same"]:
            if not seen_quote:
                errors.append(f"{where}: 'same utterance' but there is no earlier quote")
        elif it["speaker"] != UNKNOWN and it["speaker"] not in cast:
            errors.append(f"{where}: speaker {it['speaker']!r} is not in the cast")
        seen_quote = True
    return errors


# --- lint (SPEC §15.9): checks on the label files only, independent of app.dialogue ---------------

FAMILY_OF_STYLE = {"curly_double": "double", "straight_double": "double",
                   "curly_single": "single", "straight_single": "single"}  # fmt: skip
FAMILY_MARK_SET = {"double": "“”\"", "single": "‘’'"}
LINT_EXCERPT_CHARS = 60
THOUGHT_LOOKAHEAD_CHARS = 25
_LINT_VERB = rf"(?:{_VERBS})\b"
_LINT_SUBJECT = r"(?:the\s+[a-z][\w-]*(?:\s+[A-Za-z][\w-]*)?|the\s+[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*)*|(?!The\b)[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*)*|he|she|they|I|we|you)"
# Inside a span: punctuation, then a speech tag (`! said Alice`, `, the Hatter said`, `! she thought`).
_NARRATION_IN_SPAN = re.compile(
    rf"[!?,.;]\s+(?:and\s+)?(?:{_LINT_VERB}\s+{_LINT_SUBJECT}|{_LINT_SUBJECT}(?:\s+[a-z]+ly)?\s+{_LINT_VERB})"
)
_HAS_VERB = re.compile(rf"\b{_LINT_VERB}", re.IGNORECASE)


@dataclass
class Finding:
    level: str  # "error" | "warn"
    chapter: str
    paragraph: int
    start: int
    end: int
    excerpt: str
    problem: str

    def line(self) -> str:
        return (f"{self.level.upper():<5} {self.chapter} ¶{self.paragraph} [{self.start}:{self.end}] "
                f"`{self.excerpt}` — {self.problem}")  # fmt: skip


def _excerpt(text: str) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= LINT_EXCERPT_CHARS else text[: LINT_EXCERPT_CHARS - 1] + "…"


def lint_labels(
    chapter: str,
    paragraphs: list[str],
    lines: list[dict[str, Any]],
    cast: dict[str, dict[str, Any]],
    items: list[dict[str, Any]] | None,
) -> list[Finding]:
    """Label-only checks: gold `lines` (the .jsonl), the cast, and the tool's items (.state.json)."""
    family = FAMILY_OF_STYLE[dominant_style(paragraphs)]
    marks = FAMILY_MARK_SET[family]
    known = cast_lookup(cast)
    findings: list[Finding] = []

    def add(level: str, line: dict[str, Any], problem: str) -> None:
        p, s, e = line["paragraph"], line["start"], line["end"]
        findings.append(Finding(level, chapter, p, s, e, _excerpt(paragraphs[p][s:e]), problem))

    by_paragraph: dict[int, list[dict[str, Any]]] = {}
    for line in sorted(lines, key=lambda x: (x["paragraph"], x["start"], x["end"])):
        p, s, e = line["paragraph"], line["start"], line["end"]
        if not (0 <= p < len(paragraphs) and 0 <= s < e <= len(paragraphs[p])):
            findings.append(Finding("error", chapter, p, s, e, "", "span out of range"))
            continue
        by_paragraph.setdefault(p, []).append(line)
        text = paragraphs[p][s:e]
        if line["speaker"] != UNKNOWN and line["speaker"].casefold() not in known:
            add("error", line, f"speaker {line['speaker']!r} is not in the cast")
        if text[0] not in marks or text[-1] not in marks:
            where = " and ".join(w for w, ok in (("start", text[0] in marks), ("end", text[-1] in marks)) if not ok)
            add("warn", line, f"does not {where} with a {family} quote mark")
        if _NARRATION_IN_SPAN.search(text):
            add("warn", line, "may contain narration (punctuation + speech tag inside the span)")

    for p, row in by_paragraph.items():
        for a, b in zip(row, row[1:]):
            if b["start"] < a["end"]:
                add("error", b, f"overlaps the span [{a['start']}:{a['end']}] in the same paragraph")
                continue
            between = paragraphs[p][a["end"] : b["start"]].strip()
            if a["utterance"] != b["utterance"] and _HAS_VERB.search(between) and between[-1:] in ",—:":
                add("warn", b, f"possible broken quote: tag `{_excerpt(between)}` between this and the previous "
                               "span, but the utterances differ (Same utterance?)")  # fmt: skip

    for it in items or []:
        if it["decision"] == "not_quote":
            after = paragraphs[it["p"]][it["e"] : it["e"] + THOUGHT_LOOKAHEAD_CHARS]
            if "thought" in after.casefold():
                fake = {"paragraph": it["p"], "start": it["s"], "end": it["e"]}
                add("warn", fake, "marked Not a quote but followed by `thought` (D38: thoughts belong to the thinker)")

    findings.extend(_dropped_quotes(chapter, paragraphs, by_paragraph, items or [], marks, family))
    findings.sort(key=lambda f: (f.paragraph, f.start, f.level))
    return findings


_CANDIDATE_KEY = re.compile(r"^c(\d+):(\d+):(\d+)$")


def _dropped_quotes(
    chapter: str,
    paragraphs: list[str],
    gold_by_paragraph: dict[int, list[dict[str, Any]]],
    items: list[dict[str, Any]],
    marks: str,
    family: str,
) -> list[Finding]:
    """Narrowed candidates: quote marks of the family left in the original candidate span that no
    gold span or Not-a-quote decision covers (a quote lost when the span was narrowed)."""
    covered: dict[int, list[tuple[int, int]]] = {}
    for p, row in gold_by_paragraph.items():
        covered.setdefault(p, []).extend((x["start"], x["end"]) for x in row)
    for it in items:
        if it["decision"] == "not_quote":
            covered.setdefault(it["p"], []).append((it["s"], it["e"]))

    findings: list[Finding] = []
    for it in items:
        m = _CANDIDATE_KEY.match(str(it.get("key", "")))
        if it.get("origin") != "candidate" or not m:
            continue
        p, s0, e0 = (int(g) for g in m.groups())
        if (it["s"], it["e"]) == (s0, e0) or not 0 <= p < len(paragraphs):
            continue
        text = paragraphs[p]
        loose = [
            i for i in range(s0, min(e0, len(text)))
            if text[i] in marks
            and not (family == "single" and _is_apostrophe(text, i))
            and not any(s <= i < e for s, e in covered.get(p, []))
        ]  # fmt: skip
        if loose:
            s, e = loose[0], loose[-1] + 1
            findings.append(Finding("warn", chapter, p, s, e, _excerpt(text[s:e]),
                                    f"possible dropped quote: {len(loose)} quote mark(s) of the narrowed candidate "
                                    f"[{s0}:{e0}] not covered by a gold span or Not a quote"))  # fmt: skip
    return findings


def lint_files(text_path: Path, labels_dir: Path = LABELS_DIR) -> list[Finding]:
    name = text_path.stem
    paragraphs = json.loads(text_path.read_text(encoding="utf-8"))["paragraphs"]
    jsonl = labels_dir / f"{name}.jsonl"
    if not jsonl.exists():
        return [Finding("error", name, 0, 0, 0, "", f"{jsonl.name} not found")]
    lines = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").splitlines() if x.strip()]
    cast_path, state_path = labels_dir / f"{name}.cast.json", labels_dir / f"{name}.state.json"
    cast = json.loads(cast_path.read_text(encoding="utf-8")) if cast_path.exists() else {}
    items = json.loads(state_path.read_text(encoding="utf-8"))["items"] if state_path.exists() else None
    return lint_labels(name, paragraphs, lines, cast, items)


def run_lint(text_path: Path, labels_dir: Path = LABELS_DIR) -> int:
    findings = lint_files(text_path, labels_dir)
    for f in findings:
        print(f.line())
    errors = sum(f.level == "error" for f in findings)
    print(f"{text_path.stem}: {errors} error(s), {len(findings) - errors} warning(s)")
    return 1 if errors else 0


class Store:
    """Files for one chapter; writes are serialised and atomic."""

    def __init__(self, text_path: Path, labels_dir: Path = LABELS_DIR) -> None:
        self.text_path = text_path
        self.name = text_path.stem
        self.chapter = json.loads(text_path.read_text(encoding="utf-8"))
        self.paragraphs: list[str] = self.chapter["paragraphs"]
        self.labels_dir = labels_dir
        self.state_path = labels_dir / f"{self.name}.state.json"
        self.jsonl_path = labels_dir / f"{self.name}.jsonl"
        self.cast_path = labels_dir / f"{self.name}.cast.json"
        self.lock = threading.Lock()
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            style, items = initial_items(self.paragraphs)
            self.state = {"style": style, "items": items, "cast": {}, "cursor": 0, "done": False}

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name, "title": self.chapter.get("title", ""), "paragraphs": self.paragraphs,
            "unknown": UNKNOWN, **self.state,
        }  # fmt: skip

    def save(self, update: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            for field in ("items", "cast", "cursor"):
                if field in update:
                    self.state[field] = update[field]
            self.state["done"] = False
            self._write()
            return {"ok": True, "decided": sum(it["decision"] is not None for it in self.state["items"])}

    def finish(self) -> dict[str, Any]:
        """Validate and lint; Done is refused on errors (lint warnings are returned, not blocking)."""
        with self.lock:
            items, cast = self.state["items"], self.state["cast"]
            lines = gold_lines(self.paragraphs, items)
            errors = validate(self.paragraphs, items, cast)
            findings = [] if errors else lint_labels(self.name, self.paragraphs, lines, cast, items)
            errors += [f.line() for f in findings if f.level == "error"]
            self.state["done"] = not errors
            self._write()
            return {
                "ok": not errors, "errors": errors, "quotes": len(lines),
                "utterances": len({line["utterance"] for line in lines}),
                "findings": [asdict(f) for f in findings],
            }  # fmt: skip

    def _write(self) -> None:
        self.labels_dir.mkdir(parents=True, exist_ok=True)
        lines = gold_lines(self.paragraphs, self.state["items"])
        jsonl = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        _atomic_write(self.jsonl_path, jsonl)
        _atomic_write(self.cast_path, json.dumps(self.state["cast"], indent=2, ensure_ascii=False) + "\n")
        _atomic_write(self.state_path, json.dumps(self.state, indent=1, ensure_ascii=False) + "\n")


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# --- server -------------------------------------------------------------------------------------


def make_handler(store: Store) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # quieter than the default
            logger.debug(fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json")

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                self._send(HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(store.payload())
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json({"error": "bad json"}, HTTPStatus.BAD_REQUEST)
                return
            if self.path == "/api/save":
                self._json(store.save(body))
            elif self.path == "/api/done":
                self._json(store.finish())
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Label dialogue speakers for one chapter.")
    parser.add_argument("text_file", type=Path, help="testset_dialogue/text/<name>.json")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--lint", action="store_true", help="check the label files and exit (no server)")
    parser.add_argument("--labels-dir", type=Path, default=LABELS_DIR, help=argparse.SUPPRESS)  # for testing
    args = parser.parse_args()

    if args.lint:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.exit(run_lint(args.text_file.resolve(), args.labels_dir))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    store = Store(args.text_file.resolve(), args.labels_dir)
    items = store.state["items"]
    logger.info(
        "%s: %d paragraphs, quote style %s, %d items (%d decided). Labels: %s",
        store.name, len(store.paragraphs), store.state["style"], len(items),
        sum(it["decision"] is not None for it in items), store.labels_dir,
    )  # fmt: skip
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    url = f"http://localhost:{args.port}/"
    logger.info("Open %s  (Ctrl+C to stop; everything is already saved)", url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
