"""Evaluate rejection and chapter detection on the labeled test set (SPEC §13).

    python scripts\\evaluate.py [--testset ..\\testset] [--history]

Writes <testset>/results.md and a text dump of every accepted document to <testset>/output/.
--history also appends a dated summary to <testset>/results_history.md (never overwritten).
"""

import argparse
import json
import logging
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import write_dump  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import DocumentModel  # noqa: E402
from app.pipeline import PipelineError, run_pipeline  # noqa: E402
from app.pipeline.clean import TERMINAL_PUNCTUATION, is_page_number_text  # noqa: E402
from app.pipeline.titles import MatchResult, match_titles, prf  # noqa: E402

# Noise check (b): a short paragraph repeated at least this often, with no sentence punctuation at
# the end and not opening with a quote, is treated as a leaked running header.
NOISE_REPEAT_MIN = 3
NOISE_MAX_WORDS = 12
QUOTE_STARTS = tuple("\"'‘’“”")


@dataclass
class FileResult:
    name: str
    expected: str
    actual: str
    seconds: float
    document: DocumentModel | None = None
    match: MatchResult | None = None
    noise: int = 0
    labeled: int = 0  # labeled chapters, counted as missed when an expected accept is not accepted
    error: str = ""  # unexpected exception, if the pipeline crashed

    @property
    def warnings(self) -> list[str]:
        return self.document.warnings if self.document else []

    @property
    def is_reject(self) -> bool:
        return self.expected != "accept"

    @property
    def passed(self) -> bool:
        return self.expected == self.actual


def noise_count(document: DocumentModel) -> int:
    """Paragraphs that look like leaked page numbers or running headers."""
    texts = [" ".join(s.text for s in p.segments) for c in document.chapters for p in c.paragraphs]
    counts = Counter(texts)
    noise = 0
    for t in texts:
        if is_page_number_text(t):
            noise += 1
        elif (
            counts[t] >= NOISE_REPEAT_MIN
            and len(t.split()) <= NOISE_MAX_WORDS
            and not t.endswith(TERMINAL_PUNCTUATION + (",",))
            and not t.startswith(QUOTE_STARTS)
        ):
            noise += 1
    return noise


def evaluate_file(path: Path, label: dict, output_dir: Path) -> FileResult:
    settings = get_settings()
    labeled = len(label.get("chapters", []))
    start = time.perf_counter()
    try:
        document = run_pipeline(path, path.name, settings)
    except PipelineError as e:
        return FileResult(path.name, label["expect"], e.code, time.perf_counter() - start, labeled=labeled)
    except Exception as e:  # noqa: BLE001 - one crashing file must not stop the evaluation
        logging.exception("pipeline crashed on %s", path.name)
        return FileResult(
            path.name,
            label["expect"],
            "CRASH",
            time.perf_counter() - start,
            labeled=labeled,
            error=f"{type(e).__name__}: {e}",
        )
    seconds = time.perf_counter() - start
    result = FileResult(path.name, label["expect"], "accept", seconds, document, labeled=labeled)
    titles = [c.title for c in document.chapters]
    result.match = match_titles(titles, label.get("chapters", []), label.get("optional", []))
    result.noise = noise_count(document)
    write_dump(document, path.stem, output_dir)
    return result


def git_commit() -> str:
    """Short HEAD hash, marked when the working tree has uncommitted changes."""

    def git(*args: str) -> str:
        out = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_DIR, timeout=10)
        return out.stdout.strip()

    try:
        commit = git("rev-parse", "--short", "HEAD") or "unknown"
        dirty = git("status", "--porcelain", "--untracked-files=no", "--", "backend")
        return f"{commit} + uncommitted changes" if dirty else commit
    except Exception:  # noqa: BLE001 - the hash is informational only
        return "unknown"


def _fmt(x: float) -> str:
    return f"{x:.2f}"


@dataclass
class Summary:
    tp: int
    detected: int
    labeled: int
    precision: float
    recall: float
    f1: float
    reject_ok: int
    rejects: int
    noise: int


