# Iteração 3 — MODEL VALIDATION & PREDICTIVE QUALITY — Relatório

Data: 2026-09-23. Baseline auditado antes de qualquer mudança em
[`ITERATION_3_BASELINE.md`](ITERATION_3_BASELINE.md). Tudo abaixo foi medido por
execução real (engine em `127.0.0.1:8765`, banco populado com Superbet +
football-data + international_results), testes automatizados e leitura de
código. Onde o modelo **não** supera o baseline, o texto diz isso.

## 0. A resposta curta (regra final do spec)

| Pergunta | Número |
|---|---|
| Replay N (clubes, com odds) | **14.393 partidas** (14.353 com odds), 11 ligas, 2022-08 → 2026-09, 463 janelas walk-forward de 30 dias |
| Brier EdgeFut (campeão `ensemble`) vs baseline mercado, 1X2 | **0,5857 vs 0,5718 — o mercado é melhor**. ΔBrier +0,0147, IC 95 % [+0,0128; +0,0166], lift −2,56 %, campeão melhor em 122/463 janelas → **NO CLEAR ADVANTAGE** |
| Brier EdgeFut vs baseline ingênuo (frequência da liga), 1X2 | 0,5857 vs 0,6480 — ΔBrier −0,0624 [−0,0670; −0,0579], lift +9,63 %, 412/463 janelas → **CONSISTENT** |
| Over/Under 2,5 | mercado 0,2389 vs `ensemble` 0,2452 — mercado melhor |
| IC por mercado (apostas simuladas, gate simplificado) | 1X2: ROI −12,6 % [−15,7; −9,2], N 7.939 · OU 2,5: ROI −7,6 % [−9,9; −5,2], N 7.271 · total −10,2 % [−12,2; −8,1], N 15.210, CLV −3,46 % — **NEGATIVE, conclusivo** |
| International N | **15.956 partidas** (2010 →, 66 janelas de 90 dias), **0 com odds** → só `MODEL_ONLY` |
| International vs ingênuo | Brier 0,5175 vs 0,6336 — ΔBrier −0,1162 [−0,1214; −0,1105], lift +18,3 %, 66/66 janelas → **CONSISTENT**; vs ELO puro lift +3,3 % CONSISTENT |
| Leakage tests | **PASS** — 3 testes do `TemporalFeatureStore` + 5 do Lab + 2 do replay (`test_replay_is_leak_free…`, `test_replay_refuses_to_use_future_rows`) |
| Settlement reconciliation | **PASS** — 117 liquidadas, 16 pendentes (jogos terminados há < 72 h sem resultado na fonte), 0 erros, 0 não classificados, 0 divergências entre fonte e liquidação |
| Shadow mode | **PASS como mecanismo**, **INSUFFICIENT como evidência** — 2.438 previsões append-only gravadas, **0 liquidadas** (o primeiro dia de shadow é hoje) |
| Testes automatizados | **151 passed** (era 132) · `pnpm -r typecheck` e `pnpm build` verdes |

**Veredito honesto**: nos mercados em que existe preço histórico (1X2 e OU 2,5
de 11 ligas europeias), **nenhum modelo do EdgeFut supera o mercado**. Todos
superam os baselines ingênuos com folga e de forma consistente — ou seja, o
modelo *sabe futebol*, mas não sabe mais do que a odd média pré-jogo. A
simulação de apostas com o gate simplificado do replay dá ROI negativo
conclusivo para **todos** os modelos (−8,5 % a −10,6 %). Isso não foi ajustado:
é o que os dados dizem, e passou a ser exibido no app (MODEL HEALTH → `WATCH`).

Consequência operacional: o Radar de hoje (data FIFA + MLS) tem **0 VALUE, 0
VALUE CANDIDATE**, 47 eventos `MODEL_ONLY` e 13 `NO_BET`. Está correto assim.

## 1. O que foi construído (na ordem exigida A → N)

### A. Baseline (`docs/ITERATION_3_BASELINE.md`)

