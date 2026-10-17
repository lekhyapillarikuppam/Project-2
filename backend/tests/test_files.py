from urllib.parse import unquote

from app.api.files import chapter_filename, content_disposition, full_book_filename, sanitize_part


def test_windows_invalid_and_control_characters_removed() -> None:
    assert sanitize_part('a<b>c:d"e/f\\g|h?i*j', "x") == "abcdefghij"
    assert sanitize_part("tab\there\x00null\x1f", "x") == "tab_herenull"


def test_whitespace_runs_become_underscores() -> None:
    assert sanitize_part("  The   Pool of\nTears  ", "x") == "The_Pool_of_Tears"


def test_parts_cut_to_sixty_characters() -> None:
    long_title = "A Very Long Chapter Title " * 10
    name = chapter_filename("Book", 3, long_title)
    book, nn, title = name.removesuffix(".mp3").split("_", 2)
    assert (book, nn) == ("Book", "03") and len(title) == 60
    assert len(full_book_filename("x" * 200).removesuffix(".mp3")) == 60


def test_empty_parts_fall_back() -> None:
    assert chapter_filename("???", 12, "***") == "audiobook_12_chapter.mp3"


def test_chapter_and_full_names() -> None:
    assert chapter_filename("Alice in Wonderland", 1, "Down the Rabbit-Hole") == (
        "Alice_in_Wonderland_01_Down_the_Rabbit-Hole.mp3"
    )
    assert full_book_filename("Alice in Wonderland") == "Alice_in_Wonderland.mp3"


def test_content_disposition_non_ascii() -> None:
    name = chapter_filename("Café", 2, "Ελληνικά — naïve")
    header = content_disposition(name)
    ascii_part = header.split('filename="', 1)[1].split('"', 1)[0]
    assert ascii_part == "".join(c if c.isascii() else "_" for c in name)
    assert ascii_part.startswith("Caf__02_") and ascii_part.endswith("_na_ve.mp3")
    encoded = header.split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded) == name and encoded.isascii()
    assert header.startswith("attachment; ")
