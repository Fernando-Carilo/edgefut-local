# Iteração 4 — MARKET-AWARE INTELLIGENCE & SUPERBET VALIDATION — Relatório

Data: 2026-09-24 (UTC). Base auditada: `docs/ITERATION_4_BASELINE.md`
(commit `baea2ae`). Runs citados: discovery market-aware **#18**, holdout
congelado **#22**, replay de clubes **#41**, relatório Superbet
`superbet-shadow-v1` 15:48 UTC, shadow report 15:45 UTC. Todos reproduzíveis
pelas rotas indicadas em cada seção. Nada abaixo é mock ou DEMO.

## 0. A resposta (§53 — as 7 perguntas)

| # | Pergunta | Resposta |
|---|---|---|
| 1 | O modelo bate o mercado no holdout congelado? | **Não.** 1X2: mercado Brier 0,5831 vs EdgeFut 0,5958 (Δ +0,0127 [+0,0072; +0,0181], 0/8 janelas); OU 2,5: 0,4752 vs 0,4853 (Δ +0,0101 [+0,0043; +0,0159], 0/8). |
| 2 | Algum challenger market-aware adiciona informação ao preço? | **Não.** Melhor challenger no holdout: 1X2 `residual` Δ −0,0001 [−0,0020; +0,0017], p 0,89, 4/8 janelas; OU 2,5 `logistic`/`residual` Δ −0,0002 [−0,0013; +0,0009], 5/8. Nenhum critério `brier_better` passou. |
| 3 | Qual α venceu? | **α = 1,0 (mercado puro)** em 10/11 janelas de descoberta no 1X2 (média 0,995) e 8/11 no OU 2,5 (média 0,977); congelado em 1,0 nos dois mercados. Segundo o §3: *EdgeFut não adicionou valor.* |
| 4 | Quando o EdgeFut discorda forte do mercado, quem está certo? | **O mercado**, nos quatro testes (discovery e holdout, 1X2 e OU 2,5). Holdout 1X2: lado favorecido observado 33,2 % [30,2; 36,0], mercado 32,9 %, EdgeFut 42,4 %. |
| 5 | Algum segmento (competição × faixa de odd) mostra edge após FDR? | **Nenhum.** 0 sobreviventes em 14 + 13 (discovery) e 14 + 13 (holdout) testes, q = 0,10. Os únicos p < 0,05 (SP2, odds 2,00–3,00, D1) são vitórias **do mercado**. |
| 6 | Existe CLV positivo na Superbet? | **Não demonstrado.** CLV médio −1,3 % em 314 seleções de 6 eventos, IC não calculável; 0 recomendações `VALUE` para medir. |
| 7 | O N liquidado na Superbet permite qualquer conclusão? | **Não.** Raw N 244, 5 jogos, N efetivo 10 → `INSUFFICIENT`. |

**Conclusão: NO EVIDENCE OF MARKET EDGE. Sem maquiagem.**

O EdgeFut é um previsor de futebol razoável (`FOOTBALL PREDICTION STATUS =
PREDICTIVE (vs naive)`, lift 9,6 % sobre o naive, consistente em 440
janelas), mas **não contém informação além do preço** (`MARKET EDGE STATUS =
UNPROVEN`). Isso é um resultado válido (§51: *MARKET STILL BETTER*) e nenhum
threshold, split, hiperparâmetro ou metodologia foi alterado para evitá-lo.
Model Health continua `WATCH`; Radar continua com **0 VALUE**.

## 1. O que foi construído (ordem do spec)

