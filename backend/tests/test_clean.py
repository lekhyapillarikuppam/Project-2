from app.models import TextLine
from app.pipeline.clean import (
    body_font_size,
    build_vocabulary,
    clean_lines,
    drop_captions_footnotes_equations,
    header_key,
    is_equation_line,
    is_page_number_text,
    keep_compound_hyphen,
    merge_into_paragraphs,
    normalize_chars,
    remove_running_headers_footers,
)

PAGE_H = 800.0
BODY = 10.0


def line(
    text: str,
    page: int | None = 0,
    y: float = 400.0,
    x0: float = 50.0,
    x1: float = 500.0,
    size: float = BODY,
    block: int = 0,
    bold: bool = False,
    font: str = "Body",
    math: bool = False,
    style: str | None = None,
) -> TextLine:
    bbox = (x0, y, x1, y + size) if page is not None else None
    return TextLine(
        text=text,
        font_size=size,
        bold=bold,
        page=page,
        bbox=bbox,
        style=style,
        block_id=block,
        is_math_font=math,
        font=font,
    )


def heights(n: int) -> dict[int, float]:
    return {p: PAGE_H for p in range(n)}


def body_page(page: int, words: str = "Body text of the page goes here.") -> TextLine:
    return line(words * 5, page=page, y=300, block=page * 10)


# --------------------------------------------------------------------------- normalize_chars


def test_normalize_chars_ligatures_spaces_and_soft_hyphens() -> None:
    assert normalize_chars("ﬁne ﬂow") == "fine flow"
    assert normalize_chars("a\u00a0b\u2009c") == "a b c"
    assert normalize_chars("co\u00adoperate") == "cooperate"
    assert normalize_chars("  many   spaces\n\there ") == "many spaces here"


def test_normalize_chars_quotes_and_tex_artifacts() -> None:
    assert normalize_chars("„Hi‟") == "“Hi“"
    assert normalize_chars("stop¡ now¿") == "stop! now?"
    assert normalize_chars("bad\ufffdglyph") == "badglyph"


# --------------------------------------------------------------------------- body size


def test_body_font_size_is_weighted_by_characters() -> None:
    lines = [line("Big heading", size=20), line("x" * 200, size=10), line("y" * 50, size=12)]
    assert body_font_size(lines) == 10
    assert body_font_size([]) == 0.0


# --------------------------------------------------------------------------- headers / footers


def test_page_number_text() -> None:
    for t in ("12", "xiv", "Page 3", "3 of 10", "- 7 -", "IV"):
        assert is_page_number_text(t), t
    for t in ("Chapter 3", "12 apples", "", "Page three"):
        assert not is_page_number_text(t), t


def test_header_key_strips_digits_and_roman_numerals() -> None:
    assert header_key("Chapter IV  Page 12") == "chapter page"
    assert header_key("88 Introductory Statistics") == "introductory statistics"


def test_repeated_header_removed_when_threshold_met() -> None:
    lines = []
    for p in range(4):
        lines.append(line("My Book Title", page=p, y=10, block=p * 10 + 1))
        lines.append(body_page(p))
    out = remove_running_headers_footers(lines, 4, heights(4))
    assert [ln.text for ln in out if "Title" in ln.text] == []
    assert len(out) == 4


def test_header_kept_below_threshold_but_page_numbers_always_removed() -> None:
    lines = [
        line("Header On Three", page=p, y=10, block=p * 10 + 1) for p in range(3)
    ] + [line(str(p + 1), page=p, y=780, block=p * 10 + 2) for p in range(12)]
    lines += [body_page(p) for p in range(12)]
    out = remove_running_headers_footers(lines, 12, heights(12))
    texts = [ln.text for ln in out]
    assert texts.count("Header On Three") == 3  # 3 pages < max(3, 30% of 12 = 3.6)
    assert not any(t.isdigit() for t in texts)


def test_two_page_document_header_on_every_page_is_removed() -> None:
    lines = [
        line("Workshop Preprint", page=0, y=10, block=1),
        line("Workshop Preprint", page=1, y=10, block=11),
        line("1", page=0, y=780, block=2),
        line("2", page=1, y=780, block=12),
        body_page(0),
        body_page(1),
    ]
    out = remove_running_headers_footers(lines, 2, heights(2))
    assert [ln.text for ln in out] == [body_page(0).text, body_page(1).text]


