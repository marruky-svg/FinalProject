-- Esquema da ingestão (documentação, secção 7.1).
-- Idempotente: pode correr várias vezes sem erro nem perda de dados.
-- As tabelas de embeddings, pedidos e avaliação entram nos módulos seguintes.

CREATE TABLE IF NOT EXISTS article (
    pmid              text        PRIMARY KEY,
    title             text        NOT NULL,
    abstract          text        NOT NULL DEFAULT '',
    abstract_sections jsonb       NOT NULL DEFAULT '[]',
    journal           text        NOT NULL DEFAULT '',
    year              integer,
    mesh              text[]      NOT NULL DEFAULT '{}',
    origin            text        NOT NULL CHECK (origin IN ('gold', 'distractor', 'test')),
    fetched_at        timestamptz NOT NULL DEFAULT now()
);

-- Para os filtros por ano (RF-06) e por termos MeSH (RF-07).
CREATE INDEX IF NOT EXISTS article_year_idx ON article (year);
CREATE INDEX IF NOT EXISTS article_mesh_idx ON article USING GIN (mesh);

CREATE TABLE IF NOT EXISTS chunk (
    id            bigserial PRIMARY KEY,
    pmid          text      NOT NULL REFERENCES article (pmid) ON DELETE CASCADE,
    unit          text      NOT NULL CHECK (unit IN ('full', 'section')),
    ordinal       integer   NOT NULL,
    section_label text,
    content       text      NOT NULL,
    tsv           tsvector  GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (pmid, unit, ordinal)
);

CREATE INDEX IF NOT EXISTS chunk_tsv_idx ON chunk USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunk_unit_idx ON chunk (unit);