| Fase | Entrega | Onde |
|---|---|---|
| A | Baseline auditado: odds football-data sem carimbo → *RESEARCH MARKET BENCHMARK*; `closing_odd` nulo em 100 % do shadow; N efetivo ~48 eventos | `docs/ITERATION_4_BASELINE.md` |
| B | Frame por partida do replay (features em T) em Parquet versionado por hash; teste **closing line nunca é feature** | `validation/replay.py`, `data/processed/replay_frames/`, `tests/test_market_aware.py::test_market_aware_never_uses_closing_line` |
| C | Challengers `market-model-blend-v1`, `market-logistic-stack-v1`, `market-residual-v1`; CV temporal aninhada; ablação; discovery → freeze automático; holdout único por `config_hash + dataset_version` | `validation/market_aware.py`, `POST /validation/market-aware`, `/holdout`, `/latest` |
| D | Bootstrap por cluster de evento, `effective_sample_size`, BH-FDR, decomposição de Brier | `validation/bootstrap.py` |
| E | `SuperbetEvidenceEngine`: cobertura, buckets T-24h…T-15m só quando existem, overround por mercado/competição/faixa/tempo, opening/rec/closing, viés, Superbet fair como baseline, CLV próprio, painel shadow; backfill de closing | `validation/superbet.py`, `GET /validation/superbet`, `/selection` |
| F | `RequiredEdgeEngine` + intervalo de incerteza + EDGE BRUTO vs AJUSTADO; razões `EDGE_NOT_ROBUST` / `RESIDUAL_EDGE_LOW`; bloco MARKET vs EDGEFUT (híbrido congelado); `HistoricalQualityGate` com `UNAVAILABLE_IN_REPLAY` | `recommendations/required_edge.py`, `analysis/market_view.py`, `pipeline.py`, `replay.py` |
| G | Shadow v2 (Superbet fair × EdgeFut × híbrido, CLV, validação OOS de grau A/B/C/D e bins do Opportunity Score); regra de promoção market-aware com CLV ≥ 0; Model Health V2; migração v4 | `validation/shadow.py`, `governance.py`, `model_health.py`, `db/migrations.py` |
| H | UI: Match → MARKET vs EDGEFUT + edge bruto/ajustado/required; Validação → abas Market-Aware e Superbet; Shadow v2; Performance → Market Efficiency Lab e Superbet Lab; Dashboard → Model Health V2 | `apps/desktop/src/pages/MarketAwarePanels.tsx`, `TrustPanels.tsx`, `Dashboard.tsx`, `packages/contracts` |

Testes: **183 passed** (`engine/tests`), inclusive `test_market_aware.py`
(closing nunca feature; discovery não toca holdout e holdout é congelado;
promoção exige N efetivo; bootstrap por cluster), `test_required_edge.py`
(exemplo do spec → OBSERVATION; penalidades só elevam a barra; engine bloqueia
VALUE com edge não robusto; gate histórico), `test_superbet_evidence.py`
(justa/overround só em mercados completos; timeline; backfill + CLV shadow),
`test_shadow_v2_health.py` (migração v4; painel por cluster; promoção com CLV;
health v2 UNPROVEN). Typecheck e build do frontend verdes.

## 2. RESEARCH MARKET BENCHMARK — market-aware (runs #18 e #22)

Frame `replay-frame-v1:55b9f039e3fa730c`: 14.053 partidas, 11 ligas (B1 D1 E0
F1 I1 N1 P1 SC0 SP1 SP2 T1), 2021-08 → 2026-01-23 (descoberta, 11.768 linhas)
+ holdout ≥ 2026-01-23 (2.285 linhas). Base: `ensemble` (campeão). Janelas de
teste 90 d, validação 180 d, treino ≥ 1.000. `config_hash 6e0e8a7db0fe9119`,
`model_hash a5e8d42f97f764d7`. Classificação: **RESEARCH MARKET BENCHMARK**
(as odds do football-data não têm carimbo por partida nem são da Superbet).

### 2.1 1X2 — discovery (n 8.387, 11 janelas 2023-07 → 2026-01)