def test_two_page_document_header_on_one_page_is_kept() -> None:
    lines = [line("Workshop Preprint", page=1, y=10, block=11), body_page(0), body_page(1)]
    out = remove_running_headers_footers(lines, 2, heights(2))
    assert "Workshop Preprint" in [ln.text for ln in out]


def test_one_page_document_keeps_header_text() -> None:
    lines = [line("Memo Header", page=0, y=10, block=1), line("7", page=0, y=780, block=2), body_page(0)]
    out = remove_running_headers_footers(lines, 1, heights(1))
    assert [ln.text for ln in out] == ["Memo Header", body_page(0).text]


def test_alternating_headers_each_need_threshold() -> None:
    lines = []
    for p in range(6):
        text = "Even Header" if p % 2 == 0 else "Odd Header"
        lines.append(line(text, page=p, y=10, block=p * 10 + 1, font="Body"))
        lines.append(body_page(p))
    lines.append(line("Rare Header", page=0, y=12, block=99, font="Body"))
    out = remove_running_headers_footers(lines, 6, heights(6))
    texts = [ln.text for ln in out]
    assert "Even Header" not in texts and "Odd Header" not in texts
    assert "Rare Header" in texts


def test_running_header_style_removes_changing_chapter_titles() -> None:
    lines = []
    for p in range(6):
        lines.append(line(f"Chapter title {chr(65 + p // 2)}", page=p, y=10, size=9, font="SmallCaps", block=p * 10 + 1))
        lines.append(body_page(p))
    out = remove_running_headers_footers(lines, 6, heights(6))
    assert all(ln.font == "Body" for ln in out)


def test_lines_outside_zones_and_docx_lines_untouched() -> None:
    lines = [line("Same text", page=p, y=400, block=p) for p in range(5)]
    lines.append(line("12", page=None))
    out = remove_running_headers_footers(lines, 5, heights(5))
    assert len(out) == 6


# --------------------------------------------------------------------------- captions / footnotes / equations


def test_captions_drop_whole_block_but_not_prose() -> None:
    lines = [
        line("Figure 4.1: A density curve with", block=1, y=100),
        line("two standard deviations.", block=1, y=112),
        line("Figure 4.2 shows the exponential density.", block=2, y=200),
        line("Table 3. Results by type", block=3, y=300),
        line("Fig. 2 — overview", block=4, y=400),
    ]
    out = drop_captions_footnotes_equations(lines, BODY, heights(1))
    assert [ln.text for ln in out] == ["Figure 4.2 shows the exponential density."]


def test_footnotes_in_bottom_region_with_small_font_dropped() -> None:
    lines = [
        line("Body text near the bottom of the page.", y=650, block=1),
        line("1A footnote that explains something", y=700, size=8, block=2),
        line("and continues on a second line.", y=710, size=8, block=2),
        line("Small text high on the page", y=100, size=8, block=3),
    ]
    out = drop_captions_footnotes_equations(lines, BODY, heights(1))
    assert [ln.text for ln in out] == [
        "Body text near the bottom of the page.",
        "Small text high on the page",
    ]


def test_equation_detection() -> None:
    assert is_equation_line(line("Av = λv.", math=True))
    assert is_equation_line(line("(4.1)"))
    assert is_equation_line(line("det(A − λI) = λ² − 7λ + 10"))
    assert not is_equation_line(line("A matrix of size n × n with real entries unless stated."))
    assert not is_equation_line(line("The year was 1837."))


# --------------------------------------------------------------------------- merging


def test_merge_joins_hyphenated_lines_and_wraps() -> None:
    lines = [
        line("In another moment down went Alice, never once con-", y=100, x1=500, block=1),
        line("sidering how in the world she was to get out again.", y=112, x1=300, block=1),
    ]
    out = merge_into_paragraphs(lines, BODY)
    assert [u.text for u in out] == [
        "In another moment down went Alice, never once considering how in the world she was to get out again."
    ]


def test_hyphen_kept_before_capital_letter() -> None:
    lines = [
        line("the Anglo-", y=100, block=1),
        line("Saxon chronicle continued for many more pages of text", y=112, block=1),
    ]
    assert merge_into_paragraphs(lines, BODY)[0].text.startswith("the Anglo- Saxon")


def _split_pair(first: str, second: str, block: int = 1, y: float = 100) -> list[TextLine]:
    return [line(first, y=y, block=block), line(second, y=y + 12, block=block)]