Sete invariantes verificados, três achados de operação (scheduler sem watchdog,
execuções órfãs, settlement perdido não reexecutado) e a prova de que o
`MODEL_ONLY` estava sendo tratado como se tivesse preço (Under 3,5 @ 1,27 em
seleções aparecia como VALUE).

### B. Settlement & reconciliação (`backtesting/reconciliation.py`)

- Todo evento terminado há ≥ 3 h é classificado em `SETTLED`,
  `SETTLEMENT_PENDING` (< 72 h sem resultado) ou `SETTLEMENT_ERROR` (≥ 72 h).
  Nenhum evento fica sem estado (`test_reconciliation_classifies_every_finished_event`).
- Divergência = resultado da fonte ≠ resultado usado na liquidação → contada e
  logada; hoje **0**.
- Job `reconcile` a cada 6 h (também liquida `shadow_prediction`); `job_run`
  órfãos são marcados `interrupted` no boot
  (`test_orphan_job_runs_are_marked_interrupted_on_boot`).
- Endpoint `GET /validation/runs` lista `validation_run` (replay, decay,
  bootstrap, shadow_report, drift, promotion).

### C. TemporalFeatureStore (`features/temporal.py`)

Toda leitura de histórico dentro do pipeline e do replay passa por
`TemporalFeatureStore.team_matches / competition_matches / h2h(as_of=…)`, que
filtra `date < as_of` e levanta `LeakageError` se um backend devolver linha
futura. Três testes anti-leakage dedicados
(`test_temporal_store_never_returns_rows_at_or_after_as_of`,
`test_temporal_store_raises_if_a_backend_leaks_future_rows`,
`test_temporal_store_uses_distinct_codes_per_team`).

### D. strength-v2 (`models/strength_v2.py`, `goals-poisson-v2`)

Ataque/defesa ajustados ao adversário por ponto fixo iterativo (40 iterações,
tol 1e-6), shrinkage bayesiano (`PRIOR_K = 6` pseudo-gols para 1,0; `COND_K =
10` da condição casa/fora para o pooled), vantagem de mando estimada por
grupo (≥ 100 jogos, senão do conjunto), decaimento `w = 0,5^(idade/half-life)`.
Half-life **escolhido por walk-forward** (§H): 365 dias.

Resultado: `poisson_v2` melhora o Poisson v1 de forma conclusiva (ΔBrier −0,0070
[−0,0092; −0,0048]), mas **não** supera o campeão (`ensemble`): ΔBrier +0,0016
[+0,0005; +0,0028] → **KEEP CHAMPION**. `ensemble_v2` (v1 + poisson_v2) é
marginalmente melhor que o campeão (ΔBrier −0,0004 [−0,0007; −0,0001]) mas
ganha em só 246/463 janelas (53 % < 60 %) → **PROMISING · UNSTABLE**, não
promovido.

### E. international-strength-v1 (`models/international_strength.py`)

strength-v2 sobre `international_results` com `tournament_type`
(FRIENDLY / QUALIFIER / TOURNAMENT / NATIONS_LEAGUE / CONTINENTAL / OTHER),
peso 0,6 para amistosos, campo neutro do dataset, nota de histórico curto
abaixo de 8 jogos. Alimenta seleções no pipeline. Replay por tipo de torneio
em §3.

### F. Baselines & lift (`validation/replay.py`)

Cinco baselines por partida: **A** mercado (odds médias pré-jogo do
football-data, margem removida), **B** frequência ingênua da liga (casa /
empate / fora até `as_of`), **C** Poisson simples da liga (médias de gols, sem
força), **D** ELO puro, **E** favorito da casa (probabilidade 1 no favorito).
`lift_% = (Brier_baseline − Brier_modelo) / Brier_baseline`, com IC do
bootstrap **pareado** por partida.

### G. Historical Replay Engine (`validation/replay.py`, `POST /validation/replay`)

