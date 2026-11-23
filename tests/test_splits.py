import pytest

from helixrag.ingest.models import BioASQQuestion
from helixrag.ingest.splits import _proportional_quotas, eligible, read_ids, split_questions, write_ids


def q(qid, qtype, gold=("1",), duplicate_of=()):
    return BioASQQuestion(qid, qtype, f"pergunta {qid}", tuple(gold), None, None, duplicate_of)


def make_pool():
    # 50 factoid, 30 yesno, 20 summary: proporção 5:3:2
    pool = [q(f"f{i}", "factoid") for i in range(50)]
    pool += [q(f"y{i}", "yesno") for i in range(30)]
    pool += [q(f"s{i}", "summary") for i in range(20)]
    return pool


def test_sizes_are_exact_and_sets_are_disjoint():
    splits = split_questions(make_pool(), [("dev", 20), ("test", 30)], seed="s")
    assert len(splits["dev"]) == 20
    assert len(splits["test"]) == 30
    assert not set(splits["dev"]) & set(splits["test"])


def test_split_is_stratified():
    splits = split_questions(make_pool(), [("dev", 20)], seed="s")
    prefixes = [qid[0] for qid in splits["dev"]]
    assert (prefixes.count("f"), prefixes.count("y"), prefixes.count("s")) == (10, 6, 4)


def test_same_seed_same_split_regardless_of_input_order():
    pool = make_pool()
    a = split_questions(pool, [("dev", 20)], seed="s")
    b = split_questions(list(reversed(pool)), [("dev", 20)], seed="s")
    assert a == b


def test_different_seed_gives_different_split():
    pool = make_pool()
    assert split_questions(pool, [("dev", 20)], seed="a") != split_questions(pool, [("dev", 20)], seed="b")


def test_excludes_duplicates_and_questions_without_gold():
    pool = [q("ok", "yesno"), q("dup", "yesno", duplicate_of=("ok",)), q("nogold", "yesno", gold=())]
    assert [x.id for x in eligible(pool)] == ["ok"]
    with pytest.raises(ValueError, match="só há 1"):
        split_questions(pool, [("dev", 2)], seed="s")


def test_quotas_always_sum_to_size():
    assert _proportional_quotas({"a": 50, "b": 30, "c": 20}, 10) == {"a": 5, "b": 3, "c": 2}
    assert _proportional_quotas({"a": 1, "b": 1, "c": 1}, 10) == {"a": 4, "b": 3, "c": 3}
    assert sum(_proportional_quotas({"a": 1695, "b": 1541, "c": 1363, "d": 1130}, 500).values()) == 500


def test_ids_roundtrip(tmp_path):
    path = tmp_path / "dev.txt"
    write_ids(path, ["b", "a"], header="semente: s\n2 perguntas")
    assert path.read_text().startswith("# semente: s\n# 2 perguntas\n")
    assert read_ids(path) == ["b", "a"]
