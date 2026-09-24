# EdgeFut AI — Roadmap

Fases conforme a especificação. Estado após a **iteração 5 (SUPERBET DATA
FLYWHEEL & SPECIALIZED MARKET DISCOVERY)**. Cada ✅ abaixo foi verificado por
código, teste automatizado e execução real (baselines em `ITERATION_N_BASELINE.md`;
relatórios em `ITERATION_N_REPORT.md`, N = 2…5).

| Fase | Escopo | Estado |
|---|---|---|
| 0 | Foundation: desktop abre, API local, SQLite | ✅ engine + frontend; Tauri shell criado (build Windows via `scripts/build-windows.ps1` e workflow GitHub Actions) |
| 1 | Provider Superbet: eventos reais, odds reais, histórico de odds | ✅ API pública de oferta, 14 mercados mapeados, snapshots de odds; **V2**: Shin + multiplicativa armazenadas, `LineMovement`, closing line (só CLV) |
| 2 | Dados históricos, Team Strength, ELO | ✅ football-data.co.uk + international_results; strength-v1; elo-v1 |
| 3 | Poisson, Dixon-Coles, Monte Carlo | ✅ goals-poisson-v1, goals-dixon-coles-v1, mc-v1; **+ goals-bivariate-poisson-v1 e ensemble-v1** (pesos por walk-forward); **iteração 3**: strength-v2 opponent-adjusted + goals-poisson-v2 + international-strength-v1 como **challengers** (não promovidos — ver governança) |
| 4 | Radar, Página do Jogo, Odds, Edge, Confiança, NO BET | ✅ **Radar V2**: contadores, Opportunity Score V2 (0–100, 9 componentes), Quality Gate, rótulos HIGH PROBABILITY ≠ VALUE, WHY / WHY NOT, EDGEFUT CONFIDENCE 0–100 com breakdown |
| 5 | Corners, Cards, Shots | ✅ engines v1 (dependem de HC/AC/HS/HST no histórico — ligas football-data) |
| 6 | Jogadores | ⏳ apenas arquitetura (`providers/player`, `PlayerProvider` protocol). Sem fonte pública permitida → mercados de jogador exibem `LINEUP_UNCERTAINTY` |
| 7 | Backtesting, Performance, Calibração | ✅ Lab walk-forward **anti-leakage** (`as_of` em todo insumo), métricas por mercado/competição com `INSUFFICIENT SAMPLE`, calibração isotônica (≥300 amostras) com diagrama de confiabilidade RAW vs CALIBRATED; **iteração 3**: Historical Replay com 5 baselines, bootstrap IC 95 %, qualidade de amostra, significância, performance por cluster/estado |
| — | **Validação & governança (iteração 3)** | ✅ TemporalFeatureStore, reconciliação de settlement, shadow mode append-only + relatório diário, drift monitor (só alerta), decay por walk-forward, champion/challenger com regra de promoção explícita, MODEL HEALTH, estados MODEL_ONLY → VALUE, clusters (uma primária por tese), price target/watchlist, WHY MODEL CHANGED, extreme probability guard |
| 8 | Edge AI | ✅ Q&A grounded por templates; Ollama opcional |
| 9 | Empacotamento Windows | ✅ scripts PowerShell + sidecar PyInstaller + workflow `windows-build.yml` (release `dev-latest`) |
| — | **Ao Vivo (observação)** | ✅ `offerState=live`, polling com backoff, placar/minuto/odds reais, **sem recomendações**, nada estimado |
| — | **Data Quality** | ✅ freshness FRESH/AGING/STALE/EXPIRED por tipo de insumo, Source Conflict Engine, CanonicalEventResolver, Health Dashboard (Sistema → Diagnóstico) |
| — | **Operação** | ✅ Scheduler V2 com `job_run` e correlation id (Sistema → Jobs), alertas locais, imutabilidade de snapshots + correções, `model_registry`, cache por versão |
| — | **Market-aware & Superbet (iteração 4)** | ✅ mercado como prior, challengers blend/stack/residual, holdout congelado (1×), Superbet evidence engine, required edge; **resultado: NO EVIDENCE OF MARKET EDGE** |
| — | **Data Flywheel (iteração 5)** | ✅ MODEL FREEZE; raw/normalizada **append-only** (triggers), alvos T-x + closing, cobertura, saúde, lacunas, quarentena, registo de todo marketId; settlement por mercado (nunca assume derrota); Margin Lab/CLV V2/STEAM-DRIFT-STABLE; hipóteses pré-registadas + FDR; estados de edge por mercado, `VALUE_ENABLED=false`, `RESEARCH_SIGNAL`, staking DISABLED; **coletor em segundo plano** (bandeja, autostart); backup 7/4/3, restore, export; **resultado: INSUFFICIENT — 0 VALUE é o resultado correto** |

