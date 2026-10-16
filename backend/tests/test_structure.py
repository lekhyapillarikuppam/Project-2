from app.models import TextLine
from app.pipeline.structure import (
    Boundary,
    Draft,
    apply_front_matter,
    build_drafts,
    combine_heading_labels,
    detect_structure,
    drop_back_matter,
    merge_tiny_chapters,
    remove_toc_pages,
    split_huge_chapters,
    strategy_fallback,
    strategy_outline,
    strategy_patterns,
    strategy_styles,
    strategy_typography,
    to_models,
)

BODY = 10.0


def unit(
    text: str,
    page: int | None = 0,
    y: float = 100.0,
    size: float = BODY,
    bold: bool = False,
    style: str | None = None,
) -> TextLine:
    bbox = (50.0, y, 500.0, y + size) if page is not None else None
    return TextLine(text=text, font_size=size, bold=bold, page=page, bbox=bbox, style=style)


def words(n: int, word: str = "word") -> str:
    return " ".join([word] * n)


def draft(title: str, n_words: int) -> Draft:
    units = [unit(words(n_words))] if n_words else []
    return Draft(title, units, [False] * len(units))


# --------------------------------------------------------------------------- heading text rule


def test_label_and_title_lines_are_combined() -> None:
    units = [
        unit("Chapter I", y=100, size=20, bold=True),
        unit("Down the Rabbit-Hole", y=140, size=24, bold=True),
        unit(words(50)),
    ]
    out = combine_heading_labels(units, BODY)
    assert out[0].text == "Chapter I: Down the Rabbit-Hole"
    assert out[0].font_size == 24
    assert len(out) == 2


def test_label_without_title_line_stays() -> None:
    units = [unit("Chapter XII", size=20, bold=True), unit(words(50))]
    assert combine_heading_labels(units, BODY)[0].text == "Chapter XII"


def test_numbered_heading_keeps_number_and_is_not_combined() -> None:
    units = [unit("2 Eigenvalues and Eigenvectors", size=14, bold=True), unit("2.1 Definition", size=12, bold=True)]
    out = combine_heading_labels(units, BODY)
    assert [u.text for u in out] == ["2 Eigenvalues and Eigenvectors", "2.1 Definition"]


def test_label_and_title_on_different_pages_not_combined() -> None:
    units = [unit("Part Two", page=3, size=20, bold=True), unit("The Return", page=4, size=24, bold=True)]
    assert len(combine_heading_labels(units, BODY)) == 2


# --------------------------------------------------------------------------- post-processing 1: TOC


def test_toc_page_headed_contents_is_removed() -> None:
    units = [unit("Contents", page=1, size=24), unit("Down the Rabbit-Hole 3", page=1), unit(words(20), page=2)]
    assert [u.page for u in remove_toc_pages(units)] == [2]


def test_toc_page_detected_by_page_numbers() -> None:
    entries = [unit(f"Section {i} ........ {i * 3}", page=1) for i in range(1, 6)]
    body = [unit(words(30), page=2)]
    assert remove_toc_pages(entries + body) == body


def test_docx_toc_styles_removed() -> None:
    units = [
        unit("Table of Contents", page=None),
        unit("Intro\t1", page=None, style="TOC 1"),
        unit("Part one\t4", page=None, style="toc 2"),
        unit(words(10), page=None),
    ]
    assert [u.text for u in remove_toc_pages(units)] == [words(10)]


# --------------------------------------------------------------------------- strategies


def test_outline_matches_heading_lines_and_prefers_large_font() -> None:
    units = [
        unit("Title page", page=0, size=24),
        unit("To My Friend", page=2, y=100, size=24),
        unit("To My Friend", page=2, y=200),
        unit(words(60), page=2, y=220),
        unit("Chapter I: The First Day", page=4, size=24),
        unit(words(60), page=4, y=300),
    ]
    outline = [(1, "To My Friend", 3), (1, "The First Day", 5)]
    result = strategy_outline(units, outline)
    assert result is not None
    assert [(b.index, b.title) for b in result] == [(1, "To My Friend"), (4, "Chapter I: The First Day")]


