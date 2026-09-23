# EdgeFut AI — Validação de modelos

Como o EdgeFut mede se um modelo presta, o que ele compara, e o que ele **não**
consegue provar. Números da última execução em
[`ITERATION_3_REPORT.md`](ITERATION_3_REPORT.md); regras de governança em
[`MODEL_GOVERNANCE.md`](MODEL_GOVERNANCE.md).

Princípio: **a prioridade é a verdade sobre o modelo, não o Radar cheio.**
Nenhum limiar é relaxado para gerar entradas; nenhum resultado é "ajustado".

## 1. Camadas de evidência

| Camada | Fonte | O que prova | Módulo |
|---|---|---|---|
| Testes de invariante e anti-leakage | pytest | que o código não vê o futuro e que as distribuições são válidas | `tests/` |
| **Historical Replay** | football-data (11 ligas) e international_results | Brier/LogLoss/ECE do modelo vs baselines, em janelas walk-forward, com IC | `validation/replay.py` |
| Walk-forward de hiperparâmetro | idem | escolha do half-life do strength-v2 | `validation/decay.py` |
| **Shadow mode** | previsões reais do app (Superbet) liquidadas pelo `reconcile` | performance out-of-sample real, por estado e mercado | `validation/shadow.py` |
| Reconciliação de settlement | eventos terminados × `prediction_snapshot` | que nada terminado fica sem estado e que fonte e liquidação concordam | `backtesting/reconciliation.py` |
| Drift monitor | shadow recente vs referência | que a distribuição das previsões não mudou em silêncio | `validation/drift.py` |
| Performance | snapshots liquidados | Hit/Brier/ROI/Yield/CLV com IC por mercado, competição, cluster, estado | `backtesting/performance.py` |

## 2. Anti-leakage

Toda leitura de histórico em produção e no replay passa pelo
`TemporalFeatureStore` (`features/temporal.py`):

- `team_matches`, `competition_matches`, `h2h` recebem `as_of` e devolvem só
  `date < as_of`;
- se um backend devolver uma linha em ou após `as_of`, `assert_before`
  levanta `LeakageError` — a análise falha em vez de vazar;
- no replay, `reference_date` (para o decaimento temporal) é o **início da
  janela**, nunca "agora";
- os pesos do ensemble em cada janela vêm **apenas** das janelas anteriores
  (primeira janela: pesos iguais, declarado nas limitações);
- a decisão de aposta usa a odd pré-jogo; a closing line só entra em CLV
  depois da decisão.

Testes: `test_temporal_store_never_returns_rows_at_or_after_as_of`,
`test_temporal_store_raises_if_a_backend_leaks_future_rows`,
`test_temporal_store_uses_distinct_codes_per_team`,
`test_replay_is_leak_free_and_reports_baselines_with_ci`,
`test_replay_refuses_to_use_future_rows`,
`test_strength_v2_time_decay_never_sees_future_and_weights_recent`, mais os
cinco de `test_backtest_leakage.py`.

## 3. Historical Replay Engine

`POST /validation/replay` (assíncrono; `GET /validation/replay/latest`,
`?international=true` para seleções).

Parâmetros: `datasets`, `start`, `end`, `window_days` (30 clubes · 90
seleções), `scheme` (`expanding` | `rolling` com `rolling_years`), `models`,
`half_life_days` (`"settings"` usa o valor governado), `min_train` (150),
`is_national`, `friendly_weight`, `bet_simulation`.

Para cada janela:

1. treino = partidas anteriores à janela (via `TemporalFeatureStore(frame=…)`);
2. ajuste de todos os modelos (`poisson`, `dixon_coles`, `bivariate_poisson`,
   `poisson_v2`, `ensemble`, `ensemble_v2`) com `reference_date` = início;
3. probabilidades 1X2 e Over/Under 2,5 de cada modelo e de cada baseline;
4. comparação com as odds daquele momento e liquidação com o placar real;
5. acumulação por modelo, dataset, janela, mercado e grupo (temporada para
   clubes; `tournament_type` para seleções).

Persistência: `validation_run(kind="replay")` com `summary` (para o Model
Health) e `detail` (relatório completo). Nunca escreve em
`prediction_snapshot`.

### 3.1 Baselines

