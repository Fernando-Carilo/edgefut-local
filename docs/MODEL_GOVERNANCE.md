# EdgeFut AI — Governança de modelos

Quem decide em produção, como um modelo novo entra, e o que **nunca** acontece
sozinho. Metodologia de medição em [`VALIDATION.md`](VALIDATION.md); números da
última rodada em [`ITERATION_3_REPORT.md`](ITERATION_3_REPORT.md).

## 1. Papéis (`model_registry.role`)

| Papel | Significado | Hoje |
|---|---|---|
| `champion` | decide em produção. O consenso campeão é `ensemble` (v1: Poisson + Dixon-Coles + Bivariate Poisson) e seus membros | `ensemble`, `poisson`, `dixon_coles`, `bivariate_poisson` |
| `challenger` | roda em paralelo em toda análise e em todo replay; aparece em Model Comparison; **não** entra no consenso | `ensemble_v2` (v1 + `poisson_v2`), `poisson_v2`, `strength_v2`, `international_strength` |
| `baseline` | referência para lift; nunca decide | `elo` |
| `none` | componentes sem papel preditivo próprio | calibração, MC, corners/cards/shots, confiança, opportunity, pipeline |

O campeão atual é lido de `model_registry.parameters.champion_consensus` da
linha `ensemble` (`governance.current_champion`); default `ensemble`. Toda
análise e todo `prediction_snapshot` / `shadow_prediction` gravam o campeão
em uso, então uma troca é auditável.

## 2. Regra de promoção (`governance.evaluate_promotion`)

Avaliada sobre o **último replay de clubes** (out-of-sample, com odds). Um
challenger só é `eligible` se passar em **todos** os cinco checks:

| # | Check | Regra |
|---|---|---|
| 1 | `brier_better` | Brier do challenger < Brier do campeão **e** IC 95 % da diferença (bootstrap pareado por partida) inteiramente < 0 |
| 2 | `logloss_not_worse` | IC da diferença de LogLoss não inteiramente > 0 |
| 3 | `calibration_not_worse` | ECE do challenger ≤ ECE do campeão + 0,005 |
| 4 | `sample_adequate` | N pareado ≥ `sample_moderate_min` (300) |
| 5 | `stable_across_windows` | challenger melhor em ≥ 60 % das janelas walk-forward |

**ROI não é critério** (`roi_is_not_a_criterion: true` no relatório). ROI em
apostas simuladas depende do gate, da faixa de odd e do ruído das odds; um
modelo pode ter ROI melhor por sorte em poucas apostas de odd alta. A promoção
é sobre qualidade probabilística.

Vereditos exibidos:

- `PROMOTE` — todos os checks ok (`eligible: true`); a promoção ainda é decisão humana;
- `INSUFFICIENT DATA` — challenger ausente do replay ou amostra abaixo do mínimo;
- `PROMISING · UNSTABLE` — melhor em qualidade, mas falha em estabilidade (ou
  amostra);
- `KEEP CHAMPION` — não é melhor.

Estado em 2026-09-23: `ensemble_v2` → **PROMISING · UNSTABLE** (Brier 0,5854
vs 0,5857, Δ −0,0004 [−0,0007; −0,0001]; LogLoss e ECE ok; N 14.152; melhor
em 246/463 janelas = 53 % < 60 %). `poisson_v2` → **KEEP CHAMPION** (Brier e
LogLoss piores que o campeão). Nenhuma promoção foi feita. No replay de
seleções, `ensemble_v2` também não é melhor (0,5177 vs 0,5175; ECE pior).

## 3. Promoção é ação explícita

`POST /validation/governance/promote {consensus: "ensemble" | "ensemble_v2",
reason: "…"}` (motivo obrigatório, 5–500 caracteres):

1. grava `champion_consensus` e `champion_since` na linha `ensemble` do
   registry, ajusta `role` de `poisson_v2`/`strength_v2`;
2. grava `validation_run(kind="promotion")` com `from`, `to`, ator, motivo e a
   avaliação da regra no momento;
3. invalida o cache de análises (a próxima análise já usa o novo campeão e o
   **WHY MODEL CHANGED** mostra `CHAMPION_CHANGED`).

