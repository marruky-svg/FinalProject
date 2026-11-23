"""Modelos de dados da ingestão.

São dataclasses simples, sem dependências de base de dados nem de rede.
Todos os outros módulos da ingestão trocam informação através destes tipos,
o que permite testar cada peça (parser, chunking, BioASQ) isoladamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AbstractSection:
    """Uma secção de um resumo.

    Nos resumos estruturados, `label` é o nome da secção (por exemplo
    "BACKGROUND" ou "RESULTS"). Nos resumos de um só bloco, é None.
    """

    label: str | None
    text: str


@dataclass
class Article:
    """Um artigo do PubMed, já normalizado (RF-23)."""

    pmid: str
    title: str
    sections: list[AbstractSection] = field(default_factory=list)
    journal: str = ""
    year: int | None = None
    mesh: list[str] = field(default_factory=list)

    @property
    def abstract(self) -> str:
        """O resumo inteiro, com o rótulo de cada secção quando existe."""
        return "\n".join(
            f"{s.label}: {s.text}" if s.label else s.text for s in self.sections
        )

    @property
    def has_abstract(self) -> bool:
        """Artigos sem resumo são guardados, mas não indexados (UC-11, fluxo 4a)."""
        return any(s.text.strip() for s in self.sections)


@dataclass(frozen=True)
class Chunk:
    """A unidade de pesquisa. `ordinal` é a posição do chunk dentro do artigo."""

    pmid: str
    unit: str  # "full" ou "section"
    ordinal: int
    section_label: str | None
    text: str


@dataclass(frozen=True)
class BioASQQuestion:
    """Uma pergunta do BioASQ Task B, com os PMIDs dos documentos de ouro."""

    id: str
    type: str  # "yesno", "factoid", "list" ou "summary"
    body: str
    gold_pmids: tuple[str, ...]
    ideal_answer: str | None
    exact_answer: object | None
    # IDs de perguntas de que esta é duplicado (campo "duplicate_tmp" do BioASQ).
    duplicate_of: tuple[str, ...] = ()
