# EdgeFut AI

Assistente **local** (Windows desktop) de análise quantitativa de futebol com referência às odds da Superbet.
Coleta dados públicos, roda modelos estatísticos, simula, compara com o mercado e recomenda uma entrada — ou diz, com motivo, **NÃO ENTRAR**.

> O EdgeFut **não realiza apostas**, não faz login, não armazena credenciais de casas de apostas e não contorna bloqueios. O motor Python escuta apenas em `127.0.0.1`.

---

## O que ele faz

```
COLETA → NORMALIZAÇÃO → QUALIDADE DOS DADOS → FEATURES → MODELOS → SIMULAÇÃO →
PROBABILIDADE → ODD JUSTA → COMPARAÇÃO SUPERBET → EDGE → RISCO → RECOMENDAÇÃO ou NO BET → EXPLICAÇÃO
```

| Camada | O que entra nesta versão |
|---|---|
| **Fontes** | Superbet (oferta pública JSON: eventos, 14 mercados, odds, movimento), football-data.co.uk (17 ligas: gols, finalizações, escanteios, cartões, odds de fechamento), martj42/international_results (seleções, cidade/país/campo neutro) |
| **Normalização** | Nomes PT→EN (países e clubes), mapeamento torneio→dataset, detecção **MANDANTE / VISITANTE / CAMPO NEUTRO** (não assume que o primeiro time listado é mandante) |
| **Features** | Força com janelas 5/10/20 e peso de recência; splits geral/casa/fora; ataque/defesa relativos; H2H com peso menor que a forma |
| **Qualidade** | Frescor por insumo (**FRESH / AGING / STALE / EXPIRED** — EXPIRED nunca é usado em silêncio → `NO BET · STALE_DATA`), **Source Conflict Engine** (divergências entre fontes registradas, resolvidas por regra e clicáveis), `CanonicalEventResolver`, Health Dashboard (Sistema → Diagnóstico) |
| **Modelos** | `elo-v1`, `goals-poisson-v1`, `goals-dixon-coles-v1` (decaimento temporal), `goals-bivariate-poisson-v1` (correlação entre gols), `ensemble-v1` (pesos por competição derivados de walk-forward, nunca fixos à mão), `mc-v1` (10k–100k simulações, seed = eventId), `corners-v1`, `cards-v1`, `shots-v1`; `calibration-isotonic-v1` só ativa com ≥ 300 amostras liquidadas |
| **Odds** | Probabilidade implícita, remoção de margem **Shin** e multiplicativa (ambas armazenadas, método configurável), abertura × atual com probabilidade implícita ("1,72 → 1,54 −10,5 % · 58,1 % → 64,9 %"), closing line usada **só para CLV** |
| **Decisão** | Edge (pp), EV, **EDGEFUT CONFIDENCE 0–100** com breakdown em 6 grupos, **Opportunity Score V2** 0–100 (9 componentes configuráveis, nunca a odd), **Quality Gate** para TOP OPORTUNIDADES, rótulos **HIGH PROBABILITY ≠ VALUE**, **WHY THIS BET / WHY NOT**, **NO BET** com 11 motivos. Limiares nunca são reduzidos automaticamente |
| **Avaliação** | `prediction_snapshot` imutável (correções em tabela separada) gravado antes do jogo e liquidado depois; Backtest Lab walk-forward **anti-leakage** (`as_of`); Hit rate, Brier, Log loss, ROI, Yield, Drawdown, CLV por mercado/competição com `INSUFFICIENT SAMPLE`; diagrama de confiabilidade RAW vs CALIBRATED; nível de evidência por análise (`MODEL_ONLY` / `BACKTEST_ODDS` / `SETTLED`) |
| **Ao Vivo** | Modo **observação**: placar, minuto e odds reais da Superbet (`offerState=live`), "Odds atualizadas há N s", **sem recomendações**; estatísticas que a fonte não expõe não são estimadas |
| **Operação** | Scheduler V2 com histórico de execuções e correlation id (Sistema → Jobs), alertas locais, `model_registry`, cache de análise por versão de modelo, logs JSON estruturados |
| **Explicação** | ExplanationEngine por templates + **Edge AI** (perguntas respondidas só com os números da análise); Ollama local opcional para reescrever |
| **UI** | Início (resumo da manhã + **VER RADAR**), Radar V2, Jogos, Partida (com **Ver fontes**), Melhores Entradas, Ao Vivo, Múltiplas (com correlação), Backtest Lab, Histórico, Favoritos, Fontes V2, Modelos, Performance (Resultados · Calibração), Alertas, Sistema → Jobs, Sistema → Diagnóstico, Configurações |

