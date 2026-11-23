"""Leitura dos ficheiros JSON do BioASQ Task B.

Cada pergunta indica os documentos de ouro como URLs do PubMed, por exemplo
"http://www.ncbi.nlm.nih.gov/pubmed/23515712". Daqui extraímos o PMID.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from helixrag.ingest.models import BioASQQuestion

# O PMID é a sequência de dígitos no fim do URL (com ou sem "/" final).
_PMID_AT_END = re.compile(r"(\d+)/?\s*$")


def extract_pmid(url: str) -> str | None:
    match = _PMID_AT_END.search(url)
    return match.group(1) if match else None


def load_questions(path: str | Path) -> list[BioASQQuestion]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = []
    for raw in data["questions"]:
        pmids = [extract_pmid(url) for url in raw.get("documents", [])]
        # dict.fromkeys remove repetidos mantendo a ordem original.
        gold = tuple(dict.fromkeys(p for p in pmids if p))
        questions.append(
            BioASQQuestion(
                id=raw["id"],
                type=raw.get("type", ""),
                body=raw["body"],
                gold_pmids=gold,
                ideal_answer=_first(raw.get("ideal_answer")),
                exact_answer=raw.get("exact_answer"),
                duplicate_of=_duplicate_ids(raw.get("duplicate_tmp")),
            )
        )
    return questions


def gold_pmids(questions: list[BioASQQuestion]) -> list[str]:
    """Todos os PMIDs de ouro, sem repetidos, por ordem numérica."""
    return sorted({p for q in questions for p in q.gold_pmids}, key=int)


def _first(value: object) -> str | None:
    """O ideal_answer pode vir como texto ou como lista de textos."""
    if isinstance(value, list):
        return value[0] if value else None
    return value if isinstance(value, str) else None


def _duplicate_ids(value: object) -> tuple[str, ...]:
    """O BioASQ marca perguntas repetidas com {"id": [...], ...} em "duplicate_tmp"."""
    if not isinstance(value, dict):
        return ()
    ids = value.get("id", [])
    if isinstance(ids, str):
        ids = [ids]
    return tuple(i for i in ids if isinstance(i, str))
