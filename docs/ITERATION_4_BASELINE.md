# Iteração 4 — Baseline (auditoria antes de qualquer mudança)

Data: 2026-09-24 14:15 UTC. Commit auditado: `baea2ae`. Método: banco real
(`data/edgefut.sqlite3`), engine em `127.0.0.1:8765`, leitura de código.
Nada foi presumido a partir do relatório da iteração 3.

## 1. Estado herdado da iteração 3 (confirmado)

| Item | Valor |
|---|---|
| Replay clubes (run 3) | 14.393 partidas · 14.353 com odds · 463 janelas · Brier 1X2 `ensemble` 0,5857 vs mercado 0,5718 (`NO CLEAR ADVANTAGE`) · OU 2,5 0,2452 vs 0,2389 |
| Apostas simuladas (gate simplificado) | 1X2 ROI −12,6 % · OU 2,5 −7,6 % · CLV −3,5 % → `NEGATIVE` |
| Replay seleções (run 10) | 15.956 partidas · 0 com odds · `MODEL_ONLY` |
| Model Health | `WATCH` — mantido; não será alterado artificialmente |
| Radar | 0 VALUE · 0 VALUE CANDIDATE |
| Testes | 151 passed; typecheck/build verdes |

## 2. O que as odds históricas do football-data representam (§7 do spec)

`providers/historical/football_data.py`:

| Coluna EdgeFut | Origem | Timestamp econômico |
|---|---|---|
| `odds_h/d/a` | `B365H/D/A` (fallback `AvgH/D/A`) | odd da Bet365 **coletada pelo football-data em um instante não registrado por linha** (historicamente sextas à tarde para jogos de fim de semana / terças para meio de semana). Não há carimbo de hora por partida |
| `odds_o25/u25` | `Avg>2.5` / `Avg<2.5` | **média** de casas no mesmo instante de coleta; também sem carimbo |
| `odds_close_*` | `PSCH/D/A` (Pinnacle closing) | fechamento — usado só para CLV |
| formato "new" (BRA/USA…) | `PH/PD/PA` = Pinnacle "pré-fechamento" e `PSC*` fechamento | idem, sem carimbo |

**Conclusão**: não é possível provar que uma odd existia no horário simulado
pelo replay (início da janela ou `as_of` da partida), nem que era da Superbet.
Portanto o replay **não** pode ser chamado de *tradable historical backtest*.
A partir desta iteração ele é classificado como **RESEARCH MARKET BENCHMARK**
e separado da **SUPERBET SHADOW VALIDATION** (única evidência de
executabilidade).

## 3. Evidência Superbet acumulada até agora

| Item | Valor |
|---|---|
| `odds_snapshot` | 83.806 linhas · 229 eventos · desde 2026-09-23 13:18 UTC · ~4 snapshots por seleção em média; cadência real 5–25 min (job `odds` a cada 5 min, mas com interrupções por suspensão da VM: `sync_odds` 7 × `interrupted`) |
| `closing_line` | 4.876 linhas · 94 eventos |
| `shadow_prediction` | **3.740** linhas · **48 eventos únicos** · **212 liquidadas** (5,7 %) · 0 erros |
| Shadow por evidência | `MODEL_ONLY` 3.638 (INTL) · `BACKTEST_ODDS` 102 (SP2) · dataset USA 53 |
| Shadow liquidadas por mercado | TOTAL_GOALS 40 · HANDICAP 40 · CORRECT_SCORE 32 · TEAM_TOTAL_HOME/AWAY 24+24 · 1X2 12 · DOUBLE_CHANCE 12 · FIRST_GOAL 12 · DNB 8 · BTTS 8 |
| Shadow por estado | MODEL_ONLY 1.750 · MARKET_OBSERVED 909 · NO_BET 628 · OBSERVATION 453 · **VALUE / VALUE_CANDIDATE 0** |
| `prediction_snapshot` | 206 · 34 liquidados |