Todo número na tela carrega `source, sourceUrl, collectedAt, confidence, sampleSize` e um status de frescor. Não há dados mock: o que não existe aparece como indisponível.

---

## Estrutura

```
apps/desktop/          React + TypeScript + Vite + Tailwind (janela Tauri 2)
  src-tauri/           shell Rust: inicia o sidecar Python e espera /health
packages/contracts/    tipos TS espelhando os schemas Pydantic do engine
packages/shared/       formatadores (pt-BR) e matemática de odds
engine/                Python 3.12 · FastAPI · SQLAlchemy · DuckDB/Parquet · pandas/scipy
  edgefut/api          rotas (health, events, radar, entries, tools, sources, settings, system: jobs/alerts/health…)
  edgefut/providers    http client (cache, rate-limit, retry, circuit breaker), Superbet (prematch + live), football-data, international_results, SourceResolver, player (só arquitetura)
  edgefut/collectors   sync de eventos/odds e coletor ao vivo (polling com backoff)
  edgefut/normalization times, países, competições, CanonicalEventResolver
  edgefut/domain       frescor (políticas por insumo), proveniência, conflitos entre fontes, tipos da análise
  edgefut/quality      Source Conflict Engine, Health (componentes do sistema)
  edgefut/features     força, forma, H2H, local do jogo, qualidade dos dados
  edgefut/models       ELO, Poisson, Dixon-Coles, Bivariate Poisson, ensemble, calibração isotônica, registry
  edgefut/simulation   Monte Carlo
  edgefut/odds         implícita, margem (Shin/multiplicativa), movimento, closing line
  edgefut/recommendations edge, confiança v2, Opportunity V2, Quality Gate, WHY/WHY NOT, NO BET, múltiplas
  edgefut/backtesting  Lab (as_of), liquidação, métricas, performance por mercado/competição
  edgefut/alerts       alertas locais
  edgefut/scheduler    jobs APScheduler com job_run + correlation id
  edgefut/db           SQLAlchemy, migrações, guarda de imutabilidade de snapshots
  edgefut/explanations templates, Edge AI, Ollama
  tests/               pytest (regressão + invariantes matemáticas + anti-leakage)
data/                  raw · processed (Parquet) · cache · sqlite (ignorado no git)
docs/                  ARCHITECTURE · DATA_SOURCES · MODELS · ROADMAP · ITERATION_2_BASELINE · ITERATION_2_REPORT
scripts/               dev.ps1 · install.ps1 · build-windows.ps1 · dev.sh
```

---

## Rodando no Windows

### 1. Instalar dependências (uma vez)

```powershell
.\scripts\install.ps1
```

Instala (via winget, se faltar) Python 3.12, uv, Node LTS, pnpm, Rust e os Build Tools do Visual Studio; cria a venv em `engine\.venv` e roda `pnpm install`.

### 2. Desenvolvimento

```powershell
.\scripts\dev.ps1
```

Sobe o engine em `http://127.0.0.1:8765` (com reload) e a janela Tauri apontando para o Vite em `http://127.0.0.1:1420`.
No primeiro boot o app cria o banco, baixa os datasets públicos, valida a Superbet e carrega os próximos eventos — tudo visível na tela de preparação.

### 3. Gerar o instalador

```powershell
.\scripts\build-windows.ps1
```

1. roda os testes do engine e o empacota com PyInstaller (`edgefut-engine.exe`, sidecar do Tauri) e valida `/health`;
2. compila o frontend (`tsc` + `vite build`);
3. gera o NSIS installer e copia para **`dist\EdgeFutAI-Setup.exe`**.

O usuário final não precisa de terminal: o instalador traz o engine embutido e o app abre pronto.

### 4. Download sem compilar (GitHub Actions)

O workflow [`build-windows.yml`](.github/workflows/build-windows.yml) roda os mesmos passos num runner Windows a cada push e publica o instalador:

