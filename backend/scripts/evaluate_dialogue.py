"""Evaluate dialogue segmentation and speaker attribution against the hand labels (SPEC §15.9).

    python scripts\\evaluate_dialogue.py [--set dev|heldout|all] [--chapters alice_ch07,brown_glass]
        [--methods baseline,llm,llm_with_baseline_override | all] [--models qwen/qwen3.8-27b,…] [--history]

For each chapter: loads testset_dialogue/text/<name>.json and labels/<name>.jsonl, runs the
segmenter, and matches predicted quote spans to gold spans one-to-one (same paragraph, character
overlap >= 80% of the longer span, greedy by overlap). Reports precision / recall / F1, utterance
grouping accuracy, and how many spans the labeller marked "Not a quote" were found anyway.

Attribution (each method; the LLM methods once per model): scored on gold utterances that detection
matched (an utterance's first matched span decides its predicted utterance); gold utterances
detection missed are counted. Attribution accuracy (overall, explicit, implicit), % predicted
UNKNOWN, and voice accuracy with the system's own voice map, each chapter ranked as a whole book.
LLM responses are cached on disk (LLM_CACHE_DIR), so re-scoring never calls Groq again; the token
and latency figures are those of the original requests. The D39 rule picks the winner.

Writes testset_dialogue/results.md (dev) or results_heldout.md (held-out / all). --history also
appends a dated summary to testset_dialogue/results_history.md (never overwritten).
The held-out chapter (D25, D35) is only evaluated with --i-know-this-is-the-held-out-run.
"""

import argparse
import asyncio
import json
import statistics
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

from app.config import get_settings  # noqa: E402
from app.dialogue.attribute import (  # noqa: E402
    METHODS,
    UNKNOWN,
    Attribution,
    Method,
    Roster,
    baseline_attribute,
    combine,
    llm_attribute,
    rank_texts,
)
from app.dialogue.cast import Cast, normalize_name  # noqa: E402
from app.dialogue.llm import GroqClient, TokenLimiter, UsageRecord  # noqa: E402
from app.dialogue.segment import QuoteSpan, SegmentStats, find_quote_spans, quote_family_of_texts  # noqa: E402
from app.dialogue.voices import VoiceCasting, cast_with_voices, new_casting  # noqa: E402
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
    spans: list[list[QuoteSpan]] = field(default_factory=list)  # the segmenter's, per paragraph
    lines: list[dict] = field(default_factory=list)  # gold label lines
    pred_utt: list[str] = field(default_factory=list)  # utterance id per predicted span
    matched: dict[int, int] = field(default_factory=dict)  # predicted span index -> gold index

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
        stats=stats, paragraphs=paragraphs, spans=spans, lines=lines, pred_utt=pred_utt, matched=matched,
    )  # fmt: skip
    result.gold_groups, result.gold_groups_ok = grouping(gold_utt, pred_utt, reverse)
    result.pred_groups, result.pred_groups_ok = grouping(pred_utt, gold_utt, matched)

    not_quote = load_not_quote(name)
    if not_quote is not None:
        result.not_quote_total = len(not_quote)
        result.not_quote_found = len(match_spans(predicted, not_quote))
    return result


# --- attribution (SPEC §15.9, D39) ---------------------------------------------------------------

TIE_POINTS = 0.02  # D39: candidates within 2 percentage points of the best voice accuracy tie


@dataclass(frozen=True)
class Candidate:
    method: Method
    model: str | None  # None for the baseline

    @property
    def name(self) -> str:
        return "baseline" if self.model is None else f"{self.method} · {self.model}"


