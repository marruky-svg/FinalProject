"""Testes do cliente da NCBI, sem rede: a sessão HTTP, o sleep e o relógio são falsos."""

import pytest
import requests

from helixrag.ingest.ncbi_client import NCBIClient, NCBIError


class FakeResponse:
    def __init__(self, status_code=200, content=b"<xml/>", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Devolve as respostas pela ordem dada e regista cada pedido."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data, timeout):
        self.calls.append((url, data))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(responses, api_key=None):
    clock = FakeClock()
    session = FakeSession(responses)
    client = NCBIClient(api_key=api_key, email="a@b.pt", session=session, sleep=clock.sleep, clock=clock.time)
    return client, session, clock


def test_esearch_returns_pmids_and_sends_identification():
    client, session, _ = make_client([FakeResponse(payload={"esearchresult": {"idlist": ["1", "2"]}})], api_key="K")
    assert client.esearch("cftr", retmax=2) == ["1", "2"]
    url, data = session.calls[0]
    assert url.endswith("/esearch.fcgi")
    assert data["tool"] == "helixrag" and data["email"] == "a@b.pt" and data["api_key"] == "K"


def test_api_key_is_omitted_when_absent():
    client, session, _ = make_client([FakeResponse(payload={"esearchresult": {"idlist": []}})])
    client.esearch("x")
    assert "api_key" not in session.calls[0][1]


def test_rate_limit_spaces_requests():
    client, _, clock = make_client([FakeResponse(), FakeResponse()])
    client.efetch(["1"])
    client.efetch(["2"])
    assert clock.sleeps == [pytest.approx(0.34)]


def test_rate_limit_is_faster_with_api_key():
    client, _, clock = make_client([FakeResponse(), FakeResponse()], api_key="K")
    client.efetch(["1"])
    client.efetch(["2"])
    assert clock.sleeps == [pytest.approx(0.11)]


def test_retries_transient_errors_with_backoff():
    responses = [FakeResponse(429), requests.ConnectionError("boom"), FakeResponse(503), FakeResponse(content=b"ok")]
    client, session, clock = make_client(responses)
    assert client.efetch(["1"]) == b"ok"
    assert len(session.calls) == 4
    backoffs = [s for s in clock.sleeps if s >= 1]
    assert backoffs == [1, 2, 4]


def test_gives_up_after_max_retries():
    client, session, _ = make_client([FakeResponse(500)] * 5)
    with pytest.raises(NCBIError, match="5 tentativas"):
        client.efetch(["1"])
    assert len(session.calls) == 5


def test_client_errors_are_not_retried():
    client, session, _ = make_client([FakeResponse(400)])
    with pytest.raises(NCBIError, match="400"):
        client.efetch(["1"])
    assert len(session.calls) == 1


def test_efetch_batches_split_the_list():
    client, session, _ = make_client([FakeResponse(content=b"a"), FakeResponse(content=b"b")])
    batches = list(client.efetch_batches(["1", "2", "3"], batch_size=2))
    assert batches == [(["1", "2"], b"a"), (["3"], b"b")]
    assert [call[1]["id"] for call in session.calls] == ["1,2", "3"]


def test_invalid_arguments():
    client, _, _ = make_client([])
    with pytest.raises(ValueError):
        client.efetch([])
    with pytest.raises(ValueError):
        client.esearch("x", retmax=0)