- como **artifact** `EdgeFutAI-Setup` do run (aba *Actions*);
- como asset da pré-release **`dev-latest`** em [Releases](https://github.com/Fernando-Carilo/edgefut-local/releases/tag/dev-latest) (pushes em `main`/`cursor/**`), ou de uma release `vX.Y.Z` ao publicar uma tag.

### Linux/macOS (só desenvolvimento)

```bash
./scripts/dev.sh     # engine + Vite; abra http://127.0.0.1:1420
```

---

## Engine (API local)

```bash
cd engine
uv venv .venv --python 3.12 && uv pip install -e ".[dev]" --python .venv/bin/python
.venv/bin/python -m edgefut.main            # http://127.0.0.1:8765/docs
.venv/bin/python -m pytest -q               # testes
```

Principais rotas: `GET /health`, `GET /events?window=48h`, `GET /events/{id}/analysis?simulations=50000`,
`GET /radar`, `GET /entries`, `GET /dashboard`, `POST /chat`, `POST /multiples/evaluate`, `POST /simulator`,
`POST /bankroll/stake`, `POST /backtest`, `GET /performance`, `GET /calibration`, `GET /history`, `GET /sources`, `GET /models`, `GET|PUT /settings`,
`GET /live`, `GET /events/{id}/conflicts`, `GET /events/{id}/odds/history`, `GET /health/system`, `GET /jobs`, `POST /jobs/{job}/run`, `GET /alerts`.

Configuração por variáveis `EDGEFUT_*` (ver `engine/edgefut/core/config.py`). O host é fixo em loopback e não pode ser alterado.

---

## Como ler uma análise

- **MANDANTE / MANDANTE NÃO CONFIRMADO / CAMPO NEUTRO** — em amistosos e torneios de seleções a vantagem de mando só é aplicada integralmente quando confirmada; não confirmada usa 50 % (configurável); neutro remove. Você pode corrigir manualmente em "Definir local".
- **Frescor** — cada insumo (odds, resultados, histórico, evento) tem uma política própria e um status FRESH / AGING / STALE / EXPIRED. Um insumo EXPIRED nunca alimenta uma recomendação em silêncio: o jogo vira `NO BET · STALE_DATA` e a faixa de frescor mostra qual insumo venceu.
- **Divergências de dados** — quando duas fontes discordam (data, nome, mando…) o conflito é registrado com a regra que o resolveu e aparece como "N divergências de dados resolvidas" clicável.
- **Comparação de modelos** — Poisson, Dixon-Coles e Bivariate Poisson lado a lado, com o **consenso** (ensemble ponderado) e a divergência máxima em pp. Modelos com peso < 10 % não vetam a análise. Acima de 10 pp entre modelos relevantes o jogo vira `NO BET · MODEL_DISAGREEMENT`.
- **Edge** = probabilidade do modelo − probabilidade justa do mercado (em pontos percentuais). **EV** = prob × odd − 1. A probabilidade justa vem da remoção de margem (Shin ou multiplicativa; as duas ficam gravadas).
- **RAW vs CALIBRATED** — a probabilidade calibrada só existe quando o calibrador da competição/mercado tem ≥ 300 amostras liquidadas; até lá a tela diz explicitamente que está usando RAW.
- **EDGEFUT CONFIDENCE 0–100** — grupos `DATA_QUALITY`, `HISTORICAL_SAMPLE`, `MODEL_AGREEMENT`, `CALIBRATION`, `FRESHNESS`, `CONTEXT`; clique para ver cada componente. Grau A/B/C/D deriva do score: só A e B entram no Radar; C fica em observação; D nunca é recomendado.
- **HIGH PROBABILITY ≠ VALUE** — um mercado pode ter alta probabilidade sem ter edge (SAFE) e vice-versa. Os rótulos são independentes e cada card mostra o seu.
- **Opportunity Score V2** — 20 % confiança do modelo, 15 % qualidade dos dados, 10 % qualidade da calibração, 15 % edge, 10 % EV, 10 % frescor das odds, 10 % concordância entre modelos, 5 % performance histórica, 5 % tamanho da amostra. Pesos editáveis em Configurações; nunca a odd.
- **Quality Gate** — para entrar em TOP OPORTUNIDADES a recomendação passa por 10 checks (qualidade ≥ 60 %, confiança ≥ 65, amostra ≥ 15 jogos, divergência ≤ 10 pp, edge plausível ≤ 15 pp sem calibrador, frescor, odds completas…). Os limiares são editáveis mas têm **pisos e tetos rígidos** e o sistema nunca os reduz sozinho ("não caçar entradas").
- **WHY THIS BET / WHY NOT** — motivos em texto, gerados só a partir dos números da análise, sem linguagem de garantia.
- **Evidência** — `MODEL_ONLY` (só modelo), `BACKTEST_ODDS` (competição com backtest sobre odds reais) ou `SETTLED` (previsões desta competição já liquidadas). Aparece em todo card.
- **Ver fontes** — origem, URL, data de coleta, confiança e amostra de cada estatística, mais os checks de qualidade e os componentes da confiança.

Motivos de NO BET: `LOW_DATA`, `LOW_CONFIDENCE`, `NO_EDGE`, `MODEL_DISAGREEMENT`, `UNRELIABLE_SOURCE`, `SMALL_SAMPLE`, `LINEUP_UNCERTAINTY`, `EXTREME_ODDS_MOVEMENT`, `UNSUPPORTED_COMPETITION`, `STALE_DATA`, `QUALITY_GATE`.

---

## Validação com jogos reais (iteração 1)

| Jogo | Resultado do pipeline |
|---|---|
| **Japão x Uruguai** (Amistoso Internacional) | mandante não confirmado → 50 % da vantagem; ELO 1933 × 1869; Poisson 61/25/13 vs Dixon-Coles 49/35/16 → **NO BET · MODEL_DISAGREEMENT (12,7 pp)**; 51 mercados; snapshot gravado |
| **Servette (F) x Olympique Lyon (F)** (UEFA) | **NO BET · UNSUPPORTED_COMPETITION** — não há fonte pública de histórico para futebol feminino; qualidade 17 %, confiança D |
| **Seattle Sounders x Real Salt Lake** (MLS) | mandante confirmado; qualidade 79 %; confiança B; recomendações em gols com edge > 10 pp |
| Backtest E0 2025/26, 1X2, Dixon-Coles, odds de fechamento | 420 jogos, ROI −4,4 %, Brier 0,213 — mostrado sem maquiagem |

## Validação com jogos reais (iteração 2)

| Verificação | Resultado |
|---|---|
| **Japão x Uruguai** reanalisado com ensemble | pesos INTL: Poisson 3,6 % · Dixon-Coles 56,3 % · Bivariate 40,1 % (derivados de walk-forward); Poisson excluído do veto por peso < 10 %; Under 3,5 @ 1,27 edge +14,2 pp rotulado `MODEL_ONLY`, sem calibrador |
| Radar V2 em data FIFA (198 eventos) | 36 analisados hoje, 48 recomendações, 34 NO BET; todos `MODEL_ONLY` (sem backtest com odds para seleções) |
| Liquidação ponta a ponta | Iraque x Omã 1–1 liquidado pelo job `settle` (37 verificados, 28 liquidados em 72 s) — corrige o bug #1 do baseline |
| Ao Vivo | 44 jogos observados com placar, minuto e odds reais; nenhuma recomendação emitida; nenhuma estatística estimada |
| Backtest E0 com CLV | CLV médio −1,49 % (a closing line entra só nesta métrica) |
| Calibração | inativa por amostra (3 previsões liquidadas ≪ 300) — a tela diz RAW explicitamente |

Detalhes, limitações e riscos em [docs/ITERATION_2_REPORT.md](docs/ITERATION_2_REPORT.md).

---

## Segurança e ética

- Escuta apenas `127.0.0.1` (nunca `0.0.0.0`); CORS restrito à janela Tauri e ao Vite local.
- Nenhum login, nenhuma credencial de casa de apostas, nenhuma automação de aposta.
- Não contorna CAPTCHA/Cloudflare/anti-bot: a Superbet é consultada só pela API pública de oferta; páginas HTML protegidas não são raspadas. Bloqueios são registrados (`source_log`) e o app segue com o que tem.
- Rate-limit, cache em disco, retry, timeout e circuit breaker em todo acesso HTTP.
- Nenhuma API paga.

---

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — pipeline, módulos, contratos, segurança
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — fontes, endpoints, mapeamento de mercados e competições
- [docs/MODELS.md](docs/MODELS.md) — fórmulas, parâmetros e versões dos modelos
- [docs/ROADMAP.md](docs/ROADMAP.md) — fases, estado e próximos passos
- [docs/ITERATION_2_BASELINE.md](docs/ITERATION_2_BASELINE.md) — auditoria do estado real antes da iteração 2 (o que estava quebrado)
- [docs/ITERATION_2_REPORT.md](docs/ITERATION_2_REPORT.md) — o que foi implementado, verificado com dados reais, limitações e riscos

## Licença

Uso pessoal. Este software é uma ferramenta de análise e não constitui recomendação financeira.
