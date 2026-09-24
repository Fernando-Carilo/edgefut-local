# EdgeFut AI — Modelos market-aware (iteração 4)

Pergunta única desta camada: **o EdgeFut contém informação incremental além
do preço do mercado?** Tudo aqui é medição. Nenhum threshold de Quality Gate,
Confidence, amostra, calibração ou value foi alterado; o Model Health continua
`WATCH` e o Radar continua com 0 `VALUE`. O resultado real está no fim
(seção 9) e em `docs/ITERATION_4_REPORT.md`.

Código: `engine/edgefut/validation/market_aware.py` (challengers, CV temporal
aninhada, holdout congelado, artefato), `validation/bootstrap.py` (bootstrap
por cluster, N efetivo, BH-FDR, decomposição de Brier),
`recommendations/required_edge.py` (incerteza + required edge),
`analysis/market_view.py` (bloco MARKET vs EDGEFUT por jogo),
`validation/governance.py::evaluate_market_aware_promotion` (§29),
`validation/model_health.py::model_health_v2` (§49).
Rotas: `POST /validation/market-aware` (discovery + congelamento),
`GET /validation/market-aware/latest`, `POST /validation/market-aware/holdout`
(uma vez por `config_hash + dataset_version`; 409 se repetido).

## 1. Mercado como prior (§2)

A probabilidade justa do mercado (overround removido, `odds/implied.py`) é o
ponto de partida. O EdgeFut (`ensemble`, campeão da governança) só pode
**ajustar** esse prior; a pergunta passa a ser "quanto peso o modelo merece",
não "o modelo acerta". Uma divergência EdgeFut − mercado é chamada de
**MODEL DISAGREEMENT**; só vira **RESIDUAL EDGE** se o híbrido validado
(seção 3) se afastar do mercado na mesma direção.

## 2. Frame de replay versionado (base de dados)

O replay de clubes (`validation/replay.py`) passou a persistir, por partida e
janela, tudo o que existia **em T** (início da janela walk-forward):
probabilidades dos 6 modelos e 5 baselines, odds de abertura do football-data
(`odds_*`), Pinnacle closing (`close_*`, **só para CLV**), overround, ELO,
força ataque/defesa, forma (5 jogos), amostra mínima de time, mês/dia,
divergência entre modelos de gols. Arquivo Parquet em
`data/processed/replay_frames/`, identificado por
`dataset_version = replay-frame-v1:<sha256 do conteúdo>`. O frame usado nesta
iteração é `replay-frame-v1:55b9f039e3fa730c` (14.053 partidas, 11 ligas,
2021-08 → 2026-01-23 + holdout).

Classificação dessa base: **RESEARCH MARKET BENCHMARK**. As odds do
football-data não têm carimbo de hora por partida nem são da Superbet
(`docs/ITERATION_4_BASELINE.md` §2), logo nada aqui prova executabilidade.
A evidência executável é a **SUPERBET SHADOW VALIDATION**
(`docs/SUPERBET_VALIDATION.md`).

## 3. Challengers (§3–5)

| Challenger | Versão | Forma | Hiperparâmetro (escolhido só em treino/validação) |
|---|---|---|---|
| A — blend geométrico | `market-model-blend-v1` | `p ∝ p_mkt^α · p_edgefut^(1−α)`, renormalizado | α ∈ {0,5; 0,6; 0,7; 0,8; 0,9; 0,95; **1,0**} (1,0 = mercado puro, referência) |
| B — stacking logístico | `market-logistic-stack-v1` | multinomial (1X2) / binomial (OU 2,5) regularizada L2, gradiente com passo fixo; features: log-prob do mercado, log-prob do EdgeFut, diferença, concordância entre modelos, força att/def, ELO, forma, gd, vantagem de casa, dummies de competição, mês (seno/cosseno), progresso da temporada, log da amostra mínima | λ ∈ {0,03; 0,1; 0,3; 1; 3} |
| C — residual | `market-residual-v1` | regressão do logit do mercado como **offset** fixo; só o resíduo é aprendido, a partir de `diff`, `|diff|`, concordância, força, ELO, casa, competição, tempo, amostra (sem a probabilidade do EdgeFut como feature direta) | λ idem |

