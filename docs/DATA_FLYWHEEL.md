# EdgeFut AI — Data Flywheel (iteração 5)

> "Estamos construindo evidência. Não fabricando apostas."

O **Data Flywheel** é o ciclo que faz o dataset Superbet crescer e ser interrogado sem que nada disso
produza uma aposta antes de haver evidência: **coletar → normalizar → liquidar → medir por mercado →
registrar hipótese → confirmar → (só então) candidatar a VALUE → decisão humana**. Código em
`engine/edgefut/flywheel/`, API em `GET|POST /flywheel/*`, UI em *Dados → Data Flywheel / Superbet Lab /
Pesquisa* e *Sistema → Dados / Configurações*.

## 1. Estado do modelo: congelado (§2, §66)

`register_freeze` grava no primeiro boot da v5 `model_hash`, `config_hash` e `dataset_version`
(`history:<hash>`) de **21 modelos** (poisson, dixon_coles, bivariate_poisson, elo, strength v1/v2,
international_strength, ensemble v1/v2, calibration, corners, cards, shots, confidence, opportunity,
market-model-blend, market-logistic-stack, market-residual-v1, pipeline, monte_carlo). `freeze_status` compara
o hash atual com o congelado: `INTACT` ou `DRIFTED` (aparece na UI e no relatório). Famílias proibidas:
deep learning, transformers, redes neurais, gradient boosting (XGBoost/LightGBM/CatBoost). `PAUSED`:
"1X2 market-aware modeling". Nenhum retreino ou novo challenger nesta iteração.

## 2. O ciclo e os jobs

| Passo | Job (APScheduler) | Cadência | Módulo |
|---|---|---|---|
| Coletar oferta dos próximos jogos | `events` / `odds` (+ `live_poll`) | `events_refresh_min` / `odds_refresh_min` (5 min) | `collector.ingest_event_payload` (hook em `collectors/odds.py`) |
| Registrar lacuna de coleta | boot + cada ciclo `odds` | — | `collector.record_gap_if_needed` |
| Liquidar por mercado | `flywheel_settle` | 30 min | `settlement.settle_events` |
| Saúde do coletor + alertas | `flywheel_health` | 10 min | `reliability.collector_health`, `emit_health_alerts` |
| Pesquisa por mercado | `flywheel_research` | 6 h | `reports.run_research` → `research.*`, `governance.update_edge_states` |
| Relatório diário / semanal | `flywheel_daily` / `flywheel_weekly` | 24 h / 7 d | `reports.daily_report`, `reports.weekly_report` |
| Backup | `backup` | 24 h | `storage.create_backup` (retenção 7/4/3) |

Todos os jobs correm dentro do sidecar FastAPI (`127.0.0.1` apenas), gravam em `job_run` e podem ser
disparados pela UI (*Sistema → Jobs*). Nenhum job escreve em raw fora do `ingest`.

## 3. Coletar (§4–§9) — `collector.py`

Ver [SUPERBET_DATASET.md](SUPERBET_DATASET.md). Pontos-chave: raw append-only por triggers; confirmação
por referência quando o payload não muda; alvos T-48h…T-5m + `LAST_PRE_KO` sem interpolação; quarentena em
vez de descarte; `market_mapping_registry` para tudo o que a Superbet publica; `collector_gap` para
downtime; saúde `HEALTHY|DEGRADED|BROKEN`; backfill único e honesto do cache HTTP.

## 4. Liquidar (§11–§13) — `settlement.py`

`settle_canonical(category, selection, line, result)` implementa a regra por mercado:
`MATCH_RESULT`, `DOUBLE_CHANCE`, `DNB` (empate → `VOID`), `TOTAL_GOALS`, `BTTS`, `TEAM_TOTAL`,
`CORNERS_TOTAL`, `TEAM_CORNERS`, `CARDS_TOTAL`, `TEAM_CARDS`, `SHOTS`, `SHOTS_ON_TARGET`
(linha inteira e total igual → `VOID`); `PLAYER_*` → `UNSUPPORTED`.

- Estatística em falta (ex.: sem escanteios no resultado) → **`UNSETTLED_DATA_MISSING`** com
  `missing_fields`; **nunca `LOST`**. Reavaliado a cada ciclo; `WON/LOST/VOID/UNSUPPORTED` nunca são
  reescritos.
- Evento é "terminado" para liquidação a partir de kickoff + 3 h e só com resultado em `event`
  (`home_score/away_score`, `result_source`).
- `settlement_audit`: eventos terminados/liquidados/pendentes, `missing_result`, `missing_market_stats`,
  1X2 vs secundários, e **MARKET DATA COVERAGE** por categoria (eventos com snapshots vs liquidados vs em
  falta). UI: *Sistema → Dados → Settlement*.

## 5. Medir por mercado (§15–§20) — `research.py`

Tudo **descritivo**, por `market_category`, com N mínimo 30 por métrica e bootstrap por cluster de evento:

- **Margin Lab** — overround `Σ(1/odd)/book_total − 1` por mercado × faixa da odd favorita
  (1,00–1,50 … 5+), × alvo T-x, × linha, só com mercado completo no mesmo payload.
