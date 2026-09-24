# EdgeFut AI — Modelos

Todo modelo tem versão (`core/versions.py`). Snapshots de previsão apontam para
a versão utilizada; nunca são recalculados depois do resultado.

| Modelo | Versão | Arquivo |
|---|---|---|
| Team Strength | `strength-v1` | `features/strength.py` |
| Team Strength opponent-adjusted (challenger) | `strength-v2` | `models/strength_v2.py` |
| Força de seleções (challenger) | `international-strength-v1` | `models/international_strength.py` |
| ELO | `elo-v1` (papel `baseline`) | `models/elo.py` |
| Poisson | `goals-poisson-v1` | `models/poisson.py` |
| Poisson sobre strength-v2 (challenger) | `goals-poisson-v2` | `models/strength_v2.py` |
| Dixon-Coles | `goals-dixon-coles-v1` | `models/dixon_coles.py` |
| Bivariate Poisson | `goals-bivariate-poisson-v1` | `models/bivariate_poisson.py` |
| Ensemble / consenso (**campeão**) | `ensemble-v1` | `models/ensemble.py` |
| Ensemble v2 = v1 + poisson_v2 (challenger) | `ensemble-v1` (chave `ensemble_v2`) | `models/ensemble.py` |
| Calibração | `calibration-isotonic-v1` | `models/calibration.py` |
| Monte Carlo | `mc-v1` | `simulation/monte_carlo.py` |
| Corners | `corners-v1` | `models/counts.py` |
| Cards | `cards-v1` | `models/counts.py` |
| Shots | `shots-v1` | `models/counts.py` |
| Confidence | `confidence-v2` | `recommendations/confidence.py` |
| Opportunity Score | `opportunity-v3` | `recommendations/opportunity.py` |
| Pipeline | `pipeline-v3` | `analysis/pipeline.py` |

A tupla de versões chaveia o cache de análise: mudar qualquer versão invalida
todas as análises em cache. `model_registry` (sincronizado no boot por
`models/registry.py`) guarda uma linha por (modelo, versão) com features,
parâmetros, janela de treino e **papel** (`champion` / `challenger` /
`baseline` / `none`); versões que saem de `versions.ALL_MODELS` ficam
`deprecated`, nunca são apagadas — um snapshot antigo sempre aponta para uma
linha existente. Quem decide em produção, e como um challenger vira campeão,
está em [`MODEL_GOVERNANCE.md`](MODEL_GOVERNANCE.md).

**Resultado da validação (iteração 3, resumo):** no replay walk-forward de 11
ligas com odds (N 14.393, 2022–2026) **o mercado é melhor que todos os
modelos** em 1X2 (Brier 0,5718 vs 0,5857 do campeão, NO CLEAR ADVANTAGE) e em
OU 2,5; todos os modelos superam os baselines ingênuos de forma CONSISTENT. Em
seleções (N 15.956, sem odds) o campeão bate o ingênuo em +18,3 % e o ELO em
+3,3 %. Detalhes e IC em [`ITERATION_3_REPORT.md`](ITERATION_3_REPORT.md).

**Resultado market-aware (iteração 4, resumo):** com o mercado como prior,
três challengers (`market-model-blend-v1`, `market-logistic-stack-v1`,
`market-residual-v1`) foram avaliados com CV temporal aninhada e holdout
congelado (≥ 2026-01-23, `model_hash a5e8d42f97f764d7`). **α = 1,0 (mercado
puro) venceu**; nenhum challenger passou `brier_better` (1X2 Δ −0,0001
[−0,0020; +0,0017]; OU 2,5 −0,0002 [−0,0013; +0,0009]); quando o EdgeFut
discorda ≥ 5 pp do mercado, o mercado está certo. Veredicto **NO EVIDENCE OF
MARKET EDGE**; nenhum challenger alimenta recomendações. O único efeito no
pipeline é o **required edge** (seção 16) e o bloco informativo MARKET vs
EDGEFUT. Detalhes em [`MARKET_AWARE_MODELS.md`](MARKET_AWARE_MODELS.md) e
[`ITERATION_4_REPORT.md`](ITERATION_4_REPORT.md).

## 1. Team Strength Engine (`strength-v1`)

Janelas: últimos 5, 10 e 20 jogos, com peso de recência exponencial
`w_i = exp(-λ·i)` (λ = 0.08, i = posição a partir do jogo mais recente).
Separação: geral / mandante / visitante.

Métricas por janela: gols pró, gols contra, chutes, chutes no alvo, conversão
(gols / chutes no alvo), escanteios pró/contra, cartões, clean sheets, BTTS %,
Over 1.5 / 2.5 / 3.5 %.

