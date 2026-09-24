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
| **Modelos** | `elo-v1`, `goals-poisson-v1`, `goals-dixon-coles-v1` (decaimento temporal), `goals-bivariate-poisson-v1` (correlação entre gols), `ensemble-v1` (**campeão**; pesos por competição derivados de walk-forward, nunca fixos à mão), `mc-v1` (10k–100k simulações, seed = eventId), `corners-v1`, `cards-v1`, `shots-v1`; `calibration-isotonic-v1` só ativa com ≥ 300 amostras liquidadas. **Challengers** (rodam em paralelo, não decidem): `strength-v2` opponent-adjusted + `goals-poisson-v2` (half-life escolhido por walk-forward: 365 d), `international-strength-v1` (seleções, tipo de torneio), `ensemble_v2` |
| **Odds** | Probabilidade implícita, remoção de margem **Shin** e multiplicativa (ambas armazenadas, método configurável), abertura × atual com probabilidade implícita ("1,72 → 1,54 −10,5 % · 58,1 % → 64,9 %"), closing line usada **só para CLV** |
| **Decisão** | Edge (pp), EV, **EDGEFUT CONFIDENCE 0–100** com breakdown em 6 grupos, **Opportunity Score V3** 0–100 (9 componentes configuráveis + ajustes explícitos, nunca a odd), **Quality Gate** para TOP OPORTUNIDADES, **estados** `MODEL_ONLY` → `MARKET_OBSERVED` → `VALUE_CANDIDATE` → `VALUE` / `OBSERVATION` / `NO_BET` (VALUE exige prova out-of-sample no mercado), rótulos **MODEL FAVORITE ≠ VALUE**, **clusters** (uma primária por tese, alternativas marcadas, exposição por evento), **price target** (break-even, odd mínima aceitável, sensibilidade do edge, watchlist), **WHY THIS BET / WHY NOT / WHY MODEL CHANGED**, **NO BET** com 12 motivos. Limiares nunca são reduzidos automaticamente |
| **Avaliação** | `prediction_snapshot` imutável (correções em tabela separada) gravado antes do jogo e liquidado depois; `TemporalFeatureStore` (`as_of` em toda leitura, `LeakageError` se vazar); Backtest Lab e **Historical Replay** walk-forward com 5 baselines (mercado, ingênuo, Poisson simples, ELO, favorito), **bootstrap IC 95 %**, qualidade de amostra (INSUFFICIENT / EARLY / MODERATE / STRONG) e significância; **shadow mode** append-only com relatório diário; **reconciliação de settlement**; **drift monitor** (só alerta); Hit rate, Brier, Log loss, ROI, Yield, Drawdown, CLV por mercado/competição/cluster/estado; diagrama de confiabilidade RAW vs CALIBRATED; nível de evidência por análise (`MODEL_ONLY` / `BACKTEST_ODDS` / `SETTLED`) |
| **Governança** | `model_registry` com papéis champion / challenger / baseline; **regra de promoção** explícita (Brier OOS com IC pareado, LogLoss, ECE, N ≥ 300, estabilidade ≥ 60 % das janelas; **ROI não é critério**); promoção é ação humana registrada, nunca automática; **MODEL HEALTH** discreto no Início |
| **Ao Vivo** | Modo **observação**: placar, minuto e odds reais da Superbet (`offerState=live`), "Odds atualizadas há N s", **sem recomendações**; estatísticas que a fonte não expõe não são estimadas |
| **Operação** | Scheduler V2 com histórico de execuções e correlation id (Sistema → Jobs), alertas locais, `model_registry`, cache de análise por versão de modelo, logs JSON estruturados |
| **Explicação** | ExplanationEngine por templates + **Edge AI** (perguntas respondidas só com os números da análise); Ollama local opcional para reescrever |
| **UI** | Início (resumo da manhã + **VER RADAR** + MODEL HEALTH), Radar V2 (cards por estado), Jogos, Partida (com **Ver fontes**, **Teses e exposição**, **Why model changed**, price target), Melhores Entradas, Ao Vivo, Múltiplas (com correlação), Backtest Lab, **Validação** (Model Validation · Model Comparison · Coverage Map · Shadow · Drift), Histórico, Favoritos, Fontes V2, Modelos, Performance (Resultados · Calibração · por cluster/estado), Alertas, Sistema → Jobs, Sistema → Diagnóstico, Configurações |

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
  edgefut/features     força, forma, H2H, local do jogo, qualidade dos dados, TemporalFeatureStore (as_of)
  edgefut/models       ELO, Poisson, Dixon-Coles, Bivariate Poisson, ensemble (campeão/challenger), strength_v2, international_strength, calibração, registry
  edgefut/simulation   Monte Carlo
  edgefut/odds         implícita, margem (Shin/multiplicativa), movimento, closing line
  edgefut/recommendations edge, confiança v2, Opportunity V3, Quality Gate, estados, pricing, clusters/correlação, WHY/WHY NOT, NO BET, múltiplas
  edgefut/validation   replay (baselines), bootstrap, decay, shadow, drift, governance, model_health
  edgefut/backtesting  Lab (as_of), liquidação, reconciliação, métricas, performance por mercado/competição/cluster/estado
  edgefut/alerts       alertas locais
  edgefut/scheduler    jobs APScheduler com job_run + correlation id
  edgefut/db           SQLAlchemy, migrações, guarda de imutabilidade de snapshots
  edgefut/explanations templates, Edge AI, Ollama
  tests/               pytest (regressão + invariantes matemáticas + anti-leakage)
