"""Testes de integração com o PostgreSQL real (o do Docker).

Correm num esquema temporário, dentro de uma transação desfeita no fim, por
isso não tocam nos dados do projeto. Se a base de dados não estiver acessível, são saltados.
"""

import os
import uuid

import pytest

from helixrag.ingest.models import AbstractSection, Article, Chunk

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    from dotenv import load_dotenv

    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL não definido")
    try:
        connection = psycopg.connect(url, connect_timeout=3)
    except psycopg.OperationalError as error:
        pytest.skip(f"base de dados inacessível: {error}")

    # Tudo acontece dentro de uma única transação que nunca é confirmada:
    # o rollback final apaga o esquema temporário e tudo o que lá foi criado.
    schema = f"test_{uuid.uuid4().hex[:8]}"
    connection.execute(f'CREATE SCHEMA "{schema}"')
    connection.execute(f'SET LOCAL search_path TO "{schema}"')
    try:
        from helixrag.db.repository import apply_schema

        apply_schema(connection)
        yield connection
    finally:
        connection.rollback()
        connection.close()


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