Força ofensiva/defensiva relativa à média da competição:

```
attack  = média ponderada de gols pró  / média de gols da liga (mesma condição)
defense = média ponderada de gols contra / média de gols da liga (mesma condição)
```

Score de força exibido (0–100) = combinação do ELO normalizado (60%) e do
diferencial ofensivo/defensivo (40%).

## 1b. Team Strength opponent-adjusted (`strength-v2`) e `goals-poisson-v2`

O strength-v1 usa médias simples de gols pró/contra — um time que marcou 3 em
um adversário fraco vale o mesmo que 3 em um forte. O strength-v2 resolve
ataque e defesa **em função do adversário**, por ponto fixo iterativo
(40 iterações, tolerância 1e-6), com shrinkage bayesiano:

```
att_i = (Σ w·gf_i + k) / (Σ w·μ·def_opp·ha + k)      k = PRIOR_K = 6 pseudo-gols → 1,0
def_i = (Σ w·ga_i + k) / (Σ w·μ·att_opp·ha + k)
w     = 0,5^(idade_dias / half_life)                  half_life = settings.strength_half_life_days
```

- ratings por condição (casa / fora) encolhidos para o rating pooled com
  `COND_K = 10` pseudo-gols;
- vantagem de mando `ha` estimada por grupo (temporada / tipo de torneio) com
  ≥ 100 jogos, senão do conjunto, senão 1,15;
- campo neutro ⇒ `ha = 1`; `UNCONFIRMED` ⇒ raiz quadrada;
- `MIN_MATCHES = 60`: abaixo disso o modelo não se pronuncia.

`goals-poisson-v2` = Poisson independente sobre `λ_home = μ·att_h·def_a·ha`,
`λ_away = μ·att_a·def_h`. É **challenger**: entra na comparação de modelos e no
`ensemble_v2`, não no consenso campeão.

**Half-life escolhido por walk-forward**, não à mão (`validation/decay.py`,
Validação → Model Comparison): 30 / 60 / 90 / 180 / 365 / 730 / sem decaimento
sobre 14.152 partidas e 463 janelas → 365 d (0,58737) ≈ 180 d (0,58801,
diferença inconclusiva) > 730 > none > 90 > 60 > 30. Entre equivalentes, o
mais simples: **365 dias**.

Resultado no replay: `poisson_v2` melhora o Poisson v1 de forma conclusiva
(ΔBrier −0,0070 [−0,0092; −0,0048]) mas não bate o campeão (+0,0016 [+0,0005;
+0,0028]). Testes: `test_strength_v2_recovers_true_ratings_and_home_advantage`,
`test_strength_v2_output_respects_neutral_venue_and_bounds`,
`test_strength_v2_time_decay_never_sees_future_and_weights_recent`.

## 1c. Força de seleções (`international-strength-v1`)

O mesmo algoritmo do strength-v2 sobre `international_results`, com:

- `tournament_type(nome)` ∈ FRIENDLY / QUALIFIER / TOURNAMENT / NATIONS_LEAGUE /
  CONTINENTAL / OTHER, derivado do campo `tournament` do dataset;
- peso **0,6** para amistosos (`FRIENDLY_WEIGHT`) — contam, mas menos;
- campo neutro vindo da flag `neutral` do dataset (não inferido);
- `MIN_TEAM_MATCHES = 8`: abaixo disso a previsão sai com nota de histórico
  curto.

Alimenta análises de seleções no pipeline; no replay de seleções (N 15.956,
2010 →, janelas de 90 d) o consenso campeão tem Brier 0,5175 vs 0,6336 do
ingênuo (+18,3 %, 66/66 janelas) e 0,5351 do ELO (+3,3 %); por tipo de
torneio, o lift vai de +12,2 % (CONTINENTAL) a +24,9 % (QUALIFIER). Como não
há odds históricas de seleções, **toda** seleção de seleções fica `MODEL_ONLY`.

## 2. ELO (`elo-v1`)

```
E_home = 1 / (1 + 10^((R_away − R_home − H) / 400))
R' = R + K · G · (S − E)
```

- `H` = vantagem de mandante em pontos ELO (100 para clubes; 0 se
  `neutralVenue`; 50 se venue `UNCONFIRMED`).
- `K` = 20 (ligas), 30 (competições internacionais de seleções oficiais),
  15 (amistosos).
