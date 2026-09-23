# Iteração 3 — Baseline (auditoria antes de qualquer mudança)

Data da auditoria: 2026-09-23 21:25 UTC. Commit auditado: `ef978a1`.
Método: execução real (engine em 127.0.0.1:8765 com banco populado pela
iteração 2), testes automatizados, leitura de código. Nada abaixo foi presumido
a partir do ROADMAP.

## 1. Estado de compilação e testes

| Verificação | Resultado |
|---|---|
| `pytest` (engine) | **132 passed** em 3,8 s |
| `pnpm -r typecheck` | OK (`packages/shared`, `apps/desktop`) |
| `pnpm build` (Vite) | OK (vendor 206 kB · charts 405 kB gzip 110 kB) |
| `GET /health` | `ok`, 12 datasets, scheduler com carimbos de todos os jobs |
| `GET /health/system` | `HEALTHY`; 8 componentes HEALTHY, 2 UNAVAILABLE por design (jogadores, Ollama) |

## 2. Saúde real dos providers

| Componente | Status | Detalhe observado |
|---|---|---|
| Superbet (odds pré-jogo) | HEALTHY | 143 eventos · 9.156 mercados |
| Superbet (fixtures) | HEALTHY | sincronizados há instantes |
| Histórico (football-data / international_results) | HEALTHY | última sync há 7 h · 12 datasets · 38.677 partidas |
| Resultados (settlement) | HEALTHY | 3 liquidadas · 3 pendentes |
| Jogadores / escalações | UNAVAILABLE | por design (sem fonte permitida) |
| Ollama | UNAVAILABLE | por design (templates em uso) |

## 3. Saúde real do scheduler

- 11 jobs registrados; `job_run` com 100 execuções nas últimas 6 h e 0 erros.
- **Achado 1 — congelamento invisível.** Entre 18:59:34 e 21:23:12 UTC não
  houve nenhuma execução (VM suspensa). O `job_run` de `live_poll` registrou
  `ok em 8.601.536 ms` (2 h 23 min) e o APScheduler logou `Run time of job …
  was missed` para `sync_odds`, `radar`, `closing_lines`, `sync_events` e
  **`settle`**. O `settle` perdido **não é reexecutado** — só volta na próxima
  hora cheia. O Health reportou "0 erros" durante e depois do congelamento.
  Conclusão: o scheduler não tem watchdog nem re-execução de jobs perdidos, e
  o Health não distingue "nada rodou" de "tudo correu bem".
- **Achado 2 — execuções órfãs.** `job_run` tem linhas em `running` que nunca
  terminam (`sync_odds` ×3, `radar` ×1, `live_poll` ×2), herdadas de reinícios
  do processo. Nada as marca como interrompidas.

## 4. Os sete invariantes exigidos

| # | Invariante | Verificação | Resultado |
|---|---|---|---|
| 1 | Settlement roda via scheduler | `job_run(job=settle, status=ok, 72 s)` em 18:25 UTC; 3 snapshots liquidados; `Event.settled_at` preenchido em 66 eventos | **CONFIRMADO**, com ressalvas do §3 (job perdido não reexecuta) e do §5 (cobertura parcial) |
| 2 | `prediction_snapshot` imutável | `tests/test_models_v2.py::test_snapshot_prediction_fields_are_immutable_but_result_is_settable` (guarda `before_flush` levanta `ImmutableSnapshotError`) | **CONFIRMADO** |
| 3 | Closing odds nunca entram na decisão | `tests/test_backtest_leakage.py::test_closing_odds_never_drive_the_decision`; `odds/closing.py` só alimenta `closing_odds` do snapshot (CLV) | **CONFIRMADO** |
| 4 | `as_of` impede leakage | `test_walk_forward_windows_never_see_future`, `test_assert_no_leakage_raises_on_future_rows`; pipeline usa `before=as_of` em `team_matches`, `competition_matches`, `h2h` | **CONFIRMADO no Lab**. Ressalva: a proteção está espalhada em chamadas individuais ao `HistoricalStore`; não há camada única que impeça um modelo de consultar dados futuros (é o que a iteração 3 chama de TemporalFeatureStore) |
| 5 | Dado EXPIRED gera NO BET | `tests/test_freshness_conflicts.py::test_expired_odds_block_recommendations`; `pipeline._freshness` + `engine._event_no_bet(STALE_DATA)` | **CONFIRMADO** |
| 6 | Calibração não ativa com N < 300 | `tests/test_calibration.py::test_unreliable_calibrator_returns_raw_probability`, `test_engine_uses_calibrated_probability_only_when_reliable`; `MIN_CALIBRATION_N = 300`; hoje 0 calibradores confiáveis | **CONFIRMADO** |
| 7 | MODEL_ONLY não vira VALUE | Radar real: `value: 33`, `gate_passed_by_evidence: {MODEL_ONLY: 33}` — **todas** as 33 seleções rotuladas VALUE têm evidência MODEL_ONLY. `opportunity_label()` ignora a evidência | **FALHA** |

