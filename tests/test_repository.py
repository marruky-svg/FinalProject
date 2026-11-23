"""Testes de integração da ingestão (a ligação vem de conftest.py)."""

import pytest

from helixrag.ingest.models import AbstractSection, Article, Chunk

pytest.importorskip("psycopg")
pytestmark = pytest.mark.integration


def article(pmid, title="T"):
    return Article(pmid, title, [AbstractSection("RESULTS", "CFTR mutations cause cystic fibrosis.")], "J", 2020, ["Cystic Fibrosis"])


def test_schema_is_idempotent(conn):
    from helixrag.db.repository import apply_schema

    apply_schema(conn)
    apply_schema(conn)


def test_upsert_keeps_gold_origin(conn):
    from helixrag.db.repository import upsert_articles

    upsert_articles(conn, [article("1")], origin="gold")
    upsert_articles(conn, [article("1", title="Novo")], origin="distractor")
    row = conn.execute("SELECT title, origin, mesh FROM article WHERE pmid = '1'").fetchone()
    assert row == ("Novo", "gold", ["Cystic Fibrosis"])


def test_replace_chunks_and_lexical_column(conn):
    from helixrag.db.repository import corpus_stats, replace_chunks, upsert_articles

    upsert_articles(conn, [article("1")], origin="test")
    two = [Chunk("1", "section", 0, "A", "CFTR mutations"), Chunk("1", "section", 1, "B", "chloride channel")]
    replace_chunks(conn, two, unit="section")
    replace_chunks(conn, two[:1], unit="section")
    rows = conn.execute("SELECT ordinal, tsv @@ plainto_tsquery('english', 'mutation') FROM chunk").fetchall()
    assert rows == [(0, True)]
    assert corpus_stats(conn)["chunks"] == {"section": 1}


def test_reload_keeps_chunk_ids_and_only_updates_changed_text(conn):
    from helixrag.db.repository import replace_chunks, upsert_articles

    upsert_articles(conn, [article("1")], origin="test")
    first = [Chunk("1", "section", 0, "A", "CFTR mutations"), Chunk("1", "section", 1, "B", "chloride channel")]
    replace_chunks(conn, first, unit="section")
    ids_before = dict(conn.execute("SELECT ordinal, id FROM chunk").fetchall())

    changed = [first[0], Chunk("1", "section", 1, "B", "chloride channel defect")]
    replace_chunks(conn, changed, unit="section")
    rows = conn.execute("SELECT ordinal, id, content FROM chunk ORDER BY ordinal").fetchall()
    assert [(o, i) for o, i, _ in rows] == sorted(ids_before.items())
    assert rows[1][2] == "chloride channel defect"


def test_units_are_independent(conn):
    from helixrag.db.repository import replace_chunks, upsert_articles

    upsert_articles(conn, [article("1")], origin="test")
    replace_chunks(conn, [Chunk("1", "full", 0, None, "T\nall")], unit="full")
    replace_chunks(conn, [Chunk("1", "section", 0, "A", "a"), Chunk("1", "section", 1, "B", "b")], unit="section")
    replace_chunks(conn, [Chunk("1", "section", 0, "A", "a")], unit="section")
    assert conn.execute("SELECT unit, count(*) FROM chunk GROUP BY unit ORDER BY unit").fetchall() == [
        ("full", 1),
        ("section", 1),
    ]