data/                  raw · processed (Parquet) · cache · sqlite (ignorado no git)
docs/                  ARCHITECTURE · DATA_SOURCES · MODELS · VALIDATION · MODEL_GOVERNANCE · ROADMAP · ITERATION_{2,3}_BASELINE · ITERATION_{2,3}_REPORT
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
`GET /live`, `GET /events/{id}/conflicts`, `GET /events/{id}/odds/history`, `GET /health/system`, `GET /jobs`, `POST /jobs/{job}/run`, `GET /alerts`,
`POST|GET /validation/replay[/latest]`, `POST|GET /validation/decay[/latest]`, `GET /validation/shadow`, `GET /validation/drift`, `GET /validation/coverage`, `GET /validation/governance`, `POST /validation/governance/promote`, `GET /validation/runs`.

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
- **MODEL FAVORITE ≠ VALUE** — um mercado pode ter alta probabilidade sem ter edge e vice-versa. Os rótulos são independentes e cada card mostra o seu.
- **Estados** — `MODEL ONLY` ("Probabilidade calculada, mas sem preço de mercado válido para determinar valor." — inclui **todas** as seleções de competições sem odds históricas, como seleções e MLS, mesmo que a Superbet tenha odd), `VALUE CANDIDATE` (passou no gate, falta prova out-of-sample ≥ 100 apostas no mercado), `VALUE` (passou e o mercado tem prova não negativa), `WATCH`/`OBSERVATION` (gate falhou, OOS negativo, ou preço curto: "Probabilidade interessante, mas preço atual não oferece margem suficiente."), `NO BET`.
- **Teses e exposição** — seleções da mesma tese (ex.: 1, 1X, DNB casa) formam um cluster com **uma** primária; as outras são `ALTERNATIVA`. A exposição do evento (LOW / MEDIUM / HIGH) avisa quando teses acionáveis são correlacionadas.
- **Price target** — break-even (1/p), odd mínima aceitável (satisfaz edge e EV mínimos), gap de preço, e se o edge sobrevive a −3 pp de probabilidade.
- **Opportunity Score V3** — 20 % confiança do modelo, 15 % qualidade dos dados, 10 % qualidade da calibração, 15 % edge, 10 % EV, 10 % frescor das odds, 10 % concordância entre modelos, 5 % performance histórica, 5 % tamanho da amostra; depois ajustes explícitos (MODEL ONLY → 0; VALUE CANDIDATE −5; alternativa × 0,85), visíveis no tooltip. Pesos editáveis em Configurações; nunca a odd.
- **Quality Gate** — para entrar em TOP OPORTUNIDADES a recomendação passa por 10 checks (qualidade ≥ 60 %, confiança ≥ 65, amostra ≥ 15 jogos, divergência ≤ 10 pp, edge plausível ≤ 15 pp sem calibrador, frescor, odds completas…). Os limiares são editáveis mas têm **pisos e tetos rígidos** e o sistema nunca os reduz sozinho ("não caçar entradas").
- **WHY THIS BET / WHY NOT** — motivos em texto, gerados só a partir dos números da análise, sem linguagem de garantia.
- **Evidência** — `MODEL_ONLY` (só modelo), `BACKTEST_ODDS` (competição com backtest sobre odds reais) ou `SETTLED` (previsões desta competição já liquidadas). Aparece em todo card.
- **Ver fontes** — origem, URL, data de coleta, confiança e amostra de cada estatística, mais os checks de qualidade e os componentes da confiança.