## Iteração 2 — critérios de saída

- [x] baseline auditado antes de qualquer mudança (`docs/ITERATION_2_BASELINE.md`)
- [x] nenhum dado EXPIRED usado silenciosamente (NO BET `STALE_DATA`)
- [x] divergências entre fontes registradas e clicáveis ("N divergências resolvidas")
- [x] odds: Shin + multiplicativa, ambas armazenadas; método configurável
- [x] movimento de linha com probabilidade implícita ("1.72 → 1.54 −10.5%")
- [x] closing line usada **apenas** para CLV
- [x] backtest sem vazamento (teste de invariante: nenhum insumo posterior a `as_of`)
- [x] calibração só ativa com ≥300 amostras; RAW vs CALIBRATED sempre visível
- [x] Bivariate Poisson + Model Comparison + Consensus
- [x] pesos do ensemble derivados de walk-forward (nunca fixos manualmente)
- [x] Opportunity Score V2 com pesos configuráveis; limiares nunca reduzidos automaticamente
- [x] Quality Gate para TOP OPORTUNIDADES (pisos e tetos rígidos)
- [x] Ao Vivo em modo observação, "Odds atualizadas há N s", sem recomendações
- [x] Performance por mercado/competição com `INSUFFICIENT SAMPLE`
- [x] 132 testes backend (regressão + invariantes matemáticas), typecheck e build verdes
- [x] documentação atualizada (README, ARCHITECTURE, MODELS, DATA_SOURCES, relatório)

## Iteração 3 — critérios de saída (regra final: números, não "funciona")

- [x] baseline auditado antes de qualquer mudança (`ITERATION_3_BASELINE.md`)
- [x] settlement reconciliado: todo evento terminado tem estado; 117 SETTLED · 16 PENDING · 0 ERROR · 0 divergências
- [x] `TemporalFeatureStore` no pipeline e no replay; leakage tests **PASS** (3 novos + 5 do Lab + 2 do replay)
- [x] strength-v2 opponent-adjusted; half-life por walk-forward (365 d, empate com 180)
- [x] international-strength-v1 com tipo de torneio e peso de amistoso
- [x] 5 baselines + lift com IC pareado
- [x] Historical Replay: **clubes N 14.393** (11 ligas, 463 janelas) · **seleções N 15.956** (66 janelas)
- [x] Brier campeão vs mercado 1X2: **0,5857 vs 0,5718 — mercado melhor (NO CLEAR ADVANTAGE, lift −2,56 %)**; vs ingênuo +9,63 % CONSISTENT
- [x] IC por mercado nas apostas simuladas: 1X2 ROI −12,6 % [−15,7; −9,2] · OU 2,5 −7,6 % [−9,9; −5,2] — **NEGATIVE**
- [x] bootstrap, qualidade de amostra (100 / 300 / 1.000), significância, walk-forward v2
- [x] clusters com uma primária por tese; exposição por evento
- [x] estados MODEL_ONLY / MARKET_OBSERVED / VALUE_CANDIDATE / VALUE / OBSERVATION / NO_BET; §29 (competição MODEL_ONLY ⇒ tudo MODEL_ONLY)
- [x] price target, break-even, odd mínima aceitável, sensibilidade do edge, watchlist
- [x] shadow mode append-only: **2.438 previsões, 0 liquidadas** (mecanismo PASS, evidência INSUFFICIENT)
- [x] drift monitor só alerta (`INSUFFICIENT DATA` hoje); extreme probability guard penaliza confiança, nunca trunca
- [x] WHY MODEL CHANGED; champion/challenger com regra de promoção (ROI não é critério); nenhuma promoção
- [x] UI: uma página nova (Validação) + estados/clusters/price target/model health nas telas existentes; Player Engine continua fechado
- [x] 151 testes backend, typecheck e build verdes
- [x] documentação: `ITERATION_3_REPORT.md`, `VALIDATION.md`, `MODEL_GOVERNANCE.md`; README, ARCHITECTURE, MODELS, DATA_SOURCES atualizados

## Iteração 4 — critérios de saída (§50) e resultado