- **Price efficiency by T** — Brier/log-loss da fair da Superbet vs resultado, por alvo (quando há liquidação).
- **CLV V2** — para cada alvo, `fair_T − fair_close` em pp por mercado (IC por evento). Mede o movimento
  do preço da casa; não é edge.
- **Line movement** — `STABLE` (|Δ| < 1,5 pp), `DRIFT`, `STEAM` (≥ 3 pp com ≥ 60 % do movimento nas
  últimas ~3 h na mesma direção). Rótulos descritivos; nunca "smart money".
- **Lead/lag (SIGNAL VS MOVEMENT)** — sinal = `p_EdgeFut − fair(T)` na última observação Superbet
  anterior à previsão; movimento = `fair(close) − fair(T)`. Se o preço da casa se move na direção do sinal
  (agregado, N efetivo ≥ 30) o mercado é `LEAD`/`LAG` em relação ao EdgeFut; nunca por jogo, sem
  interpretação causal.
- **Market discovery** — por mercado: `raw_n`, `unique_events`, `effective_n` (clusters), maturidade
  `COLLECTING (<50) | EARLY (<200) | TESTABLE (<500) | MATURE (≥500)`, overround mediano, Superbet fair vs
  resultado, EdgeFut vs fair (ΔBrier com IC), CLV perto do fecho.

## 6. Registrar e confirmar hipóteses (§25–§30) — `experiment_registry`

Oito hipóteses **pré-registadas** no primeiro boot (`seed_experiments`), cada uma com mercado, filtro,
métrica, direção esperada, N mínimo, `confirmation_start` (= data do registo) e `status = CONFIRMING`:

| id | Mercado | Hipótese |
|---|---|---|
| H1 | MATCH_RESULT | seleções a 3,00+ pagam menos do que a fair implica (favourite-longshot) |
| H2 | TOTAL_GOALS | OVER liquida abaixo da fair |
| H3 | BTTS | SIM liquida abaixo da fair |
| H4 | CORNERS_TOTAL | OVER liquida abaixo da fair |
| H5 | CORNERS_TOTAL | `corners-v1` tem Brier menor que a fair da Superbet |
| H6 | CARDS_TOTAL | `cards-v1` tem Brier menor que a fair da Superbet |
| H7 | TEAM_TOTAL | OVER liquida abaixo da fair |
| H8 | MATCH_RESULT | preços em T-24h têm CLV bruto ≠ 0 vs fecho |

`evaluate_experiments` só usa dados **posteriores** a `confirmation_start`, aplica **BH-FDR (q = 0,10)** ao
conjunto e devolve `SUPPORTED | NOT_SUPPORTED | INSUFFICIENT` + `survives_fdr`. Nova hipótese = nova linha
com nova data; não se edita uma existente. O relatório nunca destaca "melhor segmento" sem FDR.

## 7. Governança por mercado (§31–§36, §63–§65) — `governance.py`

Máquina de estados `market_edge_state`: `UNPROVEN → COLLECTING → PROMISING → VALIDATED | REJECTED`.

- `UNPROVEN`: sem seleção liquidada. `COLLECTING`: maturidade COLLECTING/EARLY. `PROMISING`: ΔBrier
  EdgeFut−fair conclusivo e IC superior < 0. `REJECTED`: mercado claramente melhor que o EdgeFut com amostra
  MATURE. `VALIDATED`: **só por ação humana** (`POST /flywheel/edge-states/{cat}/value`) e só quando
  `enablement_candidate = true`.
- `VALUE_ENABLEMENT_CANDIDATE` exige todas as regras: N efetivo ≥ 500 · EdgeFut bate a fair (ΔBrier IC < 0) ·
  CLV IC inferior ≥ 0 · calibração sem viés detetável · hipótese pré-registada `SUPPORTED` após FDR ·
  ≥ 30 dias de confirmação. Regras cumpridas/falhadas ficam em `evidence` e na UI (*Pesquisa*).
- `VALUE_ENABLED = false` por mercado, por defeito. Ligar/desligar grava `history` e `manual_correction`;
  desligar devolve o estado a `PROMISING`/`COLLECTING` — nunca fica "VALIDATED" sem VALUE.
- **Required Edge V2** por mercado: margem por seleção + erro de preço do mercado (1,96·SE do viés) +
  penalidade de amostra (100/√N, máx. 6 pp) + penalidade de maturidade (3/2/1/0 pp). Confiança por mercado
  `INSUFFICIENT | D | C | B | A` (N efetivo e fração liquidada).
- **Staking/Kelly DESLIGADO** enquanto nenhum mercado tiver `VALIDATED` + VALUE (`staking_enabled()`), e a
  UI diz porquê.
- Recomendações: uma seleção que passa o quality gate num mercado com VALUE desligado é
  **`RESEARCH_SIGNAL`** (cartão próprio no Radar/Dashboard), nunca `VALUE`. `value_enabled_for(market)` é
  consultado no motor de recomendações.

## 8. UI (§3, §37–§45)