| Código | Baseline | Definição |
|---|---|---|
| A | `market` | odds médias pré-jogo do football-data com margem removida (multiplicativa). Só existe onde há odds |
| B | `naive` | frequências casa/empate/fora da liga até `as_of` (Over 2,5: taxa observada) |
| C | `simple_poisson` | Poisson com médias de gols casa/fora da liga, sem força de time |
| D | `elo` | ELO puro com taxa de empate da liga |
| E | `favorite` | probabilidade 1 no favorito do mercado (Brier de um "chute cego no favorito") |

`lift_% = (Brier_baseline − Brier_modelo) / Brier_baseline · 100`. Positivo =
modelo melhor.

### 3.2 Métricas

- **Brier** multiclasse (1X2: soma dos quadrados nas três classes → varia de 0
  a 2, por isso ~0,58 e não ~0,2) e binário (OU 2,5);
- **LogLoss**; **ECE** (10 bins); hit rate; probabilidade média por classe;
  share de probabilidades > 90 %;
- **paired bootstrap** da diferença modelo − baseline por partida (mesmas
  partidas para os dois lados), IC 95 %;
- `windows_better / windows_total` — em quantas janelas o modelo teve Brier
  menor que o baseline;
- simulação de apostas (opcional): gate simplificado `edge ≥ min_edge_pp`,
  `EV ≥ min_ev_pct`, `odd ∈ [min_odd, max_odd]`, uma seleção por mercado;
  ROI, Yield, hit, CLV (odd de fechamento) com IC; veredito `POSITIVE` /
  `NEGATIVE` / `INCONCLUSIVE` / `INSUFFICIENT`.

### 3.3 O que o replay não reproduz

Declarado em `report.limitations` e na UI: odds football-data ≠ Superbet;
gate simplificado (sem confiança, frescor, qualidade de dados, clusters); só
1X2 e OU 2,5 têm preço histórico; pesos iguais na primeira janela.

## 4. Bootstrap, qualidade de amostra e significância (`validation/bootstrap.py`)

- `bootstrap_ci(values, stat, resamples=1000, alpha=0.05, zero_test, seed)` —
  IC percentílico; `conclusive=False` quando `zero_test` e o IC cruza zero
  (ROI, Yield, CLV, lift, Δ). `n < 10` → sem IC.
- `paired_bootstrap_diff(a, b)` — IC da média de `a − b` por observação
  pareada.
- **Qualidade de amostra** (`sample_quality(n)`): `INSUFFICIENT` < 100 ·
  `EARLY` < 300 · `MODERATE` < 1.000 · `STRONG` ≥ 1.000. Limiares em
  `settings.sample_early_min / sample_moderate_min / sample_strong_min` com
  piso rígido (não podem ser reduzidos pela UI).
- **Significância vs baseline** (`model_significance(...)`):
  - `INSUFFICIENT DATA` — amostra INSUFFICIENT ou sem IC;
  - `NO CLEAR ADVANTAGE` — IC da ΔBrier inclui 0 **ou** é inteiramente > 0
    (modelo pior);
  - `PROMISING` — IC inteiro < 0 mas amostra não STRONG ou < 75 % das janelas;
  - `CONSISTENT` — IC < 0, amostra STRONG e ≥ 75 % das janelas melhores.

Toda métrica agregada exibida na UI carrega `n`, `sample_quality` e, quando há
IC, `[low; high]`. Grupos INSUFFICIENT mostram a etiqueta em vez do número.

## 5. Walk-forward v2 — escolha do half-life (`validation/decay.py`)

`POST /validation/decay` roda o replay do `poisson_v2` para cada candidato de
half-life (30 / 60 / 90 / 180 / 365 / 730 dias / sem decaimento) nas mesmas
janelas, e devolve ranking por Brier, IC pareado do melhor contra cada um dos
outros, `tie_with_runner_up` e `recommended`.

Regra: se o melhor é estatisticamente indistinguível do segundo (IC da Δ
inclui 0), recomenda-se o **mais simples** entre os equivalentes (half-life
maior = menos "reatividade"). O valor recomendado é gravado em
`settings.strength_half_life_days` e exibido em Validação → Model Comparison.
Nenhum half-life é escolhido "no olho".

## 6. Shadow mode (`validation/shadow.py`)

Cada seleção avaliada pelo pipeline em produção grava uma linha
`shadow_prediction` (evento, mercado, seleção, linha, estado, status,
probabilidade, odd, edge, cluster, primária/alternativa, versão do campeão).
A tabela é **append-only**: campos preditivos não podem ser alterados (guarda
em `db/immutability.py`); só o resultado é anexado pelo `reconcile`.