Motivos de NO BET / WATCH: `LOW_DATA`, `LOW_CONFIDENCE`, `NO_EDGE`, `MODEL_DISAGREEMENT`, `UNRELIABLE_SOURCE`, `SMALL_SAMPLE`, `LINEUP_UNCERTAINTY`, `EXTREME_ODDS_MOVEMENT`, `UNSUPPORTED_COMPETITION`, `STALE_DATA`, `QUALITY_GATE`, `MODEL_ONLY`, `OOS_NEGATIVE`, `WATCHING_PRICE`, `EXTREME_PROBABILITY` (probabilidade > 90 % sem amostra forte: penaliza a confiança, nunca trunca).

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

## Validação do modelo (iteração 3) — os números, sem maquiagem

| Pergunta | Resposta medida |
|---|---|
| Replay clubes (11 ligas com odds, 2022-08 → 2026-09, 463 janelas de 30 d) | **N 14.393** partidas |
| Brier 1X2 — campeão `ensemble` vs **mercado** | **0,5857 vs 0,5718 — o mercado é melhor.** ΔBrier +0,0147 [+0,0128; +0,0166], lift −2,56 %, melhor em 122/463 janelas → `NO CLEAR ADVANTAGE`. O mercado vence em 11/11 ligas |
| Brier 1X2 — campeão vs **ingênuo** (frequência da liga) | 0,5857 vs 0,6480 — lift +9,63 %, 412/463 janelas → `CONSISTENT` |
| Over/Under 2,5 | mercado 0,2389 vs ensemble 0,2452 — mercado melhor |
| Apostas simuladas (gate simplificado) | 1X2: ROI **−12,6 %** [−15,7; −9,2] N 7.939 · OU 2,5: **−7,6 %** [−9,9; −5,2] N 7.271 · CLV −3,5 % — `NEGATIVE` para todos os 6 modelos |
| Challengers | `ensemble_v2` Δ −0,0004 [−0,0007; −0,0001] mas só 53 % das janelas → `PROMISING · UNSTABLE`; `poisson_v2` → `KEEP CHAMPION`. **Nenhuma promoção** |
| Replay seleções (2010 →, 66 janelas de 90 d, **sem odds**) | **N 15.956**; Brier 0,5175 vs 0,6336 do ingênuo (+18,3 %, 66/66 janelas) e 0,5351 do ELO (+3,3 %) → `CONSISTENT`; por tipo de torneio lift +12 % (CONTINENTAL) a +25 % (QUALIFIER). Tudo `MODEL_ONLY` |
| Leakage tests | **PASS** (TemporalFeatureStore, Lab, replay) |
| Reconciliação de settlement | **PASS** — 117 liquidadas · 16 pendentes (< 72 h) · 0 erros · 0 divergências |
| Shadow mode | mecanismo **PASS** (2.438 previsões append-only), evidência **INSUFFICIENT** (0 liquidadas ainda) |
| Radar hoje (data FIFA + MLS) | 60 analisados · **0 VALUE · 0 VALUE CANDIDATE** · 47 eventos MODEL ONLY · 13 NO BET |

Conclusão honesta: o EdgeFut sabe futebol (bate os baselines ingênuos com
folga), mas **não sabe mais do que a odd média pré-jogo** — por isso hoje ele
não recomenda VALUE em nenhum mercado, e diz isso na tela (MODEL HEALTH:
`WATCH`). Metodologia em [docs/VALIDATION.md](docs/VALIDATION.md), regras de
promoção em [docs/MODEL_GOVERNANCE.md](docs/MODEL_GOVERNANCE.md), relatório
completo em [docs/ITERATION_3_REPORT.md](docs/ITERATION_3_REPORT.md).

---

## Market-aware & Superbet (iteração 4) — o modelo tem informação além do preço?

