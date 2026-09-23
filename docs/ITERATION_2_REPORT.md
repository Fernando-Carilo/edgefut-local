# Iteração 2 — Relatório: TRUST, LIVE & INTELLIGENCE

Pergunta central da iteração: **"Posso acreditar nos dados e nas probabilidades que estou vendo?"**

Este relatório descreve o que foi implementado, o que foi corrigido em relação ao baseline
([ITERATION_2_BASELINE.md](ITERATION_2_BASELINE.md)), o que foi verificado com dados reais e o que
**ainda não** está provado. Nenhum número abaixo foi estimado: todos vêm de execução real do engine
em 2026-09-23 (intervalo internacional de seleções, sem ligas de clubes na oferta de 48 h).

---

## 1. Resumo executivo

| Área | Antes (baseline) | Depois |
|---|---|---|
| Testes backend | 35 | **132** (invariantes, leakage, calibração, frescor/conflitos, identidade, odds v2, modelos v2, recomendações v2, API) |
| Frescor dos dados | check binário "odds < 30 min" | `Freshness{collectedAt, validUntil, ageSeconds, status}` por insumo (odds, forma, histórico, venue, escalação); EXPIRED nunca entra em recomendação |
| Conflitos entre fontes | escolha silenciosa | `source_conflict` persistido com as duas versões, regra e confiança; "N divergências de dados resolvidas" clicável |
| Identidade de evento | `eventId` Superbet | `canonical_event_id` (`YYYYMMDD-comp-home-away`) + detecção de duplicatas |
| Remoção de margem | multiplicativa | multiplicativa **e** Shin, ambas gravadas em cada seleção; método ativo configurável |
| Movimento de odds | abertura × atual | `LineMovement` (abertura, atual, mín, máx, %, pp de probabilidade implícita, direção, extremo) + *closing line* separada (só CLV) |
| Backtest | odd de fechamento decidia a aposta (leakage) | `as_of` por partida com `LeakageError`, walk-forward expanding/rolling, closing só para CLV |
| Calibração | 1 − Brier normalizado | isotônica por mercado × grupo, mínimo 300 previsões liquidadas; RAW e CALIBRATED gravadas lado a lado |
| Modelos de gols | Poisson + Dixon-Coles | + **Bivariate Poisson**; consenso ponderado por pesos walk-forward (`ensemble-v1`); `model_registry` |
| Snapshots | mutáveis em nível de banco | guard de imutabilidade + `snapshot_correction` (correções registradas, nunca sobrescritas) |
| Radar | 5 cards, score v1 fixo | contadores de cobertura, **Quality Gate** (10 checks), Opportunity Score V2 (9 pesos configuráveis), HIGH PROBABILITY ≠ VALUE, WHY / WHY NOT, nível de evidência |
| Ao vivo | `available=false` | observação real (placar, minuto, stats expostas, odds ao vivo, movimento), polling com backoff, **zero recomendações** |
| Scheduler | estado em memória | `job_run` persistido (início, fim, duração, registros, erros, correlation-id) + tela Sistema → Jobs |
| Observabilidade | log texto | JSON com correlation-id; `/health/system` por componente; Diagnóstico na UI |
| Cache de análise | TTL 5 min | chave por `evento | pipeline | versão das odds | hash dos limiares` |

---

## 2. O que foi implementado (por fase, na ordem obrigatória)

### A. Baseline
`docs/ITERATION_2_BASELINE.md`: 35 testes, providers em funcionamento, 17 limitações auditadas por execução real.

### B. Data Quality Layer
- `domain/freshness.py` — políticas por tipo (odds FRESH < 10 min, AGING < 45 min, STALE < 6 h, EXPIRED; forma/histórico 12 dias; venue 1 ano).
- `domain/conflicts.py` + tabela `source_conflict` — Source Conflict Engine (venue detectado × usuário, placar Superbet × CSV, kickoff).
- `normalization/identity.py` — `CanonicalEventResolver` (aliases, duplicatas). Testes em `test_identity.py`.
- `quality/health.py` — `SystemHealth` por componente (superbet, fixtures, historical, results, player_data, database, duckdb, scheduler, ollama, cache) e `SourceCard` para Fontes V2.
- `core/logging.py` — JSON estruturado com `correlation_id`; `core/context.py`.

