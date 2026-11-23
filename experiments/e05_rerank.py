"""Exercise 5 — Reranking with a cross-encoder."""

import time

from sentence_transformers import CrossEncoder

from e01_embeddings import PHRASES, embed, load_config
from e02_pgvector import create_table, insert, search, start
from e03_lexical import add_lexical_index, lexical_search
from e04_hybrid import rrf

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
QUESTIONS = [
    "Which gene is mutated in cystic fibrosis?",
    "Do CFTR mutations cause cystic fibrosis?",
    "BRCA1 breast cancer risk",
    "Que gene está mutado na fibrose quística?",
]


def load_reranker() -> CrossEncoder:
    return CrossEncoder(RERANK_MODEL)

def rerank(model: CrossEncoder, question: str, docs: list[str]) -> list[tuple[str, float]]:
    pairs = [(question, doc) for doc in docs]
    scores = model.predict(pairs)
    ranked = sorted(zip(docs, scores), key=lambda item: (-item[1], item[0]))
    return [(doc, float(score)) for doc, score in ranked]


def print_comparison(
    question: str, before: list[str], after: list[tuple[str, float]], elapsed_ms: float
) -> None:
    print(f"\n=== {question} ===  (reranking: {elapsed_ms:.0f} ms)")
    print(f"{'score':>7}  {'antes':>5}  {'depois':>6}  frase")
    for new_pos, (doc, score) in enumerate(after, start=1):
        old_pos = before.index(doc) + 1
        print(f"{score:7.3f}  {old_pos:>5}  {new_pos:>6}  {doc}")


if __name__ == "__main__":
    host = load_config()
    vectors = embed(PHRASES, host)
    model = load_reranker()

    with start() as conn:
        create_table(conn)
        insert(conn, PHRASES, vectors)
        add_lexical_index(conn)

        for question in QUESTIONS:
            vector = embed([question], host)[0]
            dense = [content for content, _ in search(conn, vector, k=8)]
            lexical = [content for content, _ in lexical_search(conn, question, "or", k=8)]
            fused = [doc for doc, _ in rrf([dense, lexical])]

            t0 = time.perf_counter()
            reranked = rerank(model, question, fused)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            print_comparison(question, fused, reranked, elapsed_ms)