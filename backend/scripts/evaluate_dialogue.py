"""Evaluate dialogue segmentation against the hand labels (SPEC §15.9). Phase 6.1: detection only.

    python scripts\\evaluate_dialogue.py [--set dev|heldout|all] [--chapters alice_ch07,brown_glass] [--history]

For each chapter: loads testset_dialogue/text/<name>.json and labels/<name>.jsonl, runs the
segmenter, and matches predicted quote spans to gold spans one-to-one (same paragraph, character
overlap >= 80% of the longer span, greedy by overlap). Reports precision / recall / F1, utterance
grouping accuracy, and how many spans the labeller marked "Not a quote" were found anyway.

Writes testset_dialogue/results.md (dev) or results_heldout.md (held-out / all). --history also
appends a dated summary to testset_dialogue/results_history.md (never overwritten).
The held-out chapter (D25, D35) is only evaluated with --i-know-this-is-the-held-out-run.
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluate import git_commit  # noqa: E402

from app.dialogue.segment import SegmentStats, find_quote_spans, quote_family_of_texts  # noqa: E402
from app.pipeline.titles import prf  # noqa: E402

DIALOGUE_DIR = REPO_DIR / "testset_dialogue"
SETS = {"dev": ["alice_ch07", "brown_glass"], "heldout": ["ball_ch03"]}
HELDOUT = set(SETS["heldout"])
MATCH_MIN_OVERLAP = 0.8  # of the longer span
EXCERPT_CHARS = 60

Span = tuple[int, int, int]  # paragraph, start, end


@dataclass
class ChapterResult:
    name: str
    family: str | None
    gold: int
    predicted: int
    tp: int
    false_positives: list[Span] = field(default_factory=list)
    false_negatives: list[Span] = field(default_factory=list)
    gold_groups: int = 0  # gold utterances with >= 2 spans
    gold_groups_ok: int = 0
    pred_groups: int = 0  # predicted utterances with >= 2 spans
    pred_groups_ok: int = 0
    not_quote_found: int | None = None  # None: no labelling state file to read
    not_quote_total: int | None = None
    stats: SegmentStats = field(default_factory=SegmentStats)
    paragraphs: list[str] = field(default_factory=list)

    @property
    def prf(self) -> tuple[float, float, float]:
        return prf(self.tp, self.predicted, self.gold)

    @property
    def grouping(self) -> float:
        total = self.gold_groups + self.pred_groups
        return (self.gold_groups_ok + self.pred_groups_ok) / total if total else 1.0


def overlap_ok(a: tuple[int, int], b: tuple[int, int]) -> float:
    """Overlap length when it is at least MATCH_MIN_OVERLAP of the longer span, else 0."""
    overlap = min(a[1], b[1]) - max(a[0], b[0])
    longer = max(a[1] - a[0], b[1] - b[0])
    return overlap if overlap > 0 and overlap >= MATCH_MIN_OVERLAP * longer else 0


def match_spans(predicted: list[Span], gold: list[Span]) -> dict[int, int]:
    """One-to-one matching, greedy by overlap: predicted index -> gold index."""
    by_paragraph: dict[int, list[int]] = defaultdict(list)
    for g, (p, _, _) in enumerate(gold):
        by_paragraph[p].append(g)
    pairs = []
    for i, (p, s, e) in enumerate(predicted):
        for g in by_paragraph[p]:
            ov = overlap_ok((s, e), gold[g][1:])
            if ov:
                pairs.append((ov, i, g))
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    matched: dict[int, int] = {}
    used: set[int] = set()
    for _, i, g in pairs:
        if i not in matched and g not in used:
            matched[i] = g
            used.add(g)
    return matched


def grouping(
    utterances_a: list[str], utterances_b: list[str], a_to_b: dict[int, int]
) -> tuple[int, int]:
    """Of the utterances of side A with >= 2 spans: how many have every span matched, all to
    spans of one utterance of side B. Returns (groups, groups_ok)."""
    members: dict[str, list[int]] = defaultdict(list)
    for i, u in enumerate(utterances_a):
        members[u].append(i)
    groups = [idx for idx in members.values() if len(idx) >= 2]
    ok = sum(
        all(i in a_to_b for i in idx) and len({utterances_b[a_to_b[i]] for i in idx}) == 1
        for idx in groups
    )
    return len(groups), ok


def load_not_quote(name: str) -> list[Span] | None:
    state = DIALOGUE_DIR / "labels" / f"{name}.state.json"
    if not state.exists():
        return None
    items = json.loads(state.read_text(encoding="utf-8"))["items"]
    return [(it["p"], it["s"], it["e"]) for it in items if it["decision"] == "not_quote"]


def evaluate_chapter(name: str) -> ChapterResult:
    text = json.loads((DIALOGUE_DIR / "text" / f"{name}.json").read_text(encoding="utf-8"))
    paragraphs: list[str] = text["paragraphs"]
    labels_path = DIALOGUE_DIR / "labels" / f"{name}.jsonl"
    lines = [json.loads(x) for x in labels_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    gold = [(x["paragraph"], x["start"], x["end"]) for x in lines]
    gold_utt = [x["utterance"] for x in lines]

    family = quote_family_of_texts(paragraphs)
    spans, stats = find_quote_spans(paragraphs, family)
    predicted: list[Span] = []
    pred_utt: list[str] = []
    for p, row in enumerate(spans):
        for q in row:
            predicted.append((p, q.start, q.end))
            pred_utt.append(q.utterance_id)

    matched = match_spans(predicted, gold)
    reverse = {g: i for i, g in matched.items()}
    result = ChapterResult(
        name=name, family=family, gold=len(gold), predicted=len(predicted), tp=len(matched),
        false_positives=[predicted[i] for i in range(len(predicted)) if i not in matched],
        false_negatives=[gold[g] for g in range(len(gold)) if g not in reverse],
        stats=stats, paragraphs=paragraphs,
    )  # fmt: skip
    result.gold_groups, result.gold_groups_ok = grouping(gold_utt, pred_utt, reverse)
    result.pred_groups, result.pred_groups_ok = grouping(pred_utt, gold_utt, matched)

    not_quote = load_not_quote(name)
    if not_quote is not None:
        result.not_quote_total = len(not_quote)
        result.not_quote_found = len(match_spans(predicted, not_quote))
    return result


# --- output --------------------------------------------------------------------------------------


def _f(x: float) -> str:
    return f"{x:.2f}"


def excerpt(paragraphs: list[str], span: Span) -> str:
    p, s, e = span
    text = paragraphs[p][s:e].replace("\n", " ")
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1] + "…"


def micro(results: list[ChapterResult]) -> tuple[int, int, int, float, float, float, float]:
    tp = sum(r.tp for r in results)
    predicted = sum(r.predicted for r in results)
    gold = sum(r.gold for r in results)
    groups = sum(r.gold_groups + r.pred_groups for r in results)
    groups_ok = sum(r.gold_groups_ok + r.pred_groups_ok for r in results)
    return tp, predicted, gold, *prf(tp, predicted, gold), (groups_ok / groups if groups else 1.0)


def stats_cell(s: SegmentStats) -> str:
    return (
        f"marks {s.marks_seen}, apostrophes {s.apostrophes_skipped}, stray closers {s.stray_closers}, "
        f"reopened {s.reopened}, unclosed closed {s.unclosed_closed}, continuations {s.continuations}, "
        f"speech-tag closures {s.speech_tag_closures}, in-paragraph tag closures {s.damaged_tag_closures}"
    )


def render_markdown(results: list[ChapterResult], commit: str, list_errors: bool) -> str:
    tp, predicted, gold, precision, recall, f1, group = micro(results)
    out = [
        "# Dialogue detection results",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/evaluate_dialogue.py`, commit `{commit}`.",
        f"Match: same paragraph, overlap ≥ {MATCH_MIN_OVERLAP:.0%} of the longer span, one-to-one.",
        "",
        "| Chapter | Family | Gold | Predicted | TP | P | R | F1 | Grouping (gold→pred, pred→gold) | Not-a-quote found |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        p, rc, f = r.prf
        nq = "n/a" if r.not_quote_total is None else f"{r.not_quote_found}/{r.not_quote_total}"
        out.append(
            f"| {r.name} | {r.family or 'none'} | {r.gold} | {r.predicted} | {r.tp} | {_f(p)} | {_f(rc)} | "
            f"{_f(f)} | {_f(r.grouping)} ({r.gold_groups_ok}/{r.gold_groups}, {r.pred_groups_ok}/{r.pred_groups}) | {nq} |"
        )
    out.append(
        f"| **micro** | | {gold} | {predicted} | {tp} | {_f(precision)} | {_f(recall)} | {_f(f1)} | {_f(group)} | |"
    )
    out += ["", "Grouping: of the utterances with ≥ 2 spans (gold, then predicted), the share whose spans are",
            "all matched to spans of one utterance on the other side.", "", "## Segmenter statistics", ""]  # fmt: skip
    out += [f"- **{r.name}**: {stats_cell(r.stats)}" for r in results]
    if list_errors:
        for r in results:
            out += ["", f"## {r.name}: errors", ""]
            if not r.false_positives and not r.false_negatives:
                out.append("None.")
            for label, spans in (("FP", r.false_positives), ("FN", r.false_negatives)):
                for span in spans:
                    out.append(f"- {label} ¶{span[0]} [{span[1]}:{span[2]}] `{excerpt(r.paragraphs, span)}`")
    return "\n".join(out) + "\n"


def render_history_entry(results: list[ChapterResult], commit: str, set_name: str) -> str:
    tp, predicted, gold, precision, recall, f1, group = micro(results)
    per = ", ".join(f"{r.name} F1 {_f(r.prf[2])} grouping {_f(r.grouping)}" for r in results)
    return "\n".join(
        [
            f"## {datetime.now():%Y-%m-%d %H:%M} - commit `{commit}` - set `{set_name}` (detection)",
            "",
            f"- Precision / recall / F1 (micro): {_f(precision)} / {_f(recall)} / {_f(f1)} "
            f"(TP {tp}, predicted {predicted}, gold {gold})",
            f"- Grouping accuracy (micro): {_f(group)}",
            f"- Per chapter: {per}",
        ]
    ) + "\n"


HISTORY_HEADER = (
    "# Dialogue evaluation history\n\n"
    "Appended by `evaluate_dialogue.py --history`; never overwritten. The held-out chapter's first\n"
    "entry is its reported score (D25, D35).\n"
)


def append_history(path: Path, entry: str) -> None:
    """Append a section to the history file (created with a header if it does not exist)."""
    with path.open("a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write(HISTORY_HEADER)
        f.write("\n" + entry)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate dialogue detection (SPEC §15.9).")
    parser.add_argument("--set", choices=["dev", "heldout", "all"], default="dev")
    parser.add_argument("--chapters", help="comma-separated names (overrides --set)")
    parser.add_argument("--history", action="store_true", help="append to testset_dialogue/results_history.md")
    parser.add_argument("--i-know-this-is-the-held-out-run", dest="heldout_ok", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.chapters:
        names = [n.strip() for n in args.chapters.split(",") if n.strip()]
    else:
        names = SETS["dev"] + SETS["heldout"] if args.set == "all" else SETS[args.set]
    touches_heldout = bool(HELDOUT & set(names))
    if touches_heldout and not args.heldout_ok:
        print("Refusing: this includes the held-out chapter (D25, D35). Its first run happens once, in")
        print("Phase 6.5. Pass --i-know-this-is-the-held-out-run to run it.")
        sys.exit(2)

    results = [evaluate_chapter(n) for n in names]
    commit = git_commit()
    for r in results:
        p, rc, f = r.prf
        print(f"{r.name:<14} family {r.family or 'none':<6} gold {r.gold:>4} pred {r.predicted:>4} "
              f"TP {r.tp:>4}  P {_f(p)} R {_f(rc)} F1 {_f(f)}  grouping {_f(r.grouping)}")  # fmt: skip
    tp, predicted, gold, precision, recall, f1, group = micro(results)
    print(f"{'micro':<14} {'':<13} gold {gold:>4} pred {predicted:>4} TP {tp:>4}  "
          f"P {_f(precision)} R {_f(recall)} F1 {_f(f1)}  grouping {_f(group)}")  # fmt: skip

    out = DIALOGUE_DIR / ("results_heldout.md" if touches_heldout else "results.md")
    out.write_text(render_markdown(results, commit, list_errors=not touches_heldout), encoding="utf-8")
    print(f"\nwrote {out}")
    if args.history:
        history = DIALOGUE_DIR / "results_history.md"
        set_name = args.chapters or args.set
        append_history(history, render_history_entry(results, commit, set_name))
        print(f"appended {history}")


if __name__ == "__main__":
    main()