## 5. Achados adicionais (medidos)

### 5.1 Redundância de recomendações no mesmo evento

Japão × Uruguai (`/events/15015442/analysis`), 7 seleções RECOMMENDED e 7 WATCH,
todas derivadas de **duas teses**:

| Tese | Seleções RECOMMENDED |
|---|---|
| Japão superior | DNB Japão @1,34 · Handicap Japão +0,5 @1,22 · Dupla Chance 1X @1,23 · Empate @3,50 |
| Poucos gols | Japão Under 1,5 @1,73 · Japão Under 0,5 @4,10 · Japão Under 2,5 @1,20 |

O Radar conta 33 "value" para 36 eventos porque cada evento contribui com
várias seleções correlacionadas. Não existe cluster nem `primary`.

### 5.2 Viés sistemático para Under em seleções

Das 15 recomendações principais no Radar, 13 são `TEAM_TOTAL_* UNDER` e 1 é
`TOTAL_GOALS UNDER`. No mesmo evento o modelo dá Under 2,5 = 74 % contra 55 %
do mercado e Under 3,5 = 90 % contra 75 %. Edge de +15 a +20 pp em mercados de
gols de seleções, sem qualquer validação contra mercado (`MODEL_ONLY`), é mais
provável erro de modelo (força não ajustada por adversário; média de gols de
amistosos) do que ineficiência da Superbet. Não há hoje nenhuma medição que
confirme ou refute isso.

### 5.3 Cobertura histórica real (não é o que o README sugere)

| Dataset | Partidas | Período | Com odds | Com chutes/escanteios/cartões |
|---|---|---|---|---|
| E0, SP1, I1 | 810 / 829 / 810 | ago-2024 → set-2026 | 100 % | 100 % |
| D1, F1, N1, P1, B1, T1, SC0 | 498–702 cada | ago-2024 → set-2026 | ~100 % | ~100 % |
| USA (MLS) | 6.203 | 2012 → 2026 | 0 % | 0 % |
| INTL (seleções) | 25.485 | 2000 → 2026 | 0 % | 0 % |

Só **10 ligas × 2 temporadas (~7.000 jogos)** têm odds reais; `bootstrap_seasons`
lista `2324` mas nenhuma linha de 2023/24 está carregada. Seleções: 3.149
amistosos e 2.618 eliminatórias desde 2015 — sem odds, portanto validáveis só
em Brier/LogLoss contra baselines ingênuos, nunca em ROI.

### 5.4 Cobertura do settlement

121 eventos com kickoff há mais de 3 h; 66 com resultado; 2 eventos com
snapshot já encerrados e ainda sem liquidação. Não há estado explícito
(`SETTLED / SETTLEMENT_PENDING / SETTLEMENT_ERROR`) — um evento sem resultado
simplesmente não aparece em nenhuma métrica.

### 5.5 Performance real

`prediction_snapshot`: 101 gravados, 3 liquidados, 0 apostas RECOMMENDED
liquidadas. Performance por mercado: tudo `INSUFFICIENT_SAMPLE`. Nenhuma métrica
tem intervalo de confiança; não existe baseline de comparação (mercado, ELO,
frequência ingênua); o Lab só roda Dixon-Coles/Poisson em 1X2 e O/U 2,5.

### 5.6 Strength-v1

`features/strength.py::attack_defense` usa média de gols pró/contra do time
dividida pela média da liga, com shrinkage por N — **sem qualquer ajuste pela
força dos adversários enfrentados**. Um time que marcou 10 gols contra
adversários fracos recebe o mesmo ataque que um que marcou 10 contra fortes.
Vantagem de mando: multiplicador único da liga (`la.home_goals / la.away_goals`),
sem variação por competição/temporada além disso. Seleções usam exatamente o
mesmo pipeline dos clubes (com filtro de 3 anos), sem distinguir amistoso de
oficial.

## 6. O que a iteração 3 precisa provar (e o que este baseline já indica)

1. Se os modelos têm capacidade preditiva → só medível com replay histórico +
   baselines + IC; hoje inexistente.
2. Em quais mercados/competições → hoje só 1X2 e O/U 2,5 em 10 ligas.
3. Com qual amostra → ~7.000 jogos com odds; seleções sem odds.
4. Estabilidade → walk-forward existe, mas sem comparação expanding × rolling
   nem IC por janela.
5. Se superam baselines → sem baselines.
6. Se as oportunidades não são redundantes → §5.1 mostra que são.

O achado mais relevante para o usuário é o invariante 7: **hoje a tela chama de
VALUE algo que nunca foi comparado com mercado nenhum.**