- `G` = multiplicador de margem: 1 (≤1 gol), 1.5 (2 gols), (11+d)/8 (≥3).
- Rating inicial 1500; atualização cronológica sobre todo o dataset.
- Probabilidades 1X2 via ELO: `p_home_win_or_draw` da fórmula acima e taxa de
  empate da competição para separar empate (calibrada empiricamente:
  `p_draw = d_base · exp(−|ΔELO|/600)`).

## 3. Poisson (`goals-poisson-v1`)

```
λ_home = μ_home_liga · attack_home · defense_away · HA
λ_away = μ_away_liga · attack_away · defense_home
```

`HA` = 1 se campo neutro; vantagem plena da liga se confirmado; média
geométrica (raiz quadrada) se `UNCONFIRMED`.

Gera P(0..7+) para cada equipe e matriz de placares (independência).

## 4. Dixon-Coles (`goals-dixon-coles-v1`)

Ajuste por máxima verossimilhança com decaimento temporal
(`ξ = 0.0018 / dia`, Dixon-Coles 1997 usam ~0.0065 por semana) sobre os últimos
~3 anos da competição:

```
P(x, y) = τ(x, y) · Pois(x; λ) · Pois(y; μ)
τ corrige 0-0, 1-0, 0-1, 1-1 com parâmetro ρ
```

Parâmetros: ataque/defesa por time (com restrição Σ ataque = n), vantagem de
mandante γ, ρ. Otimização `scipy.optimize.minimize` (L-BFGS-B).

Se a competição não tiver pelo menos 150 jogos ajustáveis, o DC não roda e
apenas Poisson é usado (a divergência entre modelos vira `MODEL_DISAGREEMENT`
apenas quando há ao menos dois modelos relevantes).

## 4b. Bivariate Poisson (`goals-bivariate-poisson-v1`)

Karlis & Ntzoufras (2003): `X = X1 + X3` (gols do mandante), `Y = X2 + X3`
(gols do visitante), com `X1, X2, X3 ~ Poisson(λ1, λ2, λ3)` independentes. `λ3`
é a covariância entre os gols das duas equipes (jogos "abertos"/"fechados" em
conjunto) e substitui a correção `τ` do Dixon-Coles.

Ajuste em dois passos, reprodutível:

1. ataque/defesa/mando por MLE Poisson ponderado no tempo (mesmo `ξ` e mesmo
   otimizador do DC, com `ρ` fixado em 0);
2. `λ3` por MLE unidimensional (bounded) na verossimilhança bivariada, com as
   marginais fixas: `λ1 = max(ε, λ_home − λ3)`, `λ2 = max(ε, λ_away − λ3)`.

Mesmo requisito de 150 jogos. Não substitui Poisson/Dixon-Coles: entra na
**comparação de modelos** e no **consenso**. Invariante testado: a matriz
bivariada soma 1 e preserva as marginais `λ_home`, `λ_away`.

## 4c. Ensemble / consenso (`ensemble-v1`)

`models/ensemble.py` mostra Poisson, Dixon-Coles e Bivariate Poisson lado a
lado (1X2, Over 2.5, BTTS) e produz o **Consenso** como média ponderada das
matrizes de placar.

Os pesos **nunca são fixados à mão**. O job `ensemble_weights` roda um
walk-forward (12 meses, janelas de 60 dias) por competição e calcula o log loss
1X2 de cada modelo; o peso é

```
w_k ∝ exp(−κ · (logloss_k − min_j logloss_j)),   κ = 25
```

normalizado para somar 1 — modelos piores recebem menos, nunca zero. Pesos por
competição só valem com N ≥ 200 partidas avaliadas; caso contrário usa-se o
grupo `GLOBAL`; sem nenhum peso calculado, pesos iguais. A origem
(`competição` ou `GLOBAL`, com N) é exibida na tabela de comparação.

Um modelo com peso < 10 % (`LOW_WEIGHT_THRESHOLD`) continua visível, mas **não
participa do veto** `MODEL_DISAGREEMENT`: a divergência máxima é medida só
entre modelos relevantes. Exemplo real (INTL, seleções): Poisson 3,6 %,
Dixon-Coles 56,3 %, Bivariate 40,1 % — o Poisson de força (média simples de
ataque/defesa, sem ajuste por adversário) é claramente pior nesse dataset.

### Campeão e challenger