| Modelo | Brier | LogLoss | ECE | Δ Brier vs mercado [IC 95 %] | janelas melhores | p |
|---|---|---|---|---|---|---|
| mercado (α = 1) | 0,5695 | 0,9596 | 0,0122 | — | — | — |
| EdgeFut (`ensemble`) | 0,5855 | 0,9835 | 0,0149 | **+0,0160 [+0,0136; +0,0185]** | 0/11 | 0,001 |
| blend (α escolhido) | 0,5696 | 0,9596 | 0,0124 | +0,0000 [0; +0,0001] | 9/11 | 0,041 |
| logistic-stack | 0,5698 | 0,9592 | 0,0099 | +0,0003 [−0,0008; +0,0013] | 4/11 | 0,618 |
| residual | 0,5689 | 0,9579 | 0,0081 | −0,0007 [−0,0016; +0,0003] | 7/11 | 0,176 |

Decomposição de Brier (mercado vs EdgeFut): reliability 0,0020 vs 0,0015,
**resolution 0,0798 vs 0,0645** — o problema do EdgeFut não é calibração, é
menos discriminação do que o preço.

α por janela: 0,95, 1,0 ×10 → média **0,995**, 91 % das janelas em 1,0.

Ablação (stack logístico; Δ Brier vs mercado): `market_only` 0,5688 (−0,0007
[−0,0014; −0,0000]) · +`form` 0,5691 · +`home_adv` 0,5691 · +`model`
(EdgeFut) 0,5694 (−0,0001 [−0,0009; +0,0007]) · +`strength` 0,5695 · +`elo`
0,5697 · `all` 0,5698 (+0,0003). **Adicionar a probabilidade do EdgeFut ao
preço não melhora nada; a melhor variante é a que só recalibra o mercado.**

Buckets de divergência |EdgeFut − mercado| (§44):

| Bucket | n | Brier mercado | Brier EdgeFut | Δ [IC] | lado favorecido: observado / mercado / EdgeFut |
|---|---|---|---|---|---|
| 0–2 pp | 1.172 | 0,5491 | 0,5501 | +0,0010 [−0,0001; +0,0021] | 33,5 % / 33,9 % / 35,0 % |
| 2–5 pp | 2.932 | 0,5634 | 0,5667 | +0,0033 [+0,0016; +0,0051] | 33,8 % / 35,0 % / 38,1 % |
| 5–10 pp | 2.861 | 0,5807 | 0,5962 | +0,0155 [+0,0116; +0,0190] | 30,7 % / 33,8 % / 40,2 % |
| 10+ pp | 1.422 | 0,5765 | 0,6322 | **+0,0557 [+0,0445; +0,0668]** | 30,0 % / 32,4 % / 45,4 % |

Quanto maior a divergência, pior o EdgeFut. Contrarian (≥ 5 pp, n 3.800):
observado 32,0 % [30,6; 33,5], mercado 34,9 %, EdgeFut 44,1 % → **MARKET
CORRECT**.

Segmentos (challenger `blend` vs mercado, 14 testes, BH q = 0,10): 0
sobreviventes. p < 0,05 só em D1 (0,033) e SP2 (0,017), ambos **MARKET**
(ajustados 0,231). Por liga, o EdgeFut perde para o mercado nas 11.

### 2.2 1X2 — FROZEN HOLDOUT (n 2.231, 8 janelas ≥ 2026-01-23, rodado 1×)

| Modelo | Brier | LogLoss | ECE | Δ Brier vs mercado [IC] | janelas | p |
|---|---|---|---|---|---|---|
| mercado | 0,5831 | 0,9795 | 0,0107 | — | — | — |
| EdgeFut | 0,5958 | 0,9988 | 0,0105 | **+0,0127 [+0,0072; +0,0181]** | 0/8 | 0,001 |
| blend (α = 1,0 congelado) | 0,5831 | 0,9795 | 0,0107 | 0 | 5/8 | 0,077 |
| logistic-stack (congelado) | 0,5834 | 0,9802 | 0,0122 | +0,0003 [−0,0019; +0,0024] | 4/8 | 0,808 |
| residual (congelado, selecionado) | 0,5829 | 0,9790 | 0,0119 | −0,0001 [−0,0020; +0,0017] | 4/8 | 0,888 |