- **Faixa de estado** (Dashboard, Data Flywheel): `FOOTBALL MODEL PREDICTIVE VS NAIVE · MARKET EDGE UNPROVEN ·
  SUPERBET EVIDENCE COLLECTING`. Muda só quando o estado por mercado muda.
- **Data Flywheel**: crescimento (snapshots/dia, eventos únicos, normalizadas, liquidadas), cobertura por
  alvo/mercado, saúde do coletor, mapeamento, lacunas, quarentena, armazenamento, freeze, staking.
- **Superbet Lab**: filtros por mercado/competição/faixa/alvo; Margin Lab; eficiência por T; CLV V2; LINE
  MOVEMENT (top movimentos com rótulo STEAM/DRIFT/STABLE); SIGNAL VS MOVEMENT (a fair EdgeFut vs o movimento
  da casa, por seleção).
- **Pesquisa**: maturidade por mercado, edge state, regras de enablement, hipóteses e FDR, relatório
  semanal com a resposta ao §67 ("em que mercado vale a pena investir pesquisa?").
- **Sistema → Dados**: Settlement audit, Mercados desconhecidos (decisão manual auditada), Quarentena,
  Armazenamento (backups/restore/export), Identidade (duplicados, sem id canônico, kickoff inconsistente,
  correções), Relatório diário.
- **Notificações locais** (`ALERT_KINDS`): `COLLECTOR_DEGRADED`, `SCHEMA_CHANGED`, `SETTLEMENT_COMPLETED`,
  `LINE_MOVED`, `PRICE_TARGET_REACHED`, `RESEARCH_SIGNAL`. Nunca "BET NOW".
- **Morning workflow V2** (Dashboard): a faixa de estado, o texto da manhã (contagens reais de VALUE /
  VALUE CANDIDATE / **RESEARCH SIGNAL**, com a frase "VALUE está desativado em todos os mercados até haver
  validação contra a Superbet — os sinais são para observar preço e movimento, não para apostar"), a saúde do
  coletor no rodapé da barra lateral e os cartões do Radar. Rotina: saúde/lacunas → cobertura do dia →
  liquidação de ontem → movimentos → research signals.
- **PRICE WATCH**: `PRICE_TARGET_REACHED` dispara quando uma seleção observada atinge a odd mínima
  aceitável calculada pelo motor (`min_acceptable_odd`); é um alerta de **observação**, não uma ordem.

## 9. Windows always-on (§46–§48) — `apps/desktop/src-tauri/src/lib.rs`

O dataset só cresce com o app a correr; por isso a shell Tauri trata o coletor como serviço:

- **RUN DATA COLLECTOR IN BACKGROUND** (`Configurações → Manter coletor em segundo plano`): fechar a janela
  esconde-a para a **bandeja** em vez de encerrar; o sidecar Python continua a coletar.
- **Ícone de bandeja** com tooltip `saúde · última sync · snapshots/última hora` (poll a `GET
  /flywheel/collector/health` a cada 60 s) e menu: Abrir · Estado do coletor · Ver Data Flywheel ·
  Atualizar · **Sair** (encerra janela, bandeja e sidecar).
- **Iniciar com o Windows** (`tauri-plugin-autostart`, arg `--tray` → arranca minimizado na bandeja),
  opcional e desligado por defeito.
- **Downtime** → no boot seguinte o engine grava `collector_gap`; a UI mostra a lacuna, nunca a preenche.
- **Instância única** (`tauri-plugin-single-instance`): abrir o app com ele na bandeja só traz a janela
  existente — nunca uma 2.ª instância/2.º ícone.
- **Instalador/desinstalador** (`src-tauri/windows/hooks.nsh`): como o motor sobrevive ao fecho da janela,
  os hooks NSIS encerram `edgefut-engine.exe` (e a shell) antes de escrever/apagar ficheiros; sem isto uma
  atualização falhava com "Erro ao abrir o arquivo pra gravação: …\edgefut-engine.exe".
- A shell recebe **apenas duas flags** (`background_collector`, `autostart_on_login`); nunca credenciais.
  Nenhuma automação de aposta existe em lado nenhum.

## 10. Relatórios (§60–§62) — `reports.py`

- **Diário**: coleta (snapshots, eventos, alvos cobertos), saúde, liquidação de ontem, movimentos,
  quarentena, armazenamento. Carimbo `RESEARCH ONLY`.
- **Semanal**: por mercado `verdict` (`INSUFFICIENT | COLLECTING | PROMISING | REJECTED`), hipóteses e FDR,
  headline, `answer_to_67` (`promising`, `rejected`, `growing`, `answer`). Enquanto nenhum mercado tiver
  N efetivo suficiente a resposta é literalmente `INSUFFICIENT — ainda sem mercado com evidência para decidir`.

## 11. O que este ciclo nunca faz

- Alterar raw ou normalizadas (triggers), preencher lacunas, fabricar snapshots históricos.
- Assumir derrota por falta de estatística.
- Chamar movimento de "smart money" ou movimento de "edge".
- Ligar VALUE, staking ou Kelly sem `VALIDATED` + decisão humana.
- Treinar, retreinar ou introduzir challengers (freeze `INTACT`).
- Fazer login, guardar credenciais, clicar em apostar, contornar anti-bot.