| Pergunta | Resposta medida |
|---|---|
| α do blend `p_mkt^α · p_edgefut^(1−α)` escolhido só em treino/validação | **α = 1,0 (mercado puro)** em 10/11 janelas 1X2 e 8/11 OU 2,5 → o EdgeFut não adicionou valor |
| Challengers `market-logistic-stack-v1` e `market-residual-v1` no **holdout congelado** (≥ 2026-01-23, rodado 1×, `model_hash a5e8d42f97f764d7`) | 1X2 n 2.231: mercado Brier 0,5831 · residual 0,5829 (Δ −0,0001 [−0,0020; +0,0017], 4/8 janelas) · stack 0,5834 · EdgeFut 0,5958 (+0,0127 [+0,0072; +0,0181]). OU 2,5 n 2.230: 0,4752 · 0,4749 · 0,4749 · 0,4853. **Nenhum passa `brier_better`** |
| Ablação (stack só com preço vs preço + features do EdgeFut) | `market_only` é a melhor variante nos dois mercados; adicionar a probabilidade do EdgeFut não melhora |
| Divergência forte (≥ 5 pp) — quem acerta? | **O mercado**, nos 4 testes (discovery/holdout × 1X2/OU). Bucket 10+ pp holdout 1X2: mercado 0,5755 vs EdgeFut 0,6290 |
| Segmentos após BH-FDR (q 0,10) | **0 sobreviventes** em 54 testes; os únicos p < 0,05 são vitórias do mercado |
| Superbet (coleta própria, 26 h): overround 1X2 · viés · CLV · shadow liquidado | 9,4 % (10,7 % a T-15m) · NO BIAS DETECTED em 13 segmentos · CLV −1,3 % (314 sel., 6 ev., sem IC) · 244 seleções / 5 jogos / **N efetivo 10** → `INSUFFICIENT` |
| Required edge (margem + incerteza + calibração + amostra + eficiência) | aplicado a toda seleção 1X2/OU 2,5; ex. Granada × Andorra OVER 2,5: bruto +11,7 pp < required 12,4 pp → `OBSERVATION` |
| Model Health V2 | FOOTBALL MODEL `PREDICTIVE (vs naive)` · MARKET MODEL `UNPROVEN` · SUPERBET EVIDENCE `COLLECTING` · SHADOW SETTLED `INSUFFICIENT` · **MARKET EDGE UNPROVEN** |

**Conclusão: NO EVIDENCE OF MARKET EDGE.** Prever futebol ≠ bater o mercado;
o EdgeFut faz o primeiro e não o segundo. Nenhum threshold foi relaxado, o
Radar continua com 0 VALUE e a tela diz isso. Detalhes em
[docs/ITERATION_4_REPORT.md](docs/ITERATION_4_REPORT.md),
[docs/MARKET_AWARE_MODELS.md](docs/MARKET_AWARE_MODELS.md) e
[docs/SUPERBET_VALIDATION.md](docs/SUPERBET_VALIDATION.md).

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
- [docs/VALIDATION.md](docs/VALIDATION.md) — replay, baselines, bootstrap, qualidade de amostra, shadow, reconciliação, drift
- [docs/MODEL_GOVERNANCE.md](docs/MODEL_GOVERNANCE.md) — champion/challenger, regra de promoção, o que nunca muda sozinho
- [docs/ROADMAP.md](docs/ROADMAP.md) — fases, estado e próximos passos
- [docs/ITERATION_2_BASELINE.md](docs/ITERATION_2_BASELINE.md) — auditoria do estado real antes da iteração 2 (o que estava quebrado)
- [docs/ITERATION_2_REPORT.md](docs/ITERATION_2_REPORT.md) — o que foi implementado, verificado com dados reais, limitações e riscos
- [docs/ITERATION_3_BASELINE.md](docs/ITERATION_3_BASELINE.md) — auditoria antes da iteração 3
- [docs/ITERATION_3_REPORT.md](docs/ITERATION_3_REPORT.md) — validação do modelo com números reais, limitações e riscos
- [docs/ITERATION_4_BASELINE.md](docs/ITERATION_4_BASELINE.md) — auditoria antes da iteração 4 (odds históricas sem carimbo, closing nulo no shadow, N efetivo)
- [docs/MARKET_AWARE_MODELS.md](docs/MARKET_AWARE_MODELS.md) — mercado como prior, challengers blend/stack/residual, CV temporal aninhada, holdout congelado, required edge
- [docs/SUPERBET_VALIDATION.md](docs/SUPERBET_VALIDATION.md) — evidência Superbet: cobertura, buckets, overround, movimento, viés, Superbet fair como baseline, CLV, shadow v2
- [docs/ITERATION_4_REPORT.md](docs/ITERATION_4_REPORT.md) — resultado: NO EVIDENCE OF MARKET EDGE, com todos os números

## Licença

Uso pessoal. Este software é uma ferramenta de análise e não constitui recomendação financeira.
