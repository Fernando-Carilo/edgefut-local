# EdgeFut AI — Arquitetura

> Aplicativo desktop LOCAL (Windows) de análise quantitativa de futebol.
> Não realiza apostas. Não automatiza login. Não armazena credenciais. Não contorna anti-bot.

## 1. Visão geral

```
┌──────────────────────────────────────────────────────────────────────┐
│  apps/desktop  (Tauri 2)                                             │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  React + TypeScript + Vite + Tailwind + TanStack Query + Zustand │  │
│  │  Radar V2 · Jogos · Página do Jogo · Melhores Entradas · Lab ·  │  │
│  │  Ao Vivo · Histórico · Fontes V2 · Modelos · Performance ·       │  │
│  │  Alertas · Sistema (Jobs · Diagnóstico) · Edge AI                │  │
│  └───────────────────────────┬────────────────────────────────────┘  │
│                              │ HTTP (127.0.0.1:8765)                  │
│  ┌───────────────────────────▼────────────────────────────────────┐  │
│  │  sidecar: edgefut-engine.exe  (FastAPI, Python 3.12)             │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                               │
     ┌─────────────────────────┼──────────────────────────────┐
     ▼                         ▼                              ▼
 SQLite (app)          DuckDB / Parquet (histórico)     data/cache (HTTP)
```

O **engine** (Python) é a única fonte de verdade quantitativa. O frontend só
renderiza o que o engine produz. O texto de IA (templates ou Ollama) apenas
explica números que já existem na análise estruturada.

## 2. Pipeline obrigatório

```
COLETA → NORMALIZAÇÃO → QUALIDADE DOS DADOS → FEATURES → MODELOS →
SIMULAÇÃO → PROBABILIDADE → ODD JUSTA → COMPARAÇÃO SUPERBET → EDGE →
RISCO → RECOMENDAÇÃO ou NO BET → EXPLICAÇÃO
```

Implementado em `engine/edgefut/analysis/pipeline.py` (`analyze_event`). Cada
etapa produz um objeto Pydantic tipado; a etapa seguinte só consome a anterior.
Nunca há "atalho" do texto para a probabilidade.

Desde a iteração 2 o pipeline também responde à pergunta "posso acreditar
nisso?": cada insumo carrega um **status de frescor**, cada análise carrega um
**nível de evidência** (`MODEL_ONLY` / `BACKTEST_ODDS` / `SETTLED`), as
divergências entre fontes ficam registradas, e a recomendação só entra em TOP
OPORTUNIDADES depois de um **Quality Gate** explícito. Toda execução roda dentro
de um `correlation id` (`core/context.py`) que aparece nos logs JSON e em
`job_run`.

## 3. Estrutura do repositório

