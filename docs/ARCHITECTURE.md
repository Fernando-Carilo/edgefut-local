# EdgeFut AI — Arquitetura

> Aplicativo desktop LOCAL (Windows) de análise quantitativa de futebol.
> Não realiza apostas. Não automatiza login. Não armazena credenciais. Não contorna anti-bot.

## 1. Visão geral

```
┌──────────────────────────────────────────────────────────────────────┐
│  apps/desktop  (Tauri 2)                                             │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  React + TypeScript + Vite + Tailwind + TanStack Query + Zustand │  │
│  │  Radar · Jogos · Página do Jogo · Melhores Entradas · Lab ·     │  │
│  │  Histórico · Fontes · Modelos · Performance · Edge AI            │  │
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

## 3. Estrutura do repositório

```
edgefut-local/
├── apps/
│   └── desktop/               # Tauri 2 + frontend React (src/) + src-tauri/
├── engine/                    # Serviço Python (FastAPI) — o motor quantitativo
│   ├── edgefut/
│   │   ├── api/               # FastAPI app + routers (127.0.0.1 apenas)
│   │   ├── core/              # config, paths, logging, versões de modelo
│   │   ├── db/                # SQLAlchemy models, sessão, migrations
│   │   ├── providers/         # Superbet, football-data, international_results
│   │   ├── collectors/        # sync de eventos/odds → SQLite (histórico de odds)
│   │   ├── normalization/     # aliases de times, mapeamento de competições
│   │   ├── features/          # força, forma, H2H, venue, data quality
│   │   ├── models/            # ELO, Poisson, Dixon-Coles, corners, cards, shots
│   │   ├── simulation/        # Monte Carlo
│   │   ├── odds/              # implied probability, remoção de margem, movimento
│   │   ├── recommendations/   # edge, confiança, NO BET, opportunity score
│   │   ├── explanations/      # templates PT-BR, Edge AI (grounded), Ollama opcional
│   │   ├── backtesting/       # Lab + métricas (Brier, LogLoss, ROI, drawdown)
│   │   ├── scheduler/         # APScheduler (eventos 15m, odds 5m, histórico diário)
│   │   └── analysis/          # orquestração do pipeline por evento
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

### 4.2 Banco de dados

- **SQLite** (`data/edgefut.sqlite3`): eventos, odds (snapshots), times,
  competições, `prediction_snapshot`, `source_log`, favoritos, settings.
- **Parquet + DuckDB** (`data/processed/*.parquet`): partidas históricas
  (football-data.co.uk, international_results). Consultas analíticas via DuckDB.

Migrations versionadas em `db/migrations.py` (SQL idempotente, tabela
`schema_version`).

### 4.3 Histórico de odds

Cada coleta grava uma linha em `odds_snapshot` (event, market, selection, line,
price, collected_at). A odd "atual" é o último snapshot; a "inicial" é o
primeiro. Movimento % e direção são derivados, nunca armazenados.

### 4.4 Prediction snapshot

Ao analisar um evento futuro, o pipeline grava `prediction_snapshot`
(timestamp, features, modelVersion, probabilities, odds, recommendations).
Snapshots são imutáveis; o resultado é anexado depois via job de settlement.
Métricas de performance leem apenas snapshots settled.

## 5. Frontend

- Rotas: `/` (Início), `/radar`, `/jogos`, `/jogos/:id`, `/entradas`,
  `/ao-vivo`, `/multiplas`, `/lab`, `/historico`, `/favoritos`, `/fontes`,
  `/modelos`, `/performance`, `/configuracoes`.
- Estado servidor: TanStack Query. Estado UI: Zustand (filtros, banca, Edge AI).
- Design tokens em `apps/desktop/src/styles/tokens.css` (paleta EdgeFut).
- Todo número exibido vem do engine. Qualquer dado não real é marcado `DEMO`.

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
`engine/edgefut/api/schemas.py`. Qualquer mudança em um exige mudança no outro
(teste `tests/test_contracts_shape.py` valida os campos obrigatórios).
