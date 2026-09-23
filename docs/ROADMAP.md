# EdgeFut AI — Roadmap

Fases conforme a especificação. Estado após a **iteração 2 (TRUST, LIVE &
INTELLIGENCE)**. Cada ✅ abaixo foi verificado por código, teste automatizado
e execução real (ver `docs/ITERATION_2_BASELINE.md` para o que estava quebrado
antes e `docs/ITERATION_2_REPORT.md` para o que foi verificado depois).

| Fase | Escopo | Estado |
|---|---|---|
| 0 | Foundation: desktop abre, API local, SQLite | ✅ engine + frontend; Tauri shell criado (build Windows via `scripts/build-windows.ps1` e workflow GitHub Actions) |
| 1 | Provider Superbet: eventos reais, odds reais, histórico de odds | ✅ API pública de oferta, 14 mercados mapeados, snapshots de odds; **V2**: Shin + multiplicativa armazenadas, `LineMovement`, closing line (só CLV) |
| 2 | Dados históricos, Team Strength, ELO | ✅ football-data.co.uk + international_results; strength-v1; elo-v1 |
| 3 | Poisson, Dixon-Coles, Monte Carlo | ✅ goals-poisson-v1, goals-dixon-coles-v1, mc-v1; **+ goals-bivariate-poisson-v1 e ensemble-v1** (pesos por walk-forward) |
| 4 | Radar, Página do Jogo, Odds, Edge, Confiança, NO BET | ✅ **Radar V2**: contadores, Opportunity Score V2 (0–100, 9 componentes), Quality Gate, rótulos HIGH PROBABILITY ≠ VALUE, WHY / WHY NOT, EDGEFUT CONFIDENCE 0–100 com breakdown |
| 5 | Corners, Cards, Shots | ✅ engines v1 (dependem de HC/AC/HS/HST no histórico — ligas football-data) |
| 6 | Jogadores | ⏳ apenas arquitetura (`providers/player`, `PlayerProvider` protocol). Sem fonte pública permitida → mercados de jogador exibem `LINEUP_UNCERTAINTY` |
| 7 | Backtesting, Performance, Calibração | ✅ Lab walk-forward **anti-leakage** (`as_of` em todo insumo), métricas por mercado/competição com `INSUFFICIENT SAMPLE`, calibração isotônica (≥300 amostras) com diagrama de confiabilidade RAW vs CALIBRATED |
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

## Próximos passos (após a iteração 2)

1. **Acumular liquidações**: hoje há 3 previsões liquidadas; calibração e
   pesos por competição só ganham significado com centenas de eventos
   finalizados (o scheduler já faz isso sozinho com o app aberto).
2. **Player Engine**: integrar fonte pública de minutos/xG/chutes quando
   houver uma permitida; até então NO BET em mercados de jogador.
3. **Árbitros**: expandir cards-v1 com árbitro quando a escala for pública.
4. **Strength Poisson ajustado por adversário** (hoje é a média simples de
   ataque/defesa — por isso o peso do ensemble em INTL é baixo).
5. **Importação manual de CSV** do usuário pela UI (Fontes → Importar).
6. **Testes de componente no frontend** (hoje só typecheck + build + testes de
   `packages/shared`).
7. **Auto-update** do desktop (Tauri updater) e assinatura do instalador.

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