```
edgefut-local/
├── apps/
│   └── desktop/               # Tauri 2 + frontend React (src/) + src-tauri/
├── engine/                    # Serviço Python (FastAPI) — o motor quantitativo
│   ├── edgefut/
│   │   ├── api/               # FastAPI app + routers events/radar/tools/system (127.0.0.1 apenas)
│   │   ├── core/              # config (+ HARD_FLOORS/CEILINGS), paths, logging JSON, correlation id, versões
│   │   ├── db/                # SQLAlchemy models, sessão, migrations, guarda de imutabilidade
│   │   ├── domain/            # frescor (políticas), proveniência, conflitos, tipos da análise
│   │   ├── providers/         # Superbet (prematch + live), football-data, international_results,
│   │   │                      # SourceResolver, player/ (só protocolo — sem fonte permitida)
│   │   ├── collectors/        # sync de eventos/odds → SQLite; live.py (polling com backoff)
│   │   ├── normalization/     # aliases de times, competições, CanonicalEventResolver
│   │   ├── quality/           # Source Conflict Engine, Health (componentes do sistema)
│   │   ├── features/          # força, forma, H2H, venue, data quality
│   │   ├── models/            # ELO, Poisson, Dixon-Coles, Bivariate Poisson, ensemble,
│   │   │                      # calibração isotônica, registry; corners, cards, shots
│   │   ├── simulation/        # Monte Carlo
│   │   ├── odds/              # implied (Shin + multiplicativa), movimento, closing line (só CLV)
│   │   ├── recommendations/   # edge, confiança v2, opportunity v2, gate, why, NO BET, múltiplas
│   │   ├── explanations/      # templates PT-BR, Edge AI (grounded), Ollama opcional
│   │   ├── backtesting/       # Lab (as_of), settlement, métricas, performance por grupo
│   │   ├── alerts/            # alertas locais (tabela alert)
│   │   ├── scheduler/         # APScheduler V2: 11 jobs com job_run + correlation id
│   │   └── analysis/          # orquestração do pipeline por evento (cache por versão)
│   └── tests/
├── packages/
│   ├── contracts/             # tipos TypeScript espelhando os schemas Pydantic
│   └── shared/                # utilitários de formatação compartilhados
├── data/
│   ├── raw/                   # CSVs baixados (football-data, international_results)
│   ├── processed/             # Parquet normalizado (lido via DuckDB)
│   └── cache/                 # cache HTTP (JSON) com TTL
├── scripts/                   # dev.ps1, install.ps1, build-windows.ps1, dev.sh
└── docs/
```

## 4. Engine — módulos

### 4.1 Providers (`providers/`)

Interface `Provider` com `name`, `kind`, `health()`, e registro em `source_log`.
Toda chamada HTTP passa por `providers/http_client.py`:

- cache em disco (`data/cache`) com TTL por tipo de recurso;
- rate limit por host (token bucket);
- retry com backoff exponencial (3 tentativas);
- timeout (15s padrão);
- circuit breaker por host (abre após 5 falhas consecutivas, meia-abertura em 5 min);
- registro de `source`, `sourceUrl`, `collectedAt`, latência e status.

