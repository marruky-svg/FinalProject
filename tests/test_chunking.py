import pytest

from helixrag.ingest.chunking import chunk_article
from helixrag.ingest.models import AbstractSection, Article

STRUCTURED = Article(
    pmid="1",
    title="Title",
    sections=[AbstractSection("BACKGROUND", "b."), AbstractSection("RESULTS", "r.")],
)
PLAIN = Article(pmid="2", title="Plain", sections=[AbstractSection(None, "Only text.")])
EMPTY = Article(pmid="3", title="No abstract")


def test_full_unit_is_one_chunk_with_title():
    [chunk] = chunk_article(STRUCTURED, "full")
    assert chunk.text == "Title\nBACKGROUND: b.\nRESULTS: r."
    assert (chunk.unit, chunk.ordinal, chunk.section_label) == ("full", 0, None)


def test_section_unit_splits_and_repeats_title():
    chunks = chunk_article(STRUCTURED, "section")
    assert [c.text for c in chunks] == ["Title\nBACKGROUND: b.", "Title\nRESULTS: r."]
    assert [c.ordinal for c in chunks] == [0, 1]
    assert [c.section_label for c in chunks] == ["BACKGROUND", "RESULTS"]


def test_section_unit_on_plain_abstract_gives_single_chunk():
    [chunk] = chunk_article(PLAIN, "section")
    assert chunk.unit == "section"
    assert chunk.text == "Plain\nOnly text."


def test_article_without_abstract_has_no_chunks():
    assert chunk_article(EMPTY, "full") == []
    assert chunk_article(EMPTY, "section") == []


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError):
        chunk_article(PLAIN, "sentence")