Relatório diário (`shadow_report`, `GET /validation/shadow?live=true`):
eventos observados/analisados, seleções e eventos por estado, liquidadas hoje,
e performance por janela (7 d / 30 d / 90 d / tudo) separando:

- `priced` — seleções com preço (VALUE, VALUE_CANDIDATE, OBSERVATION);
- `value_only` — só VALUE;
- `model_only` — **nunca** entra em ROI/Yield/CLV (não há preço); mede-se só
  Brier/hit.

É esta camada que, com o tempo, responde à pergunta que o replay não
consegue: **a Superbet, especificamente, é batível em algum mercado?**

## 7. Reconciliação de settlement (`backtesting/reconciliation.py`)

Todo evento com kickoff ≥ 3 h atrás recebe exatamente um estado:

| Estado | Regra |
|---|---|
| `SETTLED` | todos os snapshots do evento têm resultado |
| `SETTLEMENT_PENDING` | sem resultado e terminou há < 72 h |
| `SETTLEMENT_ERROR` | sem resultado há ≥ 72 h (alerta) |

Divergência = o resultado que a fonte informa agora ≠ o que foi usado na
liquidação; é contada, logada e vira alerta. `GET /dashboard` e
`GET /health/system` expõem `settled / pending / error / unclassified /
unsettled_finished / divergences`. Teste:
`test_reconciliation_classifies_every_finished_event`.

## 8. Drift monitor (`validation/drift.py`)

Compara `shadow_prediction` dos últimos 30 dias com os 90 dias anteriores
(mínimo 50 linhas em cada lado):

| Sinal | Limiar |
|---|---|
| probabilidade média | Δ ≥ 0,05 |
| share de probabilidades > 90 % | +5 pp |
| edge médio | Δ ≥ 2 pp |
| share MODEL_ONLY | +15 pp |
| Brier em liquidados | piora ≥ 0,02 |
| hit − probabilidade média | Δ ≥ 0,05 |

Resultado: `OK` / `WATCH` / `DRIFT` / `INSUFFICIENT DATA`, com histograma de
probabilidades, auditoria de probabilidade (prob média × frequência observada
por bin nos liquidados) e lista de alertas. **Nunca há ação automática**: o
drift é um sinal para revisão humana e para uma eventual rodada de replay.

## 9. Performance em produção (`backtesting/performance.py`)

Sobre `prediction_snapshot` liquidados: Hit, Brier, LogLoss, ROI, Yield,
Drawdown, CLV, com IC do ROI por bootstrap e `sample_quality`. Cortes: por
mercado, competição, **primárias × alternativas** (`selection_vs_cluster`),
**por cluster** (só primárias), **por estado**, e `secondary_markets`
(escanteios, cartões, finalizações…) com veredito `INSUFFICIENT` /
`PROMISING` / `NO CLEAR ADVANTAGE`. Grupos com N abaixo do piso mostram
`INSUFFICIENT SAMPLE`.

## 10. Como rodar

```bash
# replay de clubes (padrão: 11 ligas com odds, 2022-08 →, janelas de 30 d)
curl -X POST 127.0.0.1:8765/validation/replay -H 'content-type: application/json' -d '{}'
# replay de seleções
curl -X POST 127.0.0.1:8765/validation/replay -H 'content-type: application/json' \
  -d '{"datasets":["INTL"],"start":"2010-01-01","window_days":90,"is_national":true,"bet_simulation":false}'
# walk-forward do half-life
curl -X POST 127.0.0.1:8765/validation/decay -d '{}'
# resultados
curl 127.0.0.1:8765/validation/replay/latest
curl '127.0.0.1:8765/validation/replay/latest?international=true'
curl 127.0.0.1:8765/validation/decay/latest
curl '127.0.0.1:8765/validation/shadow?live=true'
curl '127.0.0.1:8765/validation/drift?live=true'
curl 127.0.0.1:8765/validation/coverage
curl 127.0.0.1:8765/validation/governance
```

Na UI: **Validação** (abas Model Validation · Model Comparison · Coverage Map ·
Shadow · Drift). O replay completo de clubes leva alguns minutos (ajuste de
seis modelos × 463 janelas × 11 ligas); o botão mostra `running` até terminar.