Anda em janelas cronológicas; em cada janela ajusta todos os modelos com
`reference_date` = início da janela, pesos do ensemble **só das janelas
anteriores**, compara com as odds daquele momento, liquida com o placar real.
Persiste `validation_run(kind="replay")`; nunca toca `prediction_snapshot`.
Limitações declaradas no próprio relatório (odds football-data como proxy do
mercado; gate simplificado; só 1X2 e OU 2,5).

### H. Bootstrap CI, qualidade de amostra, significância, walk-forward v2 (`validation/bootstrap.py`, `validation/decay.py`)

- IC 95 % percentílico com 1.000 reamostragens (seed fixa) em toda métrica
  agregada (Brier, LogLoss, ROI, Yield, CLV, lift); `conclusive=false` quando o
  IC cruza zero.
- Qualidade da amostra: `INSUFFICIENT` < 100 · `EARLY` < 300 · `MODERATE` <
  1.000 · `STRONG` ≥ 1.000 (configurável, com piso).
- Significância vs baseline: `INSUFFICIENT DATA` / `NO CLEAR ADVANTAGE` /
  `PROMISING` / `CONSISTENT` (IC < 0, amostra STRONG e ≥ 75 % das janelas).
- Walk-forward v2 do half-life (`POST /validation/decay`): 30 / 60 / 90 / 180 /
  365 / 730 / sem decaimento, N 14.152, 463 janelas. Ranking por Brier:
  **365 (0,58737)** ≈ 180 (0,58801) > 730 (0,58821) > none (0,59033) > 90 >
  60 > 30 (0,60845). 365 vs 180 é **inconclusivo** (Δ −0,0006 [−0,0015;
  +0,0002]); 365 vs 730 conclusivo. Regra: entre equivalentes, o mais simples →
  **365 dias**, gravado em `settings.strength_half_life_days`.

### I. Correlation Engine (`recommendations/correlation.py`)

Cada seleção recebe um `cluster_id` (17 clusters: `HOME_TEAM_POSITIVE`,
`GOALS_HIGH`, `CORNERS_LOW`…) e um `thesis_group` (RESULT / GOALS / CORNERS /
CARDS / SHOTS). Dentro de um cluster **uma só** seleção é `PRIMARY`
(melhor estado → status → mercado mais "puro" → maior Opportunity); as demais
viram `ALTERNATIVA` com `primary_of`. Pares cruzados correlacionados
(mandante bem ⇄ mandante marca, poucos gols ⇄ empate…) alimentam a
**Exposição** (LOW / MEDIUM / HIGH) do evento. Performance passa a separar
`primaries_only` de `alternatives_only`
(`test_performance_report_separates_primaries_from_alternatives`).

### J. Estados, price target, watchlist (`recommendations/engine.py`, `pricing.py`)

Estados por seleção: `MODEL_ONLY` → `MARKET_OBSERVED` → `VALUE_CANDIDATE` →
`VALUE` → `OBSERVATION` → `NO_BET`. Regras principais:

- **Competição sem preço histórico válido ⇒ todas as seleções `MODEL_ONLY`**,
  nunca VALUE/WATCHING_PRICE (§29 do spec). Texto fixo: *"Probabilidade
  calculada, mas sem preço de mercado válido para determinar valor."*
- `VALUE_CANDIDATE` = passou edge/EV/gate mas o mercado ainda não tem
  `≥ value_min_oos_bets` (100) apostas out-of-sample (replay do campeão e/ou
  apostas reais liquidadas). `VALUE` exige essa prova **com veredito não
  negativo**; se o OOS do mercado é `NEGATIVE` (caso de 1X2 e OU 2,5 hoje, ver
  §2.4) a seleção cai para `OBSERVATION · OOS_NEGATIVE`. **Hoje nenhum
  mercado passa** → nenhuma seleção pode ser VALUE, por definição.
