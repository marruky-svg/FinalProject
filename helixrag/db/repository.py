"""Acesso à base de dados para a ingestão.

Todas as consultas usam parâmetros (%s), nunca texto concatenado (RNF-08).
As funções recebem a ligação por parâmetro e não fazem commit: quem as chama
decide os limites da transação.
"""

from __future__ import annotations

from dataclasses import asdict
from importlib import resources

import psycopg
from psycopg.types.json import Jsonb

from helixrag.ingest.models import Article, Chunk

ORIGINS = ("gold", "distractor", "test")


def apply_schema(conn: psycopg.Connection) -> None:
    sql = resources.files("helixrag.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.execute(sql)


def upsert_articles(conn: psycopg.Connection, articles: list[Article], origin: str) -> int:
    """Insere ou atualiza artigos. Um artigo de ouro nunca passa a distrator.

    Um PMID pode aparecer nas duas listas (ouro e distratores). Se a lista de
    distratores fosse carregada depois, sem esta regra, o artigo deixaria de
    contar como ouro e as métricas ficariam erradas sem nenhum aviso.
    """
    if origin not in ORIGINS:
        raise ValueError(f"origem desconhecida: {origin!r} (esperado: {ORIGINS})")
    rows = [
        (
            a.pmid,
            a.title,
            a.abstract,
            Jsonb([asdict(s) for s in a.sections]),
            a.journal,
            a.year,
            a.mesh,
            origin,
        )
        for a in articles
    ]
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO article (pmid, title, abstract, abstract_sections, journal, year, mesh, origin)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (pmid) DO UPDATE SET
                title = EXCLUDED.title,
                abstract = EXCLUDED.abstract,
                abstract_sections = EXCLUDED.abstract_sections,
                journal = EXCLUDED.journal,
                year = EXCLUDED.year,
                mesh = EXCLUDED.mesh,
                origin = CASE WHEN article.origin = 'gold' THEN 'gold' ELSE EXCLUDED.origin END,
                fetched_at = now()
            """,
            rows,
        )
    return len(rows)


def replace_chunks(conn: psycopg.Connection, chunks: list[Chunk], unit: str) -> int:
    """Substitui os chunks de uma unidade para os artigos envolvidos.

    Apagar e reinserir (em vez de atualizar) garante que não ficam chunks
    antigos para trás quando um artigo passa a ter menos secções.
    """
    pmids = sorted({c.pmid for c in chunks})
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunk WHERE unit = %s AND pmid = ANY(%s)", (unit, pmids))
        cur.executemany(
            """
            INSERT INTO chunk (pmid, unit, ordinal, section_label, content)
            VALUES (%s, %s, %s, %s, %s)
            """,
            [(c.pmid, c.unit, c.ordinal, c.section_label, c.text) for c in chunks],
        )
    return len(chunks)


def corpus_stats(conn: psycopg.Connection) -> dict:
    """Totais do corpus (RF-25)."""
    by_origin = dict(conn.execute("SELECT origin, count(*) FROM article GROUP BY origin").fetchall())
    without_abstract = conn.execute("SELECT count(*) FROM article WHERE abstract = ''").fetchone()[0]
    by_unit = dict(conn.execute("SELECT unit, count(*) FROM chunk GROUP BY unit").fetchall())
    return {"articles": by_origin, "without_abstract": without_abstract, "chunks": by_unit}