Regra §29 por challenger: `brier_better` **falhou** nos três; logistic ainda
falhou `logloss_not_worse` e `stable_windows`; residual falhou
`stable_windows` (4/8 < 60 %). Veredicto: **NO EVIDENCE OF MARKET EDGE**.

Buckets 10+ pp (n 376): mercado 0,5755 vs EdgeFut 0,6290 (+0,0535 [+0,0241;
+0,0796]); favorecido observado 29,0 % / mercado 28,6 % / EdgeFut 42,0 %.
Contrarian (n 959): 33,2 % [30,2; 36,0] vs 32,9 % vs 42,4 % → MARKET CORRECT.
Segmentos: 0/14 sobrevivem; SP2 (p 0,032) e odds 2,00–3,00 (p 0,049) são
vitórias do mercado (ajustado 0,336).

### 2.3 OU 2,5 — discovery (n 8.387)

| Modelo | Brier (2 classes) | LogLoss | ECE | Δ vs mercado [IC] | janelas | p |
|---|---|---|---|---|---|---|
| mercado | 0,4772 | 0,6698 | 0,0076 | — | — | — |
| EdgeFut | 0,4880 | 0,6814 | 0,0282 | **+0,0108 [+0,0078; +0,0137]** | 0/11 | 0,001 |
| blend | 0,4773 | 0,6699 | 0,0072 | +0,0001 [−0,0001; +0,0003] | 5/11 | 0,264 |
| logistic-stack | 0,4779 | 0,6705 | 0,0053 | +0,0007 [−0,0003; +0,0016] | 3/11 | 0,168 |
| residual | 0,4775 | 0,6701 | 0,0045 | +0,0003 [−0,0004; +0,0009] | 4/11 | 0,354 |

α: 1,0 em 8/11 janelas (0,95; 0,9; 0,9 nas outras), média 0,977. Ablação:
`market_strength` 0,4770 e `market_only` 0,4771 são as melhores; nenhuma com
IC fora de zero. Buckets 10+ pp (n 1.217): +0,0359 [+0,0204; +0,0522] a favor
do mercado. Contrarian (n 3.801): 48,5 % [46,8; 50,0] vs mercado 49,8 % vs
EdgeFut 59,1 % → MARKET CORRECT. Segmentos: 0/13 sobrevivem (menor p 0,12).

### 2.4 OU 2,5 — FROZEN HOLDOUT (n 2.230, 8 janelas)

| Modelo | Brier | LogLoss | ECE | Δ vs mercado [IC] | janelas | p |
|---|---|---|---|---|---|---|
| mercado | 0,4752 | 0,6676 | 0,0114 | — | — | — |
| EdgeFut | 0,4853 | 0,6780 | 0,0188 | **+0,0101 [+0,0043; +0,0159]** | 0/8 | 0,001 |
| blend (α = 1,0, selecionado) | 0,4752 | 0,6676 | 0,0114 | 0 | 5/8 | 0,126 |
| logistic-stack | 0,4749 | 0,6673 | 0,0090 | −0,0002 [−0,0013; +0,0009] | 5/8 | 0,683 |
| residual | 0,4749 | 0,6673 | 0,0071 | −0,0002 [−0,0011; +0,0007] | 5/8 | 0,639 |

`brier_better` falhou nos três; veredicto **NO EVIDENCE OF MARKET EDGE**.
Contrarian (n 1.057): 47,8 % [44,8; 50,9] vs 47,9 % vs 57,2 % → MARKET
CORRECT. Segmentos: 0/13.

### 2.5 Replay de clubes com HistoricalQualityGate (run #41)

