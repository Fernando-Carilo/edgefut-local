# EdgeFut AI — Modelos

Todo modelo tem versão (`core/versions.py`). Snapshots de previsão apontam para
a versão utilizada; nunca são recalculados depois do resultado.

| Modelo | Versão | Arquivo |
|---|---|---|
| Team Strength | `strength-v1` | `features/strength.py` |
| ELO | `elo-v1` | `models/elo.py` |
| Poisson | `goals-poisson-v1` | `models/poisson.py` |
| Dixon-Coles | `goals-dixon-coles-v1` | `models/dixon_coles.py` |
| Monte Carlo | `mc-v1` | `simulation/monte_carlo.py` |
| Corners | `corners-v1` | `models/corners.py` |
| Cards | `cards-v1` | `models/cards.py` |
| Shots | `shots-v1` | `models/shots.py` |
| Confidence | `confidence-v1` | `recommendations/confidence.py` |
| Opportunity Score | `opportunity-v1` | `recommendations/opportunity.py` |

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
apenas quando ambos existem).

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
normalizedMarketProbability = implied_i / Σ implied_j   (remoção multiplicativa da margem)
overround = Σ implied_j − 1
```

Remoção de margem só quando o mercado tem todas as seleções complementares
(1X2 completo, Over+Under da mesma linha, BTTS Sim+Não). Caso contrário,
`marketFairProbability = impliedProbability` e o Edge é marcado como
"margem não removida".

## 10. Edge & EV (`recommendations/edge.py`)

```
EDGE = modelProbability − marketFairProbability      (em pontos percentuais)
EV   = modelProbability · odd − 1
```

Limiares padrão (configuráveis): `min_edge = 3 pp`, `min_ev = 3 %`,
`odd ∈ [1.20, 6.00]`.

## 11. Confidence Engine (`confidence-v1`)

Score 0–100 a partir de componentes com pesos:

| Componente | Peso | Como |
|---|---|---|
| Qualidade dos dados | 25 | Data Quality do evento |
| Quantidade de jogos | 15 | min(n_home, n_away) / 20 |
| Recência | 10 | idade média da amostra (dias) |
| Consistência (forma) | 10 | 1 − desvio-padrão dos gols normalizado |
| Calibração histórica do mercado | 15 | hit/Brier do mercado nos snapshots settled (ou neutro 0.5 se < 30 amostras) |
| Divergência entre modelos | 15 | 1 − |P_poisson − P_dc| / 0.10 |
| Incerteza de venue | 5 | 1 confirmado · 0.5 unconfirmed |
| Disponibilidade de jogadores | 5 | 0 (sem fonte) — sempre penaliza mercados de jogador |

Grades: A ≥ 80 · B ≥ 65 · C ≥ 50 · D < 50.

Radar mostra apenas A e B. C aparece em "Análises". D → NÃO RECOMENDAR.

## 12. NO BET (`recommendations/no_bet.py`)

Razões (ordem de verificação):

`UNSUPPORTED_COMPETITION` → `LOW_DATA` → `SMALL_SAMPLE` → `UNRELIABLE_SOURCE`
→ `LINEUP_UNCERTAINTY` (mercados de jogador) → `EXTREME_ODDS_MOVEMENT`
(> 15 % desde a abertura) → `MODEL_DISAGREEMENT` (> 10 pp) →
`LOW_CONFIDENCE` (grade D) → `NO_EDGE`.

Se nenhuma seleção passar, o evento exibe **NENHUMA ENTRADA RECOMENDADA** com a
razão dominante.

## 13. Opportunity Score (`opportunity-v1`)

```
score = 100 · (0.30·dataQuality + 0.30·confidence + 0.25·edgeNorm + 0.10·stability + 0.05·calibration)
edgeNorm = clamp(edge_pp / 10, 0, 1)
stability = 1 − clamp(|movimento_odd| / 0.15, 0, 1)
```

Nunca ordena por odd.

## 14. Calibração e Backtest (`backtesting/`)

Métricas por mercado: Hit Rate, Brier Score, Log Loss, ROI, Yield, Average
Edge, Maximum Drawdown, CLV (quando houver odd de fechamento — football-data
traz `PSCH/PSCD/PSCA`).

O Lab reexecuta Poisson/Dixon-Coles em janela rolante (walk-forward) sobre o
histórico da liga, usando apenas jogos anteriores à data de cada partida, e
compara com as odds de fechamento do CSV (Bet365 / Pinnacle). Nenhum dado
futuro vaza para o modelo.

## 15. Múltiplas (`recommendations/multiples.py`)

Correlação detectada por regras sobre o mesmo evento (Over 1.5 + BTTS,
1X2 + DNB, etc.). Quando correlacionada, a probabilidade conjunta é obtida da
mesma simulação Monte Carlo (contando cenários onde todas as seleções vencem),
não do produto ingênuo. Entre eventos diferentes, assume-se independência.