Se a regra **não** estava cumprida no último replay, a promoção ainda é
possível, mas a resposta traz `warning: "Promoção sem regra cumprida … —
registrada como decisão do operador."` e isso fica no histórico. Nenhum job,
alerta, drift ou replay promove sozinho.

Rollback = promover o consenso anterior, com motivo.

## 4. Hiperparâmetros governados

| Parâmetro | Como é escolhido | Valor |
|---|---|---|
| `strength_half_life_days` (strength-v2 / poisson_v2) | walk-forward `POST /validation/decay` — melhor Brier; empate → o mais simples | **365 d** (empate estatístico com 180; ambos melhores que 730/none/90/60/30, conclusivo) |
| pesos do ensemble por competição | walk-forward `ensemble_weights` (log loss, κ = 25; N ≥ 200 senão GLOBAL) | recalculado a cada 24 h; nunca fixo à mão |
| calibração isotônica | só `reliable` com ≥ 300 liquidados por (competição, mercado) | hoje nenhum calibrador confiável → RAW |
| limiares de decisão (edge, EV, faixa de odd, gate) | Configurações, com `HARD_FLOORS/CEILINGS` | nunca relaxados automaticamente |
| `value_min_oos_bets` | fixo (piso) | 100 apostas OOS no mercado para uma seleção poder ser `VALUE` |

## 5. Sinais que **não** mudam nada sozinhos

| Sinal | O que faz | O que não faz |
|---|---|---|
| **Drift monitor** (`OK / WATCH / DRIFT`) | alerta, mostra o que deslocou, sugere revisão | não reajusta modelo, não muda limiar, não desliga mercado |
| **Model Health** (`OK / WATCH / DRIFT / UNVALIDATED / UNAVAILABLE`) | resume replay, reconciliação, shadow, drift, decay no Início | não bloqueia o Radar; MODEL_ONLY/VALUE continuam sendo decididos pelas regras do pipeline |
| **Extreme probability guard** | penaliza a **confiança** (×0,85 ou ×0,70) e registra `EXTREME_PROBABILITY` | não trunca a probabilidade |
| **Reconciliação** (`SETTLEMENT_ERROR`, divergências) | alerta | não reescreve snapshots (correções vão para `snapshot_correction`) |
| **Decay walk-forward** | recomenda e grava o half-life | não roda sozinho: é disparado por operador |
| **Market discovery / hipóteses** (it. 5) | mede por mercado, muda maturidade e estado de edge, marca `VALUE_ENABLEMENT_CANDIDATE` | **não** liga VALUE, não muda staking, não altera modelo nem thresholds |
| **Collector health / schema change** (it. 5) | alerta, quarentena, `DEGRADED/BROKEN` | não adivinha campos, não apaga raw, não preenche lacunas |

## 6. O que é gravado (auditoria)

- `model_registry` — versão, features, parâmetros, papel, `champion_consensus`, `champion_since`.
- `validation_run` — todo replay, decay, bootstrap, shadow_report, drift e
  promoção, com request, summary e detail; `GET /validation/runs`.
- `prediction_snapshot` (imutável) e `shadow_prediction` (append-only) —
  probabilidades, odds, estado, cluster, campeão e versões no instante da
  análise.
- `snapshot_correction` — correções legítimas (ex.: mando errado) sem tocar o
  original.
- `job_run` — cada execução de job com correlation id; execuções órfãs
  marcadas `interrupted` no boot.

## 7. Como um modelo novo entra

1. Implementar como challenger (`versions.py`, `models/registry.py` com
   `role="challenger"`), disponível ao replay (`validation/replay.py::MODELS`).
2. Rodar o replay de clubes; olhar `promotion_evaluation`.
3. Se `PROMOTE` e a leitura humana concordar, promover com motivo.
4. Acompanhar shadow e drift; se piorar, promover o anterior (rollback com
   motivo).

Nunca: fixar pesos à mão, escolher decay "no olho", promover por ROI, relaxar
limiar para o Radar encher, ou apagar uma versão que algum snapshot referencie.