Nenhuma rede neural, transformer, deep learning ou gradient boosting (§41–42):
tudo é linear, interpretável e com poucos parâmetros (1X2: 33 coeficientes por
classe no stacking, 26 no residual, intercepto incluído).

Features **proibidas** em qualquer challenger: `close_h, close_d, close_a,
close_o25, close_u25` (`FORBIDDEN_COLUMNS`). O teste
`tests/test_market_aware.py::test_market_aware_never_uses_closing_line`
sobrescreve as colunas de closing por ruído e exige previsões idênticas; se um
challenger usasse closing, o teste falha. O closing só entra em CLV (§6, §28).

## 4. Validação temporal aninhada (§15)

Nunca há split aleatório. Para cada mercado:

```
[ treino ........ ][ validação 180 d ][ teste 90 d ]  → janela w
[ treino ................................ ][ val ][ teste ]  → janela w+1
```

- α, λ (e λ de cada ablação) são escolhidos **na validação** de cada janela,
  minimizando LogLoss; o modelo é reajustado em treino+validação e avaliado
  **só** no teste.
- Mínimo 1.000 linhas de treino e 100 de validação; janelas sem isso são
  puladas (não estimadas).
- Todas as janelas de teste concatenadas formam o conjunto OOS de descoberta.

Discovery real: 11 janelas por mercado (2023-07-27 → 2026-01-23), 8.387
partidas OOS por mercado, treino crescendo de 1.633 para 9.627 linhas.

## 5. Discovery vs confirmação: FROZEN HOLDOUT (§16–17)

O último `holdout_days = 240` do frame (≥ 2026-01-23) **nunca é visto** na
descoberta (`test_discovery_never_touches_holdout_and_holdout_is_frozen`). Ao
fim da descoberta o motor congela um artefato com:

| Campo | Valor real |
|---|---|
| `model_hash` | `a5e8d42f97f764d7` |
| `config_hash` | `6e0e8a7db0fe9119` |
| `dataset_version` | `replay-frame-v1:55b9f039e3fa730c` |
| `frozen_at` / `run_timestamp` (holdout) | 2026-09-24 14:48:03 / 14:55:11 UTC |
| `holdout_start` | 2026-01-23 |
| α congelado | 1X2 = **1,0** · OU 2,5 = **1,0** (moda das janelas de descoberta) |
| challenger selecionado por menor Brier de descoberta | 1X2 `residual` · OU 2,5 `blend` |
| coeficientes | gravados em `data/processed/market_aware/frozen_<model_hash>.json` (+ `active.json`) |

O holdout roda **uma vez** por `config_hash + dataset_version`
(`POST /validation/market-aware/holdout` → 409 `holdout_consumed` depois).
Não há botão "ajustar e repetir": qualquer mudança gera novo `config_hash`,
nova descoberta e um holdout novo — o relatório marca `repeated=true` nesse
caso. Na UI: Validação → Market-Aware → "Rodar holdout (1×)" / "Holdout
consumido".

## 6. Estatística honesta (§18–20, §43)

- **Bootstrap por cluster de evento** (`cluster_bootstrap_ci`): reamostra
  eventos, não seleções. No replay cada partida é um cluster (ρ = 1, N efetivo
  = N bruto); no shadow Superbet 244 seleções de 5 jogos dão **N efetivo 10**
  (ρ estimado 0,5, ~49 seleções por jogo). A UI mostra sempre *Raw N* e
  *Effective N*.