- [x] baseline auditado (`ITERATION_4_BASELINE.md`): odds football-data sem carimbo → RESEARCH MARKET BENCHMARK; `closing_odd` nulo em 100 % do shadow (corrigido); N efetivo
- [x] mercado como prior; challengers `market-model-blend-v1` (α ∈ {0,5…1,0}), `market-logistic-stack-v1`, `market-residual-v1`; sem deep learning nem boosting
- [x] closing line **nunca** feature/treino/recomendação — teste automatizado; só CLV
- [x] CV temporal aninhada (validação 180 d / teste 90 d), nunca split aleatório; ablação por grupo de features
- [x] FROZEN HOLDOUT (≥ 2026-01-23) com `model_hash a5e8d42f97f764d7`, `config_hash 6e0e8a7db0fe9119`, `dataset_version replay-frame-v1:55b9f039e3fa730c`, rodado **1×** (409 se repetido)
- [x] bootstrap por cluster de evento, N efetivo (Raw N vs Effective N na UI), BH-FDR, decomposição de Brier, buckets de divergência, teste contrarian
- [x] `SuperbetEvidenceEngine`: buckets T-24h…T-15m só quando existem, overround por mercado/competição/faixa/tempo, opening/rec/closing, viés, Superbet fair como baseline, CLV próprio; camadas raw append-only / `superbet-shadow-v1`
- [x] `RequiredEdgeEngine` + intervalo de incerteza + EDGE BRUTO vs AJUSTADO; `HistoricalQualityGate` com `UNAVAILABLE_IN_REPLAY`
- [x] shadow v2 (Superbet fair × EdgeFut × híbrido), validação OOS de grau A/B/C/D e bins do Opportunity Score; regra de promoção market-aware com CLV ≥ 0; Model Health V2
- [x] UI: MARKET vs EDGEFUT no jogo; abas Market-Aware e Superbet; Market Efficiency Lab; Superbet Lab
- [x] 183 testes backend, typecheck e build verdes
- [x] **Resultado: α = 1,0 venceu; nenhum challenger passa `brier_better` no holdout (1X2 Δ −0,0001 [−0,0020; +0,0017]; OU 2,5 −0,0002 [−0,0013; +0,0009]); mercado correto nos testes contrarian; 0 segmentos sobrevivem ao FDR; Superbet N efetivo 10 → NO EVIDENCE OF MARKET EDGE.** Nenhum threshold relaxado; Model Health `WATCH`; 0 VALUE

## Iteração 5 — critérios de saída (§69) e resultado

- [x] baseline auditado (`ITERATION_5_BASELINE.md`): sem raw layer, snapshots só quando o preço muda, unknown markets descartados (14/414 mapeados), sem métricas de coletor, settlement ambíguo, sem quarentena/backup/background
- [x] **MODEL FREEZE**: 21 modelos com `model_hash 4608dbf2fe2b87e4`, `config_hash 9846d74330b32763`; sem DL/boosting; 1X2 market-aware em PAUSE; nenhum treino nesta iteração
- [x] DB v5: `raw_superbet_snapshot` e `superbet_normalized_v1` **append-only por triggers SQLite**; `market_mapping_registry`, `data_quarantine`, `collector_gap`, `experiment_registry`, `manual_correction`, `market_edge_state`
- [x] Collector V2: 1 raw por fetch real, confirmação por referência, alvos T-48h…T-5m + `LAST_PRE_KO`, sem interpolação; cobertura por alvo/mercado; saúde HEALTHY/DEGRADED/BROKEN; lacunas de downtime; schema change; backfill único e honesto do cache HTTP
- [x] canonicalização de 15 mercados; 460 marketIds registados → 24 MAPPED / 436 OUT_OF_SCOPE (18 motivos) / **0 UNKNOWN**; decisão manual auditada; mapear ambíguo é proibido
- [x] Settlement V2 por mercado: `WON/LOST/VOID/UNSETTLED_DATA_MISSING/UNSUPPORTED`; nunca assume derrota; auditoria + MARKET DATA COVERAGE
- [x] Research: Margin Lab (faixa × T × linha), eficiência por T, CLV V2, STEAM/DRIFT/STABLE, SIGNAL VS MOVEMENT; discovery com maturidade COLLECTING/EARLY/TESTABLE/MATURE; 8 hipóteses pré-registadas; BH-FDR q 0,10
- [x] Governança: estados UNPROVEN→COLLECTING→PROMISING→VALIDATED/REJECTED por mercado; 6 regras de `VALUE_ENABLEMENT_CANDIDATE`; `VALUE_ENABLED=false`; `RESEARCH_SIGNAL`; Required Edge V2; staking/Kelly DISABLED
- [x] UI: faixa §3; Data Flywheel; Superbet Lab (3 abas); Pesquisa; Sistema → Dados (6 abas); cartão RESEARCH SIGNAL; saúde do coletor no rodapé; Configurações de background
- [x] Windows always-on: bandeja com saúde/último sync, close-to-tray, autostart opcional (`--tray`), Sair encerra sidecar, `collector_gap` no boot
- [x] backup diário 7/4/3 + manifest, restore com cópia prévia (recusa ficheiro ilegível), export CSV/Parquet, storage dashboard, quarentena com UI, relatórios diário/semanal RESEARCH ONLY
- [x] 201 testes backend (18 novos §68), ruff, typecheck e build verdes; correção de bloqueio de ficheiro sqlite3 apanhada pelo CI Windows
- [x] **Resultado: `INSUFFICIENT` — 640 raw, 381 jogos, 16 095 normalizadas, 0 liquidadas (primeiro kickoff com preço pré-jogo real 24/09 19:00 UTC), 15 mercados COLLECTING/UNPROVEN, 0 VALUE, 0 candidatos. Overround por mercado medido (1X2 9,0 %, totais 8,0–8,2 %, escanteios 7,9 %). 0 VALUE é o resultado correto (§72).**