- `price_target`: `break_even_odd = 1/p`, `min_acceptable_odd` = menor odd que
  satisfaz edge ≥ `min_edge_pp` **e** EV ≥ `min_ev_pct`, `price_gap_pct`,
  sensibilidade do edge a ±3 pp de probabilidade (`edge_survives_minus`).
  `WATCHING_PRICE` só quando a odd-alvo está dentro de `[min_odd, max_odd]` e o
  gap ≤ 12 % — texto: *"Probabilidade interessante, mas preço atual não
  oferece margem suficiente."*
- Opportunity Score **V3**: mesmo breakdown do V2 mais penalidades explícitas
  (incerteza OOS, correlação/alternativa, MODEL_ONLY), mostradas no tooltip.

### K. Shadow mode (`validation/shadow.py`)

`shadow_prediction` é **append-only** (guarda de imutabilidade testada em
`test_reconciliation_settles_shadow_rows_and_shadow_is_append_only`): uma linha
por seleção avaliada com estado, probabilidade, odd, edge e cluster; liquidada
pelo `reconcile`. Relatório diário (`shadow_report`, `GET /validation/shadow`)
com janelas 7 d / 30 d / 90 d / tudo, separando `priced`, `value_only` e
`model_only` (este nunca entra em ROI/CLV — não há preço). Estado: 2.438
linhas, 0 liquidadas — a evidência começa a existir a partir de amanhã.

### L. Drift, auditoria de probabilidade, guarda de extremos, WHY MODEL CHANGED, Champion/Challenger

- **Drift monitor** (`validation/drift.py`, job diário): compara 30 d recentes
  vs 90 d de referência em probabilidade média, share > 90 %, edge médio,
  share MODEL_ONLY, Brier e (hit − prob) nos liquidados. Só **alerta**; nunca
  altera modelo ou limiar. Hoje `INSUFFICIENT DATA` (referência n = 0).
- **Extreme probability guard** (`extreme_probability_guard`): probabilidade
  > 90 % exige menor amostra de time ≥ 30 jogos **e** ≥ 300 previsões
  liquidadas no mercado; caso contrário a **confiança** é multiplicada por
  0,85 (um critério atendido) ou 0,70 (nenhum) e o motivo `EXTREME_PROBABILITY` aparece no WHY. A probabilidade
  **nunca é truncada**
  (`test_extreme_probability_penalizes_confidence_but_never_truncates`).
- **WHY MODEL CHANGED** (`analysis/changes.py`): compara a análise com o último
  `prediction_snapshot` do evento e atribui drivers (`ODDS_MOVED`,
  `NEW_MATCHES`, `RATINGS_CHANGED`, `MODEL_VERSION`, `CHAMPION_CHANGED`,
  `CALIBRATION`) com Δ em pp por seleção e mudança de estado.
- **Champion / Challenger** (`validation/governance.py`): campeão `ensemble`
  (v1); challengers `ensemble_v2`, `poisson_v2`, `strength_v2`,
  `international_strength`; baseline `elo`. Regra de promoção em
  [`MODEL_GOVERNANCE.md`](MODEL_GOVERNANCE.md) — ROI **não** é critério; a
  promoção é uma ação explícita (`POST /validation/governance/promote`),
  nunca automática. Nenhuma promoção ocorreu nesta iteração.

### M. UI (sem novas dezenas de páginas)

Uma página nova — **Validação** — com abas Model Validation (replay clubes /
seleções), Model Comparison (campeão, challengers, decay, regra de promoção,
estabilidade por janela), Coverage Map, Shadow Performance e Drift. O resto
entrou nas telas existentes: estados e chips `ALTERNATIVA` no Radar e nos
cards; **Teses e exposição** e **Why model changed** na página do jogo;
**Price target** no detalhe da recomendação; **MODEL HEALTH** discreto no
Início; Performance com primárias × alternativas, por cluster, por estado e
mercados secundários. Rótulos: `MODEL FAVORITE` (ex-HIGH PROBABILITY),
`MODEL ONLY`, `WATCH`, `VALUE CANDIDATE`, `VALUE`, `NO BET`.

## 2. Replay de clubes — números completos

