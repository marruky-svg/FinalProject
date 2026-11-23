"""Divisão das perguntas do BioASQ em conjuntos de dev e de teste.

O dev serve para afinar o sistema (k, MIN_SCORE, pesos, prompts); o teste só
se usa no fim, para os números do relatório. Os dois nunca partilham perguntas.

A divisão é:
- estratificada: cada conjunto tem os quatro tipos de pergunta na mesma
  proporção que o ficheiro inteiro;
- reprodutível: a mesma semente dá sempre os mesmos conjuntos, em qualquer
  máquina e versão do Python, e independentemente da ordem das perguntas no
  ficheiro.

Para isso não se usa o random.shuffle: cada pergunta é ordenada pelo SHA-256
de "semente:id", que é sempre igual para o mesmo id e a mesma semente.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path

from helixrag.ingest.models import BioASQQuestion


def eligible(questions: list[BioASQQuestion]) -> list[BioASQQuestion]:
    """Perguntas que podem entrar na avaliação.

    Ficam de fora as que não têm documentos de ouro (não se pode medir a
    recuperação) e as marcadas como duplicado de outra (se uma ficasse no dev e
    a outra no teste, o teste deixava de ser independente).
    """
    return [q for q in questions if q.gold_pmids and not q.duplicate_of]


def split_questions(
    questions: list[BioASQQuestion],
    sizes: list[tuple[str, int]],
    seed: str,
) -> dict[str, list[str]]:
    """Devolve {nome do conjunto: [ids]} para cada (nome, tamanho) pedido.

    Os conjuntos são tirados por ordem da mesma sequência baralhada, por isso
    são disjuntos.
    """
    pool = eligible(questions)
    total = sum(size for _, size in sizes)
    if any(size < 0 for _, size in sizes):
        raise ValueError("os tamanhos têm de ser positivos")
    if total > len(pool):
        raise ValueError(f"pedidas {total} perguntas, só há {len(pool)} elegíveis")

    by_type: dict[str, list[BioASQQuestion]] = defaultdict(list)
    for question in pool:
        by_type[question.type].append(question)
    for group in by_type.values():
        group.sort(key=lambda q: _rank(seed, q.id))

    counts = {qtype: len(group) for qtype, group in by_type.items()}
    taken = dict.fromkeys(by_type, 0)
    result: dict[str, list[str]] = {}
    for name, size in sizes:
        quotas = _proportional_quotas(counts, size)
        ids = []
        for qtype, quota in quotas.items():
            start = taken[qtype]
            if start + quota > counts[qtype]:
                raise ValueError(f"não há perguntas do tipo {qtype!r} suficientes")
            ids += [q.id for q in by_type[qtype][start : start + quota]]
            taken[qtype] += quota
        result[name] = sorted(ids)
    return result


def _rank(seed: str, question_id: str) -> str:
    return hashlib.sha256(f"{seed}:{question_id}".encode()).hexdigest()


def _proportional_quotas(counts: dict[str, int], size: int) -> dict[str, int]:
    """Reparte `size` pelos tipos em proporção a `counts`, somando exatamente `size`.

    Método dos maiores restos: cada tipo recebe a parte inteira da sua quota, e
    as unidades que sobram vão para os tipos com a maior parte decimal.
    Exemplo com size=10 e counts {a: 50, b: 30, c: 20}: quotas 5, 3, 2.
    Com counts {a: 1, b: 1, c: 1}: 3,33 cada → 3, 3, 3, e a unidade que falta
    vai para o primeiro em ordem alfabética (desempate fixo).
    """
    total = sum(counts.values())
    exact = {t: size * c / total for t, c in counts.items()}
    quotas = {t: int(v) for t, v in exact.items()}
    leftover = size - sum(quotas.values())
    by_remainder = sorted(exact, key=lambda t: (-(exact[t] - quotas[t]), t))
    for qtype in by_remainder[:leftover]:
        quotas[qtype] += 1
    return dict(sorted(quotas.items()))


def write_ids(path: str | Path, ids: list[str], header: str = "") -> None:
    lines = [f"# {line}" for line in header.splitlines()]
    lines += ids
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_ids(path: str | Path) -> list[str]:
    ids = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if any(ch.isspace() for ch in value):
            raise ValueError(f"{path}:{number}: id inválido: {value!r}")
        ids.append(value)
    return list(dict.fromkeys(ids))


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