def summarize(results: list[FileResult]) -> Summary:
    """Micro-averaged scores. An expected accept that was rejected, crashed or is missing counts
    with 0 detected and all its labeled chapters missed, so failures lower recall."""
    rejects = [r for r in results if r.is_reject]
    accepts = [r for r in results if not r.is_reject]
    tp = sum(r.match.tp for r in accepts if r.match)
    detected = sum(r.match.detected for r in accepts if r.match)
    labeled = sum(r.match.labeled if r.match else r.labeled for r in accepts)
    precision, recall, f1 = prf(tp, detected, labeled)
    return Summary(
        tp,
        detected,
        labeled,
        precision,
        recall,
        f1,
        reject_ok=sum(1 for r in rejects if r.passed),
        rejects=len(rejects),
        noise=sum(r.noise for r in accepts),
    )


def _warnings_cell(r: FileResult) -> str:
    return "; ".join(r.warnings) if r.warnings else "-"


def render_markdown(results: list[FileResult], commit: str) -> str:
    rejects = [r for r in results if r.is_reject]
    accepts = [r for r in results if not r.is_reject]
    summary = summarize(results)
    tp, detected, labeled = summary.tp, summary.detected, summary.labeled
    precision, recall, f1 = summary.precision, summary.recall, summary.f1
    reject_ok = summary.reject_ok
    slowest = max(results, key=lambda r: r.seconds)

    lines = [
        "# Test set results",
        "",
        f"- Date: {date.today().isoformat()}",
        f"- Commit: `{commit}`",
        "- Command: `python scripts\\evaluate.py` (from `backend\\`)",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Reject accuracy | {reject_ok}/{len(rejects)} |",
        f"| Chapter precision (micro) | {_fmt(precision)} |",
        f"| Chapter recall (micro) | {_fmt(recall)} |",
        f"| Chapter F1 (micro) | {_fmt(f1)} |",
        f"| TP / detected / labeled | {tp} / {detected} / {labeled} |",
        f"| Total noise paragraphs | {sum(r.noise for r in accepts)} |",
        f"| Slowest file | {slowest.name} ({slowest.seconds:.1f} s) |",
        "",
        "## Rejections",
        "",
        "| File | Expected | Actual | Result | Warnings |",
        "|---|---|---|---|---|",
    ]
    for r in rejects:
        result = "pass" if r.passed else "FAIL"
        lines.append(f"| {r.name} | {r.expected} | {r.actual} | {result} | {_warnings_cell(r)} |")
    lines += [
        "",
        "## Chapter detection",
        "",
        "| File | Actual | Structure | Detected | Labeled | P | R | F1 | Order OK | Noise | Time (s) "
        "| Warnings |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in accepts:
        if r.match is None or r.document is None:
            lines.append(
                f"| {r.name} | {r.actual} | - | 0 | {r.labeled} | 0.00 | 0.00 | 0.00 | - | - | "
                f"{r.seconds:.1f} | - |"
            )
            continue
        m = r.match
        lines.append(
            f"| {r.name} | {r.actual} | {r.document.structure_source} | {m.detected} | {m.labeled} | "
            f"{_fmt(m.precision)} | {_fmt(m.recall)} | {_fmt(m.f1)} | {'yes' if m.order_ok else 'no'} | "
            f"{r.noise} | {r.seconds:.1f} | {_warnings_cell(r)} |"
        )
    lines += ["", "## Unmatched titles", ""]
    for r in accepts:
        if r.match is None:
            detail = f": {r.error}" if r.error else ""
            lines.append(f"- **{r.name}**: not accepted ({r.actual}{detail})")
            continue
        unmatched_d = "; ".join(r.match.unmatched_detected) or "none"
        unmatched_l = "; ".join(r.match.unmatched_labeled) or "none"
        optional = "; ".join(r.match.optional_matched) or "none"
        lines.append(f"- **{r.name}**")
        lines.append(f"  - unmatched detected: {unmatched_d}")
        lines.append(f"  - unmatched labeled: {unmatched_l}")
        lines.append(f"  - optional titles detected (not counted): {optional}")
    return "\n".join(lines) + "\n"


def render_history_entry(results: list[FileResult], commit: str, testset: Path) -> str:
    """One dated section for results_history.md."""
    s = summarize(results)
    per_file = ", ".join(
        f"{r.name} {_fmt(r.match.f1)}" if r.match else f"{r.name} 0.00 ({r.actual})"
        for r in results
        if not r.is_reject
    )
    reject_detail = ", ".join(
        f"{r.name} {'pass' if r.passed else 'FAIL ' + r.actual}" for r in results if r.is_reject
    )
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"## {stamp} - commit `{commit}`",
        "",
        f"- Test set: `{testset.resolve().name}`",
        f"- Precision / recall / F1 (micro): {_fmt(s.precision)} / {_fmt(s.recall)} / {_fmt(s.f1)} "
        f"(TP {s.tp}, detected {s.detected}, labeled {s.labeled})",
        f"- Reject accuracy: {s.reject_ok}/{s.rejects}" + (f" ({reject_detail})" if reject_detail else ""),
        f"- Total noise paragraphs: {s.noise}",
        f"- Per-file F1: {per_file}",
    ]
    return "\n".join(lines) + "\n"


