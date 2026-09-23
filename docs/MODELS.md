# EdgeFut AI — Modelos

Todo modelo tem versão (`core/versions.py`). Snapshots de previsão apontam para
a versão utilizada; nunca são recalculados depois do resultado.

| Modelo | Versão | Arquivo |
|---|---|---|
| Team Strength | `strength-v1` | `features/strength.py` |
| ELO | `elo-v1` | `models/elo.py` |
| Poisson | `goals-poisson-v1` | `models/poisson.py` |
| Dixon-Coles | `goals-dixon-coles-v1` | `models/dixon_coles.py` |
| Bivariate Poisson | `goals-bivariate-poisson-v1` | `models/bivariate_poisson.py` |
| Ensemble / consenso | `ensemble-v1` | `models/ensemble.py` |
| Calibração | `calibration-isotonic-v1` | `models/calibration.py` |
| Monte Carlo | `mc-v1` | `simulation/monte_carlo.py` |
| Corners | `corners-v1` | `models/counts.py` |
| Cards | `cards-v1` | `models/counts.py` |
| Shots | `shots-v1` | `models/counts.py` |
| Confidence | `confidence-v2` | `recommendations/confidence.py` |
| Opportunity Score | `opportunity-v2` | `recommendations/opportunity.py` |
| Pipeline | `pipeline-v2` | `analysis/pipeline.py` |

A tupla de versões chaveia o cache de análise: mudar qualquer versão invalida
todas as análises em cache. `model_registry` (sincronizado no boot por
`models/registry.py`) guarda uma linha por (modelo, versão) com features,
parâmetros e janela de treino declarados; versões que saem de
`versions.ALL_MODELS` ficam `deprecated`, nunca são apagadas — um snapshot
antigo sempre aponta para uma linha existente.

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

Dois métodos de remoção de margem, **ambos calculados e armazenados** em cada
`odds_snapshot`; o que alimenta o Edge é escolhido em Configurações
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

## 12c. Rótulos e evidência

- **HIGH PROBABILITY** — probabilidade do modelo ≥ `high_probability_min`
  (padrão 65 %). Fala de probabilidade, não de valor ("seguro" ≠ "vale").
- **VALUE** — edge ≥ `min_edge_pp` e EV ≥ `min_ev_pct`. Fala de edge, não de
  probabilidade.
- **HIGH PROBABILITY + VALUE** — as duas condições. São rótulos independentes.
- **Evidência** (por análise, a partir da competição): `SETTLED` (≥ 30 apostas
  liquidadas na competição, com ROI/CLV medidos) → `BACKTEST_ODDS` (dataset com
  odds históricas reais; o backtest modelo × mercado é possível) →
  `MODEL_ONLY` (só há evidência probabilística; nunca comparado com odds
  reais). Seleções (international_results) são sempre `MODEL_ONLY`.

## 13. Opportunity Score V2 (`opportunity-v2`)

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

## 15. Múltiplas (`recommendations/multiples.py`)

Correlação detectada por regras sobre o mesmo evento (Over 1.5 + BTTS,
1X2 + DNB, etc.). Quando correlacionada, a probabilidade conjunta é obtida da
mesma simulação Monte Carlo (contando cenários onde todas as seleções vencem),
não do produto ingênuo. Entre eventos diferentes, assume-se independência.
