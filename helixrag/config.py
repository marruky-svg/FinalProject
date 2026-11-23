"""Configuração lida das variáveis de ambiente (preenchidas a partir do .env).

O .env é carregado só pelos pontos de entrada (a linha de comandos e, mais
tarde, a API), e não ao importar este módulo: importar código não deve ter
efeitos secundários, e os testes podem assim definir as variáveis que quiserem.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Falta uma variável de configuração obrigatória."""


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    ncbi_api_key: str | None
    ncbi_email: str | None
    ollama_host: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            ncbi_api_key=os.getenv("NCBI_API_KEY") or None,
            ncbi_email=os.getenv("NCBI_EMAIL") or None,
            ollama_host=(os.getenv("OLLAMA_HOST") or "http://localhost:11434").rstrip("/"),
        )

    def require_database_url(self) -> str:
        """A base de dados nunca se adivinha: sem DATABASE_URL, falha logo."""
        if not self.database_url:
            raise ConfigError("DATABASE_URL não está definido no .env")
        return self.database_url
