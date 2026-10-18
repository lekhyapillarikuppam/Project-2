"""Baseline attribution rules, combining, reply validation and ranking (SPEC §15.3, §15.5)."""

import pytest

from app.dialogue.attribute import (
    UNKNOWN,
    Attribution,
    BaselineResult,
    ChapterAttribution,
    ResponseError,
    apply_speakers,
    baseline_attribute,
    combine,
    parse_response,
    rank_characters,
)
from app.dialogue.segment import find_quote_spans, segment_chapter
from app.models import Chapter, DocumentModel, Paragraph, Segment


def attribute(*paragraphs: str) -> list[tuple[str, str, str]]:
    """(quote text, speaker, reason) per utterance."""
    spans, _ = find_quote_spans(list(paragraphs), "single")
    result = baseline_attribute(list(paragraphs), spans)
    first = {q.utterance_id: paragraphs[p][q.start : q.end] for p, row in enumerate(spans) for q in reversed(row)}
    return [(first[uid], a.speaker, a.reason) for uid, a in result.attributions.items()]


@pytest.mark.parametrize(
    "paragraph, speaker",
    [
        ("‘Have some wine,’ the March Hare said.", "March Hare"),
        ("‘Have some wine,’ said the March Hare.", "March Hare"),
        ("‘I do,’ Alice hastily replied.", "Alice"),
        ("‘Twinkle!’ said the old man, smiling.", "the old man"),
        ("‘I tried,’ said the girl shortly.", "the girl"),
        ("The Hatter said, ‘Wake up!’", "Hatter"),
        ("‘Well,’ said Alice, ‘I suppose so.’", "Alice"),
    ],
)
def test_explicit_tags(paragraph: str, speaker: str) -> None:
    (_, got, reason), *_ = attribute(paragraph)
    assert (got, reason) == (speaker, "explicit")


def test_pronoun_tag_resolves_to_the_one_named_character_of_that_gender() -> None:
    found = attribute(
        "‘Come, we shall have some fun now!’ said Alice; she was glad they had begun.",
        "The Hatter looked at Alice.",
        "‘I believe I can guess that,’ she added.",
    )
    assert found[-1][1:] == ("Alice", "pronoun")


def test_pronoun_tag_with_two_candidates_is_unknown() -> None:
    found = attribute(
        "‘Yes,’ said Alice, and she smiled.",
        "‘Indeed,’ said Mary, and she frowned.",
        "Alice looked at Mary.",
        "‘No,’ she said.",
    )
    assert found[-1][1:] == (UNKNOWN, "unknown")


def test_group_speech_is_unknown_and_stops_turn_taking() -> None:
    found = attribute(
        "‘Yes,’ said Alice.",
        "‘No,’ said the Hatter.",
        "‘No room!’ they cried.",
        "‘Plenty of room!’",
    )
    assert found[2][1:] == (UNKNOWN, "unknown")
    assert found[3][1:] == (UNKNOWN, "unknown")


def test_turn_taking_alternates_inside_a_dialogue_run() -> None:
    found = attribute(
        "‘Yes,’ said Alice.",
        "‘No,’ said the Hatter.",
        "‘Why not?’",
        "‘Because.’",
        "Alice sat and thought about the whole affair for a very long time, and nobody said anything at all for many minutes afterwards.",
        "‘Well?’",
    )
    assert [f[1:] for f in found[2:]] == [("Alice", "turn"), ("Hatter", "turn"), (UNKNOWN, "unknown")]


@pytest.mark.parametrize("paragraph", ["She said, ‘I know.’", "But I said, ‘No.’", "‘Yes,’ It said."])
def test_sentence_initial_pronouns_and_conjunctions_are_not_names(paragraph: str) -> None:
    assert attribute(paragraph)[0][2] != "explicit"


def test_everything_else_is_unknown() -> None:
    assert attribute("He thought for a while. ‘Very odd.’")[0][1:] == (UNKNOWN, "unknown")


def _baseline(**reasons: tuple[str, str]) -> BaselineResult:
    return BaselineResult({u: Attribution(u, s, r, "baseline") for u, (s, r) in reasons.items()}, {}, {})  # type: ignore[arg-type]


def test_combine_override_keeps_explicit_baseline_tags() -> None:
    baseline = _baseline(U1=("Alice", "explicit"), U2=("Hatter", "turn"))
    llm = ChapterAttribution({u: Attribution(u, "Dormouse", "llm", "llm") for u in ("U1", "U2")}, 1, 1)
    over = combine("llm_with_baseline_override", baseline, llm).attributions
    assert (over["U1"].speaker, over["U2"].speaker) == ("Alice", "Dormouse")
    assert combine("llm", baseline, llm).attributions["U1"].speaker == "Dormouse"
    assert combine("baseline", baseline, llm).attributions["U2"].speaker == "Hatter"


def test_parse_response_accepts_a_valid_reply() -> None:
    speakers, new = parse_response('{"q":{"Q1":"Alice","Q2":"UNKNOWN"},"new":[{"name":"Dormouse","gender":"male"}]}', ["Q1", "Q2"])
    assert speakers == {"Q1": "Alice", "Q2": "UNKNOWN"} and new[0]["name"] == "Dormouse"


@pytest.mark.parametrize(
    "content, problem",
    [
        ("not json", "not valid JSON"),
        ('{"q":{"Q1":"Alice"}}', "missing ids: Q2"),
        ('{"q":{"Q1":"Alice","Q2":"Bob","Q3":"Eve"}}', "unknown ids: Q3"),
        ('{"q":{"Q1":"Alice","Q1":"Bob","Q2":"Eve"}}', "more than once: Q1"),
        ('{"q":{"Q1":"","Q2":"Eve"}}', "non-empty string for: Q1"),
        ('{"speakers":{}}', '"q" object'),
    ],
)
def test_parse_response_rejects(content: str, problem: str) -> None:
    with pytest.raises(ResponseError, match=problem):
        parse_response(content, ["Q1", "Q2"])


def _chapter(*texts: str) -> Chapter:
    return Chapter(
        index=1, title="T", word_count=sum(len(t.split()) for t in texts),
        paragraphs=[Paragraph(index=i, segments=[Segment(text=t)]) for i, t in enumerate(texts, 1)],
    )  # fmt: skip


def test_apply_speakers_sets_quotes_and_leaves_unknown_to_the_narrator() -> None:
    chapter, _ = segment_chapter(_chapter("‘Hi,’ said Alice. ‘Hello,’ said the Hatter."), "single")
    out = apply_speakers(chapter, {"U1": "Alice", "U2": UNKNOWN})
    speakers = [(s.kind, s.speaker) for s in out.paragraphs[0].segments]
    assert speakers == [("quote", "Alice"), ("narration", "narrator"), ("quote", "narrator"), ("narration", "narrator")]


def test_rank_characters_counts_baseline_lines_over_the_book() -> None:
    texts = ["‘Yes,’ said Alice, and she smiled.", "‘No,’ said the Hatter.", "‘Why?’ said Alice."] * 3
    document = DocumentModel(
        source_filename="t.pdf", source_type="pdf", page_count=1, word_count=50, structure_source="fallback",
        warnings=[], chapters=[_chapter(*texts)],
    )  # fmt: skip
    ranking, genders = rank_characters(document)
    assert [(r.name, r.lines) for r in ranking] == [("Alice", 6), ("Hatter", 3)]
    assert genders["alice"] == "female"
