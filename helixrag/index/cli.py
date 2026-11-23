"""Linha de comandos da indexação (UC-12, RF-24, RF-25).

Uso, a partir da raiz do projeto:

    python -m helixrag.index.cli probe
    python -m helixrag.index.cli bench --n 500 --batch-sizes 1 16 64
    python -m helixrag.index.cli embed --unit full --batch-size 32
    python -m helixrag.index.cli build-index --unit full
    python -m helixrag.index.cli status

O embed pode ser interrompido com Ctrl+C: cada lote é confirmado na base de
dados assim que chega, e a próxima execução continua nos chunks pendentes.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import psycopg
from dotenv import load_dotenv

from helixrag.config import ConfigError, Settings
from helixrag.index.ollama_embedder import EmbeddingError, OllamaEmbedder

log = logging.getLogger("helixrag.index")

DEFAULT_MODEL = "bge-m3"
UNIT_CHOICES = ("full", "section", "all")


def _units(choice: str) -> list[str]:
    return ["full", "section"] if choice == "all" else [choice]


def format_duration(seconds: float) -> str:
    """3725 → '1 h 02 min'; 200 → '3 min 20 s'; 42.4 → '42 s'."""
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def _connect(settings: Settings):
    return psycopg.connect(settings.require_database_url())


def _embedder(args: argparse.Namespace, settings: Settings) -> OllamaEmbedder:
    # 0 = não enviar a opção e usar o valor do Ollama
    return OllamaEmbedder(
        settings.ollama_host, args.model, num_ctx=args.num_ctx or None, num_batch=args.num_batch or None
    )


def _max_prefix(embedder: OllamaEmbedder, text: str) -> int:
    """Maior número de caracteres do início do texto que o Ollama aceita (pesquisa binária)."""
    low, high = 0, len(text)  # low cabe sempre (texto vazio); procura-se o maior que cabe
    while low < high:
        middle = (low + high + 1) // 2
        try:
            embedder.embed([text[:middle]])
            low = middle
        except EmbeddingError:
            high = middle - 1
    return low


def cmd_probe(args: argparse.Namespace, settings: Settings) -> int:
    """Mede quantos tokens o Ollama aceita num texto, com várias configurações.

    Usa o chunk mais longo do corpus. Se não couber inteiro, encontra por
    pesquisa binária o maior pedaço inicial que cabe e conta-lhe os tokens.
    """
    with _connect(settings) as conn:
        longest_id, longest_text = conn.execute(
            "SELECT id, content FROM chunk ORDER BY length(content) DESC LIMIT 1"
        ).fetchone()
        lengths = [row[0] for row in conn.execute("SELECT length(content) FROM chunk").fetchall()]

    configs = [
        ("omissão do Ollama", None, None),
        ("num_ctx 8192", 8192, None),
        ("num_ctx 8192, num_batch 8192", 8192, 8192),
    ]
    print(f"chunk mais longo: id {longest_id}, {len(longest_text)} caracteres\n")
    print(f"{'configuração':<30} {'resultado':<40} {'chunks que não cabem (estimativa)'}")
    for label, num_ctx, num_batch in configs:
        embedder = OllamaEmbedder(settings.ollama_host, args.model, num_ctx=num_ctx, num_batch=num_batch)
        try:
            embedder.embed(["warm up"])
            limit = _max_prefix(embedder, longest_text)
        except EmbeddingError as error:
            print(f"{label:<30} erro: {error}")
            continue
        if limit == len(longest_text):
            tokens = embedder.count_tokens(longest_text)
            print(f"{label:<30} {'cabe inteiro (' + str(tokens) + ' tokens)':<40} 0")
        else:
            tokens = embedder.count_tokens(longest_text[:limit])
            too_long = sum(1 for n in lengths if n > limit)
            result = f"máximo {limit} caracteres ≈ {tokens} tokens"
            print(f"{label:<30} {result:<40} {too_long} de {len(lengths)}")
    return 0


def cmd_bench(args: argparse.Namespace, settings: Settings) -> int:
    """Mede chunks por segundo com vários tamanhos de lote, antes de lançar o corpus inteiro."""
    embedder = _embedder(args, settings)
    with _connect(settings) as conn:
        # Amostra pseudoaleatória mas fixa: ordenar por md5(id) mistura artigos
        # antigos e recentes (curtos e longos) e dá sempre a mesma amostra.
        texts = [
            row[0]
            for row in conn.execute(
                "SELECT content FROM chunk WHERE unit = %s ORDER BY md5(id::text) LIMIT %s",
                (args.unit, args.n),
            ).fetchall()
        ]
        longest_id, longest_len, longest_text = conn.execute(
            "SELECT id, length(content), content FROM chunk ORDER BY length(content) DESC LIMIT 1"
        ).fetchone()
        total = conn.execute("SELECT count(*) FROM chunk").fetchone()[0]
    if not texts:
        log.error("não há chunks da unidade %s; corre primeiro o load da ingestão", args.unit)
        return 1

    log.info("a aquecer o modelo (o primeiro pedido carrega-o em memória)")
    started = time.perf_counter()
    embedder.embed(["warm up"])
    log.info("modelo pronto em %.1f s", time.perf_counter() - started)

    try:
        embedder.embed([longest_text])
        log.info("o chunk mais longo (id %d, %d caracteres) cabe no contexto", longest_id, longest_len)
    except EmbeddingError as error:
        log.error("o chunk mais longo (id %d, %d caracteres) falhou: %s", longest_id, longest_len, error)

    print(f"\n{len(texts)} chunks ({args.unit}), {total} chunks no corpus")
    print(f"{'lote':>6} {'chunks/s':>10} {'tempo do corpus':>18}")
    for batch_size in args.batch_sizes:
        started = time.perf_counter()
        try:
            for start in range(0, len(texts), batch_size):
                embedder.embed(texts[start : start + batch_size])
        except EmbeddingError as error:
            print(f"{batch_size:>6} falhou: {error}")
            continue
        rate = len(texts) / (time.perf_counter() - started)
        print(f"{batch_size:>6} {rate:>10.1f} {format_duration(total / rate):>18}")
    return 0


def cmd_embed(args: argparse.Namespace, settings: Settings) -> int:
    from helixrag.db.embeddings import count_pending, ensure_model, index_exists, pending_chunks, store_embeddings

    units = _units(args.unit)
    embedder = _embedder(args, settings)
    with _connect(settings) as conn:
        table = ensure_model(conn, args.model, embedder.dimension())
        conn.commit()
        total = count_pending(conn, table, units)
        log.info("%s: %d chunks pendentes (%s)", args.model, total, ", ".join(units))
        if not total:
            return 0
        for unit in units:
            if index_exists(conn, table, unit):
                log.warning(
                    "já existe índice HNSW para %s: cada inserção atualiza o grafo e fica mais lenta", unit
                )

        done, after_id = 0, 0
        started = last_report = time.perf_counter()
        try:
            while True:
                batch = pending_chunks(conn, table, units, limit=args.batch_size, after_id=after_id)
                if not batch:
                    break
                vectors = embedder.embed([c.content for c in batch])
                store_embeddings(conn, table, batch, vectors)
                conn.commit()  # um lote confirmado nunca se perde, mesmo com Ctrl+C a seguir
                done += len(batch)
                after_id = batch[-1].id
                now = time.perf_counter()
                if now - last_report >= args.report_every or done == total:
                    rate = done / (now - started)
                    eta = (total - done) / rate if rate else 0
                    log.info(
                        "progresso: %d/%d (%.1f chunks/s, falta %s)", done, total, rate, format_duration(eta)
                    )
                    last_report = now
        except KeyboardInterrupt:
            conn.rollback()
            log.warning("interrompido: %d chunks guardados; volta a correr o embed para continuar", done)
            return 130
    log.info("%d chunks com embedding em %s", done, format_duration(time.perf_counter() - started))
    return 0


def cmd_build_index(args: argparse.Namespace, settings: Settings) -> int:
    from helixrag.db.embeddings import build_hnsw_index, count_pending, registered_models, table_name_for

    with _connect(settings) as conn:
        if args.model not in {name for name, _, _ in registered_models(conn)}:
            log.error("o modelo %s não tem embeddings; corre primeiro o embed", args.model)
            return 1
        for unit in _units(args.unit):
            pending = count_pending(conn, table_name_for(args.model), [unit])
            if pending:
                log.warning("%s: %d chunks ainda sem embedding ficam fora do índice até o embed terminar", unit, pending)
            log.info("a construir o índice HNSW de %s (m=%d, ef_construction=%d)", unit, args.m, args.ef_construction)
            started = time.perf_counter()
            name = build_hnsw_index(
                conn, args.model, unit, m=args.m, ef_construction=args.ef_construction,
                maintenance_work_mem=args.maintenance_work_mem,
            )
            conn.commit()
            log.info("índice %s pronto em %s", name, format_duration(time.perf_counter() - started))
    return 0


def cmd_status(args: argparse.Namespace, settings: Settings) -> int:
    from helixrag.db.embeddings import embedding_stats, registered_models

    with _connect(settings) as conn:
        models = registered_models(conn)
        if not models:
            print("nenhum modelo de embeddings registado")
            return 0
        for name, dimension, table in models:
            print(f"{name} (dimensão {dimension}, tabela {table})")
            for unit, s in embedding_stats(conn, name).items():
                index = "com índice HNSW" if s["index"] else "sem índice"
                print(f"  {unit:<8} {s['embedded']:>7}/{s['chunks']:<7} pendentes {s['pending']:>7}  {index}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m helixrag.index.cli", description="Indexação do HelixRAG")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("probe", help="mede quantos tokens o Ollama aceita por texto, com várias configurações")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("bench", help="mede a velocidade dos embeddings com vários tamanhos de lote")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--unit", choices=("full", "section"), default="full")
    p.add_argument("--n", type=int, default=500)
    p.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 16, 64])
    p.add_argument("--num-ctx", type=int, default=8192, help="0 = usar o valor do Ollama")
    p.add_argument("--num-batch", type=int, default=8192, help="0 = usar o valor do Ollama")
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("embed", help="calcula os embeddings dos chunks pendentes (retoma se interrompido)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--unit", choices=UNIT_CHOICES, default="all")
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--report-every", type=float, default=10.0, help="segundos entre linhas de progresso")
    p.add_argument("--num-ctx", type=int, default=8192, help="0 = usar o valor do Ollama")
    p.add_argument("--num-batch", type=int, default=8192, help="0 = usar o valor do Ollama")
    p.set_defaults(func=cmd_embed)

    p = sub.add_parser("build-index", help="constrói o índice HNSW (depois do embed)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--unit", choices=UNIT_CHOICES, default="all")
    p.add_argument("--m", type=int, default=16)
    p.add_argument("--ef-construction", type=int, default=64)
    p.add_argument("--maintenance-work-mem", default="1GB")
    p.set_defaults(func=cmd_build_index)

    p = sub.add_parser("status", help="mostra quantos chunks têm embedding, por modelo e unidade")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if getattr(args, "batch_size", 1) < 1 or any(b < 1 for b in getattr(args, "batch_sizes", [1])):
        log.error("o tamanho do lote tem de ser pelo menos 1")
        return 2
    try:
        return args.func(args, Settings.from_env())
    except (ConfigError, EmbeddingError, ValueError) as error:
        log.error("%s", error)
        return 1
    except psycopg.Error as error:
        log.error("erro da base de dados: %s", error)
        if "shared memory" in str(error):
            # A construção paralela do HNSW usa memória partilhada, e o Docker
            # dá 64 MB por omissão. Ver shm_size no docker-compose.yml.
            log.error("o contentor tem pouca memória partilhada: confirma shm_size no docker-compose.yml")
        return 1


if __name__ == "__main__":
    sys.exit(main())
