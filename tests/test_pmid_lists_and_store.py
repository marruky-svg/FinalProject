import pytest

from helixrag.ingest.models import AbstractSection, Article
from helixrag.ingest.pmid_lists import read_pmids, write_pmids
from helixrag.ingest.store import ArticleStore


def test_write_then_read_roundtrip(tmp_path):
    path = tmp_path / "pmids.txt"
    write_pmids(path, ["3", "1", "3", "2"], header="teste\nsegunda linha")
    assert path.read_text().startswith("# teste\n# segunda linha\n")
    assert read_pmids(path) == ["3", "1", "2"]


def test_read_rejects_invalid_lines(tmp_path):
    path = tmp_path / "pmids.txt"
    path.write_text("123\nabc\n")
    with pytest.raises(ValueError, match=":2:"):
        read_pmids(path)


def test_store_roundtrip_and_resume(tmp_path):
    store = ArticleStore(tmp_path / "data" / "articles.jsonl")
    assert store.pmids() == set()
    article = Article("7", "T", [AbstractSection("RESULTS", "texto com acentuação")], "J", 2020, ["M"])
    store.append([article])
    assert list(store) == [article]
    assert store.pmids() == {"7"}


def test_store_skips_corrupted_line(tmp_path):
    path = tmp_path / "articles.jsonl"
    store = ArticleStore(path)
    store.append([Article("1", "ok")])
    with path.open("a") as f:
        f.write('{"pmid": "2", "tit')  # linha cortada por uma interrupção
    assert [a.pmid for a in store] == ["1"]