Há dois consensos: `ensemble` (v1: Poisson + Dixon-Coles + Bivariate) e
`ensemble_v2` (v1 + `poisson_v2`). O que decide em produção é o **campeão**
(`model_registry.parameters.champion_consensus`, hoje `ensemble`); o outro roda
em paralelo em toda análise (Model Comparison) e em todo replay. A troca segue
a regra de promoção de [`MODEL_GOVERNANCE.md`](MODEL_GOVERNANCE.md) — Brier OOS
melhor com IC pareado < 0, LogLoss não pior, ECE ±0,005, N ≥ 300, melhor em
≥ 60 % das janelas; ROI **não** é critério — e é uma ação explícita do
operador. Estado atual: `ensemble_v2` é marginalmente melhor (Δ −0,0004
[−0,0007; −0,0001]) mas só em 53 % das janelas → **PROMISING · UNSTABLE**, não
promovido.

## 4d. Calibração (`calibration-isotonic-v1`)

Regressão isotônica (PAV) por `(mercado, competição)` sobre pares
(probabilidade crua, resultado) de `prediction_snapshot` liquidados. Um
calibrador só é `reliable` com **N ≥ 300** amostras; abaixo disso ele é gravado
com `reliable=False` e a recomendação usa a probabilidade **RAW**. Em caso de
ausência por competição, tenta-se o grupo `GLOBAL` do mercado.

A análise sempre expõe as duas probabilidades quando existem
(`model_prob_raw`, `model_prob_calibrated`) e a UI diz explicitamente qual
está em uso. O diagrama de confiabilidade (Performance → Calibração) traz, por
bucket, a probabilidade prevista média, a frequência observada e N, mais o
Brier antes/depois. Hoje (3 previsões liquidadas) nenhum calibrador é confiável
— a tela diz "RAW" e nada é maquiado.

## 5. Monte Carlo (`mc-v1`)

Padrão 50.000 simulações (10k / 25k / 50k / 100k). Semente fixa por evento
(`seed = eventId`) para reprodutibilidade dentro da mesma versão.

Amostragem do placar a partir da matriz Dixon-Coles (ou Poisson se DC ausente)
via `numpy.random.Generator.choice` sobre a grade 0–9 × 0–9. Derivados:
1X2, Dupla Chance, DNB, Over/Under 0.5–4.5, BTTS, total de gols, top placares.

Escanteios e cartões: Binomial Negativa com média dos engines respectivos e
dispersão estimada da competição (`k` via método dos momentos).

## 6. Corners (`corners-v1`)

```
E[corners_home] = 0.5·(corners_for_home_weighted + corners_against_away_weighted) · ajuste_força
E[corners_away] = análogo
```

Ajuste de força: `(1 + 0.15·tanh(ΔELO/200))` para o time mais forte, inverso
para o mais fraco (times dominantes cobram mais escanteios). Distribuição NB;
probabilidades Over/Under 6.5–10.5 e intervalo provável (percentis 20–80).

Requer `HC/AC` no histórico — indisponível para seleções (`LOW_DATA`).

## 7. Cards (`cards-v1`)

Cartões = amarelos + vermelhos (1 ponto cada, como o mercado da Superbet
"Total de Cartões" conta). Média ponderada do time e do adversário, ajustada
pela média da competição. Árbitro só entra se estiver no dataset para partidas
passadas (nunca para jogos futuros). NB com `k` da competição.

## 8. Shots (`shots-v1`)

```
E[shots_home] = 0.5·(shots_for_home + shots_conceded_away) · (1 + 0.20·tanh(ΔELO/250))
E[sot_home]   = E[shots_home] · taxa_no_alvo_home_weighted
```

Produz finalizações e finalizações no alvo esperadas por time.
Probabilidade de "A finaliza mais que B" via Poisson bivariado independente.

## 9. Odds → probabilidade (`odds/implied.py`)

```
impliedProbability = 1 / odd
overround = Σ implied_j − 1
```

Dois métodos de remoção de margem, **ambos calculados e gravados** em cada
seleção da análise (`fair_multiplicative`, `fair_shin`, `shin_z`) e, por
consequência, no `prediction_snapshot`; o que alimenta o Edge é escolhido em
Configurações
(`margin_method`, padrão `MULTIPLICATIVE`):

- **Multiplicativa**: `fair_i = implied_i / Σ implied_j`.
- **Shin (1993)**: modela a margem como a fração `z` de apostadores informados;
  resolve `z` por bissecção tal que `Σ p_i = 1`, com

```
p_i = ( sqrt(z² + 4(1−z)·π_i²/β) − z ) / (2(1−z)),   π_i = 1/odd_i,   β = Σ π_i
```

  Corrige o viés favorite-longshot: tira mais margem dos azarões e menos dos
  favoritos do que a normalização proporcional. Em mercados que não formam
  partição (ex.: Dupla Chance) devolve a multiplicativa com `z = 0`.