@dataclass
class AttributionScore:
    utterances: int = 0  # gold utterances matched by detection (the scored ones)
    missed: int = 0  # gold utterances detection missed entirely
    correct: int = 0
    explicit: int = 0
    explicit_correct: int = 0
    implicit: int = 0
    implicit_correct: int = 0
    unknown_predicted: int = 0
    voice_correct: int = 0
    errors: list[str] = field(default_factory=list)

    def add(self, other: "AttributionScore") -> None:
        for name in self.__dataclass_fields__:
            if name != "errors":
                setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class CandidateResult:
    candidate: Candidate
    per_chapter: dict[str, AttributionScore] = field(default_factory=dict)
    usage: list[UsageRecord] = field(default_factory=list)
    windows_total: int = 0
    windows_baseline: int = 0  # windows that fell back to the baseline (D34)

    @property
    def total(self) -> AttributionScore:
        t = AttributionScore()
        for s in self.per_chapter.values():
            t.add(s)
        return t

    @property
    def voice_accuracy(self) -> float:
        t = self.total
        return t.voice_correct / t.utterances if t.utterances else 0.0

    @property
    def tokens(self) -> int:
        return sum(u.prompt_tokens + u.completion_tokens for u in self.usage)

    @property
    def median_latency(self) -> float:
        return statistics.median(u.latency_seconds for u in self.usage) if self.usage else 0.0


def _ratio(a: int, b: int) -> str:
    return f"{a / b:.2f}" if b else "n/a"


def load_gold_cast(name: str) -> dict[str, dict]:
    path = DIALOGUE_DIR / "labels" / f"{name}.cast.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def score_attribution(
    r: ChapterResult, gold_cast: dict[str, dict], attributions: dict[str, Attribution], cast: Cast,
    casting: VoiceCasting,
) -> AttributionScore:  # fmt: skip
    """Score one chapter's attributions on the gold utterances detection matched."""
    gold_keys = {c: {normalize_name(n) for n in [c, *info.get("aliases", [])]} for c, info in gold_cast.items()}
    reverse = {g: i for i, g in r.matched.items()}
    utterances: dict[str, list[int]] = defaultdict(list)
    for g, line in enumerate(r.lines):
        utterances[line["utterance"]].append(g)
    score = AttributionScore()
    for indices in utterances.values():
        first = r.lines[indices[0]]
        hit = next((g for g in indices if g in reverse), None)
        if hit is None:
            score.missed += 1
            continue
        a = attributions[r.pred_utt[reverse[hit]]]
        gold = first["speaker"]
        member = cast.resolve(a.speaker) if a.speaker != UNKNOWN else None
        predicted_keys = member.keys() if member else ({normalize_name(a.speaker)} if a.speaker != UNKNOWN else set())
        keys = gold_keys.get(gold, {normalize_name(gold)})
        correct = a.speaker == UNKNOWN if gold == UNKNOWN else bool(predicted_keys & keys)
        if gold == UNKNOWN:
            expected = casting.narrator
        else:
            gold_member = next((m for m in cast.members if m.keys() & keys), None)
            gender = gold_cast.get(gold, {}).get("gender", "unknown")
            expected = casting.voice_for(gold_member) if gold_member else casting.minor_voice(gender)
        heard = casting.voice_for(member)
        explicit = any(r.lines[g]["explicit"] for g in indices)
        score.utterances += 1
        score.correct += correct
        score.explicit += explicit
        score.explicit_correct += explicit and correct
        score.implicit += not explicit
        score.implicit_correct += (not explicit) and correct
        score.unknown_predicted += a.speaker == UNKNOWN
        score.voice_correct += heard == expected
        if not correct or heard != expected:
            span = (first["paragraph"], first["start"], first["end"])
            shown = member.name if member else a.speaker
            score.errors.append(
                f"¶{span[0]} `{excerpt(r.paragraphs, span)}` gold **{gold}** → predicted **{shown}** "
                f"({a.method}/{a.reason}); voice expected {expected.label}, heard {heard.label}"
            )
    return score