Configuração: datasets B1, D1, E0, F1, I1, N1, P1, SC0, SP1, SP2, T1; início
2022-08-01; expanding; janela 30 d; `min_train` 150; half-life das settings
(365 d); simulação de apostas com gate simplificado.

### 2.1 Ranking 1X2 (Brier, N com odds 14.353; modelos 14.152)

| # | Modelo | Brier [IC 95 %] | LogLoss | ECE | Hit |
|---|---|---|---|---|---|
| 1 | **mercado** (baseline A) | **0,5718** [0,5662; 0,5771] | 0,9631 | 0,0111 | 54,3 % |
| 2 | ensemble_v2 (challenger) | 0,5854 [0,5798; 0,5911] | 0,9834 | 0,0099 | 52,7 % |
| 3 | **ensemble (campeão)** | 0,5857 [0,5802; 0,5914] | 0,9840 | 0,0099 | 52,7 % |
| 4 | dixon_coles | 0,5866 | 0,9859 | 0,0092 | 52,6 % |
| 5 | bivariate_poisson | 0,5869 | 0,9864 | 0,0113 | 52,6 % |
| 6 | poisson_v2 (challenger) | 0,5874 | 0,9865 | 0,0108 | 52,2 % |
| 7 | poisson (v1) | 0,5944 | 0,9967 | 0,0085 | 51,7 % |
| 8 | elo (baseline D) | 0,5961 | 1,0049 | 0,0489 | 52,4 % |
| 9 | simple_poisson (baseline C) | 0,6477 | 1,0708 | 0,0022 | 44,2 % |
| 10 | naive (baseline B) | 0,6480 | 1,0714 | 0,0034 | 44,2 % |
| 11 | favorite (baseline E) | 0,9135 | 9,4657 | 0,3045 | 54,3 % |

### 2.2 Campeão vs cada baseline (bootstrap pareado)

| Baseline | ΔBrier [IC] | Lift | Janelas melhores | Significância |
|---|---|---|---|---|
| mercado | **+0,0147 [+0,0128; +0,0166]** | **−2,56 %** | 122 / 463 | **NO CLEAR ADVANTAGE** |
| naive | −0,0624 [−0,0670; −0,0579] | +9,63 % | 412 / 463 | CONSISTENT |
| simple_poisson | −0,0621 [−0,0666; −0,0576] | +9,58 % | 408 / 463 | CONSISTENT |
| elo | −0,0104 [−0,0131; −0,0077] | +1,74 % | 300 / 463 | PROMISING |
| favorite | — | +35,7 % | 461 / 463 | CONSISTENT |

Todos os seis modelos perdem para o mercado (lift entre −2,49 % `ensemble_v2`
e −4,10 % `poisson`). **Por dataset**, o mercado vence em **11 de 11 ligas**
(ex.: E0 0,5902 vs 0,5761; P1 0,5440 vs 0,5360; SP2 0,6311 vs 0,6100).

### 2.3 Over/Under 2,5

Mercado Brier 0,2389 [0,2374; 0,2403], ECE 0,0114 · `ensemble` 0,2452 [0,2433;
0,2471], ECE 0,0394 (o modelo subestima Over: 51,96 % previsto vs 54,21 %
observado). Mercado melhor.

### 2.4 Apostas simuladas (gate simplificado: edge ≥ 3 pp, EV ≥ 3 %, odd 1,20–6,00, uma seleção por mercado)

| Modelo | N | ROI [IC 95 %] | CLV | Veredito |
|---|---|---|---|---|
| ensemble | 15.210 | −10,2 % [−12,2; −8,1] | −3,46 % | NEGATIVE |
| ensemble_v2 | 15.380 | −10,2 % [−12,1; −8,3] | −3,48 % | NEGATIVE |
| dixon_coles | 15.770 | −8,8 % [−10,7; −7,1] | −3,28 % | NEGATIVE |
| bivariate_poisson | 15.827 | −8,9 % [−10,8; −7,1] | −3,22 % | NEGATIVE |
| poisson_v2 | 17.176 | −8,5 % [−10,2; −6,8] | −3,26 % | NEGATIVE |
| poisson | 18.198 | −10,6 % [−12,4; −8,9] | −3,61 % | NEGATIVE |