Remoção de margem só quando o mercado tem todas as seleções complementares
(1X2 completo, Over+Under da mesma linha, BTTS Sim+Não). Caso contrário,
`marketFairProbability = impliedProbability` e o Edge é marcado como
"margem não removida". Invariante testado: as probabilidades justas somam 1
nos dois métodos.

### 9b. Movimento de linha e closing line

`LineMovement` deriva, por seleção, `abertura → atual`, variação %, e a
probabilidade implícita antes e depois — ex.: `1,72 → 1,54 (−10,5 %)`, implícita
`58,1 % → 64,9 % (+6,8 pp)`. Movimento acima de 15 % vira
`EXTREME_ODDS_MOVEMENT`. A **closing line** (última odd antes do kickoff) é
capturada à parte e usada **exclusivamente** para CLV:

```
CLV % = (odd_na_aposta / odd_de_fechamento − 1) · 100
```

CLV positivo significa que a odd tomada era melhor que a de fechamento. Nunca
entra na decisão (teste `test_closing_odds_never_drive_the_decision`).

## 10. Edge & EV (`recommendations/edge.py`)

```
EDGE = modelProbability − marketFairProbability      (em pontos percentuais)
EV   = modelProbability · odd − 1
```

Limiares padrão (configuráveis): `min_edge = 3 pp`, `min_ev = 3 %`,
`odd ∈ [1.20, 6.00]`.

## 11. EDGEFUT CONFIDENCE (`confidence-v2`)

Score 0–100 = média ponderada de componentes, cada um em um **grupo** que a UI
mostra no breakdown do indicador central:

| Grupo | Componente | Peso | Como |
|---|---|---|---|
| `DATA_QUALITY` | Qualidade dos dados | 25 | Data Quality do evento / 100 |
| `HISTORICAL_SAMPLE` | Quantidade de jogos | 15 | min(n_home, n_away) / 20 |
| `HISTORICAL_SAMPLE` | Recência | 10 | 1 se idade média ≤ 60 dias; decai até 0 em 360 dias |
| `HISTORICAL_SAMPLE` | Consistência | 10 | 1 − |saldo 5 jogos − saldo 20 jogos| / 2 (0,3 se janelas insuficientes) |
| `CALIBRATION` | Calibração histórica | 10 | acerto do mercado nos snapshots settled; neutro 0,5 se < 30 amostras |
| `MODEL_AGREEMENT` | Divergência entre modelos | 15 | 1 − divergência_pp / 10 entre modelos relevantes; neutro 0,5 com um só modelo |
| `FRESHNESS` | Frescor dos dados | 10 | 0,6 · penalidade(odds) + 0,4 · pior penalidade(forma/histórico); EXPIRED zera |
| `CONTEXT` | Campo / mandante | 3 | 1 confirmado · 0,5 unconfirmed |
| `CONTEXT` | Disponibilidade de jogadores | 2 | 0 (sem fonte pública de escalações) |

O score por grupo (0–100) é a média ponderada dos seus componentes; o breakdown
lista componente, peso, valor e nota (ex.: "17 jogos na menor amostra").

Grades derivadas: A ≥ 80 · B ≥ 65 · C ≥ 50 · D < 50. Radar mostra apenas A e
B. C aparece em observação. D → NÃO RECOMENDAR. A confiança é sobre **a
análise**, não sobre a aposta: pode ser alta com edge zero.

## 12. NO BET (`recommendations/engine.py`)

Bloqueio no nível do evento (ordem de verificação):

`STALE_DATA` (odds ou histórico EXPIRED) → `UNSUPPORTED_COMPETITION` →
`LOW_DATA` → `UNRELIABLE_SOURCE` → `SMALL_SAMPLE` → `MODEL_DISAGREEMENT`
(> `gate_max_disagreement_pp`, padrão 10 pp, só entre modelos com peso ≥ 10 %)
→ `LOW_CONFIDENCE` (grade D).

No nível da seleção: `LINEUP_UNCERTAINTY` (mercados de jogador),
`EXTREME_ODDS_MOVEMENT` (> 15 % desde a abertura), `NO_EDGE`, e `QUALITY_GATE`
(passou nos limiares de edge/EV mas falhou em algum check do gate → fica em
**WATCH**, nunca em TOP OPORTUNIDADES).

Se nenhuma seleção passar, o evento exibe **NENHUMA ENTRADA RECOMENDADA** com a
razão dominante e o texto **WHY NOT** gerado dos números.

## 12b. Quality Gate (`recommendations/gate.py`)

Checklist explícito para uma recomendação entrar em TOP OPORTUNIDADES. Cada
item aparece na UI com ✓/✗ e o valor medido:

| Check | Regra (padrão) | Piso / teto rígido |
|---|---|---|
| `data_quality` | qualidade ≥ 60 % | piso 40 % |
| `confidence` | EDGEFUT CONFIDENCE ≥ 65 (grau B) | piso 50 |
| `sample` | menor amostra ≥ 15 jogos | piso 10 |
| `disagreement` | divergência entre modelos relevantes ≤ 10 pp | teto 15 pp |
| `odds_fresh` | odds FRESH ou AGING | — |
| `provider` | provider de odds saudável | — |
| `edge` | edge ≥ `min_edge_pp` | piso 1 pp |
| `ev` | EV ≥ `min_ev_pct` | piso 1 % |
| `odd_range` | odd dentro de [`min_odd`, `max_odd`] | — |
| `edge_plausible` | edge ≤ 15 pp quando não há calibrador confiável (acima disso é mais provável erro do modelo do que do mercado) | teto 25 pp |

Os limiares são editáveis em Configurações, mas `HARD_FLOORS` / `HARD_CEILINGS`
(`core/config.py`) impedem "caçar entradas": o `PUT /settings` recusa valores
além dos pisos/tetos (teste `test_settings_floors_block_threshold_hunting`) e o
sistema **nunca** relaxa um limiar sozinho, mesmo com o Radar vazio.

## 12c. Rótulos, estados e evidência

- **MODEL FAVORITE** (chave `HIGH_PROBABILITY`) — probabilidade do modelo ≥
  `high_probability_min` (padrão 65 %). Fala de probabilidade, não de valor
  ("seguro" ≠ "vale").
- **VALUE** — passou edge/EV/gate **e** o mercado tem prova out-of-sample
  (§12e). **VALUE CANDIDATE** — passou edge/EV/gate, falta a prova.
- **MODEL ONLY** — probabilidade sem preço válido para determinar valor.
- **WATCH** — em observação (gate falhou, preço curto, OOS negativo…).
- **Evidência** (por análise, a partir da competição): `SETTLED` (≥ 30 apostas
  liquidadas na competição, com ROI/CLV medidos) → `BACKTEST_ODDS` (dataset com
  odds históricas reais; o backtest modelo × mercado é possível) →
  `MODEL_ONLY` (só há evidência probabilística; nunca comparado com odds
  reais). Seleções (international_results) e ligas sem odds históricas (MLS)
  são sempre `MODEL_ONLY`.

### 12d. Estados por seleção (`recommendations/engine.py`)

| Estado | Quando | Texto na UI |
|---|---|---|
| `MODEL_ONLY` | competição com evidência `MODEL_ONLY` (mesmo havendo odd na Superbet) **ou** seleção sem preço | "Probabilidade calculada, mas sem preço de mercado válido para determinar valor." |
| `MARKET_OBSERVED` | há preço, sem edge (`NO_EDGE`) | — |
| `VALUE_CANDIDATE` | `RECOMMENDED` (edge, EV, gate) mas OOS do mercado `INSUFFICIENT` | "Passou no quality gate; falta prova out-of-sample suficiente neste mercado." |
| `VALUE` | `RECOMMENDED` e OOS do mercado `PASS` | "Passou no quality gate e o mercado tem histórico out-of-sample suficiente." |
| `OBSERVATION` | `WATCH` — gate falhou, `OOS_NEGATIVE`, ou `WATCHING_PRICE` | inclui "Probabilidade interessante, mas preço atual não oferece margem suficiente." quando é preço |
| `NO_BET` | bloqueio de evento ou de seleção | WHY NOT |

Regra §29: em competição `MODEL_ONLY` **todas** as seleções são `MODEL_ONLY`,
nunca VALUE, VALUE_CANDIDATE ou WATCHING_PRICE — edge sobre odd nunca
validada é hipótese, não valor. Invariante verificado em
`test_recommendations.py`.

### 12e. OOS check (`oos_check`)

`VALUE` exige, para o **mercado** da seleção, `n ≥ value_min_oos_bets` (100)
apostas out-of-sample — apostas simuladas do campeão no último replay e/ou
apostas reais liquidadas — com veredito **não** `NEGATIVE`. `INSUFFICIENT` →
`VALUE_CANDIDATE`; `NEGATIVE` → `OBSERVATION · OOS_NEGATIVE`. Hoje 1X2 e OU 2,5
têm OOS **NEGATIVE** (ROI −12,6 % e −7,6 %, conclusivos) e os demais mercados
`INSUFFICIENT`, portanto nenhuma seleção pode ser `VALUE`.

