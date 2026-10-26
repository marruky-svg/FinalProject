# HelixRAG

**Sistema RAG (Retrieval-Augmented Generation) para perguntas sobre literatura científica em genómica, bioquímica e bioinformática, construído sobre resumos do PubMed e avaliado com o benchmark BioASQ.**

Projeto final do curso CET em Tecnologias e Programação de Sistemas de Informação (TPSI) — IEFP Braga.

> ⚠️ **Aviso:** o HelixRAG é uma ferramenta académica de exploração de literatura científica. Não dá aconselhamento médico e não substitui a leitura crítica dos artigos originais.

---

## Índice

1. [Objetivo](#objetivo)
2. [Porquê RAG](#porquê-rag)
3. [Arquitetura](#arquitetura)
4. [Corpus e dados de avaliação](#corpus-e-dados-de-avaliação)
5. [Stack tecnológica](#stack-tecnológica)
6. [Decisões de design](#decisões-de-design)
7. [Avaliação](#avaliação)
8. [Estrutura do projeto](#estrutura-do-projeto)
9. [Como executar](#como-executar)
10. [Planeamento](#planeamento)
11. [Limitações conhecidas](#limitações-conhecidas)
12. [Licenças e atribuição](#licenças-e-atribuição)
13. [Autor](#autor)

---

## Objetivo

O foco deste projeto é a **arquitetura de um sistema RAG**: como cada etapa do pipeline (pesquisa, fusão, reranking, geração, validação) é desenhada, isolada, medida e comparada com as alternativas.

O domínio, literatura biomédica em genómica, bioquímica e bioinformática, foi escolhido porque:

- os resumos do PubMed são texto limpo e estruturado, por isso a ingestão não domina o projeto;
- existe um benchmark público com perguntas escritas por especialistas e com a indicação dos artigos que lhes respondem (BioASQ), o que permite uma avaliação objetiva;
- o corpus pode crescer até dezenas de milhares de documentos, o que torna relevantes as questões de escala (índices aproximados, latência, filtros).

## Porquê RAG

Um LLM sozinho tem três limites quando responde sobre literatura científica:

- **Conhecimento congelado:** não conhece publicações posteriores ao treino.
- **Alucinação:** pode inventar resultados, ou até referências inteiras, com aparência credível.
- **Sem rastreabilidade:** não indica de onde veio a informação.

O RAG separa as funções: um motor de pesquisa encontra os resumos relevantes e o LLM redige a resposta **apenas com base nesses resumos**, citando os PMIDs. Daqui sai o princípio que orienta o projeto:

> **A qualidade do sistema depende sobretudo da recuperação.** Se o artigo certo não chegar ao LLM, nenhum prompt salva a resposta. Por isso, a maior parte do esforço de engenharia e de avaliação está no retrieval.

## Arquitetura

### Visão geral

O sistema é um pipeline de etapas independentes. Cada etapa é definida por uma **interface** e pode ter várias implementações, escolhidas por configuração. É isto que permite comparar alternativas sem reescrever o resto do sistema.

```
                       ┌───────────────────── Ingestão (offline) ─────────────────────┐
 Lista de PMIDs ──► E-utilities (NCBI) ──► Parser XML ──► Chunker ──► Embedder ──► PostgreSQL + pgvector
                       └──────────────────────────────────────────────────────────────┘

                       ┌──────────────────────── Consulta (online) ───────────────────────┐
 Pergunta ──► [QueryRewriter] ──► Retriever ──► [Reranker] ──► PromptBuilder ──► Generator ──► CitationValidator ──► Resposta
                                    │                                                                │
                          ┌─────────┴─────────┐                                              PMIDs citados
                     Densa (pgvector)   Lexical (full-text)                                  ⊆ PMIDs recuperados?
                          └──── Fusão RRF ────┘
                       └───────────────────────────────────────────────────────────────────┘
       (etapas entre [ ] são opcionais e ativadas por configuração)
```

### Interfaces do pipeline

| Interface | Responsabilidade | Implementações previstas |
|---|---|---|
| `Retriever` | Pergunta → lista ordenada de chunks | Densa, lexical, híbrida (RRF) |
| `Reranker` | Reordena os candidatos | Nenhum (identidade), cross-encoder |
| `QueryRewriter` | Reformula a pergunta antes da pesquisa | Nenhum, reescrita por LLM |
| `Generator` | Pergunta + excertos → resposta com citações | Ollama (local), API |
| `CitationValidator` | Confirma que cada PMID citado foi recuperado | Uma implementação |

O orquestrador (`Pipeline`) só conhece as interfaces e não as implementações concretas. A composição vem de um ficheiro de configuração, por exemplo:

```yaml
retriever: hybrid
top_k_retrieval: 50
reranker: cross_encoder
top_k_final: 8
generator: ollama
```

### Preocupações transversais

- **Observabilidade:** cada pedido regista a latência e o resultado de cada etapa (que chunks foram recuperados, em que posição, que PMIDs foram citados). Assim é possível saber *onde* o sistema falha ou gasta tempo, e não só *que* falhou.
- **Cache:** os embeddings do corpus são calculados uma vez e reutilizados. As respostas podem ser guardadas em cache pela chave (pergunta, configuração).
- **Reprodutibilidade:** o corpus é definido por uma lista versionada de PMIDs, e cada execução da avaliação regista a configuração usada.

## Corpus e dados de avaliação

### Avaliação: BioASQ (Task B)

O BioASQ é um desafio académico de pesquisa e resposta a perguntas biomédicas. Os dados de treino da Task B têm alguns milhares de perguntas em inglês, escritas por especialistas, com:

- a lista de artigos do PubMed relevantes (os "documentos de ouro");
- os excertos (snippets) relevantes de cada artigo, com a posição no texto;
- uma resposta de referência ("ideal answer") e, conforme o tipo, uma resposta exata.

As perguntas são de quatro tipos: sim/não, factuais, de lista e de resumo. O acesso aos dados exige registo gratuito em bioasq.org e a citação dos artigos do BioASQ.

**Seleção:** usa-se um subconjunto de perguntas da área de genómica, bioquímica e bioinformática. O critério de seleção (por exemplo, os termos MeSH dos documentos de ouro) fica documentado e é aplicado por script, para ser reproduzível.

### Corpus: resumos do PubMed

O corpus junta duas partes:

1. **Documentos de ouro:** todos os PMIDs relevantes das perguntas selecionadas.
2. **Distratores:** milhares de resumos do mesmo tema, obtidos por pesquisas no PubMed por termos MeSH da área.

Os distratores são essenciais. Se o corpus tiver só os artigos certos, qualquer pesquisa acerta e as métricas não significam nada. É a composição do corpus que define a dificuldade do teste. Distratores do **mesmo tema** (e não aleatórios) tornam o problema realista, porque o sistema tem de distinguir o artigo certo de artigos parecidos.

Cada registo processado contém: `pmid`, `titulo`, `resumo` (com as secções, quando o resumo é estruturado), `ano`, `revista` e `mesh`.

## Stack tecnológica

| Componente | Escolha | Papel |
|---|---|---|
| Linguagem | Python 3.12 | Todo o sistema |
| Dados | NCBI E-utilities (`esearch`, `efetch`) | Download dos resumos |
| Base de dados | PostgreSQL 16 + pgvector | Texto, metadados, vetores, índice full-text |
| Embeddings | Modelo multilingue (ex.: BGE-M3) e/ou biomédico (a avaliar) | Representação vetorial |
| Reranker | Cross-encoder (a avaliar) | Reordenação dos candidatos |
| LLM | Ollama (local) com alternativa por API | Geração das respostas |
| API | FastAPI | Endpoints de pergunta e avaliação |
| Interface | Página HTML servida pelo FastAPI (Jinja2) | Perguntar e ver as fontes |
| Infraestrutura | Docker Compose | PostgreSQL + pgvector |
| Testes | pytest | Testes unitários das etapas |

## Decisões de design

Cada decisão regista as alternativas consideradas e a razão da escolha.

### 1. Corpus: resumos, não texto integral

- **Alternativa:** artigos completos do PubMed Central (Open Access).
- **Porquê:** os resumos são curtos, limpos e existem para quase todos os artigos. Os documentos de ouro do BioASQ referem-se a artigos do PubMed, e os seus snippets vêm em grande parte dos títulos e resumos. O texto integral obrigaria a chunking complexo e a parsing pesado, o que contraria o foco do projeto.

### 2. Unidade de indexação

- **Opções:** resumo inteiro (título + resumo num só chunk) vs um chunk por secção, nos resumos estruturados (Background, Methods, Results, Conclusions).
- **Decisão inicial:** resumo inteiro, que é a opção mais simples. A alternativa por secções é uma experiência (E1). Chunks mais pequenos dão vetores mais focados, mas perdem contexto.

### 3. Embeddings: geral multilingue vs biomédico

- **Multilingue geral (ex.: BGE-M3):** permite perguntas em português sobre resumos em inglês.
- **Específico do domínio (ex.: modelos treinados com dados do PubMed):** provavelmente melhor no vocabulário biomédico, mas tipicamente só em inglês.
- **Decisão:** começar com o multilingue e comparar com um biomédico na experiência E2. A troca entre especialização e suporte multilingue é ela própria um resultado do projeto.

### 4. PostgreSQL + pgvector

- **Alternativas:** Chroma (rápido de configurar, mas é mais uma tecnologia isolada); FAISS (só uma biblioteca, sem persistência nem metadados).
- **Porquê:** uma única base de dados guarda texto, metadados (ano, MeSH), vetores e o índice full-text. Os filtros por metadados fazem-se em SQL normal.
- **Nota:** a pesquisa full-text do PostgreSQL ordena com `ts_rank`, que não é BM25. Fica registado como limitação, e uma implementação BM25 em Python é uma alternativa possível.

### 5. Índice vetorial: exato vs aproximado (HNSW)

- Com dezenas de milhares de vetores, a pesquisa exata (comparar com todos) começa a ficar lenta. O índice HNSW é muito mais rápido mas **aproximado**, por isso pode falhar vizinhos verdadeiros.
- **Experiência E4:** medir latência e recall com pesquisa exata e com HNSW em diferentes parâmetros.

### 6. Pesquisa híbrida com Reciprocal Rank Fusion

- A pesquisa densa capta o significado, mas falha em termos exatos, que são frequentes neste domínio (nomes de genes como *BRCA1*, proteínas, siglas). A pesquisa lexical faz o contrário.
- A **RRF** combina as duas listas somando `1 / (k + posição)` de cada uma. Funciona com posições e não com pontuações, por isso não é preciso normalizar valores que vêm de escalas diferentes.

### 7. Reranking em duas fases

- O cross-encoder avalia cada par (pergunta, documento) em conjunto, o que é mais preciso do que comparar vetores pré-calculados, mas também muito mais lento.
- **Por isso:** a pesquisa barata reduz o corpus a ~50 candidatos e o reranker só reordena esses. É o padrão clássico de *retrieve then rerank*.

### 8. Filtros por metadados: antes ou depois da pesquisa vetorial

- **Pré-filtragem** (ex.: só artigos depois de 2020, e só depois a pesquisa vetorial): resultados exatos, mas com índices aproximados pode devolver menos de k resultados.
- **Pós-filtragem** (pesquisa vetorial primeiro, filtro depois): mais simples, mas o filtro pode eliminar quase todos os candidatos.
- **Experiência E5** (opcional).

### 9. LLM atrás de uma interface

- O `Generator` tem duas implementações: Ollama (local, gratuito, privado) e um fornecedor por API (melhor qualidade, com custo). A comparação entre os dois é a experiência E7.

### 10. Pipeline próprio, sem LangChain/LlamaIndex

- **Alternativa:** um framework de RAG, que monta tudo em poucas linhas.
- **Porquê:** os frameworks escondem exatamente as decisões que este projeto quer estudar, medir e defender. As interfaces próprias tornam cada etapa visível, testável e substituível.
- **Custo assumido:** mais tempo de desenvolvimento no início.

### 11. Fundamentação e recusa

- O prompt obriga o modelo a responder só com base nos resumos fornecidos e a citar os PMIDs.
- Se os resumos não contiverem a resposta, o sistema diz que não a encontrou na literatura indexada.
- O `CitationValidator` sinaliza qualquer PMID citado que não tenha sido recuperado, porque é um sinal direto de alucinação.

## Avaliação

### Métricas de recuperação

Calculadas contra os documentos de ouro do BioASQ:

| Métrica | O que mede |
|---|---|
| Recall@10 | Fração dos documentos relevantes que aparecem nos 10 primeiros resultados |
| MRR | Em que posição aparece o primeiro documento relevante |
| nDCG@10 | Qualidade da ordenação, com mais peso nas primeiras posições |
| Latência | Tempo por etapa (pesquisa, reranking, geração) |

O top-10 coincide com o limite usado no próprio BioASQ, onde os sistemas devolvem no máximo 10 documentos por pergunta.

### Métricas de geração

| Métrica | Como |
|---|---|
| Correção | Comparação com a resposta de referência e com a resposta exata (sim/não, factuais); avaliação manual de uma amostra |
| Validade das citações | Fração de PMIDs citados que foram de facto recuperados |
| Fidelidade | Cada afirmação está apoiada nos resumos? (amostra manual) |

### Experiências

| # | Variável | Comparação | Prioridade |
|---|---|---|---|
| E1 | Unidade de indexação | Resumo inteiro vs por secção | Principal |
| E2 | Embeddings | Multilingue vs biomédico | Principal |
| E3 | Pesquisa | Densa vs lexical vs híbrida (RRF), com e sem reranker | Principal |
| E4 | Índice | Exato vs HNSW (latência × recall) | Principal |
| E5 | Filtros | Pré vs pós-filtragem | Opcional |
| E6 | Língua | Perguntas em inglês vs as mesmas em português | Opcional |
| E7 | Gerador | Ollama vs API | Principal |

Cada execução grava em `eval/results/` a configuração, as métricas e a data, para que os resultados possam ser comparados e reproduzidos.

## Estrutura do projeto

> Estrutura planeada; pode evoluir durante o desenvolvimento.

```
helixrag/
├── helixrag/
│   ├── ingest/         # cliente E-utilities, parser XML, chunker, indexação
│   ├── retrieval/      # retrievers (denso, lexical, híbrido), rerankers, query rewriter
│   ├── generation/     # interface Generator, implementações, prompts, validador de citações
│   ├── pipeline/       # orquestrador, configuração, observabilidade
│   ├── api/            # endpoints FastAPI
│   └── web/            # templates e estáticos
├── eval/
│   ├── select_questions.py   # seleção do subconjunto BioASQ
│   ├── run_eval.py
│   ├── configs/              # configurações de cada experiência
│   └── results/
├── corpus/
│   ├── gold_pmids.txt        # versionado
│   └── distractor_pmids.txt  # versionado
├── data/                     # NÃO versionado: resumos descarregados, dados BioASQ
├── tests/
├── docker-compose.yml
├── requirements.txt
├── .env.example              # NCBI_API_KEY, chaves de API do LLM
└── README.md
```

O repositório **não inclui os resumos nem os dados do BioASQ** (ver [Licenças e atribuição](#licenças-e-atribuição)). Inclui as listas de PMIDs e os scripts que reconstroem o corpus.

## Como executar

> Secção a completar à medida que o projeto é implementado.

Requisitos previstos: Python 3.12, Docker, Ollama, uma conta NCBI (chave de API gratuita) e registo no BioASQ.

```bash
# 1. Base de dados
docker compose up -d

# 2. Ambiente Python
python3 -m venv .venv
source .venv/bin/activate        # em Fish: source .venv/bin/activate.fish
pip install -r requirements.txt

# 3. Configuração
cp .env.example .env             # preencher NCBI_API_KEY

# 4. Modelo local
ollama pull <modelo-escolhido>

# 5. Construir o corpus           (comando a definir)
# 6. Indexar                      (comando a definir)
# 7. Arrancar a API               (comando a definir)
# 8. Correr a avaliação           (comando a definir)
```

## Planeamento

Cerca de 13 semanas.

| Semanas | Fase | Entregáveis |
|---|---|---|
| 1–3 | **Dados e fundações** | Registo BioASQ e chave NCBI; seleção das perguntas; download dos documentos de ouro e dos distratores; parser XML; base de dados; embeddings e indexação; pesquisa densa básica; **primeira avaliação de recuperação** |
| 4–8 | **Arquitetura do pipeline** | Interfaces e orquestrador; pesquisa lexical e híbrida; reranker; geração com citações e validação; observabilidade; experiências E1–E4 |
| 9–10 | **Geração e comparação** | Avaliação da geração; E7; experiências opcionais, se houver tempo |
| 11–13 | **Produto e entrega** | API e interface web; testes; relatório; preparação da defesa |

A avaliação começa na fase 1, de propósito: assim todas as decisões de arquitetura seguintes são medidas, e não escolhidas por intuição.

## Limitações conhecidas

- **Avaliação incompleta:** os documentos de ouro do BioASQ foram escolhidos por especialistas, mas não são uma lista exaustiva. Um distrator relevante que o sistema encontre conta como erro, por isso as métricas tendem a **subestimar** a qualidade real.
- **Só resumos:** respostas que dependem de detalhes do texto integral (métodos, tabelas) ficam fora do alcance do sistema.
- **Corpus limitado:** o sistema só conhece os artigos indexados, que são uma fração mínima do PubMed.
- **Lexical ≠ BM25:** a ordenação full-text do PostgreSQL é uma aproximação.
- **Modelo local:** a qualidade da redação depende do modelo que o hardware disponível consegue correr.

## Licenças e atribuição

- **PubMed / NCBI:** os dados são obtidos através das E-utilities da NCBI. Alguns resumos podem estar protegidos por direitos de autor, por isso não são redistribuídos neste repositório. A aplicação mostra o aviso legal e de copyright da NCBI, como exigido aos softwares que usam as E-utilities.
- **BioASQ:** os dados de avaliação são usados nos termos da licença de investigação do BioASQ, exigem registo e não são redistribuídos. Os artigos de referência do BioASQ são citados no relatório.

## Autor

**Elder Furtado Souza** — [GitHub](https://github.com/marruky-svg) · [LinkedIn](https://www.linkedin.com/in/elder-furtado-702b1b1a9)
