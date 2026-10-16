from app.models import Chapter, Paragraph, Segment
from app.tts.chunk import chunk_chapter, split_long_sentence, split_sentences


def chapter(title: str, paragraphs: list[str], index: int = 1) -> Chapter:
    return Chapter(
        index=index,
        title=title,
        word_count=sum(len(p.split()) for p in paragraphs),
        paragraphs=[Paragraph(index=i, segments=[Segment(text=t)]) for i, t in enumerate(paragraphs, 1)],
    )


def test_sentence_split_basic() -> None:
    assert split_sentences("One. Two! Three? Four") == ["One.", "Two!", "Three?", "Four"]


def test_no_split_on_abbreviations_initials_and_decimals() -> None:
    text = "Dr. Watson met Mr. Holmes. G. K. Chesterton paid 3.50 dollars, e.g. Today was fine."
    assert split_sentences(text) == [
        "Dr. Watson met Mr. Holmes.",
        "G. K. Chesterton paid 3.50 dollars, e.g. Today was fine.",
    ]


def test_split_after_closing_quote() -> None:
    assert split_sentences("‘Off with her head!’ the Queen shouted. Nobody moved.") == [
        "‘Off with her head!’ the Queen shouted.",
        "Nobody moved.",
    ]
    assert split_sentences("He said “Stop.” Then he left.") == ["He said “Stop.”", "Then he left."]


def test_long_sentence_split_at_semicolons_then_commas_then_spaces() -> None:
    sentence = "aaaa bbbb; cccc dddd; eeee ffff"
    assert split_long_sentence(sentence, 12) == ["aaaa bbbb;", "cccc dddd;", "eeee ffff"]
    assert split_long_sentence("one two, three four, five six", 12) == ["one two,", "three four,", "five six"]
    assert split_long_sentence("alpha beta gamma delta", 11) == ["alpha beta", "gamma delta"]
    assert all(len(p) <= 5 for p in split_long_sentence("x" * 12, 5))


def test_title_chunk_first_and_normalized() -> None:
    chunks = chunk_chapter(chapter("Chapter XII: Alice's Evidence", ["Some text."]), 1500)
    assert chunks[0].is_title and chunks[0].seq == 0 and chunks[0].paragraph_index == 0
    assert chunks[0].text == "Chapter twelve. Alice's Evidence"
    assert [c.seq for c in chunks] == list(range(len(chunks)))


def test_chunks_pack_sentences_and_never_cross_paragraphs() -> None:
    para1 = " ".join(f"Sentence number {i} is here." for i in range(1, 30))
    para2 = "A short second paragraph."
    chunks = chunk_chapter(chapter("T", [para1, para2]), 120)
    body = [c for c in chunks if not c.is_title]
    assert all(len(c.text) <= 120 for c in body)
    assert {c.paragraph_index for c in body} == {1, 2}
    by_para = [c for c in body if c.paragraph_index == 1]
    assert [c.ends_paragraph for c in by_para] == [False] * (len(by_para) - 1) + [True]
    assert body[-1].text == "A short second paragraph." and body[-1].ends_paragraph
    # Sentences are never cut: every chunk ends at a sentence end.
    assert all(c.text.endswith(".") for c in body)


def test_numbers_are_spoken_in_chunks() -> None:
    chunks = chunk_chapter(chapter("T", ["It cost $5 in 1865."]), 1500)
    assert chunks[1].text == "It cost five dollars in eighteen sixty-five."


def test_empty_paragraphs_produce_no_chunks() -> None:
    chunks = chunk_chapter(chapter("T", ["* * *", "Real text."]), 1500)
    assert [c.text for c in chunks] == ["T", "Real text."]
    assert all(c.text.strip() for c in chunks)
