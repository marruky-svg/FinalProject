"""Cliente das E-utilities da NCBI (esearch e efetch).

Responsabilidades:
- respeitar o limite de pedidos: 3 por segundo sem chave, 10 com chave;
- repetir os pedidos que falham por motivos temporários (erros de rede,
  429 e 5xx), com espera crescente (RNF-11);
- identificar a ferramenta com os parâmetros tool e email, como a NCBI pede.

O sleep e o relógio são injetáveis, para os testes correrem sem esperas reais.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Iterator

import requests

log = logging.getLogger(__name__)

EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ESEARCH_RESULTS = 10_000  # limite do esearch no PubMed


class NCBIError(RuntimeError):
    """Um pedido à NCBI falhou mesmo depois de todas as tentativas."""


class NCBIClient:
    def __init__(
        self,
        api_key: str | None = None,
        email: str | None = None,
        tool: str = "helixrag",
        session: requests.Session | None = None,
        max_retries: int = 4,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api_key = api_key or None
        self.email = email or None
        self.tool = tool
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        # Intervalo mínimo entre pedidos, com uma pequena margem de segurança.
        self.min_interval = 0.11 if self.api_key else 0.34
        self._last_request: float | None = None

    def esearch(self, term: str, retmax: int = 1000) -> list[str]:
        """Pesquisa no PubMed e devolve os PMIDs, por relevância."""
        if not 0 < retmax <= MAX_ESEARCH_RESULTS:
            raise ValueError(f"retmax tem de estar entre 1 e {MAX_ESEARCH_RESULTS}")
        response = self._request(
            "esearch.fcgi",
            {"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json", "sort": "relevance"},
        )
        return list(response.json()["esearchresult"]["idlist"])

    def efetch(self, pmids: list[str]) -> bytes:
        """Descarrega os registos completos em XML."""
        if not pmids:
            raise ValueError("efetch() recebeu uma lista vazia")
        response = self._request(
            "efetch.fcgi",
            {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
        )
        return response.content

    def efetch_batches(self, pmids: list[str], batch_size: int = 200) -> Iterator[tuple[list[str], bytes]]:
        """Descarrega em lotes e devolve (lote, xml) um de cada vez.

        Um gerador permite ao chamador guardar cada lote assim que chega: se o
        processo for interrompido, o que já foi guardado não se perde.
        """
        for start in range(0, len(pmids), batch_size):
            batch = pmids[start : start + batch_size]
            yield batch, self.efetch(batch)

    def _request(self, endpoint: str, params: dict) -> requests.Response:
        params = {**params, "tool": self.tool}
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key

        last_error = "sem resposta"
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                # POST em vez de GET: listas longas de PMIDs não cabem num URL.
                response = self.session.post(f"{EUTILS_URL}/{endpoint}", data=params, timeout=self.timeout)
            except requests.RequestException as error:
                last_error = f"erro de rede: {error}"
            else:
                if response.status_code == 200:
                    return response
                if response.status_code not in RETRYABLE_STATUS:
                    raise NCBIError(f"{endpoint}: HTTP {response.status_code} (não repetível)")
                last_error = f"HTTP {response.status_code}"

            if attempt < self.max_retries:
                wait = 2**attempt  # 1, 2, 4, 8 segundos
                log.warning("%s: %s; nova tentativa em %ds", endpoint, last_error, wait)
                self._sleep(wait)

        raise NCBIError(f"{endpoint}: falhou após {self.max_retries + 1} tentativas ({last_error})")

    def _throttle(self) -> None:
        """Espera o necessário para respeitar o intervalo mínimo entre pedidos."""
        now = self._clock()
        if self._last_request is not None:
            remaining = self.min_interval - (now - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last_request = now