async def evaluate_attribution(
    results: list[ChapterResult], methods: list[Method], models: list[str]
) -> list[CandidateResult]:
    """Every candidate on every chapter; each LLM model runs once per chapter (both LLM methods
    share its answers)."""
    settings = get_settings()
    candidates = [Candidate("baseline", None)] if "baseline" in methods else []
    candidates += [Candidate(m, model) for model in models for m in methods if m != "baseline"]
    out = {c: CandidateResult(c) for c in candidates}
    llm_methods = [m for m in methods if m != "baseline"]
    for r in results:
        gold_cast = load_gold_cast(r.name)
        baseline = baseline_attribute(r.paragraphs, r.spans)
        ranking, genders = rank_texts([r.paragraphs], r.family)
        if "baseline" in methods:
            casting = new_casting(settings.edge_voice, settings.max_cast)
            cast = cast_with_voices(casting, ranking, genders, [])
            c = Candidate("baseline", None)
            out[c].per_chapter[r.name] = score_attribution(r, gold_cast, baseline.attributions, cast, casting)
        if not llm_methods:
            continue
        if not settings.groq_api_key:
            raise SystemExit("GROQ_API_KEY is not set: LLM methods cannot run")
        for model in models:
            limiter = TokenLimiter(settings.llm_tokens_per_minute, settings.llm_max_requests_per_day)
            client = GroqClient(settings.groq_api_key, model, limiter, cache_dir=settings.llm_cache_path)
            roster = Roster()
            llm = await llm_attribute(r.paragraphs, r.spans, client, roster, baseline, settings.llm_window_words)
            for method in llm_methods:
                combined = combine(method, baseline, llm)
                casting = new_casting(settings.edge_voice, settings.max_cast)
                cast = cast_with_voices(casting, ranking, genders, roster.reports(), roster.distinct)
                result = out[Candidate(method, model)]
                result.per_chapter[r.name] = score_attribution(r, gold_cast, combined.attributions, cast, casting)
                result.usage += client.usage
                result.windows_total += llm.windows_total
                result.windows_baseline += llm.windows_total - llm.windows_llm
    return list(out.values())


def select_d39(results: list[CandidateResult]) -> tuple[CandidateResult, list[CandidateResult]]:
    """D39: highest micro voice accuracy; candidates within 2 points tie; ties broken by fewer total
    tokens, then lower median latency, then `llm_with_baseline_override` over `llm`."""
    best = max(c.voice_accuracy for c in results)
    ties = [c for c in results if best - c.voice_accuracy <= TIE_POINTS + 1e-9]
    ties.sort(key=lambda c: (c.tokens, c.median_latency, c.candidate.method != "llm_with_baseline_override"))
    return ties[0], ties


def render_attribution(results: list[CandidateResult], chapters: list[str]) -> list[str]:
    chosen, ties = select_d39(results)
    out = [
        "",
        "## Attribution candidates (D39)",
        "",
        "Scored on gold utterances matched by detection. Voice accuracy uses the system's own voice map "
        "(each chapter ranked as a whole book). Tokens and latency are those of the original Groq requests.",
        "",
        "| Candidate | Voice acc (micro) | " + " | ".join(f"Voice {n}" for n in chapters)
        + " | Attribution acc | Explicit | Implicit | % UNKNOWN | Requests | Prompt tok | Completion tok | "
        "Median latency (s) | Total latency (s) | Baseline windows |",
        "|---" * (12 + len(chapters)) + "|",
    ]
    for c in results:
        t = c.total
        per = " | ".join(_ratio(c.per_chapter[n].voice_correct, c.per_chapter[n].utterances) for n in chapters)
        prompt = sum(u.prompt_tokens for u in c.usage)
        completion = sum(u.completion_tokens for u in c.usage)
        latency = sum(u.latency_seconds for u in c.usage)
        fallback = f"{c.windows_baseline}/{c.windows_total}" if c.candidate.model else "-"
        out.append(
            f"| {c.candidate.name} | {_ratio(t.voice_correct, t.utterances)} | {per} | {_ratio(t.correct, t.utterances)} | "
            f"{_ratio(t.explicit_correct, t.explicit)} | {_ratio(t.implicit_correct, t.implicit)} | "
            f"{100 * t.unknown_predicted / t.utterances if t.utterances else 0:.0f}% | {len(c.usage)} | {prompt} | "
            f"{completion} | {c.median_latency:.2f} | {latency:.1f} | {fallback} |"
        )
    missed = ", ".join(f"{n} {results[0].per_chapter[n].missed}" for n in chapters)
    out += [
        "",
        f"Gold utterances missed by detection (not scored): {missed}.",
        "",
        f"**D39 selection: {chosen.candidate.name}** (voice accuracy {chosen.voice_accuracy:.3f}; tied within "
        f"2 points: {', '.join(c.candidate.name for c in ties)}).",
    ]
    for n in chapters:
        out += ["", f"## {n}: attribution errors ({chosen.candidate.name})", ""]
        out += [f"- {e}" for e in chosen.per_chapter[n].errors] or ["None."]
    return out


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