### C. Odds V2
- `odds/shin.py` — remoção de margem de Shin (z por bisseção). `fair_multiplicative` e `fair_shin` gravadas em toda seleção; `Settings.margin_method` escolhe a exibida.
- `odds/movement.py` — `LineMovement`; `closing_line` capturada pelo job `closing_lines` imediatamente antes do kickoff, **nunca** usada para recomendar.
- Correção: `odds_collected_at` = última confirmação (não última mudança de preço) — antes 31/48 jogos apareciam STALE indevidamente.

### D. Backtest anti-leakage
- `historical/store.py` recebe `as_of` em toda consulta; `LeakageError` se algum jogo posterior escapar.
- `backtesting/lab.py` — janelas walk-forward (expanding/rolling), `refit_every_days`, resultado por janela, `leakage_checks` contados.
- Odd de decisão = odd pré-fechamento (B365); odd de fechamento apenas para CLV.

### E. Calibration Engine
- `models/calibration.py` — isotônica (PAV) por `market_key × grupo`, mínimo **300** previsões liquidadas; `calibration_model` persistido; `/calibration` com reliability diagram por bucket.
- Recomendação usa `model_prob_calibrated` só quando `calibration_reliable`; caso contrário RAW, e a UI diz isso.

### F. Bivariate Poisson + Ensemble
- `models/bivariate_poisson.py` (`goals-bivariate-poisson-v1`, Karlis & Ntzoufras, λ3 por MLE).
- `models/ensemble.py` (`ensemble-v1`): comparação Poisson / DC / BP, consenso = média ponderada das **matrizes** de placar; pesos ∝ exp(−25·Δlogloss) por competição (N ≥ 200) ou GLOBAL, calculados pelo job `ensemble_weights` (walk-forward real, ~34 s). Modelos com peso < 10 % continuam exibidos, mas ficam fora do veto de divergência.
- `models/registry.py` + tabela `model_registry` (15 versões registradas, `confidence-v1` marcado depreciado).
- `db/immutability.py` — guard: snapshot não pode ser alterado após kickoff; correções vão para `snapshot_correction`.

### G. Radar V2
- `recommendations/opportunity.py` (`opportunity-v2`, 9 componentes, pesos em `Settings.opportunity_weights`).
- `recommendations/gate.py` — Quality Gate: qualidade de dados, confiança, amostra, divergência, odds frescas, provider saudável, edge, EV, faixa de odd, **edge plausível** (edge > 15 pp sem calibrador falha: "acima disso é mais provável erro do modelo do que do mercado").
- `recommendations/why.py` — WHY THIS BET / WHY NOT com guarda contra linguagem de garantia (`assert_no_guarantee_language`).
- `recommendations/confidence.py` (`confidence-v2`) — 6 grupos (dados, concordância, calibração, amostra, frescor, contexto) → **EDGEFUT CONFIDENCE 0–100** com breakdown.
- Labels `HIGH_PROBABILITY` / `VALUE` / `HIGH_PROBABILITY_VALUE` (SAFE ≠ VALUE). Nível de evidência `SETTLED` / `BACKTEST_ODDS` / `MODEL_ONLY` por competição.
- `HARD_FLOORS` / `HARD_CEILINGS` em `core/config.py`: o sistema **nunca** reduz limiares sozinho; `PUT /settings` corrige valores fora dos pisos.

### H. Live Mode (observação)
- `providers/superbet/live.py` + `collectors/live.py` — `offerState=live`, `metadata.status == STARTED`, minuto/período/placar/stats **somente quando a fonte expõe**; até 5 jogos com mercados completos por ciclo; poll a cada 30 s com backoff exponencial.
- `/live` devolve `mode: "OBSERVATION_ONLY"`, `age_seconds`, `freshness`, movimento de odds; **nenhum** edge/EV/recomendação.