def test_outline_needs_two_entries_and_uses_level_two_under_single_root() -> None:
    units = [unit("Only", page=0, size=20), unit(words(10))]
    assert strategy_outline(units, [(1, "Only", 1)]) is None
    units = [unit("A", page=0, size=20), unit(words(5)), unit("B", page=1, size=20), unit(words(5), page=1)]
    result = strategy_outline(units, [(1, "Book", 1), (2, "A", 1), (2, "B", 2)])
    assert result is not None and [b.title for b in result] == ["A", "B"]


def test_styles_strategy_uses_title_and_heading_1() -> None:
    units = [
        unit("Book", page=None, style="Title"),
        unit("One", page=None, style="Heading 1"),
        unit(words(10), page=None, style="Normal"),
        unit("Two", page=None, style="Heading 1"),
        unit("Sub", page=None, style="Heading 2"),
    ]
    result = strategy_styles(units)
    assert result is not None and [b.title for b in result] == ["Book", "One", "Two"]


def test_styles_strategy_falls_back_to_heading_2() -> None:
    units = [
        unit("Report", page=None, style="Heading 1"),
        unit("A", page=None, style="Heading 2"),
        unit("B", page=None, style="Heading 2"),
    ]
    result = strategy_styles(units)
    assert result is not None and [b.title for b in result] == ["Report", "A", "B"]


def test_typography_picks_largest_frequent_level() -> None:
    units = [
        unit("Big Book Title", page=0, size=30),
        unit("Chapter One Title", page=1, size=20),
        unit(words(50), page=1, y=150),
        unit("A Section", page=1, y=300, size=14),
        unit(words(50), page=1, y=330),
        unit("Chapter Two Title", page=2, size=20),
        unit(words(50), page=2, y=150),
    ]
    result = strategy_typography(units, BODY)
    assert result is not None and [b.title for b in result] == ["Chapter One Title", "Chapter Two Title"]


def test_typography_rejects_sentences_and_long_lines() -> None:
    units = [
        unit("This large line ends with a period.", page=0, size=20),
        unit(words(13), page=1, size=20),
        unit(words(50), page=1, y=200),
    ]
    assert strategy_typography(units, BODY) is None


def test_typography_bold_body_size_needs_whitespace_above() -> None:
    units = [
        unit(words(40), page=0, y=100),
        unit("Bold Heading One", page=0, y=200, bold=True),
        unit(words(40), page=0, y=215),
        unit("Bold Heading Two", page=1, y=200, bold=True),
        unit(words(40), page=1, y=215),
        unit("Bold but glued", page=1, y=226, bold=True),
    ]
    result = strategy_typography(units, BODY)
    assert result is not None and [b.title for b in result] == ["Bold Heading One", "Bold Heading Two"]


def test_patterns_strategy() -> None:
    units = [unit("Chapter 1 Arrival"), unit(words(30)), unit("CHAPTER 2"), unit(words(30))]
    result = strategy_patterns(units)
    assert result is not None and [b.index for b in result] == [0, 2]
    numbered = [unit("1 Intro"), unit(words(5)), unit("1.1 Detail"), unit("2 Method")]
    result = strategy_patterns(numbered)
    assert result is not None and [b.title for b in result] == ["1 Intro", "2 Method"]


def test_fallback_parts_of_about_3000_words() -> None:
    units = [unit(words(500)) for _ in range(20)]  # 10,000 words
    result = strategy_fallback(units)
    assert [b.title for b in result] == ["Part 1", "Part 2", "Part 3"]
    assert all(not b.consumes_unit for b in result)


# --------------------------------------------------------------------------- post-processing 2-6