**Achado 1 — Superbet CLV impossível hoje.** `shadow_prediction.closing_odd`
está `NULL` em **100 %** das linhas (0 de 3.740), embora `closing_line` tenha
4.876 preços de 94 eventos. O `reconcile` liquida o resultado mas não anexa a
odd de fechamento da Superbet → nenhum CLV bookmaker-specific pode ser
calculado. Corrigir é pré-requisito dos §9, §26–28.

**Achado 2 — snapshots por seleção não são indexados por tempo até o
kickoff.** `odds_snapshot` tem `collected_at` mas não `minutes_to_kickoff`,
`fair_probability`, `overround` nem `source_snapshot_id`. Os buckets T-24h …
T-15m (§8) precisam ser derivados de `collected_at − kickoff_utc`, e só existem
quando houve coleta naquele instante (não inventar).

**Achado 3 — amostra Superbet é pequena e correlacionada.** 3.740 linhas
vêm de 48 eventos (≈ 78 seleções por evento). O "N" real é ~48, não 3.740;
qualquer IC que trate as linhas como independentes é otimista (§19–20).

**Achado 4 — a amostra shadow é quase toda MODEL_ONLY.** 97 % das linhas são
de seleções (INTL) sem preço histórico validado. A comparação
"EdgeFut × Superbet fair × híbrido" (§12) é possível — a odd Superbet existe —
mas o estado VALUE continua vedado por §29 da iteração 3.

## 4. O que já existe e será reaproveitado

- `validation/replay.py` produz, por partida e janela, probabilidades de 6
  modelos e 5 baselines com odds "daquele momento" (football-data) — mas
  **não persiste o frame por partida**, só agregados. A camada market-aware
  precisa desse frame (features em T) → será gravado em Parquet versionado.
- `validation/bootstrap.py`: IC percentílico por observação; **não** há modo
  por cluster de evento nem `effective_sample_size`.
- `recommendations/pricing.py`: price target; **não** há required edge nem
  intervalo de incerteza.
- `odds/closing.py` + job `closing_lines`: closing da Superbet por seleção.
- `validation/shadow.py`: relatório diário com `priced / value_only /
  model_only`; sem Superbet fair como baseline, sem híbrido, sem N efetivo.

## 5. Features reconstruíveis no replay (para o HistoricalQualityGate, §13)

| Check do gate real | No replay |
|---|---|
| edge / EV / faixa de odd | reconstruível (odds football-data) |
| model agreement (divergência entre modelos relevantes) | reconstruível (DC × BP × Poisson na mesma janela) |
| sample strength (menor amostra de time) | reconstruível (jogos no treino) |
| market availability | reconstruível (odds presentes) |
| competition confidence (evidência da competição) | reconstruível (dataset com odds ⇒ BACKTEST_ODDS) |
| correlation / clusters | reconstruível parcialmente (1X2 e OU são teses distintas) |
| calibration state | reconstruível (nenhum calibrador teria ≥ 300 liquidados na época → RAW) |
| **data quality** (frescor, proveniência, conflitos) | **UNAVAILABLE_IN_REPLAY** |
| **odds freshness / provider health** | **UNAVAILABLE_IN_REPLAY** |
| **lineups** | **UNAVAILABLE_IN_REPLAY** |

## 6. O que a iteração 4 precisa provar

1. Se `p_mercado^α · p_edgefut^(1−α)` com α escolhido **no treino** bate o
   mercado puro (α = 1) fora da amostra — e com que α.
2. Se um stacking logístico regularizado e um modelo residual acrescentam
   informação **fora da amostra e no holdout congelado**.
3. Onde (mercado, competição, faixa de odd, tempo até o kickoff) o mercado
   vence, o modelo vence, ou é inconclusivo — com controle de múltiplos
   testes.
4. Se existe CLV positivo **na Superbet** (após corrigir o Achado 1).
5. Se A/B/C/D de confiança e os bins do Opportunity Score ordenam o Brier OOS.

Se nada disso for positivo: **NO EVIDENCE OF MARKET EDGE**, e o produto foca
em agregação de informação, acompanhamento de linha e visualização de
probabilidade.