def test_compound_hyphen_kept_when_hyphenated_form_appears_elsewhere() -> None:
    lines = [line("A large rose-tree stood near the entrance of the garden.", y=50, block=0)]
    lines += _split_pair("the white roses on the rose-", "tree were being painted red by three men")
    out = merge_into_paragraphs(lines, BODY)
    assert "the rose-tree were" in out[1].text


def test_split_joined_even_when_both_parts_are_words() -> None:
    # Rev 4: "some" and "thing" both appear on their own, but only an in-line "some-thing" keeps it.
    lines = [line("There was some cake and a thing on the table.", y=50, block=0)]
    lines += _split_pair("she saw some-", "thing moving in the garden")
    out = merge_into_paragraphs(lines, BODY)
    assert "saw something moving" in out[1].text


def test_ordinary_split_word_is_joined() -> None:
    lines = _split_pair("In another moment down went Alice, never once con-", "sidering how to get out")
    assert "considering how" in merge_into_paragraphs(lines, BODY)[0].text


def test_split_halves_do_not_vouch_for_themselves() -> None:
    # "tree" only occurs as the continuation of the split, so it does not count as a standalone word.
    lines = [line("The rose was red and very pretty indeed today.", y=50, block=0)]
    lines += _split_pair("near the big rose-", "tree stood the gardener")
    assert "big rosetree stood" in merge_into_paragraphs(lines, BODY)[1].text


def test_one_letter_prefix_is_joined() -> None:
    lines = [line("a way to go and a cat.", y=50, block=0)]
    lines += _split_pair("she ran a-", "way from the cat as fast as she could")
    assert "ran away from" in merge_into_paragraphs(lines, BODY)[1].text


def test_build_vocabulary_and_keep_rule() -> None:
    lines = _split_pair("one Rose-Tree and a con-", "sidering mind")
    vocab = build_vocabulary(lines)
    assert vocab.compounds == frozenset({"rose-tree"})  # the line-end "con-" is not a compound
    assert keep_compound_hyphen("rose", "tree", vocab)
    assert not keep_compound_hyphen("con", "sidering", vocab)


def test_merge_breaks_on_gap_indent_and_short_final_line() -> None:
    lines = [
        line("First paragraph line that fills the column width nicely", y=100, block=1),
        line("ends here.", y=112, x1=150, block=1),
        line("Second paragraph starts with an indent and runs on", y=124, x0=70, block=1),
        line("to the full width of the column again and again", y=136, block=1),
        line("Third paragraph after a big vertical gap in the block", y=170, block=1),
    ]
    out = merge_into_paragraphs(lines, BODY)
    assert len(out) == 3
    assert out[0].text.endswith("ends here.")
    assert out[1].text.startswith("Second")
    assert out[2].text.startswith("Third")


def test_headings_stay_separate_and_keep_first_line_style() -> None:
    lines = [
        line("Chapter Title", y=50, size=20, bold=True, block=1),
        line("body text starts here and goes on", y=100, block=2),
    ]
    out = merge_into_paragraphs(lines, BODY)
    assert [u.text for u in out] == ["Chapter Title", "body text starts here and goes on"]
    assert out[0].font_size == 20 and out[0].bold


def test_paragraph_continues_across_page_break() -> None:
    lines = [
        line("a sentence that runs to the end of the page and", page=0, y=700, block=1),
        line("continues on the next page.", page=1, y=60, block=11),
        line("A new paragraph on the next page.", page=1, y=80, x0=70, block=12),
    ]
    out = merge_into_paragraphs(lines, BODY)
    assert [u.text for u in out] == [
        "a sentence that runs to the end of the page and continues on the next page.",
        "A new paragraph on the next page.",
    ]


def test_docx_lines_are_already_paragraphs() -> None:
    lines = [line("One paragraph", page=None), line("another one", page=None)]
    assert [u.text for u in merge_into_paragraphs(lines, BODY)] == ["One paragraph", "another one"]


def test_clean_lines_runs_all_steps() -> None:
    lines = []
    for p in range(3):
        lines.append(line("Running Header", page=p, y=10, block=p * 10 + 1))
        lines.append(line(f"Body sentence number {p} on its own page.", page=p, y=300, block=p * 10 + 2))
        lines.append(line(str(p + 1), page=p, y=780, block=p * 10 + 3))
    units, body = clean_lines(lines, 3, heights(3))
    assert body == BODY
    assert [u.text for u in units] == [f"Body sentence number {p} on its own page." for p in range(3)]
