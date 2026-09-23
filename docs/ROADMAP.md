# EdgeFut AI — Roadmap

Fases conforme a especificação. Estado após a **iteração 3 (MODEL VALIDATION
& PREDICTIVE QUALITY)**. Cada ✅ abaixo foi verificado por código, teste
automatizado e execução real (baselines em `ITERATION_2_BASELINE.md` e
`ITERATION_3_BASELINE.md`; relatórios em `ITERATION_2_REPORT.md` e
`ITERATION_3_REPORT.md`).

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

## Próximos passos (após a iteração 3)

1. **Acumular shadow liquidado** (≥ 300 por mercado) para comparar o modelo
   com as odds **da Superbet** — a única forma de saber se há mercado onde a
   Superbet é menos eficiente que a média do football-data.
2. **Reproduzir o Quality Gate completo no replay** (confiança, frescor,
   qualidade, clusters) para medir se ele separa apostas boas de ruins ou só
   reduz N.
3. **Modelo híbrido mercado + modelo** (ex.: logística sobre `logit(p_mercado)`
   e `logit(p_modelo)`) como challenger, avaliado pela regra de promoção — o
   mercado é hoje o melhor preditor isolado.
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
