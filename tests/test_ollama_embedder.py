"""Testes do cliente do Ollama, sem rede: a sessão HTTP e o sleep são falsos."""

import math

import pytest
import requests

from helixrag.index.cli import format_duration
from helixrag.index.ollama_embedder import EmbeddingError, OllamaEmbedder


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("sem JSON")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append((url, json))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make(responses):
    sleeps = []
    session = FakeSession(responses)
    embedder = OllamaEmbedder("http://ollama:11434/", "bge-m3", session=session, sleep=sleeps.append)
    return embedder, session, sleeps


def ok(*vectors):
    return FakeResponse(payload={"embeddings": [list(v) for v in vectors]})


def test_sends_batch_without_silent_truncation():
    embedder, session, _ = make([ok([1, 0], [0, 1])])
    embedder.embed(["a", "b"])
    url, payload = session.calls[0]
    assert url == "http://ollama:11434/api/embed"
    assert payload["input"] == ["a", "b"]
    assert payload["truncate"] is False
    assert payload["options"] == {"num_ctx": 8192, "num_batch": 8192}


def test_vectors_are_normalized():
    embedder, _, _ = make([ok([3, 4])])
    (vector,) = embedder.embed(["a"])
    assert vector == pytest.approx([0.6, 0.8])
    assert math.isclose(sum(x * x for x in vector), 1.0)


def test_rejects_wrong_count_mixed_dimensions_and_zero_vector():
    with pytest.raises(EmbeddingError, match="pedidos 2 vetores, recebidos 1"):
        make([ok([1, 0])])[0].embed(["a", "b"])
    with pytest.raises(EmbeddingError, match="mesma dimensão"):
        make([ok([1, 0], [1, 0, 0])])[0].embed(["a", "b"])
    with pytest.raises(EmbeddingError, match="vetor nulo"):
        make([ok([0, 0])])[0].embed(["a"])
    with pytest.raises(ValueError):
        make([])[0].embed([])


def test_retries_temporary_errors_then_succeeds():
    embedder, session, sleeps = make([requests.ConnectionError("x"), FakeResponse(503), ok([1, 0])])
    assert embedder.embed(["a"]) == [[1.0, 0.0]]
    assert sleeps == [1, 2]
    assert len(session.calls) == 3


def test_client_error_is_not_retried_and_shows_ollama_message():
    embedder, session, sleeps = make([FakeResponse(400, payload={"error": "input length exceeds context"})])
    with pytest.raises(EmbeddingError, match="400: input length exceeds context"):
        embedder.embed(["a"])
    assert sleeps == [] and len(session.calls) == 1


def test_gives_up_after_max_retries():
    embedder, session, sleeps = make([FakeResponse(500)] * 4)
    with pytest.raises(EmbeddingError, match="500"):
        embedder.embed(["a"])
    assert sleeps == [1, 2, 4]


def test_dimension_probe():
    embedder, _, _ = make([ok([1, 0, 0])])
    assert embedder.dimension() == 3


def test_format_duration():
    assert format_duration(42.4) == "42 s"
    assert format_duration(200) == "3 min 20 s"
    assert format_duration(3725) == "1 h 02 min"


def test_options_are_only_sent_when_set():
    session = FakeSession([ok([1, 0]), ok([1, 0])])
    OllamaEmbedder("h", "m", num_ctx=None, num_batch=None, session=session).embed(["a"])
    OllamaEmbedder("h", "m", num_ctx=8192, num_batch=8192, session=session).embed(["a"])
    assert "options" not in session.calls[0][1]
    assert session.calls[1][1]["options"] == {"num_ctx": 8192, "num_batch": 8192}


def test_count_tokens_reads_prompt_eval_count():
    session = FakeSession([FakeResponse(payload={"embeddings": [[1, 0]], "prompt_eval_count": 7})])
    assert OllamaEmbedder("h", "m", session=session).count_tokens("abc") == 7
