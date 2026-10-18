import pytest

from app.dialogue.segment import (
    find_quote_spans,
    is_apostrophe,
    is_closer_position,
    is_opener_position,
    joins_previous,
    quote_family_of_texts,
    scan_paragraph,
    segment_chapter,
)
from app.models import Chapter, Paragraph, Segment

FILLER = "The garden was quiet and the afternoon was long and warm. " * 20  # 220 words, no marks


def quotes(text: str, family: str) -> list[str]:
    return [text[s:e] for s, e in scan_paragraph(text, family).spans]  # type: ignore[arg-type]


def chapter_of(*paragraphs: str) -> Chapter:
    return Chapter(
        index=1,
        title="Test",
        word_count=sum(len(p.split()) for p in paragraphs),
        paragraphs=[Paragraph(index=i, segments=[Segment(text=t)]) for i, t in enumerate(paragraphs, 1)],
    )


# --- rule 1: family and position ------------------------------------------------------------------


def test_curly_doubles() -> None:
    text = "“Well,” said Alice, “I suppose so.”"
    assert quotes(text, "double") == ["“Well,”", "“I suppose so.”"]


def test_straight_doubles() -> None:
    text = 'He looked up. "Is it raining?" he asked. "No," she said.'
    assert quotes(text, "double") == ['"Is it raining?"', '"No,"']


def test_curly_singles_with_swapped_glyphs() -> None:
    text = "‘I didn’t know it was YOUR table,‘ said Alice; ’it‘s laid for a great many more than three.’"
    assert quotes(text, "single") == [
        "‘I didn’t know it was YOUR table,‘",
        "’it‘s laid for a great many more than three.’",
    ]


def test_opener_and_closer_positions() -> None:
    text = "(‘Yes,’ he said)"
    assert is_opener_position(text, 1) and not is_closer_position(text, 1)
    assert is_closer_position(text, 6) and not is_opener_position(text, 6)


def test_other_family_is_ordinary_text_inside_a_quote() -> None:
    text = "“He called it ‘the best butter’ twice,” said the Hare."
    assert quotes(text, "double") == ["“He called it ‘the best butter’ twice,”"]
    text = "‘You might say that “I see what I eat” is the same,’ said the Hatter."
    assert quotes(text, "single") == ["‘You might say that “I see what I eat” is the same,’"]


def test_family_detection() -> None:
    double = [FILLER, "“Hello,” she said. “Goodbye.”"]
    single = [FILLER, "‘Hello,’ she said. ‘Don’t go.’"]
    assert quote_family_of_texts(double) == "double"
    assert quote_family_of_texts(single) == "single"


def test_document_without_dialogue_has_no_family() -> None:
    texts = [FILLER, "Alice's cat didn't care, and the Queens' garden was empty."]
    assert quote_family_of_texts(texts) is None
    spans, stats = find_quote_spans(texts, None)
    assert spans == [[], []] and stats.marks_seen == 0


def test_too_few_marks_is_no_dialogue() -> None:
    texts = [FILLER * 6, "It was called “the long room” by everyone."]  # 2 marks in ~1,330 words
    assert quote_family_of_texts(texts) is None


# --- rule 2: apostrophes --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "index"),
    [
        ("don't", 3),
        ("Alice's", 5),
        ("doesn‘t", 5),  # swapped glyph between letters
    ],
)
def test_apostrophe_between_letters(text: str, index: int) -> None:
    assert is_apostrophe(text, index, quote_open=False)


@pytest.mark.parametrize("word", ["'tis", "’Twas", "'em", "'cause", "'til", "'twere", "'ere", "'ow", "'e"])
def test_leading_elisions(word: str) -> None:
    text = f"and {word} so"
    assert is_apostrophe(text, 4, quote_open=True)


def test_leading_elision_inside_a_quote() -> None:
    assert quotes("‘I’ll tell ’em so,’ said Bill.", "single") == ["‘I’ll tell ’em so,’"]
    assert quotes("‘’Tis true,’ he said.", "single") == ["‘’Tis true,’"]


def test_plural_possessive_outside_a_quote() -> None:
    text = "They walked to the Queens’ garden. ‘Hello,’ said Alice."
    assert quotes(text, "single") == ["‘Hello,’"]
    assert not is_apostrophe("‘the boys’ ", 9, quote_open=True)  # inside a quote it may close


