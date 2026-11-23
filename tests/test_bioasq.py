from pathlib import Path

from helixrag.ingest.bioasq import extract_pmid, gold_pmids, load_questions

FIXTURE = Path(__file__).parent / "fixtures" / "bioasq_sample.json"


def test_extract_pmid_from_url_variants():
    assert extract_pmid("http://www.ncbi.nlm.nih.gov/pubmed/23515712") == "23515712"
    assert extract_pmid("https://pubmed.ncbi.nlm.nih.gov/23515712/") == "23515712"
    assert extract_pmid("not-a-pubmed-url") is None


def test_load_questions_dedupes_and_keeps_order():
    q1, q2, q3 = load_questions(FIXTURE)
    assert q1.gold_pmids == ("10000001", "10000005")
    assert q1.ideal_answer == "The CFTR gene is mutated in cystic fibrosis."
    assert q2.ideal_answer == "Yes, the Krebs cycle produces NADH."
    assert q2.exact_answer == "yes"
    assert q3.gold_pmids == ()
    assert q3.ideal_answer is None


def test_gold_pmids_are_unique_and_numerically_sorted():
    assert gold_pmids(load_questions(FIXTURE)) == ["10000001", "10000002", "10000005"]


def test_duplicate_marker_is_read(tmp_path):
    path = tmp_path / "b.json"
    path.write_text('{"questions": [{"id": "x", "type": "list", "body": "?", "documents": [],'
                    ' "duplicate_tmp": {"type": 1, "from": "version 11", "id": ["y"]}}]}')
    (question,) = load_questions(path)
    assert question.duplicate_of == ("y",)