## Próximos passos (após a iteração 5)

1. **Deixar o app a correr** (coletor em segundo plano). Marcos: `EARLY` a 50 clusters liquidados por mercado, `TESTABLE` a 200, `MATURE` a 500; hipóteses só contam para o FDR após 30 dias de confirmação (≥ 2026-10-24).
2. Quando `TOTAL_GOALS`/`CORNERS_TOTAL` chegarem a `TESTABLE`: ler **Superbet fair vs resultado** e **CLV por T** antes de qualquer EdgeFut vs fair.
3. Registar como **nova** hipótese a observação "margem de totais a T-48h/T-24h (≈ 5 %) < a T-1h (≈ 8 %)"; nunca testar no dado em que foi vista.
4. Validar em Windows real: close-to-tray, tooltip de saúde, arranque `--tray`, `collector_gap` após reboot.
5. Provider de estatísticas secundárias (escanteios/cartões/finalizações) para que `UNSETTLED_DATA_MISSING` desça — sem isso os mercados secundários ficam em COLLECTING por falta de liquidação, não por falta de preço.

## Próximos passos herdados (após a iteração 4)

1. **Acumular shadow Superbet liquidado com closing** (≥ 30 jogos, depois ≥ 300 seleções por mercado) para que CLV, viés e Superbet-fair-vs-EdgeFut tenham IC por evento.
2. Qualquer nova hipótese market-aware exige novo `config_hash`, nova descoberta e **holdout em período posterior a 2026-09-24**; nunca reajustar sobre o holdout já consumido.
3. Fonte de odds históricas com carimbo (ou o acervo Superbet do próprio app, quando tiver meses) para converter o RESEARCH MARKET BENCHMARK em backtest negociável.
4. Itens herdados da iteração 3 que continuam válidos (abaixo).

## Próximos passos herdados (após a iteração 3)

1. ~~Acumular shadow liquidado para comparar com a Superbet~~ — mecanismo
   pronto na iteração 4 (Superbet fair como baseline, CLV próprio); falta só
   tempo de coleta (ver item 1 acima).
2. ~~Reproduzir o Quality Gate no replay~~ — feito parcialmente como
   `HistoricalQualityGate` (edge/EV/odd, amostra, divergência
   `RECONSTRUCTED`; frescor, provider, lineups, clusters
   `UNAVAILABLE_IN_REPLAY`). Bloqueou 24,6 k de 83 k apostas simuladas; ROI
   continuou negativo.
3. ~~Modelo híbrido mercado + modelo~~ — feito (blend, stack, residual).
   Resultado: **NO EVIDENCE OF MARKET EDGE**; nenhuma promoção.
4. **Odds históricas de mais mercados** (OU outras linhas, BTTS, handicap) para
   ampliar a base com baseline de mercado.
5. **Player Engine**: integrar fonte pública de minutos/xG/chutes quando
   houver uma permitida; até então NO BET em mercados de jogador.
6. **Árbitros**: expandir cards-v1 com árbitro quando a escala for pública.
7. **Importação manual de CSV** do usuário pela UI (Fontes → Importar).
8. **Testes de componente no frontend** (hoje só typecheck + build + testes de
   `packages/shared`).
9. **Auto-update** do desktop (Tauri updater) e assinatura do instalador.

## Critérios de saída da iteração 1 (entregável)

- [x] projeto compilando (engine: pytest; frontend: `tsc` + `vite build`)
- [x] frontend funcionando
- [x] backend funcionando em 127.0.0.1
- [x] banco local (SQLite + Parquet/DuckDB)
- [x] SuperbetProvider inicial
- [x] Data Source Manager (SourceResolver + tela Fontes)
- [x] evento real (Japão x Uruguai · Servette (F) x Lyon (F))
- [x] tela de jogo
- [x] implied probability + remoção de margem
- [x] ELO inicial
- [x] Poisson inicial (+ Dixon-Coles)
- [x] Monte Carlo
- [x] probabilidades 1X2 / Over-Under / BTTS / placares
- [x] Confidence Engine inicial
- [x] Edge Engine
- [x] botão "Ver fontes"
- [x] design premium
- [x] README completo
- [x] scripts Windows
