"""Exercise 2 - Save and search embbedings in PostreSQL with pgvector."""

import os

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from e01_embeddings import embed, load_config, PHRASES, QUESTION

def start() -> psycopg.Connection:
	load_dotenv()
	url = os.getenv("DATABASE_URL")
	if url is None:
		raise RuntimeError("DATABASE_URL isn't define in .env")
	conn = psycopg.connect(url)
	register_vector(conn)
	return conn

def create_table(conn: psycopg.Connection) -> None:
	conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
	conn.execute("DROP TABLE IF EXISTS phrases")
	conn.execute("""
		CREATE TABLE phrases(
		id			serial PRIMARY KEY,
		content		text NOT NULL,
		emb			vector(1024) NOT NULL
		)
	""")

def insert(conn: psycopg.Connection, texts: list[str], vectors) -> None:
	with conn.cursor() as cur:
		cur.executemany(
			"INSERT INTO phrases (content, emb) VALUES (%s, %s)",
			list(zip(texts, vectors)),
		)

def search(conn: psycopg.Connection, question_vector, k: int = 3) -> list[tuple]:
	return conn.execute(
		"""
		SELECT content, 1 - (emb <=> %s) AS s
		FROM phrases
		ORDER BY emb <=> %s
		LIMIT %s
		""",
		(question_vector, question_vector, k)
	).fetchall()

if __name__ == "__main__":
	host = load_config()
	vectors = embed(PHRASES, host)
	question = embed([QUESTION], host)[0]

	with start() as conn:
		create_table(conn)
		insert(conn, PHRASES, vectors)

		print(f"Question: {QUESTION}\n")
		results = search(conn, question, k=8)
		for pos, (content, s) in enumerate(results, start=1):
			print(f"{pos}. {s:.3f}  {content}")