Mesmo frame (hash idêntico → holdout permanece consumido). 14.053 partidas,
440 janelas, `ensemble` Brier 1X2 0,5858 vs mercado 0,5715 (Δ +0,0152
[+0,0131; +0,0173], 99/439 janelas, `NO CLEAR ADVANTAGE`); OU 2,5 0,2448 vs
0,2388. Apostas simuladas agora passam pelo gate histórico: de 83.004
candidatas, **18.942 bloqueadas por `MODEL_DISAGREEMENT` e 5.676 por
`SMALL_SAMPLE`**; componentes `odds_freshness`, `provider_health`,
`lineups`, `clusters_correlation` declarados `UNAVAILABLE_IN_REPLAY`.
Resultado: 10.118 apostas, ROI **−9,4 %** [−11,4; −7,5], CLV Pinnacle
**−3,5 %**, `NEGATIVE` (1X2 −10,8 %; OU 2,5 −7,9 %). O gate não salvou o
resultado — e não deveria salvar: só bloqueia o que o gate de produção também
bloquearia.

## 3. SUPERBET SHADOW VALIDATION (detalhe em `docs/SUPERBET_VALIDATION.md`)

| Item | Valor real |
|---|---|
| Coleta | 2026-09-23 13:18 → 2026-09-24 15:44 UTC · 37.900 snapshots pré-kickoff · 239 eventos · 16.265 seleções · cadência mediana 46 min |
| Buckets presentes | T-15m 56 ev · T-30m 54 · T-1h 69 · T-3h 71 · T-6h 39 · T-12h 9 · T-24h 50 · > 24h 62 (todos reais) |
| Overround mediano | 1X2 **9,39 %** · OU 8,52 % · BTTS 7,95 % · DNB 10,97 % · dupla chance 9,03 % (book total 2) · AH 9,94 % · 1X2 sobe de 8,35 % (> 24h) para 10,73 % (T-15m) |
| Movimento de linha 1X2 | 382 seleções · 74 % moveram > 0,5 pp · mediana 0,99 pp · P90 3,93 pp · corr(favorito, deriva) +0,005 |
| Viés da casa | 13 segmentos testados (HOME/DRAW/AWAY, favorito/underdog, 5 faixas de odd): **NO BIAS DETECTED** em todos; maior desvio HOME +5,7 pp com 123 eventos |
| Superbet fair vs EdgeFut (shadow liquidado) | 244 seleções · 5 jogos · **N efetivo 10** · Brier 0,1706 vs 0,1878 (Δ +0,0172) · híbrido n 0 · `INSUFFICIENT DATA` |
| CLV Superbet | 314 seleções · 6 eventos · −1,3 % · IC não calculável · 0 recomendações `VALUE` |
| Painel shadow | 4.931 previsões · 261 liquidadas · VALUE **0** · OBSERVATION 481 |
| Grau A/B/C/D e Opportunity bins OOS | `INSUFFICIENT DATA` (A 1 jogo, B 4, D 5; bins 76/29/126/13 seleções) |

## 4. Da bancada para a tela (§21–24, §36–39, §49)

- **Required edge** em toda seleção 1X2/OU 2,5 com odd: margem + incerteza +
  calibração + amostra + eficiência. Exemplo real Granada × Andorra, OVER 2,5
  @ 2,02: bruto +11,7 pp, ajustado +5,9 pp, required 12,4 pp → `EDGE NÃO
  ROBUSTO` → `OBSERVATION`, com WHY NOT em texto: "Edge bruto +11,7 pp abaixo
  do required edge 12,4 pp (margem 5,1 + incerteza 5,8 + calibração 0,5 +
  amostra 0,0 + mercado 1,0): ajustado por incerteza fica em +5,9 pp.
  Divergência do modelo não é edge."
- **MARKET vs EDGEFUT** por jogo: Superbet justa · EdgeFut · híbrido congelado
  · divergência · residual edge, com chip `NÃO VALIDADO — NO EVIDENCE OF
  MARKET EDGE`. Hoje o híbrido ≈ mercado (α = 1,0), portanto residual ≈ 0 pp.