- **BH-FDR** (q = 0,10) em todos os testes por segmento
  (competição × faixa de odd); só "sobreviventes" viram hipótese, e mesmo
  assim só para confirmação em período futuro. Nunca se escolhe o segmento
  com maior ROI.
- **Decomposição de Brier** (reliability / resolution / uncertainty, 10 bins)
  por modelo, para separar "mal calibrado" de "não discrimina".
- Testes pareados por partida (Δ Brier, Δ LogLoss) com IC 95 % e teste de
  sinal por janela (`windows_better / windows_total`, p-valor binomial).

## 7. Regra de promoção market-aware (§29)

`promotion_check` exige, no OOS (e depois no holdout):

| Critério | Regra |
|---|---|
| `brier_better` | IC 95 % de Δ Brier (challenger − mercado) inteiramente < 0 |
| `logloss_not_worse` | ponto de Δ LogLoss ≤ 0 |
| `calibration_not_worse` | ECE challenger ≤ ECE mercado + 0,005 |
| `stable_windows` | vence o mercado em ≥ 60 % das janelas |
| `effective_n` | N efetivo ≥ 300 |
| sem leakage | teste de closing-line acima + holdout intocado |

Só com tudo verde o status vira `CHALLENGER BEATS MARKET`; para promover a
**camada de value** ainda é obrigatório CLV Superbet ≥ 0 com IC que não
exclua zero para baixo (`evaluate_market_aware_promotion`, §28). Caso
contrário: `NO EVIDENCE OF MARKET EDGE`.

## 8. Do laboratório para o jogo (§21–24, §36–37)

No pipeline de análise (`analysis/pipeline.py`), para 1X2 e OU 2,5 com odd
Superbet:

- **Intervalo de incerteza** da probabilidade
  (`uncertainty_interval`): meia-largura = √(spread² + ECE² + amostra²), onde
  spread = dispersão entre os modelos do consenso, ECE = calibração do campeão
  no replay (0,02 se desconhecida), amostra = 0,5·√(p(1−p)/n_min).
- **EDGE BRUTO** (`edge_raw_pp` = p_modelo − p_justa) vs **EDGE AJUSTADO**
  (bruto − meia-largura).
- **Required edge** (`required_edge`) = margem da casa (overround, teto 15 %)
  + incerteza + 0,5 pp se sem calibrador confiável + 0,5 pp se o mercado não
  tem prova OOS suficiente + 1,0 pp de "eficiência" quando o veredicto
  market-aware daquele mercado é `NO EVIDENCE OF MARKET EDGE`; nunca abaixo do
  `min_edge_pp` configurado.
  Exemplo do spec (bruto +5,2 pp, margem 5,1 %, incerteza 2,4 pp → required
  7,8 pp → `OBSERVATION`) está em `test_required_edge_spec_example_is_observation`.
- Se bruto < required → razão `EDGE_NOT_ROBUST`, estado no máximo
  `OBSERVATION`. Isso **não relaxa** nada: só adiciona uma barreira.
- **MARKET vs EDGEFUT** (`market_view`): Superbet justa · EdgeFut · híbrido do
  artefato congelado · divergência · residual edge, com o status do holdout
  daquele mercado. Como o artefato congelado tem α = 1,0 e residual ≈ mercado,
  o residual edge mostrado hoje é ≈ 0 pp e vem rotulado `NÃO VALIDADO — NO
  EVIDENCE OF MARKET EDGE`. Se o híbrido do artefato divergir do mercado num
  mercado promovido, e só então, aparece `RESIDUAL EDGE`.

Exemplo real (Granada × Andorra, OVER 2,5 @ 2,02, 2026-09-24): modelo 58,8 %
vs justa 47,1 % → bruto **+11,7 pp**, incerteza ±5,8 pp (spread 2,4 ⊕
calibração 3,6 ⊕ amostra 3,9), ajustado +5,9 pp, required **12,4 pp**
(margem 5,1 + incerteza 5,8 + calibração 0,5 + amostra 0,0 + mercado 1,0) →
`EDGE NÃO ROBUSTO` → `OBSERVATION`. Divergência do modelo +11,7 pp; residual
edge 0,0 pp; não validado.

