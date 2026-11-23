"""Divisão dos artigos em chunks, a unidade de pesquisa.

Duas estratégias, comparadas na experiência E1:

- "full": um chunk por artigo, com o título e o resumo inteiro.
- "section": um chunk por secção dos resumos estruturados. Cada chunk leva o
  título à frente, para não perder o contexto quando é lido isoladamente.
  Um resumo sem secções dá um único chunk, para que todos os artigos fiquem
  representados nas duas estratégias e a comparação seja justa.
"""

from __future__ import annotations

from helixrag.ingest.models import Article, Chunk

UNITS = ("full", "section")


def chunk_article(article: Article, unit: str = "full") -> list[Chunk]:
    """Divide um artigo segundo a estratégia pedida. Sem resumo, não há chunks."""
    if unit not in UNITS:
        raise ValueError(f"unidade desconhecida: {unit!r} (esperado: {UNITS})")

    if not article.has_abstract:
        return []

    labelled = [s for s in article.sections if s.label]
    if unit == "full" or len(labelled) < 2:
        text = _join(article.title, article.abstract)
        return [Chunk(article.pmid, unit, 0, None, text)]

    return [
        Chunk(
            pmid=article.pmid,
            unit=unit,
            ordinal=i,
            section_label=section.label,
            text=_join(article.title, f"{section.label}: {section.text}" if section.label else section.text),
        )
        for i, section in enumerate(article.sections)
    ]


def _join(title: str, body: str) -> str:
    return f"{title}\n{body}" if title else body
