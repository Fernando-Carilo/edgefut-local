# Iteração 5 — Baseline (estado real antes do Data Flywheel)

Auditoria feita em **2026-09-24 17:22 UTC** sobre `data/edgefut.sqlite3` (80,4 MB; diretório `data/` 211 MB, dos quais 111 MB de cache HTTP) e sobre os 315 payloads de evento em cache. Nenhum número abaixo é estimado: todos saem de `SELECT`s no banco ou de contagem nos JSONs. Onde não há dado, está escrito **não existe**.

## 1. O que a iteração 4 deixou

| Camada | Estado | Números |
|---|---|---|
| Eventos Superbet | `event` | 694 eventos (primeiro `first_seen_at` 2026-09-23 13:18 UTC). `settlement_status`: NULL 494 · SETTLED 181 · SETTLEMENT_PENDING 19 |
| Snapshots de odds | `odds_snapshot` | 104 634 linhas / 274 eventos; todas com `status=active`. **Dedup por preço idêntico**: só grava quando a odd mudou |
| Closing lines | `closing_line` | 9 304 linhas / 168 eventos |
| Shadow | `shadow_prediction` | 4 931 linhas / 56 eventos; 261 liquidadas. `TOTAL_CORNERS` 12 linhas, 0 liquidadas. `FIRST_GOAL` 291 linhas com `settled_at` mas `won` NULL (regra de liquidação inexistente → contadas como "liquidadas" sem resultado) |
| Previsões | `prediction_snapshot` | 243 / 40 liquidadas |
| Log de fonte | `source_log` provider=superbet | ok 9 410 · cached 478 · **0 erros, 0 bloqueios, 0 HTTP≠200** (sem stress observado ainda) |
| Jobs | `job_run` | inclui execuções `interrupted` (sync_odds 11, live_poll 6, radar 3) — restarts do engine durante a iteração 4 |
| Alertas | `alert` | MODEL_CONFIDENCE_CHANGE 50 · ODD_MOVEMENT 91 · OPPORTUNITY_APPEARED 47 · OPPORTUNITY_LOST 46 |
| Jogos terminados | `event` (kickoff > 3 h atrás) | 195; **21 sem placar** |

### Snapshots por mercado (`odds_snapshot`)

| market_key | linhas | eventos |
|---|---|---|
| CORRECT_SCORE | 23 838 | 154 |
| TOTAL_GOALS | 14 431 | 267 |
| TEAM_TOTAL_HOME | 9 894 | 241 |
| TEAM_TOTAL_AWAY | 9 479 | 242 |
| TOTAL_CORNERS | 7 965 | 154 |
| HANDICAP | 6 450 | 233 |
| 1X2 | 5 926 | 271 |
| FIRST_GOAL | 5 816 | 241 |
| ASIAN_HANDICAP | 5 692 | 159 |
| DOUBLE_CHANCE | 4 943 | 271 |
| BTTS | 3 619 | 264 |
| DRAW_NO_BET | 3 399 | 265 |
| TOTAL_CARDS | 1 644 | 58 |
| PLAYER_TO_SCORE | 1 538 | 14 |

Sem `TOTAL_SHOTS`, sem `TEAM_CORNERS`, sem `TEAM_CARDS`: os IDs existem na oferta (ver §3) mas **não estavam mapeados** — eram descartados em silêncio.

## 2. Gaps estruturais (motivo da iteração 5)

1. **Raw layer não existe.** `Event.raw` guarda o último payload *sem odds* (sobrescrito a cada sync). Nenhum payload bruto com odds é retido; o cache HTTP (`data/cache`, TTL 240 s) é efêmero e apagado pelo `cache_cleanup`.
2. **Snapshots não são observações.** `sync_odds` só grava `odds_snapshot` quando o preço mudou. Um preço confirmado igual em T-1h não deixa rasto → cobertura por bucket é subestimada e "closing = último snapshot" pode ser uma odd de horas antes.
3. **Unknown markets descartados em silêncio.** `normalize_odd` devolve `None` para `marketId` fora de `SUPERBET_MARKET_IDS`; dos **414 marketIds distintos** observados nos payloads, só **14** estavam mapeados.
4. **Sem métricas de coletor.** `source_log` regista pedidos, mas não há taxa de parse, cobertura de mapeamento, mudança de schema, health HEALTHY/DEGRADED/BROKEN nem alerta.
5. **Settlement ambíguo.** `settle_selection` devolve `None` tanto para *void* como para *dado em falta* (escanteios/cartões sem estatística). O `FIRST_GOAL` shadow mostra o efeito: 291 linhas "liquidadas" sem `won`.
6. **Sem quarentena, sem registo de experimentos, sem backup, sem export, sem modo background/tray.** Fechar a janela mata o sidecar → o dataset só cresce enquanto a UI estiver aberta.
7. **Lacunas de downtime não registadas.** Restarts aparecem como `job_run.interrupted`, mas não como intervalo de cobertura perdida.