## 9. Resultado (resumo; números completos em `ITERATION_4_REPORT.md`)

| | 1X2 discovery (n 8.387, 11 jan.) | 1X2 holdout (n 2.231, 8 jan.) | OU 2,5 discovery (8.387) | OU 2,5 holdout (2.230) |
|---|---|---|---|---|
| Brier mercado | 0,5695 | 0,5831 | 0,4772 | 0,4752 |
| Brier EdgeFut | 0,5855 (+0,0160 [+0,0136; +0,0185]) | 0,5958 (+0,0127 [+0,0072; +0,0181]) | 0,4880 (+0,0108) | 0,4853 (+0,0101) |
| Brier blend (α escolhido) | 0,5696 (α = 1,0 em 10/11) | 0,5831 (α = 1,0) | 0,4773 (α = 1,0 em 8/11) | 0,4752 (α = 1,0) |
| Brier logistic-stack | 0,5698 (+0,0003 [−0,0008; +0,0013]) | 0,5834 (+0,0003 [−0,0019; +0,0024]) | 0,4779 (+0,0007) | 0,4749 (−0,0002 [−0,0013; +0,0009]) |
| Brier residual | 0,5689 (−0,0007 [−0,0016; +0,0003], 7/11) | 0,5829 (−0,0001 [−0,0020; +0,0017], 4/8) | 0,4775 (+0,0003) | 0,4749 (−0,0002 [−0,0011; +0,0007], 5/8) |
| Contrarian (≥ 5 pp) | MARKET CORRECT | MARKET CORRECT | MARKET CORRECT | MARKET CORRECT |
| Segmentos que sobrevivem ao FDR | 0 / 14 | 0 / 14 | 0 / 13 | 0 / 13 |
| Veredicto §29 | NO EVIDENCE OF MARKET EDGE | **NO EVIDENCE OF MARKET EDGE** | NO EVIDENCE OF MARKET EDGE | **NO EVIDENCE OF MARKET EDGE** |

**α = 1,0 venceu**: o EdgeFut não adicionou valor ao mercado no blend. Os
challengers B e C empatam com o mercado dentro do ruído (Δ Brier da ordem de
±0,0002 com ICs que cruzam zero) e a ablação `market_only` — logística só com
o preço — é tão boa ou melhor do que qualquer variante com features do
EdgeFut. Isto é um resultado válido (§51): **MARKET STILL BETTER**. Nenhum
threshold ou metodologia foi alterado para evitá-lo.

## 10. Status separados (§30–31, §49)

- **FOOTBALL PREDICTION STATUS**: `PREDICTIVE (vs naive)` — o campeão bate
  os baselines ingênuos de forma consistente (lift 9,6 %), mas não o mercado.
- **MARKET EDGE STATUS**: `UNPROVEN` — vindo do holdout congelado
  (`model_hash a5e8d42f97f764d7`).
- Model Health V2 no Dashboard mostra os dois lado a lado com SUPERBET
  EVIDENCE, SHADOW SETTLED N (raw/efetivo) e MARKET EDGE UNPROVEN.

## 11. O que continua fora

- Mercados secundários (BTTS, escanteios, cartões, handicaps): sem base
  histórica com odds → nenhum challenger; ficam `EXPERIMENTAL`/`MODEL_ONLY`.
- Seleções (INTL): o replay de seleções não tem odds → sem frame market-aware;
  o bloco MARKET vs EDGEFUT usa a odd Superbet ao vivo mas o status é
  `NÃO VALIDADO`.
- Nenhum modelo market-aware alimenta recomendações. O único efeito no
  pipeline é **restritivo** (required edge) e informativo (bloco por jogo).
