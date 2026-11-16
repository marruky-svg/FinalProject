"""Exercise 4 - Hybrid search with Reciprocal Rank Fusion (RRF)."""

from e01_embeddings import PHRASES, embed, load_config
from e02_pgvector import create_table, insert, search, start
from e03_lexical import add_lexical_index, lexical_search

QUESTIONS = [
	"Which gene is mutated is cystic fibrosis?",
	"BRCA1 breast cancer risk",
	"BRC1 breast cancer risk",
]
RRF_K = 60

def rrf(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + 1 / (k + position)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
	
def print_explained(
    question: str, dense: list[str], lexical: list[str], fused: list[tuple[str, float]]
) -> None:
    print(f"\n=== {question} ===")
    print(f"{'RRF':>8}  {'densa':>5}  {'lexical':>7}  frase")
    for doc, score in fused:
        d = dense.index(doc) + 1 if doc in dense else "-"
        l = lexical.index(doc) + 1 if doc in lexical else "-"
        print(f"{score:8.5f}   {doc}")

if __name__ == "__main__":
    host = load_config()
    vectors = embed(PHRASES, host)

    with start() as conn:
        create_table(conn)
        insert(conn, PHRASES, vectors)
        add_lexical_index(conn)

        for question in QUESTIONS:
            vector = embed([question], host)[0]
            dense = [content for content, _ in search(conn, vector, k=8)]
            lexical = [content for content, _ in lexical_search(conn, question, "or", k=8)]
            fused = rrf([dense, lexical])
            print_explained(question, dense, lexical, fused)