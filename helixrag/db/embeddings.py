"""Tabelas de embeddings: uma por modelo (documentação, secção 7.6).

Um índice HNSW do pgvector só funciona numa coluna com dimensão fixa, e cada
modelo tem a sua dimensão. Por isso cada modelo tem a sua tabela, registada em
embedding_model:

    embedding_model                 embedding_bge_m3
    name    dimension table_name    chunk_id unit    content_md5 embedding
    bge-m3  1024      embedding_…   1        full    9f3c…       [0.01, …]

Duas colunas vão além do que a documentação previa:

- unit (repetida da tabela chunk): permite um índice HNSW parcial por unidade.
  A pesquisa filtra sempre por unidade; com um único índice, o HNSW devolvia os
  vizinhos mais próximos de todas as unidades e o filtro aplicado depois podia
  deixar menos resultados do que os pedidos.
- content_md5: o hash do texto que gerou o vetor. Se o texto do chunk mudar, o
  hash deixa de bater e o chunk volta a ficar pendente. Sem isto, um vetor
  antigo ficaria associado a um texto novo sem ninguém saber.

Os nomes das tabelas vêm de um nome de modelo; por isso são validados e
passados como identificadores (psycopg.sql), nunca colados no texto do SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import psycopg
from psycopg import sql
from pgvector.psycopg import register_vector

UNITS = ("full", "section")

_REGISTRY = """
CREATE TABLE IF NOT EXISTS embedding_model (
    id          serial      PRIMARY KEY,
    name        text        NOT NULL UNIQUE,
    dimension   integer     NOT NULL CHECK (dimension > 0),
    table_name  text        NOT NULL UNIQUE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    indexed_at  timestamptz
)
"""


@dataclass(frozen=True)
class PendingChunk:
    id: int
    unit: str
    content: str
    content_md5: str


def table_name_for(model: str) -> str:
    """'bge-m3' → 'embedding_bge_m3'; 'qwen3-embedding:0.6b' → 'embedding_qwen3_embedding_0_6b'."""
    slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    if not slug:
        raise ValueError(f"nome de modelo inválido: {model!r}")
    name = f"embedding_{slug}"
    if len(name) > 50:  # o PostgreSQL corta nomes acima de 63; deixa espaço para os índices
        raise ValueError(f"nome de modelo demasiado longo para uma tabela: {model!r}")
    return name


def ensure_model(conn: psycopg.Connection, model: str, dimension: int) -> str:
    """Regista o modelo e cria a sua tabela, se ainda não existirem. Devolve o nome da tabela.

    Falha se o modelo já estiver registado com outra dimensão: misturar vetores
    de dimensões diferentes na mesma tabela é impossível, e mudar de dimensão
    quer dizer que o modelo mudou.
    """
    table = table_name_for(model)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    conn.execute(_REGISTRY)
    row = conn.execute("SELECT dimension FROM embedding_model WHERE name = %s", (model,)).fetchone()
    if row and row[0] != dimension:
        raise ValueError(f"o modelo {model!r} está registado com dimensão {row[0]}, não {dimension}")
    conn.execute(
        "INSERT INTO embedding_model (name, dimension, table_name) VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING",
        (model, dimension, table),
    )
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                chunk_id    bigint      PRIMARY KEY REFERENCES chunk (id) ON DELETE CASCADE,
                unit        text        NOT NULL CHECK (unit IN ('full', 'section')),
                content_md5 text        NOT NULL,
                embedding   vector({dim}) NOT NULL,
                created_at  timestamptz NOT NULL DEFAULT now()
            )
            """
        ).format(table=sql.Identifier(table), dim=sql.Literal(dimension))
    )
    return table


def registered_models(conn: psycopg.Connection) -> list[tuple[str, int, str]]:
    exists = conn.execute("SELECT to_regclass('embedding_model') IS NOT NULL").fetchone()[0]
    if not exists:
        return []
    return conn.execute("SELECT name, dimension, table_name FROM embedding_model ORDER BY name").fetchall()


_PENDING_WHERE = sql.SQL(
    """
    FROM chunk c
    LEFT JOIN {table} e ON e.chunk_id = c.id AND e.content_md5 = md5(c.content)
    WHERE e.chunk_id IS NULL AND c.unit = ANY(%s)
    """
)


def count_pending(conn: psycopg.Connection, table: str, units: list[str]) -> int:
    query = sql.SQL("SELECT count(*) ") + _PENDING_WHERE.format(table=sql.Identifier(table))
    return conn.execute(query, (list(units),)).fetchone()[0]


