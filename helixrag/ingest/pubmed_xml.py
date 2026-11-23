"""Conversão do XML do PubMed (efetch, retmode=xml) em objetos Article.

Usa o xml.etree.ElementTree da biblioteca standard. O XML vem da NCBI, uma
fonte de confiança; para XML de origem desconhecida, a alternativa mais segura
seria o pacote defusedxml, que bloqueia ataques por expansão de entidades.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from helixrag.ingest.models import AbstractSection, Article

# Um ano plausível dentro de um texto livre como "2019 Jan-Feb" ou "Winter 2018".
_YEAR = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")


def parse_articles(xml_bytes: bytes) -> list[Article]:
    """Converte uma resposta do efetch numa lista de artigos.

    Registos de livros (PubmedBookArticle) e registos sem PMID são ignorados.
    """
    root = ET.fromstring(xml_bytes)
    articles = []
    for record in root.iter("PubmedArticle"):
        article = _parse_record(record)
        if article is not None:
            articles.append(article)
    return articles


def _parse_record(record: ET.Element) -> Article | None:
    citation = record.find("MedlineCitation")
    if citation is None:
        return None

    pmid = _text(citation.find("PMID"))
    if not pmid:
        return None

    article = citation.find("Article")
    if article is None:
        return Article(pmid=pmid, title="")

    sections = []
    for node in article.findall("Abstract/AbstractText"):
        text = _text(node)
        if text:
            sections.append(AbstractSection(label=node.get("Label") or None, text=text))

    mesh = [
        name
        for name in (
            _text(d) for d in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")
        )
        if name
    ]

    return Article(
        pmid=pmid,
        title=_text(article.find("ArticleTitle")),
        sections=sections,
        journal=_text(article.find("Journal/Title")),
        year=_parse_year(article),
        mesh=mesh,
    )


def _text(node: ET.Element | None) -> str:
    """Todo o texto de um elemento, incluindo o de sub-elementos como <i> ou <sup>.

    O node.text simples cortaria o texto no primeiro elemento interno: em
    "<AbstractText>The <i>CFTR</i> gene</AbstractText>" devolveria só "The ".
    O itertext() percorre tudo, e o split/join normaliza espaços e quebras de linha.
    """
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _parse_year(article: ET.Element) -> int | None:
    """O ano de publicação, pela ordem de fiabilidade dos campos do PubMed."""
    year = _text(article.find("Journal/JournalIssue/PubDate/Year"))
    if year.isdigit():
        return int(year)

    medline_date = _text(article.find("Journal/JournalIssue/PubDate/MedlineDate"))
    match = _YEAR.search(medline_date)
    if match:
        return int(match.group(1))

    article_year = _text(article.find("ArticleDate/Year"))
    if article_year.isdigit():
        return int(article_year)

    return None
