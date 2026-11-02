"""Exercise 3 - Lexical search with a full-text of PostgreSQL"""

from e01_embeddings import PHRASES, load_config, embed
from e02_pgvector import create_table, insert, start, search

QUESTIONS = [
	"Which gene is mutated in cystic fibrosis?",
	"BRCA1 breast cancer risk",
]

SQL_MODE_AND = """
	SELECT content, ts_rank(tsv, plainto_tsquery('english', %s)) AS point
	FROM phrases
	WHERE tsv @@ plainto_tsquery('english', %s)
	ORDER BY point DESC
	LIMIT %s
"""

SQL_MODE_OR = """
    WITH consulta AS (
        SELECT replace(plainto_tsquery('english', %s)::text, '&', '|')::tsquery AS q
    )
    SELECT content, ts_rank(tsv, q) AS point
    FROM phrases, consulta
    WHERE tsv @@ q
    ORDER BY point DESC
    LIMIT %s
"""

def add_lexical_index(conn) -> None:
	conn.execute("""
		ALTER TABLE phrases
		ADD COLUMN tsv tsvector
		GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
	""")
	conn.execute("CREATE INDEX phrases_tsv_idx ON phrases USING GIN (tsv)")

def show_tsvectors(conn) -> None:
	for content, tsv in conn.execute("SELECT content, tsv::text FROM phrases ORDER BY id"):
		print(f"{tsv}\n		<- {content}")

def show_select(conn, question: str) -> None:
	terms = conn.execute(
		"SELECT plainto_tsquery('english', %s)::text", (question, )
	).fetchone()[0]
	print(f"Terms of the question: {terms}")

def lexical_search(conn, question: str, mode: str, k: int = 8) -> list[tuple]:
	if mode == "and":
		return conn.execute(SQL_MODE_AND, (question, question, k)).fetchall()
	return conn.execute(SQL_MODE_OR, (question, k)).fetchall()

def ft_print(title: str, results: list[tuple]) -> None:
	print(f"\n {title}:")
	if not results:
		print("		(no results)")
	for pos, (content, point) in enumerate(results, start=1):
		print(f"	{pos}. {point:.3f}	{content}")

if __name__ == "__main__":
	host = load_config()
	vectors = embed(PHRASES, host)

	with start() as conn:
		create_table(conn)
		insert(conn, PHRASES, vectors)
		add_lexical_index(conn)

		print("=== Como o PostgreSQL ve cada fase ===\n")
		show_tsvectors(conn)

		for question in QUESTIONS:
			print(f"\n=== Pergunta: {question} ===")
			show_select(conn, question)
			ft_print("Lexical, AND mode", lexical_search(conn, question, "and"))
			ft_print("Lexical, OR mode", lexical_search(conn, question, "or"))
			vector = embed([question], host)[0]
			ft_print("Densa:", search(conn, vector, k = 8))