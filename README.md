# LexLaboral

**Assistente de perguntas e respostas sobre o Código do Trabalho português, baseado em RAG (Retrieval-Augmented Generation), com respostas fundamentadas e citação dos artigos.**

Projeto final do curso CET em Tecnologias e Programação de Sistemas de Informação (TPSI) — IEFP Braga.

> ⚠️ **Aviso:** o LexLaboral é um projeto académico. As respostas são informação de apoio e não substituem aconselhamento jurídico. Confirme sempre no texto oficial da lei.

---

## Índice

1. [O problema](#o-problema)
2. [A solução: RAG](#a-solução-rag)
3. [Arquitetura](#arquitetura)
4. [Stack tecnológica](#stack-tecnológica)
5. [Decisões de design](#decisões-de-design)
6. [Avaliação](#avaliação)
7. [Estrutura do projeto](#estrutura-do-projeto)
8. [Como executar](#como-executar)
9. [Planeamento](#planeamento)
10. [Limitações conhecidas](#limitações-conhecidas)
11. [Autor](#autor)

---

## O problema

O Código do Trabalho (Lei n.º 7/2009, de 12 de fevereiro, com as alterações posteriores) regula questões que afetam o dia a dia de qualquer trabalhador: férias, faltas, horários, período experimental, cessação do contrato. O texto é extenso, técnico e sofre alterações frequentes, o que torna difícil encontrar a resposta certa a uma pergunta concreta.

Perguntar diretamente a um LLM genérico não resolve o problema:

- **Conhecimento congelado:** o modelo só conhece a lei na versão que viu durante o treino, que pode já estar desatualizada.
- **Alucinação:** quando não sabe, o modelo produz uma resposta plausível mas inventada, sem dar sinal disso.
- **Sem rastreabilidade:** não indica de que artigo veio a informação, por isso não é possível verificá-la.

## A solução: RAG

O RAG divide o trabalho em duas partes:

1. **Recuperação (retrieval):** um motor de pesquisa encontra, no texto oficial da lei, os excertos relevantes para a pergunta.
2. **Geração:** um LLM redige a resposta **usando apenas esses excertos** e indica os artigos que usou.

O modelo deixa assim de ser uma fonte de conhecimento e passa a redigir respostas a partir de fontes que lhe são entregues. Daqui resulta o princípio central do projeto:

> **A qualidade do sistema depende sobretudo da recuperação.** Se o artigo certo não chegar ao LLM, nenhum prompt salva a resposta. Por isso, a maior parte do esforço de engenharia e de avaliação está no retrieval.

## Arquitetura

O sistema tem duas fases independentes.

### Ingestão (offline, corre uma vez por versão do corpus)

```
Texto oficial (HTML)
        │
        ▼
   Parsing ──► estrutura hierárquica (Livro > Título > Capítulo > Secção > Artigo > n.º)
        │
        ▼
   Chunking estrutural ──► 1 chunk por artigo (artigos longos divididos por n.º)
        │
        ▼
   Embeddings (modelo multilingue local)
        │
        ▼
   PostgreSQL + pgvector  (texto + metadados + vetor + índice full-text)
```

### Consulta (online, a cada pergunta)

```
Pergunta do utilizador
        │
        ├──► Pesquisa densa (pgvector, similaridade de cosseno)  ─┐
        │                                                         ├──► Fusão (RRF) ──► Reranker ──► top-k
        └──► Pesquisa lexical (full-text do PostgreSQL)  ─────────┘
                                                                              │
                                                                              ▼
                                                    Prompt: instruções + excertos numerados + pergunta
                                                                              │
                                                                              ▼
                                                    LLM ──► resposta com citações [Art. X.º]
                                                                              │
                                                                              ▼
                                               Validação: cada citação corresponde a um chunk recuperado?
```

## Stack tecnológica

| Componente | Escolha | Papel |
|---|---|---|
| Linguagem | Python 3.12 | Todo o sistema |
| API | FastAPI | Endpoints de pergunta, ingestão e avaliação |
| Base de dados | PostgreSQL 16 + pgvector | Chunks, metadados, vetores e índice full-text |
| Embeddings | BGE-M3 (via sentence-transformers) | Vetores multilingues, a correr localmente |
| Reranker | bge-reranker-v2-m3 (cross-encoder) | Reordenação dos candidatos |
| LLM | Ollama (modelo local ~7–8B) com alternativa por API | Geração das respostas |
| Interface | Página HTML servida pelo FastAPI (Jinja2) | Perguntar e ver as fontes |
| Infraestrutura | Docker Compose | PostgreSQL + pgvector |
| Testes | pytest | Testes unitários do pipeline |

## Decisões de design

Cada decisão regista as alternativas consideradas e a razão da escolha. Esta secção serve também de base para a defesa do projeto.

### 1. Corpus: Código do Trabalho

- **Alternativas consideradas:** regulamentos do IEFP/CET (volume pequeno, PDFs pouco estruturados); documentação técnica em inglês (concorre com ferramentas já existentes e não aproveita o desafio do português).
- **Porquê:** é um texto público e oficial, com estrutura rígida (artigos numerados), o que dá citações precisas e chunking natural. As perguntas são reais e qualquer pessoa as entende, e a resposta correta de cada uma pode ser verificada objetivamente, o que é indispensável para a avaliação. Trabalhar em português europeu é, além disso, um desafio técnico real.
- **Cuidado:** a lei é alterada com frequência. O corpus guarda **a data da versão consolidada** usada, e o sistema mostra-a nas respostas.

### 2. Chunking estrutural (por artigo)

- **Alternativas consideradas:** blocos de tamanho fixo com sobreposição (simples, mas corta artigos a meio e mistura temas); chunking semântico (mais caro e menos previsível).
- **Porquê:** na lei, o artigo é a unidade de significado e também a unidade de citação. Os artigos longos são divididos pelos seus números (n.º 1, n.º 2, …), e cada pedaço leva um **cabeçalho de contexto** com o número do artigo, a epígrafe e o capítulo, para não perder o sentido quando é lido isoladamente.
- **Experiência prevista:** comparar com blocos de tamanho fixo para medir o ganho.

### 3. Embeddings multilingues locais (BGE-M3)

- **Alternativas consideradas:** embeddings por API (custo por pedido e dependência externa); modelos treinados só em inglês (perdem qualidade em português).
- **Porquê:** suporta bem o português e corre localmente, sem custo. Reindexar o corpus várias vezes ao testar estratégias de chunking não custa nada.

### 4. PostgreSQL + pgvector

- **Alternativas consideradas:** Chroma (rápido de configurar, mas é mais uma tecnologia isolada); FAISS (só uma biblioteca de pesquisa, sem persistência nem metadados).
- **Porquê:** uma única base de dados guarda texto, metadados e vetores, com SQL normal para filtros. O mais importante é que o PostgreSQL tem **pesquisa full-text nativa** (configuração `portuguese`), o que torna a pesquisa híbrida possível sem acrescentar outro sistema.

### 5. Pesquisa híbrida + reranking

- **Problema:** a pesquisa por embeddings capta o significado, mas falha em termos exatos (ex.: "artigo 238.º", "período experimental"). A pesquisa lexical faz o contrário.
- **Solução:** correr as duas pesquisas e combinar os rankings com **Reciprocal Rank Fusion (RRF)**. A RRF soma `1 / (k + posição)` de cada lista, por isso não é preciso normalizar pontuações que vêm de escalas diferentes. Depois, um **cross-encoder** reavalia cada par (pergunta, excerto) com mais precisão do que a comparação entre vetores.
- **Porquê nesta ordem:** o cross-encoder é lento, por isso só se aplica aos ~20 melhores candidatos e não ao corpus inteiro.

### 6. LLM local com camada de abstração

- **Alternativas consideradas:** só API (melhor qualidade, mas com custo e com envio de dados para fora); só local (gratuito e privado, mas pior em português).
- **Porquê:** a geração está atrás de uma interface simples (`gerar(pergunta, excertos) -> resposta`), com duas implementações: Ollama (por omissão) e um fornecedor por API. A comparação entre as duas passa a ser **um resultado do projeto**.

### 7. Pipeline próprio, sem LangChain/LlamaIndex

- **Alternativa considerada:** usar um framework de RAG, que monta tudo em poucas linhas.
- **Porquê:** os frameworks escondem as decisões que este projeto quer estudar, medir e defender, e tornam o debug mais difícil. O pipeline é feito com peças pequenas e explícitas (sentence-transformers, psycopg, cliente do Ollama, FastAPI).
- **Custo assumido:** mais tempo de desenvolvimento no primeiro mês.

### 8. Fundamentação e recusa

- O prompt obriga o modelo a responder **apenas** com base nos excertos e a citar cada afirmação.
- Se os excertos não contiverem a resposta, o sistema responde que não encontrou a informação no corpus, em vez de improvisar.
- As citações geradas são **validadas no código**: uma citação a um artigo que não foi recuperado é sinalizada.

## Avaliação

O sistema é avaliado com um **conjunto de teste** (`eval/golden_set.jsonl`) criado manualmente, com 60 a 100 perguntas. Cada entrada tem:

- a pergunta;
- os artigos que contêm a resposta;
- uma resposta de referência;
- o tipo: factual (um artigo), multi-artigo, ou **sem resposta no corpus** (para testar a recusa).

As métricas são separadas por fase, para que seja possível perceber **onde** o sistema falha:

| Fase | Métrica | O que mede |
|---|---|---|
| Recuperação | Recall@k | O artigo certo está entre os k primeiros resultados? |
| Recuperação | MRR | Em que posição aparece o primeiro artigo certo? |
| Geração | Correção | A resposta está certa face à referência? (avaliação manual de uma amostra) |
| Geração | Fidelidade | Cada afirmação está apoiada nos excertos? |
| Geração | Validade das citações | Os artigos citados foram de facto recuperados? |
| Geração | Taxa de recusa correta | Nas perguntas sem resposta, o sistema recusa? |

### Experiências planeadas

| # | Variável | Comparação |
|---|---|---|
| E1 | Chunking | Por artigo vs blocos de tamanho fixo |
| E2 | Pesquisa | Só densa vs só lexical vs híbrida (RRF) |
| E3 | Reranking | Com vs sem cross-encoder |
| E4 | Gerador | Modelo local (Ollama) vs API |

Os resultados ficam registados em `eval/results/` e são analisados no relatório final.

## Estrutura do projeto

> Estrutura planeada; pode evoluir durante o desenvolvimento.

```
lexlaboral/
├── lexlaboral/
│   ├── ingest/        # obtenção, parsing, chunking, embeddings, indexação
│   ├── retrieval/     # pesquisa densa, lexical, fusão RRF, reranker
│   ├── generation/    # interface do LLM, implementações (Ollama, API), prompts
│   ├── api/           # endpoints FastAPI
│   └── web/           # templates e ficheiros estáticos da interface
├── eval/
│   ├── golden_set.jsonl
│   ├── run_eval.py
│   └── results/
├── data/
│   ├── raw/           # texto original, com a data da versão
│   └── processed/     # chunks gerados
├── tests/
├── docker-compose.yml
├── requirements.txt
└── README.md
```

## Como executar

> Secção a completar à medida que o projeto é implementado.

Requisitos previstos: Python 3.12, Docker, Ollama.

```bash
# 1. Base de dados (PostgreSQL + pgvector)
docker compose up -d

# 2. Ambiente Python
python3 -m venv .venv
source .venv/bin/activate        # em Fish: source .venv/bin/activate.fish
pip install -r requirements.txt

# 3. Modelo local
ollama pull <modelo-escolhido>

# 4. Ingestão do corpus      (comando a definir)
# 5. Arrancar a API           (comando a definir)
# 6. Correr a avaliação       (comando a definir)
```

## Planeamento

Cerca de 13 semanas, divididas em três fases.

| Semanas | Fase | Entregáveis |
|---|---|---|
| 1–4 | **Fundações** | Ambiente e Docker; obtenção e parsing do corpus; chunking estrutural; embeddings e indexação em pgvector; pesquisa densa básica; **primeira versão do conjunto de teste** |
| 5–9 | **Núcleo e avaliação** | Pesquisa lexical e híbrida (RRF); reranker; geração com citações e validação; API FastAPI; script de avaliação; experiências E1–E4 |
| 10–13 | **Produto e entrega** | Interface web; tratamento de erros; testes; relatório; preparação da defesa |

O conjunto de teste começa na fase 1 e não no fim, de propósito: assim todas as decisões seguintes podem ser medidas.

## Limitações conhecidas

- **Atualidade:** as respostas refletem a versão do Código do Trabalho indexada, não necessariamente a lei em vigor no momento da pergunta.
- **Âmbito:** não cobre legislação complementar, contratação coletiva nem jurisprudência, que na prática alteram muitas respostas.
- **Avaliação:** o conjunto de teste é pequeno e foi criado pelo autor, por isso os resultados indicam tendências e não têm significância estatística.
- **Modelo local:** a qualidade da redação em português depende do modelo disponível no hardware usado.

## Autor

**Elder Furtado Souza** — [GitHub](https://github.com/marruky-svg) · [LinkedIn](https://www.linkedin.com/in/elder-furtado-702b1b1a9)
