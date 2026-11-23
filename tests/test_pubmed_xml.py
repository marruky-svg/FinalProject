from pathlib import Path

import pytest

from helixrag.ingest.pubmed_xml import parse_articles

FIXTURE = Path(__file__).parent / "fixtures" / "pubmed_sample.xml"


@pytest.fixture(scope="module")
def articles():
    return {a.pmid: a for a in parse_articles(FIXTURE.read_bytes())}


def test_ignores_book_records(articles):
    assert set(articles) == {"10000001", "10000002", "10000003"}


def test_structured_abstract_keeps_labels_and_inline_markup(articles):
    a = articles["10000001"]
    assert [s.label for s in a.sections] == ["BACKGROUND", "METHODS", "RESULTS"]
    # O <i>CFTR</i> não pode cortar o texto.
    assert a.sections[0].text == "Cystic fibrosis is caused by CFTR mutations."
    assert a.title == "Mutations in the CFTR gene and chloride transport."


def test_whitespace_is_normalised(articles):
    assert articles["10000001"].sections[1].text == "We measured chloride transport in 40 patients."


def test_metadata(articles):
    a = articles["10000001"]
    assert a.journal == "Journal of Example Genetics"
    assert a.year == 2021
    assert a.mesh == ["Cystic Fibrosis", "Cystic Fibrosis Transmembrane Conductance Regulator"]


def test_unstructured_abstract_and_medline_date(articles):
    a = articles["10000002"]
    assert len(a.sections) == 1 and a.sections[0].label is None
    assert a.year == 2019
    assert a.mesh == []


def test_article_without_abstract_falls_back_to_article_date(articles):
    a = articles["10000003"]
    assert not a.has_abstract
    assert a.year == 2020


def test_abstract_property_includes_labels(articles):
    assert articles["10000001"].abstract.startswith("BACKGROUND: Cystic fibrosis")
