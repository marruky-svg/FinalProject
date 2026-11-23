"""Testes de integração das tabelas de embeddings (a ligação vem de conftest.py).

Usam vetores de dimensão 3, escritos à mão, em vez do modelo real: o que se
testa aqui é a base de dados (pendentes, retoma, texto alterado, índice), não
o Ollama.
"""

import pytest

from helixrag.db import embeddings as emb
from helixrag.ingest.models import AbstractSection, Article, Chunk

pytest.importorskip("pgvector")
pytestmark = pytest.mark.integration

MODEL = "toy-model:3d"


@pytest.fixture
def corpus(conn):
    from helixrag.db.repository import replace_chunks, upsert_articles

    upsert_articles(conn, [Article("1", "T", [AbstractSection("", "x")], "J", 2020, [])], origin="test")
    replace_chunks(conn, [Chunk("1", "full", 0, None, "CFTR and cystic fibrosis")], unit="full")
    replace_chunks(
        conn,
        [Chunk("1", "section", 0, "A", "CFTR"), Chunk("1", "section", 1, "B", "Krebs cycle")],
        unit="section",
    )
    table = emb.ensure_model(conn, MODEL, 3)
    return conn, table


def test_table_name_is_sanitized():
    assert emb.table_name_for("bge-m3") == "embedding_bge_m3"
    assert emb.table_name_for("qwen3-embedding:0.6b") == "embedding_qwen3_embedding_0_6b"
    assert emb.table_name_for('x"; DROP TABLE chunk; --') == "embedding_x_drop_table_chunk"
    with pytest.raises(ValueError):
        emb.table_name_for("::")


def test_ensure_model_is_idempotent_and_checks_dimension(corpus):
    conn, table = corpus
    assert emb.ensure_model(conn, MODEL, 3) == table
    with pytest.raises(ValueError, match="dimensão 3"):
        emb.ensure_model(conn, MODEL, 4)
    assert emb.registered_models(conn) == [(MODEL, 3, table)]


def test_pending_store_and_resume(corpus):
    conn, table = corpus
    assert emb.count_pending(conn, table, ["full", "section"]) == 3

    first = emb.pending_chunks(conn, table, ["full", "section"], limit=2)
    emb.store_embeddings(conn, table, first, [[1, 0, 0], [0, 1, 0]])
    # Uma "segunda execução" só vê o que falta.
    rest = emb.pending_chunks(conn, table, ["full", "section"], limit=10)
    assert [c.id for c in rest] == [max(c.id for c in first) + 1]
    assert emb.count_pending(conn, table, ["full", "section"]) == 1


def test_pending_pagination_by_id(corpus):
    conn, table = corpus
    first = emb.pending_chunks(conn, table, ["section"], limit=1)
    second = emb.pending_chunks(conn, table, ["section"], limit=1, after_id=first[0].id)
    assert first[0].id < second[0].id


def test_changed_text_becomes_pending_again(corpus):
    from helixrag.db.repository import replace_chunks

    conn, table = corpus
    chunks = emb.pending_chunks(conn, table, ["section"], limit=10)
    emb.store_embeddings(conn, table, chunks, [[1, 0, 0], [0, 1, 0]])
    assert emb.count_pending(conn, table, ["section"]) == 0

    replace_chunks(conn, [Chunk("1", "section", 0, "A", "CFTR"), Chunk("1", "section", 1, "B", "TCA cycle")], unit="section")
    stale = emb.pending_chunks(conn, table, ["section"], limit=10)
    assert [c.content for c in stale] == ["TCA cycle"]
    emb.store_embeddings(conn, table, stale, [[0, 0, 1]])
    assert emb.count_pending(conn, table, ["section"]) == 0


def test_partial_index_and_nearest_neighbour(corpus):
    conn, table = corpus
    chunks = emb.pending_chunks(conn, table, ["full", "section"], limit=10)
    emb.store_embeddings(conn, table, chunks, [[1, 0, 0], [0.9, 0.1, 0], [0, 1, 0]])
    name = emb.build_hnsw_index(conn, MODEL, "section", maintenance_work_mem="64MB")
    assert emb.index_exists(conn, table, "section")
    assert not emb.index_exists(conn, table, "full")

    row = conn.execute(
        f'SELECT e.chunk_id FROM "{table}" e WHERE e.unit = %s ORDER BY e.embedding <=> %s::vector LIMIT 1',
        ("section", "[1,0,0]"),
    ).fetchone()
    assert row[0] == chunks[1].id  # o mais próximo dentro da unidade section, não o chunk full
    stats = emb.embedding_stats(conn, MODEL)
    assert stats["section"] == {"chunks": 2, "embedded": 2, "pending": 0, "index": True}
    assert name.endswith("_section_hnsw")


def test_index_arguments_are_validated(corpus):
    conn, _ = corpus
    with pytest.raises(ValueError):
        emb.build_hnsw_index(conn, MODEL, "tudo")
    with pytest.raises(ValueError):
        emb.build_hnsw_index(conn, MODEL, "full", maintenance_work_mem="1GB; DROP TABLE chunk")


def test_deleting_a_chunk_deletes_its_embedding(corpus):
    conn, table = corpus
    chunks = emb.pending_chunks(conn, table, ["full"], limit=10)
    emb.store_embeddings(conn, table, chunks, [[1, 0, 0]])
    conn.execute("DELETE FROM article WHERE pmid = '1'")
    assert conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == 0