### 12f. Price target (`recommendations/pricing.py`)

```
break_even_odd     = 1 / p                                  (EV = 0)
odd_ev             = (1 + min_ev_pct/100) / p               (EV mínimo)
odd_edge           = 1 / ((p − min_edge_pp/100) · S)        (edge mínimo; S = soma das implícitas do mercado, i.e. a margem que a odd carrega)
min_acceptable_odd = max(odd_ev, odd_edge)                  (`min_odd_reason` diz qual venceu)
price_gap_pct      = odd_atual / min_acceptable_odd − 1     (negativo = preço curto)
edge_sensitivity   = edge e EV com p ± 3 pp;  edge_survives_minus = ambos ainda ≥ limiares com p − 3 pp
```

`WATCHING_PRICE` (watchlist) só quando a odd-alvo está dentro de
`[min_odd, max_odd]` e o gap ≤ 12 %. Quando a odd atual atinge
`min_acceptable_odd` de uma seleção que estava em watchlist, o job de alertas
emite `OPPORTUNITY_APPEARED` com `trigger = price_target`.

### 12g. Clusters e exposição (`recommendations/correlation.py`)

Cada seleção recebe `cluster_id` (17 clusters — `HOME_TEAM_POSITIVE`,
`AWAY_TEAM_POSITIVE`, `DRAW`, `NO_DRAW`, `GOALS_HIGH/LOW`,
`HOME/AWAY_GOALS_HIGH/LOW`, `CORNERS_*`, `CARDS_*`, `SHOTS_*`, `MISC`) e
`thesis_group` (RESULT / GOALS / CORNERS / CARDS / SHOTS). Dentro de um
cluster **uma** seleção é `PRIMARY` — melhor estado, depois status, depois
mercado mais "puro" (`MARKET_PRIORITY`), depois maior Opportunity — e as
demais são `ALTERNATIVA` (`primary_of`). Pares entre clusters correlacionados
(`CROSS_CORRELATED`: mandante bem ⇄ mandante marca, poucos gols ⇄ empate…)
definem a **Exposição** do evento: `LOW` (≤ 1 tese acionável), `MEDIUM`,
`HIGH` (teses acionáveis correlacionadas). Performance separa primárias de
alternativas; o Radar conta "teses acionáveis", não seleções.

### 12h. Extreme probability guard

Probabilidade > 90 % exige menor amostra de time ≥ 30 jogos **e** ≥ 300
previsões liquidadas no mercado. Falhando um critério, a confiança é
multiplicada por 0,85; falhando os dois, por 0,70, e `EXTREME_PROBABILITY`
entra nos motivos. A probabilidade **nunca é truncada**
(`test_extreme_probability_penalizes_confidence_but_never_truncates`).

## 13. Opportunity Score V3 (`opportunity-v3`)

V3 = breakdown do V2 (abaixo) **mais ajustes explícitos**
(`opportunity_adjustments`, listados no tooltip do score), sempre negativos:

| Ajuste | Quando | Efeito |
|---|---|---|
| `model_only` | estado `MODEL_ONLY` | score → **0** (sem preço validado não há "oportunidade" a pontuar) |
| `uncertainty_penalty` | `VALUE_CANDIDATE` (OOS insuficiente) | −5 pontos |
| `correlation_penalty` | seleção `ALTERNATIVA` de um cluster | × 0,85 (mesma tese não é oportunidade extra) |

```
score = 100 · Σ_k w_k · value_k        (Σ w_k = 1; pesos normalizados de Configurações)
```

| Componente | Peso padrão | `value` |
|---|---|---|
| `model_confidence` | 20 | confiança / 100 |
| `data_quality` | 15 | qualidade / 100 |
| `calibration_quality` | 10 | q = qualidade histórica do mercado (derivada do Brier settled); usa q se há calibrador confiável, 0,5·q + 0,25 com amostra parcial (RAW), 0,5 neutro sem amostra |
| `edge` | 15 | clamp(edge_pp / 10) |
| `ev` | 10 | clamp(ev_% / 15) |
| `odds_freshness` | 10 | FRESH 1 · AGING 0,7 · STALE 0,3 · EXPIRED 0 |
| `model_agreement` | 10 | 1 − divergência_pp / 10 (0,5 com um só modelo) |
| `historical_performance` | 5 | 0,5 + ROI_% / 20 se ≥ 30 apostas settled no mercado; senão 0,5 (`INSUFFICIENT SAMPLE`) |
| `sample_size` | 5 | clamp(menor amostra / 30) |