## 3. Mercados observados na oferta pública (315 payloads em cache)

Contagem de odds por `marketId` nos payloads `/events/{id}` em cache (o cache guarda a **última** resposta por URL; muitos são jogos já terminados, por isso o 1X2 aparece em poucos). Isto é o que orienta `docs/MARKET_MAPPING.md`:

| Categoria canónica | marketId (nome Superbet) | odds vistas |
|---|---|---|
| MATCH_RESULT | 547 (Resultado Final) | 318 |
| DOUBLE_CHANCE | 531 (Dupla Chance) | 312 |
| DNB | 555 (Empate Anula Aposta) | 208 |
| TOTAL_GOALS | 200734 (Total de Gols) | 1 075 |
| BTTS | 539 (Ambas as Equipes Marcam) | 194 |
| TEAM_TOTAL | 544 / 535 ("<Equipe> - Total de Gols") | 656 / 651 |
| CORNERS_TOTAL | 704 (Total de Escanteios) | 926 |
| TEAM_CORNERS | 713 / 733 ("<Equipe> - Total de Escanteios") | 406 / 384 |
| CARDS_TOTAL | 690 (Total de Cartões) | 226 |
| TEAM_CARDS | 700 / 708 ("<Equipe> - Total de Cartões") | 88 / 88 |
| SHOTS | 201590 (Total de Finalizações) · 201591 / 201592 (por equipe) | 124 · 88 / 88 |
| SHOTS_ON_TARGET | 200702 (Total de Chutes no Gol) · 200703 / 200704 (por equipe) | 124 · 88 / 86 |
| PLAYER_GOAL | 233475 / 236226 (Jogador - Marcar Gol; specifier `player`) | 406 / 662 |
| PLAYER_SHOTS | 236218 / 232338 (Jogador - Finalizações; `player_id`,`player_name`) | 3 539 / 229 |
| PLAYER_SOT | 236220 (Jogador - Chutes no Gol; `player_id`,`player_name`) | 1 953 |

Fora de escopo (registados, não ignorados): 1º/2º tempo, combinações (Resultado & Total, Dupla Chance & BTTS…), placar correto, primeiro gol, handicaps, faixas, "próximos 10 minutos", duelos de jogadores, treinadores, etc. O maior volume de odds da oferta está em mercados exóticos: 231194 ("1º Tempo … ; 2º Tempo …") sozinho tem 11 334 odds — mais que 1X2, DC, DNB, OU, BTTS e escanteios juntos.

Tamanho médio de um payload de evento: **285 KB** (JSON não comprimido). É isso que a raw layer precisa de acomodar — comprimido (zlib) e sem repetir payloads idênticos.

## 4. Resultado herdado que a iteração 5 **não** tenta reverter

Iteração 4 terminou em **NO EVIDENCE OF MARKET EDGE** (holdout 1X2: Brier mercado 0,5831 vs EdgeFut 0,5958; residual Δ −0,0001 [−0,0020, +0,0017]; OU 2.5: 0,4752 vs 0,4853; CLV Superbet −1,3 %, N efetivo 10). A iteração 5 congela os modelos (`docs/MODEL_GOVERNANCE.md` §9) e passa a construir evidência **por mercado** — em vez de tentar outro modelo.

## 5. O que muda nesta iteração (resumo)

- Raw layer imutável `raw_superbet_snapshot` (payload comprimido, hash, confirmações sem payload); normalizada `superbet_normalized_v1`; derivadas por relatório com `source_version / normalizer_version / model_version / settlement_version`.
- Snapshot targets T-48h … T-5m + último antes do kickoff, com cobertura observada/esperada por evento e por mercado — nunca fabricada.
- Registro de mapeamento com `UNKNOWN` / `OUT_OF_SCOPE` / `MAPPED`; quarentena (`data_quarantine`); detecção de mudança de schema; health do coletor; lacunas de downtime (`collector_gap`).
- Settlement por mercado com `UNSETTLED_DATA_MISSING` explícito; auditoria de liquidação e tabela MARKET DATA COVERAGE.
- Margin Lab, eficiência de preço por T, CLV V2 (odd e fair), classificação STEAM/DRIFT/STABLE, lead/lag EdgeFut × mercado, discovery por mercado com estados de maturidade, `experiment_registry` com BH-FDR.
- `VALUE_ENABLED=false` por mercado; RESEARCH SIGNAL / PRICE WATCH; staking desligado; máquina de estados MARKET_EDGE por mercado.
- Backup diário 7/4/3, restore, export CSV/Parquet, storage dashboard.
- Windows always-on: coletor em background, tray com saúde, autostart opcional.