Campeão por mercado: 1X2 −12,6 % [−15,7; −9,2] (N 7.939, odd média 3,23, hit
31,8 %); OU 2,5 −7,6 % [−9,9; −5,2] (N 7.271). Por seleção: `1X2:AWAY` −13,8 %
(N 3.655), `1X2:DRAW` −13,4 % (N 731, MODERATE). O CLV negativo em todos os
modelos diz que o "edge" detectado é, em média, o mercado corrigindo o preço
contra o modelo até o fechamento — não uma ineficiência do mercado.

**Leitura**: com o gate simplificado do replay, apostar as recomendações do
EdgeFut nos últimos quatro anos teria perdido dinheiro em todas as
configurações. O gate real (confiança, frescor, qualidade, clusters) não foi
reproduzido no replay e pode filtrar melhor — mas isso é hipótese, não
evidência. É exatamente por isso que `VALUE` exige prova out-of-sample e hoje
não existe.

## 3. Replay de seleções (international_results)

Configuração: dataset INTL, 2010-01-01 →, expanding, janelas de 90 d, peso 0,6
para amistosos, sem odds (sem simulação de apostas).

N 15.956 partidas, 66 janelas. Brier 1X2: `ensemble` **0,5175** [0,5118;
0,5235] < ensemble_v2 0,5177 < dixon_coles 0,5179 < bivariate 0,5184 < elo
0,5351 < poisson_v2 0,5467 < poisson 0,5668 < naive 0,6336 < simple_poisson
0,6356. LogLoss 0,8806, ECE 0,0078, hit 59,3 %, share > 90 % = 5,9 %.

Vs ingênuo: ΔBrier −0,1162 [−0,1214; −0,1105], lift **+18,3 %**, 66/66 janelas
→ CONSISTENT. Vs ELO: −0,0175 [−0,0208; −0,0142], +3,3 %, 57/66 → CONSISTENT.

Por tipo de torneio (Brier `ensemble` / naive / elo; lift vs naive):

| Tipo | N | Amostra | ensemble | naive | elo | Lift | Significância |
|---|---|---|---|---|---|---|---|
| QUALIFIER | 6.128 | STRONG | 0,4704 | 0,6263 | 0,4791 | +24,9 % | CONSISTENT |
| FRIENDLY | 5.070 | STRONG | 0,5550 | 0,6331 | 0,5813 | +12,4 % | CONSISTENT |
| OTHER | 1.920 | STRONG | 0,5141 | 0,6363 | 0,5390 | +19,2 % | CONSISTENT |
| CONTINENTAL | 1.369 | STRONG | 0,5739 | 0,6541 | 0,5899 | +12,2 % | CONSISTENT |
| NATIONS_LEAGUE | 1.080 | STRONG | 0,5309 | 0,6430 | 0,5485 | +17,4 % | CONSISTENT |
| TOURNAMENT | 389 | MODERATE | 0,5580 | 0,6439 | 0,5701 | +13,9 % | PROMISING |

**Leitura**: para seleções o modelo é claramente informativo (e mais fácil de
prever em eliminatórias, onde a disparidade é grande). Mas **não existe preço
histórico** para comparar com o mercado, então tudo que envolve seleções fica
`MODEL_ONLY` — o app exibe a probabilidade e diz que não sabe se há valor. A
comparação com o mercado começará a existir pelo shadow mode, com as odds da
Superbet que o próprio app grava.

## 4. Settlement, shadow e drift — estado real