@pytest.mark.parametrize(
    "text",
    [
        "‘I was talkin’ to him,’ said Bill.",
        "‘A cup o’ tea,’ said Bill.",
        "‘Him an’ me,’ said Bill.",
        "‘He’s goin’ home,’ said Bill.",
        "‘Stop runnin’ about,’ said Bill.",  # any word ending in'
    ],
)
def test_trailing_elisions(text: str) -> None:
    assert len(quotes(text, "single")) == 1
    assert quotes(text, "single")[0].endswith(",’")


def test_word_ending_in_in_still_closes_before_a_speech_tag() -> None:
    assert quotes("‘Try again’ said Alice.", "single") == ["‘Try again’"]


# --- rules 3, 4, 6 --------------------------------------------------------------------------------


def test_broken_quote_is_one_utterance() -> None:
    spans, _ = find_quote_spans(["‘Well,’ said Alice, ‘I suppose so.’"], "single")
    assert [s.utterance_id for s in spans[0]] == ["U1", "U1"]


def test_two_speakers_in_one_paragraph_are_two_utterances() -> None:
    text = "‘Then it wasn’t civil,’ said Alice angrily. ‘Nor was sitting down,’ said the March Hare."
    spans, _ = find_quote_spans([text], "single")
    assert [s.utterance_id for s in spans[0]] == ["U1", "U2"]


def test_intro_tag_after_a_new_sentence_starts_a_new_utterance() -> None:
    text = "“Yes.” Alice nodded. The Hatter said, “No.”"
    spans, _ = find_quote_spans([text], "double")
    assert [s.utterance_id for s in spans[0]] == ["U1", "U2"]


def test_joins_previous() -> None:
    assert joins_previous(" said Alice, ", "")
    assert not joins_previous(" said Alice. ", " said the Hatter.")


def test_multi_paragraph_continuation() -> None:
    paragraphs = ["“It was a long time ago,” he said, “and we were young.", "“Then the war came.”"]
    spans, stats = find_quote_spans(paragraphs, "double")
    assert stats.continuations == 1
    assert spans[0][-1].utterance_id == spans[1][0].utterance_id == "U1"
    assert paragraphs[0][spans[0][-1].start : spans[0][-1].end] == "“and we were young."


def test_speech_tag_exception_closes_a_damaged_quote() -> None:
    paragraphs = ["“I will not go to the party", "“Nor will I,” said Mrs MacNab."]
    spans, stats = find_quote_spans(paragraphs, "double")
    assert stats.speech_tag_closures == 1 and stats.continuations == 0
    assert spans[0][0].utterance_id != spans[1][0].utterance_id


def test_damaged_closer_before_a_tag_inside_a_paragraph() -> None:
    text = "‘At any rate I’ll never go THERE again! said Alice as she left. ’It‘s the stupidest party!’"
    assert quotes(text, "single") == ["‘At any rate I’ll never go THERE again!", "’It‘s the stupidest party!’"]
    assert quotes("‘Sh! sh! and the Dormouse sulkily remarked, ‘Go on.’", "single")[0] == "‘Sh! sh!"


def test_unclosed_quote_at_paragraph_end() -> None:
    paragraphs = ["“I will never", "The door shut."]
    spans, stats = find_quote_spans(paragraphs, "double")
    assert stats.unclosed_closed == 1
    assert paragraphs[0][spans[0][0].start : spans[0][0].end] == "“I will never"


def test_paragraph_without_dialogue() -> None:
    spans, _ = find_quote_spans(["The cat sat on the mat and didn't move."], "single")
    assert spans == [[]]


# --- segment_chapter -----------------------------------------------------------------------------


def test_segment_chapter_splits_segments_and_numbers_utterances() -> None:
    chapter = chapter_of(
        "There was a table.",
        "“Have some wine,” the March Hare said.",
        "“Well,” said Alice, “I suppose so.”",
    )
    result, stats = segment_chapter(chapter, "double")
    assert [s.kind for s in result.paragraphs[0].segments] == ["narration"]
    p2 = result.paragraphs[1].segments
    assert [(s.kind, s.text, s.utterance_id) for s in p2] == [
        ("quote", "“Have some wine,”", "U1"),
        ("narration", "the March Hare said.", None),
    ]
    p3 = result.paragraphs[2].segments
    assert [s.utterance_id for s in p3 if s.kind == "quote"] == ["U2", "U2"]
    assert all(s.speaker == "narrator" for p in result.paragraphs for s in p.segments)
    assert stats.marks_seen == 6


def test_segment_chapter_without_family_is_unchanged() -> None:
    chapter = chapter_of("“Hello,” she said.")
    result, _ = segment_chapter(chapter, None)
    assert result == chapter
    assert result.paragraphs[0].segments[0].kind == "narration"
