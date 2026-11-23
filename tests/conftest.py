"""Ligação partilhada pelos testes de integração com o PostgreSQL real (o do Docker).

Cada teste corre num esquema temporário, dentro de uma transação que nunca é
confirmada: o rollback final apaga o esquema e tudo o que lá foi criado, por
isso os dados do projeto nunca são tocados. Se a base de dados não estiver
acessível, os testes são saltados.

O search_path inclui public depois do esquema temporário só para encontrar o
tipo vector, se a extensão já tiver sido criada em public. As tabelas são
sempre criadas e lidas no esquema temporário, que vem primeiro.
"""

import os
import uuid

import pytest


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    from dotenv import load_dotenv

    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL não definido")
    try:
        connection = psycopg.connect(url, connect_timeout=3)
    except psycopg.OperationalError as error:
        pytest.skip(f"base de dados inacessível: {error}")

    schema = f"test_{uuid.uuid4().hex[:8]}"
    connection.execute(f'CREATE SCHEMA "{schema}"')
    connection.execute(f'SET LOCAL search_path TO "{schema}", public')
    try:
        from helixrag.db.repository import apply_schema

        apply_schema(connection)
        yield connection
    finally:
        connection.rollback()
        connection.close()