HISTORY_HEADER = (
    "# Evaluation history\n\n"
    "Appended by `evaluate.py --history`; never overwritten. The first entry is the held-out score.\n"
)


def append_history(path: Path, entry: str) -> None:
    """Append a section to the history file (created with a header if it does not exist)."""
    with path.open("a", encoding="utf-8") as f:
        if f.tell() == 0:
            f.write(HISTORY_HEADER)
        f.write("\n" + entry)


def print_table(results: list[FileResult]) -> None:
    header = f"{'file':<26} {'expect':<17} {'actual':<17} {'source':<11} {'det':>4} {'lab':>4} "
    header += f"{'P':>5} {'R':>5} {'F1':>5} {'order':>5} {'noise':>5} {'sec':>6}"
    print(header)
    print("-" * len(header))
    for r in results:
        row = f"{r.name:<26} {r.expected:<17} {r.actual:<17} "
        if r.match and r.document:
            m = r.match
            row += (
                f"{r.document.structure_source:<11} {m.detected:>4} {m.labeled:>4} "
                f"{m.precision:>5.2f} {m.recall:>5.2f} {m.f1:>5.2f} {str(m.order_ok):>5} {r.noise:>5}"
            )
        else:
            row += f"{'-':<11} {'':>4} {'':>4} {'':>5} {'':>5} {'':>5} {'':>5} {'':>5}"
            row += "  pass" if r.passed else "  FAIL"
        print(f"{row} {r.seconds:>6.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the pipeline on the labeled test set.")
    parser.add_argument("--testset", type=Path, default=REPO_DIR / "testset")
    parser.add_argument(
        "--history", action="store_true", help="append a dated summary to <testset>/results_history.md"
    )
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    labels = json.loads((args.testset / "labels.json").read_text(encoding="utf-8"))
    docs_dir = args.testset / "docs"
    output_dir = args.testset / "output"

    results = []
    for name, label in labels.items():
        path = docs_dir / name
        if not path.exists():
            print(f"missing: {path}")
            labeled = len(label.get("chapters", []))
            results.append(FileResult(name, label["expect"], "MISSING", 0.0, labeled=labeled))
            continue
        results.append(evaluate_file(path, label, output_dir))

    print_table(results)
    commit = git_commit()
    markdown = render_markdown(results, commit)
    (args.testset / "results.md").write_text(markdown, encoding="utf-8")
    print()
    print(markdown.split("## Rejections")[0].strip())
    print(f"\nwrote {args.testset / 'results.md'}")
    if args.history:
        history = args.testset / "results_history.md"
        append_history(history, render_history_entry(results, commit, args.testset))
        print(f"appended {history}")


if __name__ == "__main__":
    main()