`SourceResolver` (`providers/resolver.py`) recebe um pedido ("histórico do time
X na competição Y") e tenta, em ordem, os providers capazes, devolvendo o dado
com `Provenance {source, sourceUrl, collectedAt, confidence, sampleSize}`.
Quando nenhuma fonte responde, devolve `unavailable` com o motivo — o pipeline
converte isso em `LOW_DATA` / `UNSUPPORTED_COMPETITION`.

**Superbet** usa exclusivamente a API pública de oferta (JSON) que alimenta o
site. Não há login, não há cookies, não há automação de navegador contra a
Superbet. Se o endpoint responder 403/429, o provider marca indisponibilidade e
o app continua com os dados em SQLite + import manual.

O mesmo provider lê `offerState=live` para o **modo observação** ao vivo
(`collectors/live.py`): polling a cada `live_poll_seconds` (mínimo 20 s) com
backoff em erro; placar, minuto e odds vêm da fonte; nada é estimado; nenhuma
recomendação é gerada durante o jogo.

`providers/player/` define apenas o protocolo `PlayerProvider`. Não há
implementação porque não existe fonte pública permitida — mercados de jogador
retornam `LINEUP_UNCERTAINTY`.

### 4.2 Banco de dados

- **SQLite** (`data/edgefut.sqlite3`): eventos, odds (snapshots), times,
  competições, `prediction_snapshot`, `snapshot_correction`, `closing_line`,
  `source_conflict`, `model_registry`, `calibration_model`, `job_run`, `alert`,
  `source_log`, favoritos, settings.
- **Parquet + DuckDB** (`data/processed/*.parquet`): partidas históricas
  (football-data.co.uk, international_results). Consultas analíticas via DuckDB.

Migrations versionadas em `db/migrations.py` (SQL idempotente, tabela
`schema_version`).

### 4.3 Histórico de odds

Cada coleta grava uma linha em `odds_snapshot` (event, market, selection, line,
price, collected_at, e as probabilidades justas **Shin e multiplicativa**). A odd
"atual" é o último snapshot; a "inicial" é o primeiro. `LineMovement`
(`odds/implied.py`) deriva abertura → atual, variação % e probabilidade implícita
antes/depois ("1,72 → 1,54 −10,5 % · 58,1 % → 64,9 %"); movimentos acima do
limiar viram `EXTREME_ODDS_MOVEMENT`. A **closing line** (`odds/closing.py`) é a
última odd observada antes do kickoff: o job `closing_lines` tenta uma coleta
final na janela de 12 min pré-jogo e, passado o kickoff, fixa a última odd
conhecida em `closing_line`. É usada **somente** para CLV — nunca entra no
modelo nem na recomendação (que usa a odd do momento da análise, gravada no
snapshot).

### 4.4 Frescor e conflitos

`domain/freshness.py` define uma política por tipo de insumo (`odds`,
`odds_live`, `form`, `history`, `venue`, `events`, `results`…) com três
limiares: FRESH → AGING → STALE → EXPIRED. Cada `Provenance` carrega
`collectedAt`, `validUntil` e o status. Um insumo EXPIRED gera
`NO BET · STALE_DATA`; o pipeline nunca o usa em silêncio.

`quality/conflicts.py` (Source Conflict Engine) compara o que fontes distintas
dizem sobre o mesmo evento (data/hora, nomes, mando, resultado) e grava
`source_conflict` com o campo, os dois valores, a fonte vencedora e a regra
aplicada. A UI mostra "N divergências de dados resolvidas" e abre o detalhe.
`normalization/CanonicalEventResolver` garante que o mesmo jogo vindo de
fontes distintas cai no mesmo `event_id` canônico.

### 4.5 Prediction snapshot e imutabilidade

Ao analisar um evento futuro, o pipeline grava `prediction_snapshot`
(timestamp, features, modelVersion, probabilities RAW e CALIBRATED, odds,
recommendations, evidência). `db/immutability.py` bloqueia qualquer `UPDATE`
nos campos preditivos após a gravação; correções legítimas (ex.: mando errado)
vão para `snapshot_correction`, referenciando o snapshot original. O resultado é
anexado depois pelo job `settle`. Métricas de performance leem apenas snapshots
settled.

### 4.6 Modelos, ensemble e calibração

`models/registry.py` mantém `model_registry` (nome, versão, parâmetros,
treinado em, N). `models/ensemble.py` deriva os pesos por competição a partir
do log loss walk-forward (N ≥ 200; senão `GLOBAL`) — nunca há peso fixo à mão.
`models/calibration.py` ajusta isotônica por (competição, mercado) só com ≥ 300
amostras liquidadas; a análise sempre expõe RAW e, quando existe, CALIBRATED.
O cache de análise é chaveado pela tupla de versões de modelo
(`core/versions.py`), portanto uma mudança de modelo invalida o cache sozinha.

### 4.7 Scheduler, jobs, alertas e saúde

`scheduler/jobs.py` registra 11 jobs (`events`, `odds`, `history`, `settle`,
`radar`, `closing_lines`, `performance`, `calibration`, `ensemble_weights`,
`cache_cleanup`, `live_poll`). Cada execução grava `job_run` (início, fim,
status, resumo, correlation id) e pode ser disparada manualmente por
`POST /jobs/{job}/run`. `alerts/` compara o ciclo atual do radar com o anterior
e grava alertas locais (sem rede, sem push): `ODD_MOVEMENT` (≥ 5 %),
`DATA_QUALITY_CHANGE` (≥ 15 pontos), `MODEL_CONFIDENCE_CHANGE` (grau mudou),
`OPPORTUNITY_APPEARED`, `OPPORTUNITY_LOST`. `quality/health.py` consolida o
estado de cada componente (SQLite, DuckDB, cache HTTP, Superbet pré-jogo e ao
vivo, calendário, históricos, liquidação, scheduler, jogadores, Ollama) em
`GET /health/system` — é o que a tela Diagnóstico e o indicador do header
mostram.

## 5. Frontend

- Rotas: `/` (Início), `/radar`, `/jogos`, `/jogos/:id`, `/entradas`,
  `/ao-vivo`, `/multiplas`, `/lab`, `/historico`, `/favoritos`, `/fontes`,
  `/modelos`, `/performance`, `/alertas`, `/sistema/jobs`,
  `/sistema/diagnostico`, `/configuracoes`.
- Estado servidor: TanStack Query. Estado UI: Zustand (filtros, banca, Edge AI).
- Design tokens em `apps/desktop/src/styles/tokens.css` (paleta EdgeFut).
- Componentes de confiança em `components/TrustPanels.tsx`: `ConfidenceIndicator`
  (EDGEFUT CONFIDENCE 0–100 com breakdown), `FreshnessStrip`, `ConflictsModal`,
  `ModelComparisonTable`, `RecommendationDetail` (WHY / WHY NOT, gate,
  Opportunity V2), `MovementLine`, `EvidenceBanner`.
- Todo número exibido vem do engine. Qualquer dado não real é marcado `DEMO`.
  Ao vivo é observação: a UI não renderiza recomendação nem estatística que a
  fonte não forneceu.

## 6. Segurança

- Uvicorn escuta apenas `127.0.0.1` (porta 8765 por padrão). Nunca `0.0.0.0`.
- CORS restrito a `tauri://localhost`, `http://localhost:1420` e
  `http://127.0.0.1:1420`.
- Sem credenciais de casas de apostas. Sem automação de aposta. Sem bypass de
  Cloudflare/CAPTCHA.
- Configurações sensíveis (ex.: token de LLM remoto, se um dia existir) via
  keyring do SO — hoje não há nenhuma.

## 7. Empacotamento

`scripts/build-windows.ps1`:

1. `pyinstaller` gera `edgefut-engine.exe` (onefile) em
   `apps/desktop/src-tauri/binaries/edgefut-engine-x86_64-pc-windows-msvc.exe`.
2. `pnpm tauri build` gera `EdgeFutAI-Setup.exe` (NSIS).

O Tauri sobe o sidecar no boot, aguarda `/health`, e só então mostra a janela.

## 8. Contratos

`packages/contracts/src/index.ts` espelha os schemas Pydantic de
`engine/edgefut/api/schemas.py`. Qualquer mudança em um exige mudança no outro.
Os testes de API (`engine/tests/test_api.py`, ex. `test_radar_and_dashboard_shape`)
validam a forma das respostas do lado Python; `pnpm -r typecheck` valida o
consumo do lado TypeScript. Não há hoje um teste automático que compare os dois
lados campo a campo.

## 9. Observabilidade

- Logs JSON estruturados (`core/logging.py`, arquivo rotativo
  `data/logs/engine.jsonl`) com `correlation_id` e contexto; o mesmo id aparece
  em `job_run` e nas respostas de `/jobs`.
- `source_log` registra cada chamada HTTP (provider, URL, status
  ok/cached/error/blocked, HTTP status, latência) — alimenta os cards de Fontes
  V2 (taxa de erro, latência, requisições em 24 h).
- `GET /health/system` agrega tudo em um único `overall` (HEALTHY / DEGRADED /
  STALE / UNAVAILABLE) com o detalhe por componente.
- Testes: `engine/tests/test_invariants.py` (matriz de placares é distribuição
  e 1X2 soma 1; Bivariate Poisson preserva marginais; probabilidades justas
  somam 1 nos dois métodos de margem; definições de edge e EV; Monte Carlo
  determinístico e convergente), `test_backtest_leakage.py` (nenhuma janela vê
  o futuro; closing odds nunca decidem; nenhuma aposta sem odd pré-jogo) e
  `test_api.py::test_settings_floors_block_threshold_hunting` (pisos e tetos
  do gate não podem ser burlados pela UI).
