import os
from dotenv import load_dotenv
import numpy as np
import requests

#1. A com a maior semelhanca vai ser e a BRCA1 e a BRCA2
#2. Longe, vai estar no sentido contrario
#3. Muito perto, quase 1
#4. Acho que sim
#5. As duas primeiras da PHRASES

MODEL = "bge-m3"
PHRASES = [
    "Mutations in the CFTR gene cause cystic fibrosis.",
    "Cystic fibrosis results from defects in a chloride channel encoded by CFTR.",
    "Mutações no gene CFTR causam fibrose quística.",
    "CFTR mutations do not cause cystic fibrosis.",
    "BRCA1 mutations increase the risk of breast cancer.",
    "BRCA2 mutations increase the risk of breast cancer.",
    "The Krebs cycle produces NADH in the mitochondrial matrix.",
    "The football match ended in a draw.",
]
LABELS = ["original", "parafrase", "traducao PT", "negacao", "BRCA1", "BRCA2", "Krebs", "futebol"]
QUESTION = "Which gene is mutated in cystic fibrosis?"


def load_config() -> str:
	load_dotenv()
	return os.getenv("OLLAMA_HOST")

def embed(texts: list[str], host: str) -> np.ndarray:
	if not texts:
		raise ValueError("embed() received an empty list")

	response = requests.post(
		f"{host}/api/embed",
		json={'model': MODEL, "input": texts},
		timeout=60,
	)
	response.raise_for_status()
	vectors = response.json()["embeddings"]
	return np.array(vectors, dtype=np.float32)

def cosino(a: np.ndarray, b: np.ndarray) -> float:
	norma_a = np.linalg.norm(a) # sqrt((x * x) + (y * y)) if its 2D
	norma_b = np.linalg.norm(b)
	if norma_a == 0 or norma_b == 0:
		raise ValueError("the cosine isn't define for null vector")
	return float(np.dot(a, b) / (norma_a * norma_b))

def resemblance_matrix(vectors: np.ndarray) -> np.ndarray:
	norms = np.linalg.norm(vectors, axis=1, keepdims=True)
	normalizeds = vectors / norms 
	return normalizeds @ normalizeds.T

def print_matrix(matrix: np.ndarray, labels: list[str]) -> None:
	length = max(len(l) for l in labels)
	print(" " * length, *[f"{i:>6}" for i in range(len(labels))])
	for i, label in enumerate(labels):
		values = " ".join(f"{v:6.2f}" for v in matrix[i])
		print(f"{label:>{length} {values}}")

def order_by_resemblance(question: np.ndarray, vectors: np.ndarray, texts: list[str]
                         ) -> list[tuple[float, int]]:
	points = np.array([cosino(question, v) for v in vectors])
	order = np.argsort(points)[::-1]
	return [(float(points[i]), texts[i]) for i in order]
	
if __name__ == "__main__":
	host = load_config()

	vectors = embed(PHRASES, host)
	matrix = resemblance_matrix(vectors)
	assert np.isclose(matrix[0, 1], cosino(vectors[0], vectors[1]))

	question = embed([QUESTION], host)[0]
	print(f"Question: {QUESTION}")
	results = order_by_resemblance(question, vectors, PHRASES)
	for pos, (point, text) in enumerate(results, start=1):
		print(f"{pos}. {point:.3f}  {text}")