def pending_chunks(
    conn: psycopg.Connection, table: str, units: list[str], limit: int, after_id: int = 0
) -> list[PendingChunk]:
    """Chunks sem vetor (ou com vetor de um texto antigo), por ordem de id.

    A paginação é por id ("depois do id X") e não por OFFSET: cada lote começa
    onde o anterior acabou, sem o PostgreSQL ter de saltar as linhas já vistas.
    """
    query = (
        sql.SQL("SELECT c.id, c.unit, c.content, md5(c.content) ")
        + _PENDING_WHERE.format(table=sql.Identifier(table))
        + sql.SQL(" AND c.id > %s ORDER BY c.id LIMIT %s")
    )
    rows = conn.execute(query, (list(units), after_id, limit)).fetchall()
    return [PendingChunk(*row) for row in rows]


def store_embeddings(
    conn: psycopg.Connection, table: str, chunks: list[PendingChunk], vectors: list[list[float]]
) -> int:
    if len(chunks) != len(vectors):
        raise ValueError("número de chunks e de vetores diferente")
    rows = [(c.id, c.unit, c.content_md5, np.asarray(v, dtype=np.float32)) for c, v in zip(chunks, vectors)]
    with conn.cursor() as cur:
        cur.executemany(
            sql.SQL(
                """
                INSERT INTO {table} (chunk_id, unit, content_md5, embedding)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                    unit = EXCLUDED.unit,
                    content_md5 = EXCLUDED.content_md5,
                    embedding = EXCLUDED.embedding,
                    created_at = now()
                """
            ).format(table=sql.Identifier(table)),
            rows,
        )
    return len(rows)


def index_name(table: str, unit: str) -> str:
    return f"{table}_{unit}_hnsw"


def build_hnsw_index(
    conn: psycopg.Connection,
    model: str,
    unit: str,
    m: int = 16,
    ef_construction: int = 64,
    maintenance_work_mem: str = "1GB",
) -> str:
    """Cria o índice HNSW parcial de uma unidade (vector_cosine_ops: distância de cosseno, <=>).

    m e ef_construction são os valores por omissão do pgvector. Mais altos dão
    um grafo melhor (mais recall) à custa de construção mais lenta e índice
    maior; é isso que a experiência E4 mede.
    """
    if unit not in UNITS:
        raise ValueError(f"unidade inválida: {unit!r}")
    if not re.fullmatch(r"\d+(kB|MB|GB)", maintenance_work_mem):
        raise ValueError(f"maintenance_work_mem inválido: {maintenance_work_mem!r}")
    table = table_name_for(model)
    name = index_name(table, unit)
    # O grafo inteiro cabe em memória durante a construção: com o valor por
    # omissão do PostgreSQL (64 MB) a construção fica muito mais lenta.
    conn.execute(sql.SQL("SET LOCAL maintenance_work_mem = {}").format(sql.Literal(maintenance_work_mem)))
    conn.execute(
        sql.SQL(
            """
            CREATE INDEX IF NOT EXISTS {name} ON {table}
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = {m}, ef_construction = {ef})
            WHERE unit = {unit}
            """
        ).format(
            name=sql.Identifier(name),
            table=sql.Identifier(table),
            m=sql.Literal(int(m)),
            ef=sql.Literal(int(ef_construction)),
            unit=sql.Literal(unit),
        )
    )
    conn.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table)))
    conn.execute("UPDATE embedding_model SET indexed_at = now() WHERE name = %s", (model,))
    return name


def index_exists(conn: psycopg.Connection, table: str, unit: str) -> bool:
    return conn.execute("SELECT to_regclass(%s) IS NOT NULL", (index_name(table, unit),)).fetchone()[0]


def embedding_stats(conn: psycopg.Connection, model: str) -> dict:
    """Por unidade: chunks no corpus, com vetor atual, pendentes, e se há índice (RF-25)."""
    table = table_name_for(model)
    totals = dict(conn.execute("SELECT unit, count(*) FROM chunk GROUP BY unit").fetchall())
    fresh = dict(
        conn.execute(
            sql.SQL(
                "SELECT c.unit, count(*) FROM {table} e JOIN chunk c ON c.id = e.chunk_id "
                "AND e.content_md5 = md5(c.content) GROUP BY c.unit"
            ).format(table=sql.Identifier(table))
        ).fetchall()
    )
    return {
        unit: {
            "chunks": totals.get(unit, 0),
            "embedded": fresh.get(unit, 0),
            "pending": totals.get(unit, 0) - fresh.get(unit, 0),
            "index": index_exists(conn, table, unit),
        }
        for unit in UNITS
    }