### I. Performance / observabilidade
- `job_run` + `/jobs` + `POST /jobs/{job}/run`; 12 jobs com labels.
- Alertas locais (`alert`): movimento de odd, mudança de qualidade/confiança, oportunidade surgiu/perdida; `/alerts`, `POST /alerts/read`.
- Fontes V2 (`SourceCard`: status, frescor, latência, erros, fornece / não fornece).
- Player Engine só como arquitetura (`providers/player`): `PlayerProvider` abstrato, `PlayerRegistry` vazio → `PLAYER_MARKET → LINEUP_UNCERTAINTY`. Nenhum dado de jogador é inventado.
- Cache de análise por chave de versão (`analysis/pipeline.py::cache_key`).
- `tests/test_invariants.py` (28 testes): soma 1X2 = 1, fair soma = 1 (mult. e Shin), odds > 1, edge/EV coerentes, Monte Carlo converge para a matriz, gate monotônico, pisos respeitados, sem linguagem de garantia.

### UI
- Radar V2, Início (resumo da manhã + **VER RADAR**), Página do jogo (EDGEFUT CONFIDENCE, frescor, divergências, comparação de modelos, RAW vs CALIBRATED, WHY/WHY NOT, gate, opportunity, movimento de linha com probabilidade implícita), Ao Vivo (observação), Performance (por mercado/competição com INSUFFICIENT SAMPLE, Brier em destaque com tooltips, aba Calibração com reliability diagram), Fontes V2, Modelos (registry + pesos), Alertas, Sistema → Jobs, Sistema → Diagnóstico, Configurações (gate, pesos, margem, poll ao vivo).

---

## 3. Limitações do baseline — estado final

| # | Limitação auditada | Estado |
|---|---|---|
| 1 | Settlement nunca ocorria (`settled_at` gravado pelo sync) | **Corrigido** — verificado hoje: job `settle` → 37 verificados, 28 liquidados em 72 s |
| 2 | Leakage no Backtest Lab | **Corrigido** — `as_of` + `LeakageError`; closing só para CLV |
| 3 | "Calibração" = 1 − Brier | **Corrigido** — isotônica, N ≥ 300, RAW × CALIBRATED |
| 4 | Frescor só para odds | **Corrigido** — freshness por insumo, EXPIRED bloqueia |
| 5 | Identidade = eventId Superbet | **Corrigido** — `canonical_event_id` + duplicatas |
| 6 | Conflitos silenciosos | **Corrigido** — Source Conflict Engine |
| 7 | Movimento = abertura × atual; sem CLV em produção | **Corrigido** — `LineMovement` + `closing_line` (2.495 linhas capturadas) |
| 8 | Só margem multiplicativa | **Corrigido** — Shin + multiplicativa gravadas |
| 9 | Sem model registry | **Corrigido** — `model_registry` |
| 10 | Snapshot mutável | **Corrigido** — guard + `snapshot_correction` |
| 11 | Scheduler sem histórico | **Corrigido** — `job_run` (242 execuções registradas) |
| 12 | Radar sem contadores / gate | **Corrigido** — Radar V2 |
| 13 | Cache por TTL | **Corrigido** — chave por versão |
| 14 | Log sem correlation-id | **Corrigido** — JSON + correlation-id |
| 15 | Sem testes de invariantes | **Corrigido** — 28 testes |
| 16 | Viés Under em amistosos internacionais | **Não resolvido** — sem previsões liquidadas suficientes para calibrar; agora exposto como `MODEL_ONLY` + gate de edge plausível (ver §5) |
| 17 | Frontend sem testes de componentes | **Não resolvido** — `tsc` + `vite build` verdes; sem testes de componente |

---

