"""Cliente de embeddings do Ollama (endpoint /api/embed).

Um pedido leva uma lista de textos e devolve um vetor por texto, pela mesma
ordem. Este cliente acrescenta o que o pedido simples das experiências não
tinha:

- verificação da resposta: tantos vetores quantos textos, todos com a mesma
  dimensão;
- normalização: cada vetor fica com norma 1, por isso o produto interno passa
  a ser igual ao cosseno (o BGE-M3 já vem normalizado; isto protege contra um
  modelo que não venha);
- sem corte silencioso: truncate=False faz o Ollama falhar se um texto não
  couber no contexto, em vez de calcular o vetor só do início do texto;
- novas tentativas para erros temporários (rede, 5xx), como no cliente da NCBI.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Callable

import requests

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {500, 502, 503, 504}


class EmbeddingError(RuntimeError):
    """O Ollama recusou o pedido ou devolveu uma resposta inválida."""


class OllamaEmbedder:
    def __init__(
        self,
        host: str,
        model: str,
        num_ctx: int | None = 8192,
        num_batch: int | None = 8192,
        session: requests.Session | None = None,
        timeout: float = 300,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = f"{host.rstrip('/')}/api/embed"
        self.model = model
        # O contexto por omissão do Ollama pode ser menor do que o do modelo
        # (o BGE-M3 aceita 8192 tokens). Pede-se explicitamente.
        self.num_ctx = num_ctx
        # Nos modelos de embedding, o Ollama processa cada texto num único lote
        # interno, por isso o limite real é num_batch e não só num_ctx. Medido
        # com o comando probe (Ollama 0.33.3, bge-m3): sem num_batch, o máximo
        # era 2048 tokens, mesmo com num_ctx 8192; com num_batch 8192, o chunk
        # mais longo do corpus (2532 tokens) cabe inteiro.
        self.num_batch = num_batch
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep = sleep

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            raise ValueError("embed() recebeu uma lista vazia")
        data = self._request(texts)
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            got = len(vectors) if isinstance(vectors, list) else "nenhum"
            raise EmbeddingError(f"pedidos {len(texts)} vetores, recebidos {got}")
        dimension = len(vectors[0])
        if dimension == 0 or any(len(v) != dimension for v in vectors):
            raise EmbeddingError("os vetores devolvidos não têm todos a mesma dimensão")
        return [_normalize(v) for v in vectors]

    def count_tokens(self, text: str) -> int:
        """Tokens do texto, segundo o próprio Ollama (campo prompt_eval_count)."""
        return int(self._request([text]).get("prompt_eval_count", 0))

    def _request(self, texts: list[str]) -> dict:
        options = {}
        if self.num_ctx is not None:
            options["num_ctx"] = self.num_ctx
        if self.num_batch is not None:
            options["num_batch"] = self.num_batch
        payload = {"model": self.model, "input": texts, "truncate": False}
        if options:
            payload["options"] = options
        return self._post(payload)

    def dimension(self) -> int:
        """Descobre a dimensão do modelo com um pedido de um texto."""
        return len(self.embed(["dimension probe"])[0])

    def _post(self, payload: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(self.url, json=payload, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as error:
                if attempt == self.max_retries:
                    raise EmbeddingError(f"Ollama inacessível em {self.url}: {error}") from error
                reason = type(error).__name__
            else:
                if response.status_code == 200:
                    return response.json()
                if response.status_code not in RETRYABLE_STATUS or attempt == self.max_retries:
                    raise EmbeddingError(f"Ollama respondeu {response.status_code}: {_error_text(response)}")
                reason = f"HTTP {response.status_code}"
            wait = 2**attempt
            log.warning("pedido ao Ollama falhou (%s); nova tentativa em %d s", reason, wait)
            self.sleep(wait)
        raise AssertionError("inalcançável")


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise EmbeddingError("o modelo devolveu um vetor nulo")
    return [x / norm for x in vector]


def _error_text(response) -> str:
    try:
        return str(response.json().get("error", ""))[:300]
    except ValueError:
        return response.text[:300]