| Item | Valor |
|---|---|
| Reconciliação | 117 SETTLED · 16 SETTLEMENT_PENDING · 0 SETTLEMENT_ERROR · 0 sem estado · 0 divergências (última: 22:42 UTC) |
| Shadow | 2.438 linhas · 0 liquidadas · `INSUFFICIENT` em todas as janelas |
| Drift | `INSUFFICIENT DATA` — 30 d recentes n = 2.438 (prob média 0,426; share > 90 % 7,3 %; edge médio −0,36 pp; MODEL_ONLY 20,5 %), referência n = 0 |
| Model Health (Início) | **WATCH** — campeão perde para o mercado; shadow sem liquidação; 16 jogos terminados sem resultado ainda |

## 5. Radar hoje (data FIFA + MLS, 2026-09-23 22:44 UTC)

182 eventos encontrados · 47 com dados suficientes · 60 analisados · gate
passado **0** · VALUE **0** · VALUE CANDIDATE **0** · WATCH 45 · NO BET 15
(`LOW_DATA` 12, `NO_EDGE` 2, `SMALL_SAMPLE` 1) · MODEL FAVORITE 1 · eventos
`MODEL_ONLY` 47 · teses acionáveis 0. Invariante 7 do baseline ("MODEL_ONLY
nunca vira VALUE") verificado em execução.

## 6. Limitações e riscos (o que este relatório não prova)

1. **Odds do football-data ≠ Superbet.** O baseline "mercado" é a odd média
   pré-jogo das casas do football-data. A Superbet pode ser pior ou melhor que
   essa média em mercados específicos — só o shadow mode responde isso.
2. **Gate simplificado no replay.** Confiança, frescor, qualidade de dados,
   clusters e correlação não foram reproduzidos. O ROI −10 % é do gate
   edge/EV/faixa de odd; o gate completo pode filtrar melhor (ou pior).
3. **Só 1X2 e OU 2,5** têm preço histórico. Escanteios, cartões,
   finalizações, handicaps, BTTS: sem baseline de mercado → o Performance
   marca `secondary_markets` como `INSUFFICIENT` até haver liquidação real.
4. **Shadow com 0 liquidadas.** Todas as métricas "ao vivo" (ROI OOS,
   CLV vs Superbet, drift com referência) começam a existir nos próximos dias
   com o app aberto.
5. **Seleções sem mercado histórico.** O lift vs ingênuo é grande, mas lift
   vs ingênuo não paga aposta; o rótulo `MODEL_ONLY` fica até existir
   evidência com preço.
6. **Ensemble na primeira janela** usa pesos iguais (não há janelas anteriores).
7. **Player Engine** continua desativado (sem fonte pública permitida), como
   exigido.

## 7. O que mudou de verdade para o usuário

- O app agora **diz quando não sabe**: MODEL ONLY, VALUE CANDIDATE e o painel
  MODEL HEALTH deixam claro que hoje o EdgeFut não tem prova de vantagem sobre
  o mercado.
- **Nada de VALUE** enquanto não houver ≥ 100 apostas liquidadas out-of-sample
  com ROI positivo conclusivo no mercado.
- Toda métrica agregada vem com **IC 95 % e qualidade de amostra**.
- Mudanças de modelo passam por **regra de promoção** documentada e
  explícita; drift e decay são **sinais**, nunca ajustes automáticos.
- **Uma tese por evento** é primária; alternativas correlacionadas ficam
  marcadas, e a exposição do evento é exibida.

## 8. Próximos passos sugeridos (fora desta iteração)

1. Acumular shadow liquidado (≥ 300 por mercado) e comparar contra as odds da
   Superbet — é a única forma de saber se há mercado onde a Superbet é menos
   eficiente que a média do football-data.
2. Reproduzir o Quality Gate completo no replay (confiança, frescor,
   qualidade) para medir se ele separa apostas boas de ruins, ou só reduz N.
3. Testar um **modelo híbrido mercado + modelo** (ex.: regressão logística do
   resultado sobre `logit(p_mercado)` e `logit(p_modelo)`), já que o mercado é
   claramente o melhor preditor isolado — usando a regra de promoção.
4. Odds históricas de Over/Under e BTTS além de 2,5 para ampliar a base de
   mercados com baseline.
