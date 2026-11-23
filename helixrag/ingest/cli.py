"""Linha de comandos da ingestão (UC-11 e parte do UC-12).

Uso, a partir da raiz do projeto:

    python -m helixrag.ingest.cli search "cystic fibrosis[mesh] AND CFTR" --retmax 200 --out corpus/test_pmids.txt
    python -m helixrag.ingest.cli bioasq-pmids data/bioasq/training.json --out corpus/gold_pmids.txt
    python -m helixrag.ingest.cli bioasq-split data/bioasq/training.json --dev 500 --test 500
    python -m helixrag.ingest.cli fetch corpus/test_pmids.txt
    python -m helixrag.ingest.cli load corpus/test_pmids.txt --origin test --unit full
    python -m helixrag.ingest.cli stats

O fluxo tem três passos separados: obter listas de PMIDs, descarregar para
data/pubmed/articles.jsonl, e carregar para a base de dados.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from helixrag.config import ConfigError, Settings
from helixrag.ingest.bioasq import gold_pmids, load_questions
from helixrag.ingest.chunking import UNITS, chunk_article
from helixrag.ingest.ncbi_client import NCBIClient, NCBIError
from helixrag.ingest.pmid_lists import read_pmids, write_pmids
from helixrag.ingest.pubmed_xml import parse_articles
from helixrag.ingest.splits import eligible, file_sha256, split_questions, write_ids
from helixrag.ingest.store import ArticleStore

log = logging.getLogger("helixrag.ingest")

DEFAULT_STORE = Path("data/pubmed/articles.jsonl")
MISSING_FILE = Path("data/pubmed/missing.txt")


def cmd_search(args: argparse.Namespace, settings: Settings) -> int:
    client = NCBIClient(api_key=settings.ncbi_api_key, email=settings.ncbi_email)
    pmids = client.esearch(args.term, retmax=args.retmax)
    write_pmids(args.out, pmids, header=f"esearch: {args.term}\nretmax: {args.retmax}")
    log.info("%d PMIDs gravados em %s", len(pmids), args.out)
    return 0


def cmd_bioasq_pmids(args: argparse.Namespace, settings: Settings) -> int:
    questions = load_questions(args.bioasq_json)
    pmids = gold_pmids(questions)
    write_pmids(args.out, pmids, header=f"documentos de ouro de {Path(args.bioasq_json).name}")
    log.info("%d perguntas, %d PMIDs de ouro gravados em %s", len(questions), len(pmids), args.out)
    return 0


def cmd_bioasq_split(args: argparse.Namespace, settings: Settings) -> int:
    questions = load_questions(args.bioasq_json)
    splits = split_questions(questions, [("dev", args.dev), ("test", args.test)], seed=args.seed)
    by_id = {q.id: q for q in questions}
    source = Path(args.bioasq_json)
    for name, ids in splits.items():
        types: dict[str, int] = {}
        for qid in ids:
            types[by_id[qid].type] = types.get(by_id[qid].type, 0) + 1
        header = (
            f"conjunto {name} de {source.name} (sha256 {file_sha256(source)[:12]})\n"
            f"semente: {args.seed}\n"
            f"{len(ids)} perguntas: " + ", ".join(f"{t} {n}" for t, n in sorted(types.items()))
        )
        out = args.out_dir / f"{name}_questions.txt"
        write_ids(out, ids, header=header)
        log.info("%s: %d perguntas gravadas em %s (%s)", name, len(ids), out,
                 ", ".join(f"{t} {n}" for t, n in sorted(types.items())))
    log.info("%d de %d perguntas elegíveis", len(eligible(questions)), len(questions))
    return 0


def cmd_fetch(args: argparse.Namespace, settings: Settings) -> int:
    store = ArticleStore(args.store)
    wanted = [p for path in args.pmid_files for p in read_pmids(path)]
    wanted = list(dict.fromkeys(wanted))
    already = store.pmids()
    todo = [p for p in wanted if p not in already]
    log.info("%d PMIDs pedidos, %d já guardados, %d por descarregar", len(wanted), len(wanted) - len(todo), len(todo))
    if not todo:
        return 0

    client = NCBIClient(api_key=settings.ncbi_api_key, email=settings.ncbi_email)
    missing: list[str] = []
    done = 0
    for batch, xml in client.efetch_batches(todo, batch_size=args.batch_size):
        articles = parse_articles(xml)
        store.append(articles)
        returned = {a.pmid for a in articles}
        missing += [p for p in batch if p not in returned]
        done += len(batch)
        log.info("progresso: %d/%d", done, len(todo))

    if missing:
        MISSING_FILE.parent.mkdir(parents=True, exist_ok=True)
        MISSING_FILE.write_text("\n".join(missing) + "\n", encoding="utf-8")
        log.warning("%d PMIDs sem registo (ver %s)", len(missing), MISSING_FILE)
    return 0


def cmd_load(args: argparse.Namespace, settings: Settings) -> int:
    import psycopg  # importado aqui: os outros comandos não precisam da base de dados

    from helixrag.db.repository import apply_schema, replace_chunks, upsert_articles

    wanted = set(read_pmids(args.pmid_file))
    articles = [a for a in ArticleStore(args.store) if a.pmid in wanted]
    absent = len(wanted) - len({a.pmid for a in articles})
    if absent:
        log.warning(
            "%d PMIDs da lista não estão no store: corre o fetch, ou vê %s se o fetch já correu",
            absent, MISSING_FILE,
        )

    chunks = [c for a in articles for c in chunk_article(a, args.unit)]
    with psycopg.connect(settings.require_database_url()) as conn:
        apply_schema(conn)
        upsert_articles(conn, articles, origin=args.origin)
        replace_chunks(conn, chunks, unit=args.unit)
    log.info("%d artigos e %d chunks (%s) carregados", len(articles), len(chunks), args.unit)
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    import psycopg

    from helixrag.db.repository import apply_schema, corpus_stats

    with psycopg.connect(settings.require_database_url()) as conn:
        apply_schema(conn)
        stats = corpus_stats(conn)
    print(f"artigos por origem: {stats['articles']}")
    print(f"artigos sem resumo: {stats['without_abstract']}")
    print(f"chunks por unidade: {stats['chunks']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m helixrag.ingest.cli", description="Ingestão do HelixRAG")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="pesquisa no PubMed e grava a lista de PMIDs")
    p.add_argument("term")
    p.add_argument("--retmax", type=int, default=200)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("bioasq-pmids", help="extrai os PMIDs de ouro de um ficheiro BioASQ")
    p.add_argument("bioasq_json", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_bioasq_pmids)

    p = sub.add_parser("bioasq-split", help="divide as perguntas em dev e teste (estratificado, reprodutível)")
    p.add_argument("bioasq_json", type=Path)
    p.add_argument("--dev", type=int, default=500)
    p.add_argument("--test", type=int, default=500)
    p.add_argument("--seed", default="helixrag-v1")
    p.add_argument("--out-dir", type=Path, default=Path("corpus"))
    p.set_defaults(func=cmd_bioasq_split)

    p = sub.add_parser("fetch", help="descarrega os registos para o store local (retoma se interrompido)")
    p.add_argument("pmid_files", type=Path, nargs="+")
    p.add_argument("--store", type=Path, default=DEFAULT_STORE)
    p.add_argument("--batch-size", type=int, default=200)
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("load", help="carrega artigos e chunks do store para a base de dados")
    p.add_argument("pmid_file", type=Path)
    p.add_argument("--origin", choices=("gold", "distractor", "test"), required=True)
    p.add_argument("--unit", choices=UNITS, default="full")
    p.add_argument("--store", type=Path, default=DEFAULT_STORE)
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("stats", help="mostra os totais do corpus na base de dados")
    p.set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, Settings.from_env())
    except (ConfigError, NCBIError, FileNotFoundError, ValueError) as error:
        # Erros esperados: uma mensagem clara em vez de um traceback.
        log.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