def test_build_drafts_splits_front_matter_and_chapters() -> None:
    units = [unit("Front"), unit("H1", size=20), unit("Body one"), unit("H2", size=20), unit("Body two")]
    front, chapters = build_drafts(units, [False] * 5, [Boundary(1, "H1"), Boundary(3, "H2")])
    assert [u.text for u in front.units] == ["Front"]
    assert [(c.title, [u.text for u in c.units]) for c in chapters] == [
        ("H1", ["Body one"]),
        ("H2", ["Body two"]),
    ]


def test_drop_back_matter() -> None:
    chapters = [draft("1 Intro", 50), draft("References", 50), draft("7. Bibliography", 50), draft("Index", 5)]
    assert [c.title for c in drop_back_matter(chapters)] == ["1 Intro"]


def test_front_matter_kept_dropped_or_renamed() -> None:
    assert [c.title for c in apply_front_matter(draft("", 149), [draft("A", 50)])] == ["A"]
    assert [c.title for c in apply_front_matter(draft("", 150), [draft("A", 50)])] == ["Introduction", "A"]
    renamed = apply_front_matter(draft("", 200), [draft("1 Introduction", 50)])
    assert [c.title for c in renamed] == ["Front Matter", "1 Introduction"]


def test_merge_tiny_into_next_keeping_next_title_and_order() -> None:
    chapters = [draft("Book Title", 0), draft("Byline", 3), draft("Story", 100)]
    merged = merge_tiny_chapters(chapters)
    assert [c.title for c in merged] == ["Story"]
    assert [u.text for u in merged[0].units][:3] == ["Book Title", "Byline", words(3)]


def test_merge_tiny_last_into_previous_and_short_real_sections_stay() -> None:
    chapters = [draft("A", 45), draft("B", 41), draft("End", 5)]
    merged = merge_tiny_chapters(chapters)
    assert [c.title for c in merged] == ["A", "B"]
    assert merged[1].words == 41 + 1 + 5


def test_split_huge_at_sub_headings() -> None:
    units, flags = [], []
    for i in range(3):
        units += [unit(f"Section {i}"), unit(words(5000))]
        flags += [True, False]
    chapter = Draft("Long", units, flags)
    parts = split_huge_chapters([chapter])
    assert [p.title for p in parts] == ["Long (Part 1)", "Long (Part 2)"]
    assert parts[1].units[0].text.startswith("Section")


def test_split_huge_at_paragraphs_when_no_sub_headings() -> None:
    chapter = Draft("Long", [unit(words(1000)) for _ in range(25)], [False] * 25)
    parts = split_huge_chapters([chapter])
    assert len(parts) == 3
    assert all(6000 <= p.words <= 10000 for p in parts)


def test_to_models_numbers_chapters_with_narrator_segments() -> None:
    chapters = to_models([draft("A", 10), draft("B", 20)])
    assert [(c.index, c.title, c.word_count) for c in chapters] == [(1, "A", 10), (2, "B", 20)]
    segment = chapters[0].paragraphs[0].segments
    assert len(segment) == 1 and segment[0].speaker == "narrator"


def test_detect_structure_end_to_end() -> None:
    units = [
        unit("Contents", page=1, size=24),
        unit("One 3", page=1),
        unit("Chapter 1", page=2, y=50, size=18, bold=True),
        unit("The Start", page=2, y=80, size=24, bold=True),
        unit(words(300), page=2, y=150),
        unit("Chapter 2", page=5, y=50, size=18, bold=True),
        unit("The End", page=5, y=80, size=24, bold=True),
        unit(words(300), page=5, y=150),
        unit("References", page=8, y=50, size=24, bold=True),
        unit(words(100), page=8, y=150),
    ]
    chapters, source = detect_structure(units, [], "pdf")
    assert source == "typography"
    assert [c.title for c in chapters] == ["Chapter 1: The Start", "Chapter 2: The End"]