- **Model Health V2** (Dashboard): FOOTBALL MODEL `PREDICTIVE (vs naive)` ·
  MARKET MODEL `UNPROVEN` (holdout, `a5e8d42f97f764d7`) · SUPERBET EVIDENCE
  `COLLECTING` (239 eventos, 37.900 snapshots, closing 147, CLV n 314) ·
  SHADOW SETTLED N raw 244 / efetivo 10 `INSUFFICIENT` · MARKET EDGE
  `UNPROVEN`.
- Validação → Market-Aware (holdout/discovery, tabelas acima, α por janela,
  ablação, buckets, contrarian, segmentos com FDR); Validação → Superbet;
  Performance → Market Efficiency Lab e Superbet Lab.

## 5. O que não foi feito e por quê

- **Sem promoção** de nenhum challenger. Nenhum passou `brier_better`; a
  camada de value exigiria ainda CLV Superbet ≥ 0 com IC, inexistente.
- **Sem frame market-aware para seleções**: o replay INTL não tem odds
  históricas → `MODEL_ONLY`; o bloco MARKET vs EDGEFUT usa só a odd Superbet
  ao vivo, rotulado `NÃO VALIDADO`.
- **Mercados secundários** (BTTS, handicaps, escanteios, cartões): sem base
  com odds → `EXPERIMENTAL`; aparecem no Superbet Lab só como cobertura /
  overround / movimento.
- **Não** foi feita segunda rodada de discovery com outra configuração após ver
  o holdout (§16). Qualquer nova hipótese exige novo `config_hash`, nova
  descoberta e um holdout **em período futuro**.
- Closing do football-data (Pinnacle) e closing Superbet continuam apenas em
  CLV; nunca em features (teste automatizado).

## 6. Riscos e limitações declarados

1. O benchmark histórico compara com odds de abertura Bet365/média
   football-data, não com a Superbet e não com carimbo de hora. Um edge
   contra a Superbet real só pode ser medido pela shadow validation, que tem
   5 jogos liquidados.
2. 26 h de coleta Superbet com cadência irregular (VM suspensa). Todos os
   números Superbet são `EARLY`/`INSUFFICIENT` e assim rotulados.
3. O `HistoricalQualityGate` reconstrói apenas edge/EV/odd, amostra e
   divergência; frescor, provider, lineups e clusters são
   `UNAVAILABLE_IN_REPLAY`. O ROI simulado é, portanto, um limite otimista
   sobre o que o gate real permitiria — e já é negativo.
4. Challengers lineares com poucas features (§41–42 respeitados). Um modelo
   mais expressivo poderia extrair algo, mas a ablação mostra que **as
   features do EdgeFut não têm informação marginal** para o stack linear;
   não há sinal a amplificar.

## 7. O que muda para o usuário

- Nenhuma recomendação nova. O que já era `OBSERVATION` agora explica **por
  quanto** falha (required edge) e mostra se a divergência é validada (não é).
- A tela diz em cima: o EdgeFut prevê futebol melhor que o acaso, e **o
  mercado da Superbet prevê melhor que o EdgeFut**. O produto vale como
  agregador de informação, acompanhamento de linha, overround e visualização
  de probabilidade — não como gerador de value.

## 8. Próximos passos honestos (fora desta iteração)

- Deixar a shadow validation acumular ≥ 30 jogos liquidados com closing para
  que CLV, viés e Superbet-fair-vs-EdgeFut tenham IC.
- Se algum dia um challenger passar `brier_better` em nova descoberta, o
  holdout de confirmação tem de ser um período **posterior** à data de hoje.
- Fontes com odds históricas carimbadas (ou o próprio acervo Superbet do app,
  quando tiver meses) para transformar o benchmark em backtest negociável.
