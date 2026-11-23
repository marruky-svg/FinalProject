"""Armazenamento local dos artigos descarregados, em JSONL (data/, fora do Git).

O download e a base de dados ficam separados: primeiro guarda-se tudo aqui,
depois carrega-se para o PostgreSQL. Isto permite:

- retomar um download interrompido (RF-22): os PMIDs já guardados são saltados;
- recarregar a base de dados sem voltar a pedir nada à NCBI.

JSONL (um objeto JSON por linha) permite acrescentar artigos sem reescrever o
ficheiro, e uma linha corrompida por uma interrupção só afeta esse artigo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Iterator

from helixrag.ingest.models import AbstractSection, Article

log = logging.getLogger(__name__)


class ArticleStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[Article]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as f:
            for number, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    yield _from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError, TypeError):
                    log.warning("%s:%d: linha inválida ignorada", self.path, number)

    def pmids(self) -> set[str]:
        return {article.pmid for article in self}

    def append(self, articles: list[Article]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            for article in articles:
                f.write(json.dumps(asdict(article), ensure_ascii=False) + "\n")


def _from_dict(data: dict) -> Article:
    return Article(
        pmid=data["pmid"],
        title=data["title"],
        sections=[AbstractSection(**s) for s in data["sections"]],
        journal=data.get("journal", ""),
        year=data.get("year"),
        mesh=list(data.get("mesh", [])),
    )