## 8. Promoção market-aware (iteração 4, `governance.evaluate_market_aware_promotion`)

Challengers que usam o preço do mercado como prior (`market-model-blend-v1`,
`market-logistic-stack-v1`, `market-residual-v1`) têm uma regra própria e
mais dura (§29 do spec), avaliada **primeiro na descoberta e depois no
holdout congelado**, que só roda uma vez por `config_hash + dataset_version`:

| Critério | Regra |
|---|---|
| `brier_better` | IC 95 % (bootstrap por cluster) de Δ Brier challenger − mercado inteiramente < 0 |
| `logloss_not_worse` | Δ LogLoss ≤ 0 |
| `calibration_not_worse` | ECE ≤ ECE do mercado + 0,005 |
| `stable_windows` | melhor que o mercado em ≥ 60 % das janelas |
| `effective_n` | N efetivo ≥ 300 |
| sem leakage | closing line nunca é feature (teste); holdout intocado pela descoberta |
| **camada de value** | além de tudo acima, CLV Superbet ≥ 0 com IC que não exclua zero para baixo |

Vereditos: `NOT EVALUATED` · `INSUFFICIENT DATA` · `NO EVIDENCE OF MARKET
EDGE` · `CHALLENGER BEATS MARKET · CLV UNKNOWN` · `CHALLENGER BEATS MARKET ·
CLV NEGATIVE` · `PROMOTE VALUE LAYER`. Ver o holdout **não** autoriza ajustar
o modelo e repetir: qualquer mudança gera novo `config_hash` e exige holdout em
período posterior.

Estado em 2026-09-24: **NO EVIDENCE OF MARKET EDGE** nos dois mercados
(1X2, OU 2,5), `MARKET EDGE STATUS = UNPROVEN`; nenhum challenger market-aware
alimenta recomendações (`docs/ITERATION_4_REPORT.md`).

## 9. Iteração 5 — MODEL FREEZE e governança por mercado (`flywheel/governance.py`)

**Freeze.** Nenhum modelo muda nesta iteração. `register_freeze` grava
`model_hash`, `config_hash`, `dataset_version` e a lista de 21 módulos
congelados; `freeze_status` devolve `INTACT` ou `DRIFTED` (mostrado na UI e
nos relatórios). Famílias proibidas: deep learning, transformers, redes neurais,
gradient boosting. `1X2 market-aware modeling` em **PAUSE**. Qualquer alteração
de modelo é uma nova iteração com novo hash — não um ajuste "para ver".

**Estado de edge por mercado** (`market_edge_state`, 15 categorias canônicas):

```
UNPROVEN ──► COLLECTING ──► PROMISING ──► VALIDATED   (só por ação humana, com motivo)
                 │                └──► REJECTED    (mercado claramente melhor, amostra MATURE)
                 └─ regressa se a evidência deixar de ser conclusiva
```

`VALUE_ENABLEMENT_CANDIDATE` exige **todas**: N efetivo ≥ 500 · EdgeFut bate a
fair da Superbet (ΔBrier IC inteiramente < 0) · CLV IC inferior ≥ 0 · calibração
sem viés detetável · hipótese pré-registada `SUPPORTED` após BH-FDR · ≥ 30 dias
de confirmação. Regras cumpridas/falhadas ficam em `evidence` e na tela Pesquisa.

**O que nunca acontece sozinho**: ligar `VALUE_ENABLED` (é `POST
/flywheel/edge-states/{cat}/value` com `reason`, gravado em `history` e
`manual_correction`; só aceito se candidato); ligar staking/Kelly
(`staking_enabled()` exige `VALIDATED` + VALUE em pelo menos um mercado);
mapear um `marketId` ambíguo; editar raw.

**Efeito no pipeline**: uma seleção que passa o quality gate num mercado com
VALUE desligado é `RESEARCH_SIGNAL` (observar, não apostar); `/bankroll/stake`
devolve `DISABLED` com o motivo. Estado em 2026-09-24: 15 × `UNPROVEN`, 0
candidatos, 0 VALUE, staking DISABLED — o resultado correto para 0 seleções
liquidadas (`docs/ITERATION_5_REPORT.md`).