def render_markdown(
    results: list[ChapterResult], commit: str, list_errors: bool, attribution: list[CandidateResult] | None = None
) -> str:
    tp, predicted, gold, precision, recall, f1, group = micro(results)
    out = [
        "# Dialogue detection and attribution results",
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
    if attribution:
        rendered = render_attribution(attribution, [r.name for r in results])
        if not list_errors:  # held-out: no error listing (D25)
            rendered = rendered[: next(i for i, x in enumerate(rendered) if x.startswith("**D39")) + 1]
        out += rendered
    if list_errors:
        for r in results:
            out += ["", f"## {r.name}: detection errors", ""]
            if not r.false_positives and not r.false_negatives:
                out.append("None.")
            for label, spans in (("FP", r.false_positives), ("FN", r.false_negatives)):
                for span in spans:
                    out.append(f"- {label} ¶{span[0]} [{span[1]}:{span[2]}] `{excerpt(r.paragraphs, span)}`")
    return "\n".join(out) + "\n"


def render_history_entry(
    results: list[ChapterResult], commit: str, set_name: str, attribution: list[CandidateResult] | None = None
) -> str:
    tp, predicted, gold, precision, recall, f1, group = micro(results)
    per = ", ".join(f"{r.name} F1 {_f(r.prf[2])} grouping {_f(r.grouping)}" for r in results)
    kind = "detection + attribution" if attribution else "detection"
    lines = [
        f"## {datetime.now():%Y-%m-%d %H:%M} - commit `{commit}` - set `{set_name}` ({kind})",
        "",
        f"- Precision / recall / F1 (micro): {_f(precision)} / {_f(recall)} / {_f(f1)} "
        f"(TP {tp}, predicted {predicted}, gold {gold})",
        f"- Grouping accuracy (micro): {_f(group)}",
        f"- Per chapter: {per}",
    ]
    for c in attribution or []:
        t = c.total
        lines.append(
            f"- {c.candidate.name}: voice {_ratio(t.voice_correct, t.utterances)}, attribution "
            f"{_ratio(t.correct, t.utterances)} (explicit {_ratio(t.explicit_correct, t.explicit)}, implicit "
            f"{_ratio(t.implicit_correct, t.implicit)}), tokens {c.tokens}"
        )
    if attribution:
        lines.append(f"- D39 selection: {select_d39(attribution)[0].candidate.name}")
    return "\n".join(lines) + "\n"


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
    parser.add_argument("--methods", help=f"comma-separated attribution methods ({', '.join(METHODS)}) or `all`")
    parser.add_argument("--models", help="comma-separated Groq model ids for the LLM methods (default: LLM_MODEL)")
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

    attribution: list[CandidateResult] | None = None
    if args.methods:
        methods = list(METHODS) if args.methods == "all" else [m.strip() for m in args.methods.split(",") if m.strip()]
        unknown = [m for m in methods if m not in METHODS]
        if unknown:
            parser.error(f"unknown methods: {', '.join(unknown)}")
        models = [m.strip() for m in (args.models or get_settings().llm_model).split(",") if m.strip()]
        attribution = asyncio.run(evaluate_attribution(results, methods, models))  # type: ignore[arg-type]
        print()
        for c in attribution:
            t = c.total
            print(f"{c.candidate.name:<55} voice {_ratio(t.voice_correct, t.utterances)}  attribution "
                  f"{_ratio(t.correct, t.utterances)}  tokens {c.tokens:>6}  median latency {c.median_latency:.2f} s")  # fmt: skip
        print(f"D39 selection: {select_d39(attribution)[0].candidate.name}")

    out = DIALOGUE_DIR / ("results_heldout.md" if touches_heldout else "results.md")
    out.write_text(render_markdown(results, commit, not touches_heldout, attribution), encoding="utf-8")
    print(f"\nwrote {out}")
    if args.history:
        history = DIALOGUE_DIR / "results_history.md"
        set_name = args.chapters or args.set
        append_history(history, render_history_entry(results, commit, set_name, attribution))
        print(f"appended {history}")


if __name__ == "__main__":
    main()