## 4. Verificação com dados reais (2026-09-23, 18:22 UTC)

### Radar (`/radar?hours=48`)
```
events_found 198 · with_sufficient_data 36 · analyzed 48 · quality_gate_passed 34
confidence A 1 · B 33 · high_probability 4 · value 34 · watch 1 · no_bet 13 (LOW_DATA 12, NO_EDGE 1)
stale 0 · gate_passed_by_evidence {MODEL_ONLY: 34}
```
Resumo da manhã gerado: *"Fernando, analisamos 49 de 198 jogos das próximas 48 h. 35 passaram no quality gate
(1 com confiança A, 34 com B). 35 deles em competições sem odds históricas (MODEL_ONLY): edge nunca verificado
contra o mercado. 1 em observação. 13 NO BET (principal motivo: dados insuficientes)."*

### Exemplo: Japão × Uruguai (Amistoso Internacional)
- Baseline: NO BET · `MODEL_DISAGREEMENT` (Poisson × Dixon-Coles 13 pp).
- Agora: pesos walk-forward INTL → Poisson **3,6 %**, Dixon-Coles 56,3 %, Bivariate Poisson 40,1 %. Divergência
  no escopo do veto (DC × BP) = **0,1 pp**; Poisson (13,5 pp de distância) exibido, mas fora do veto.
- Recomendação: Under 3.5 @ 1,27 — modelo 89,4 % RAW × justa 75,2 % → edge +14,2 pp, EV +13,6 %,
  EDGEFUT CONFIDENCE 82 (A), Opportunity 85, gate 10/10, **MODEL_ONLY**. WHY NOT lista explicitamente:
  "sem odds históricas nesta competição; o modelo nunca foi comparado ao mercado aqui. Trate o edge como hipótese."
- 1 divergência registrada: venue `superbet-listing: UNCONFIRMED` × `user: CONFIRMED_HOME` → regra `user_override`.

### Pesos do ensemble (walk-forward, job real, 34 s)
| Grupo | N | Poisson | Dixon-Coles | Bivariate Poisson |
|---|---|---|---|---|
| GLOBAL | 4.374 | 19,4 % | 42,0 % | 38,5 % |
| INTL | 857 | 3,6 % | 56,3 % | 40,1 % |
| E0 | 362 | 34,0 % | 33,7 % | 32,3 % |
| I1 | 377 | 24,5 % | 37,9 % | 37,6 % |
| USA | 455 | 32,1 % | 34,2 % | 33,7 % |

Leitura honesta: em ligas de clubes os três modelos são quase equivalentes; o Poisson de força (não ajustado por
adversário) é claramente pior em seleções.

### Backtest anti-leakage (E0, 1X2, Dixon-Coles, 3 temporadas, expanding)
ROI negativo, Brier ≈ 0,21, **CLV −1,49 %** contra a closing line B365. O modelo **não** bate o mercado da
Premier League — o Lab mostra isso sem maquiagem, e é exatamente por isso que o gate exige edge plausível.

### Ao vivo (`/live`)
44 jogos em andamento observados (USL, Champions League feminina, ligas nórdicas…); poll de 10–33 s por ciclo;
placar/minuto/escanteios/cartões apenas quando expostos pela fonte; odds ao vivo com movimento; `notice`
explícito de observação. Nenhuma recomendação.

### Settlement (primeira liquidação real)
Iraque × Omã 1–1 (fonte Superbet) → 3 snapshots liquidados com `outcomes` por seleção; job `settle`: 37 eventos
verificados, 28 liquidados, 0 conflitos de placar, 0 correções.

### Banco local ao final
274 eventos · 104 competições · 53.818 snapshots de odds · 101 `prediction_snapshot` (3 liquidados — só um evento
finalizado tinha snapshots) · 56 eventos com resultado · 2.495 closing lines · 1 conflito · 97 alertas · 242 `job_run` · 15 `model_registry` ·
3.674 `source_log`.

---

## 5. Limitações conhecidas (o que NÃO está provado)

