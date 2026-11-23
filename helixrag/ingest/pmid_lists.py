"""Leitura e escrita das listas de PMIDs em corpus/ (versionadas no Git, RNF-04).

Formato: um PMID por linha; linhas vazias e linhas começadas por # são ignoradas.
"""

from __future__ import annotations

from pathlib import Path


def read_pmids(path: str | Path) -> list[str]:
    pmids = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if not value.isdigit():
            raise ValueError(f"{path}:{number}: PMID inválido: {value!r}")
        pmids.append(value)
    return list(dict.fromkeys(pmids))


def write_pmids(path: str | Path, pmids: list[str], header: str = "") -> None:
    lines = [f"# {line}" for line in header.splitlines() if header]
    lines += list(dict.fromkeys(pmids))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