Cada componente devolve peso, valor e nota legível — é o que a UI mostra em
"Opportunity Score breakdown". **Nunca ordena por odd.** Pesos negativos ou
desconhecidos são ignorados; soma zero cai em pesos iguais.

## 14. Backtest anti-leakage (`backtesting/lab.py`)

Métricas por mercado e por competição: Hit Rate, Brier Score, Log Loss, ROI,
Yield, Average Edge, Maximum Drawdown, CLV (quando houver odd de fechamento —
football-data traz `PSCH/PSCD/PSCA` / `B365C*`). Grupos com N < 30 exibem
`INSUFFICIENT SAMPLE` em vez de números.

Regras do Lab:

- cada partida tem `as_of` = data do jogo; o modelo usado para ela só viu jogos
  com `date < as_of` — a guarda `assert_no_leakage` levanta `LeakageError` se
  qualquer linha de treino violar isso;
- janelas **walk-forward** (expanding ou rolling), nunca split aleatório; os
  resultados por janela são devolvidos em `windows`;
- a decisão de apostar usa **sempre** a odd pré-fechamento; a odd de fechamento
  só entra como referência de CLV depois da decisão. Nunca há "recomendação
  retroativa";
- nenhuma aposta sem odd pré-jogo disponível.

Tudo isso é coberto por `tests/test_backtest_leakage.py`. Resultado real E0
2025/26, 1X2, Dixon-Coles: 420 jogos, ROI −4,4 %, Brier 0,213, CLV −1,49 % —
mostrado sem maquiagem.

Desde a iteração 3 o Lab é complementado pelo **Historical Replay Engine**
(`validation/replay.py`), que roda todos os modelos e cinco baselines em
janelas walk-forward sobre 11 ligas, com bootstrap pareado e IC 95 %, e pela
`TemporalFeatureStore` (`features/temporal.py`), que é o único caminho de
leitura de histórico tanto em produção quanto no replay. Metodologia em
[`VALIDATION.md`](VALIDATION.md); resultados em
[`ITERATION_3_REPORT.md`](ITERATION_3_REPORT.md) — em resumo, o mercado bate
todos os modelos em 1X2 e OU 2,5, e a simulação de apostas dá ROI −8,5 % a
−10,6 % (conclusivo) para todos.

## 15. Múltiplas (`recommendations/multiples.py`)

Correlação detectada por regras sobre o mesmo evento (Over 1.5 + BTTS,
1X2 + DNB, etc.). Quando correlacionada, a probabilidade conjunta é obtida da
mesma simulação Monte Carlo (contando cenários onde todas as seleções vencem),
não do produto ingênuo. Entre eventos diferentes, assume-se independência.

## 16. Required edge e intervalo de incerteza (`recommendations/required_edge.py`, iteração 4)

Aplicado a toda seleção 1X2 / OU 2,5 com odd Superbet, **antes** de qualquer
estado `VALUE`; só adiciona uma barreira, nunca relaxa o Quality Gate.

- **Intervalo de incerteza**: meia-largura = √(spread² + ECE² + amostra²),
  com spread = dispersão entre os modelos do consenso, ECE = calibração do
  campeão no replay (0,02 se desconhecida), amostra = 0,5·√(p(1−p)/n_min).
- **Edge bruto** = p_modelo − p_justa (Superbet, margem removida).
  **Edge ajustado** = bruto − meia-largura.
- **Required edge** = margem da casa (overround do mercado, teto 15 %) +
  meia-largura + 0,5 pp se sem calibrador confiável + 0,5 pp se o mercado não
  tem prova OOS suficiente + 1,0 pp se o veredicto market-aware do mercado é
  `NO EVIDENCE OF MARKET EDGE`; nunca abaixo de `min_edge_pp`.
- `robust = bruto ≥ required`; caso contrário razão `EDGE_NOT_ROBUST` e estado
  no máximo `OBSERVATION`. Exemplo do spec: bruto +5,2 pp, margem 5,1 %,
  incerteza 2,4 pp → required 7,8 pp → OBSERVATION (teste).
- **MODEL DISAGREEMENT** (EdgeFut − Superbet) é sempre mostrado; **RESIDUAL
  EDGE** (híbrido congelado − Superbet) só é chamado assim se o mercado tiver
  veredicto positivo no holdout — hoje nenhum tem, então aparece `NÃO
  VALIDADO`.

Versões dos challengers: `market-model-blend-v1`, `market-logistic-stack-v1`,
`market-residual-v1` (`validation/market_aware.py`), registradas no artefato
congelado (`model_hash`), não no `model_registry` de produção — nenhum deles
está em produção.