1. **Toda a oferta analisada hoje é MODEL_ONLY.** Durante a data FIFA só há seleções; `international_results`
   não traz odds, então nenhum edge foi verificado contra o mercado. A UI marca isso em cada card, no resumo da
   manhã e na página do jogo. Ligas de clubes (`B1 D1 E0 F1 I1 N1 P1 SC0 SP1 T1`) têm evidência `BACKTEST_ODDS`.
2. **Calibração isotônica ainda não está ativa.** Exige ≥ 300 previsões liquidadas por grupo; há 3 previsões
   liquidadas (56 eventos com resultado, mas só um deles tinha snapshot). Todas as probabilidades exibidas são **RAW**, e a UI diz isso.
3. **0 apostas recomendadas liquidadas** → ROI, hit rate e CLV de produção estão em INSUFFICIENT SAMPLE.
4. **Poisson de força não é ajustado por adversário**; em seleções recebe peso ~4 %. Substituí-lo por um Poisson
   com ataque/defesa por time ajustados conjuntamente é próximo passo.
5. **Viés Under em amistosos** (edges de 10–15 pp em Under) segue sem confirmação empírica. O gate `edge_plausible`
   (≤ 15 pp sem calibrador) limita, não resolve.
6. **Escalações / jogadores:** nenhum provider público permitido. Arquitetura pronta, dados ausentes → NO BET.
7. **Ollama** indisponível no ambiente de teste; Edge AI opera por templates (grounded).
8. **Frontend sem testes de componente**; verificação visual feita por walkthrough gravado.
9. **Builds Windows** continuam via GitHub Actions; o instalador desta iteração ainda não foi testado manualmente
   em Windows (o pipeline existente é o mesmo da iteração 1).

---

## 6. Performance observada

| Operação | Medido |
|---|---|
| Análise completa de um evento (50k simulações, 3 modelos) | ~2,1 s |
| Análise servida do cache por versão | ~20 ms |
| Job `radar` (48 eventos) | 115 s |
| Job `ensemble_weights` (walk-forward, 13 grupos) | ~34 s |
| Job `live_poll` (44 jogos, 5 com mercados completos) | 10–33 s |
| Job `settle` (37 eventos) | 72 s |
| `/health/system` (10 componentes) | < 100 ms |
| Backend `pytest` (132 testes) | 3,9 s |
| Frontend `tsc` + `vite build` | ~7 s |

---

## 7. Riscos

- **Superbet pode mudar o JSON de oferta** (campos `metadata.status`, `minutes`, `periodStatus`). Mitigação:
  provider registra indisponibilidade, circuit breaker, e a UI mostra STALE/UNAVAILABLE em vez de dados velhos.
- **Excesso de recomendações MODEL_ONLY em seleções** pode passar impressão de certeza. Mitigação já aplicada:
  label MODEL ONLY em todo card, banner na página, gate de edge plausível, resumo da manhã com a contagem.
- **Pesos walk-forward com N pequeno** (SC0 N=233) podem oscilar entre execuções; grupos < 200 usam GLOBAL.
- **Crescimento do SQLite** (53 k snapshots de odds em ~5 h de scheduler). Job `cache_cleanup` existe para o
  cache HTTP; a deduplicação de snapshots iguais já limita o crescimento, mas não há retenção configurada.

---

## 8. Próximos passos sugeridos

1. Acumular previsões liquidadas em ligas de clubes até ativar a calibração isotônica (≥ 300 por mercado) e
   medir CLV real das recomendações.
2. Poisson com ataque/defesa por time ajustados conjuntamente (substituir `strength Poisson`).
3. Testes de componente no frontend (Vitest + Testing Library) para Radar, página do jogo e Ao vivo.
4. Retenção configurável de `odds_snapshot` e `job_run`.
5. Importação manual de CSV pela UI para competições sem fonte pública.
6. Player Engine: integrar apenas quando existir fonte pública e permitida.
