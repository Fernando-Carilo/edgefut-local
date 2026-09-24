# EdgeFut AI — Superbet Dataset (iteração 5)

O ativo desta iteração não é um modelo: é um **dataset próprio de preços da Superbet**, coletado pelo app,
append-only, versionado e auditável. Este documento descreve as camadas, o esquema, as garantias e os
números reais no fecho da iteração (2026-09-24). Código: `engine/edgefut/flywheel/`.

Regra que governa tudo (§4, §7, §49): **dado bruto nunca é editado nem apagado automaticamente; o que não foi
observado não é fabricado.**

## 1. Camadas

| Camada | Tabela | Regime | Conteúdo |
|---|---|---|---|
| RAW | `raw_superbet_snapshot` | append-only (**triggers SQLite** `trg_*_no_update/no_delete`) | 1 linha por fetch real de `/events/{id}`; payload zlib(JSON) ou *confirmação* (`payload_ref_id`) quando o hash não mudou |
| NORMALIZADA | `superbet_normalized_v1` | append-only (triggers) | 1 linha por seleção canônica × instante, quando o preço mudou ou a coleta cobre um alvo T-x |
| LIQUIDAÇÃO | `superbet_settlement_v1` | upsert só para `UNSETTLED_DATA_MISSING`/`ERROR`; `WON/LOST/VOID/UNSUPPORTED` nunca são reescritos | resultado por seleção canônica, `settlement_version`, campos em falta |
| REGISTOS | `market_mapping_registry`, `data_quarantine`, `collector_gap`, `experiment_registry`, `manual_correction`, `market_edge_state` | mutáveis, com trilha (`decided_by`, `note`, `created_at`) | governança e auditoria |
| DERIVADOS | `data/processed/*.parquet`, `data/exports/` | reprodutíveis a partir das tabelas | frames de pesquisa (`normalized`, `timelines`, `settlement`, `edgefut`) e exportações CSV/Parquet |

Versões carimbadas em cada linha (§9): `source_version = superbet-offer-v2` (ou
`superbet-offer-v2/cache-backfill` para o backfill único do cache HTTP, ver §6),
`normalizer_version = superbet-normalizer-v1`, `settlement_version = superbet-settlement-v1`,
`dataset = superbet-shadow-v2`. Mudança de normalizador ⇒ tabela `superbet_normalized_vN+1`; a v1 fica como
está.

## 2. RAW — `raw_superbet_snapshot`

- **Uma linha por fetch real** (respostas servidas do cache HTTP não geram linha; o cache existe só para
  rate-limit).
- `payload` (zlib) só é gravado quando `payload_hash` muda. Se a Superbet devolve exatamente o mesmo JSON, a
  linha é uma **confirmação**: `payload = NULL`, `payload_ref_id` aponta para a linha com o payload. A
  observação no tempo fica registada sem duplicar bytes (compressão efetiva 15,9× + referências).
- Metadados por linha: `fetched_at`, `kickoff_utc`, `minutes_to_kickoff`, `event_state`
  (`prematch|live|finished|unknown`, derivado do payload e do tempo até o kickoff), contagens
  `odds_total/mapped/unknown/out_of_scope`, `parse_failures`, `markets_present` (categorias canônicas no
  payload), `schema_issues` (campos obrigatórios em falta), `snapshot_target`.
- `UNIQUE(event_id, fetched_at)`: a mesma coleta nunca entra duas vezes; uma segunda tentativa vai para
  `data_quarantine` (`DUPLICATE_SNAPSHOT`) em vez de sobrescrever.
- Não há FK para `event`: raw não depende de a identidade do jogo estar resolvida.

## 3. NORMALIZADA — `superbet_normalized_v1`

Uma linha por `(raw_snapshot_id, canonical_market_id, selection_id)` com `odd`, `implied_prob`,
`fair_prob`/`overround` (só quando o mercado canônico está **completo no mesmo payload**),
`line`, `fetched_at`, `kickoff_utc`, `minutes_to_kickoff`, `event_state`, `snapshot_target`,
`identity_confidence` (`STRONG` = `player_id`; `NAME_ONLY`) e `competition_*`.

Regra de escrita: todas as seleções ativas quando a coleta cobre um **alvo** ou quando é a primeira
observação do evento; fora disso, só as seleções cujo preço **mudou** desde a última linha. Isto mantém a
linha do tempo completa (cada preço observado tem carimbo) sem repetir preços iguais a cada 5 minutos.

Canonicalização (ids, lados, linhas, odds bloqueadas, identidade de jogador) está em
[MARKET_MAPPING.md](MARKET_MAPPING.md).

## 4. Alvos de snapshot (§5) e closing

Alvos: `T-48h, T-24h, T-12h, T-6h, T-3h, T-2h, T-1h, T-30m, T-15m, T-5m` + `LAST_PRE_KO` (a última coleta
antes do kickoff, seja qual for o alvo). As janelas não se sobrepõem (`target_for_minutes`); uma coleta só
recebe o rótulo se cair dentro da janela do alvo. **Não há interpolação**: se o app estava desligado às
T-6h, o alvo T-6h daquele jogo fica em falta e conta na cobertura como tal.

`closing` = `LAST_PRE_KO` (independente de ter havido coleta em T-5m). CLV V2 compara cada alvo com esse
fechamento, por mercado — é uma medida do **movimento de preço da casa**, não de edge.

## 5. Cobertura, saúde e lacunas (§6, §8, §48)

- `coverage_report` (`GET /flywheel/coverage`): para cada evento com kickoff nos últimos N dias e para cada
  alvo que já passou, `expected += 1`; `observed += 1` se existe raw com esse `snapshot_target`. Por alvo,
  por mercado (via `markets_present`) e por evento.
- `collector_health` (`GET /flywheel/collector/health`): `HEALTHY | DEGRADED | BROKEN` a partir da última
  hora/24 h de `source_log` (sucesso, bloqueios, 5xx, latência) e de raw (parse rate, cobertura de
  mapeamento, `schema_issue_snapshots`, staleness vs cadência). "Superbet schema changed" aparece quando
  campos obrigatórios faltam no evento (`eventId`, `matchName`, `marketCount`) ou nas odds (`marketId`,
  `price`, `name`, `marketName`).
- `collector_gap` (`GET /flywheel/gaps`): no boot e a cada ciclo, se a distância desde a última coleta
  excede `max(20 min, 3× cadência)`, grava-se uma lacuna `[from, to]` com motivo (`DOWNTIME`). Nunca é
  preenchida.

## 6. Backfill único do cache HTTP (honesto por construção)

No primeiro boot da v5 o engine lê os ficheiros do cache HTTP de `/events/{id}` (iterações 1–4) e ingere
cada um como raw com `source_version = superbet-offer-v2/cache-backfill` e `fetched_at = mtime do ficheiro`.
Consequências que o dataset mostra e que **não** são defeitos:

- 331 payloads ingeridos (206 `finished`, 85 `prematch`, 21 `live`, 19 `unknown`). Os `finished` são ofertas
  buscadas **depois do kickoff** com `odds_total = 0` — o cache guardava a última resposta, não a
  pré-jogo. Ficam como raw verdadeiro (a Superbet devolveu uma oferta vazia) e geram 0 linhas normalizadas.
- Nenhum snapshot pré-jogo foi "reconstruído" a partir de `odds_snapshot` (tabela da iteração 1): a
  primeira observação pré-jogo real da v5 é de **2026-09-24 17:45 UTC**.
- É idempotente (`UNIQUE(event_id, fetched_at)`); reiniciar o engine não duplica.

## 7. Quarentena e integridade (§53, §57)

`data_quarantine(reason, event_id, raw_id, payload_hash, detail, excerpt, status, resolved_by, note)`.
Motivos emitidos pelo coletor: `DUPLICATE_SNAPSHOT`, `UNKNOWN_EVENT`, `NEGATIVE_TIMESTAMP` (carimbo no
futuro), `SCHEMA_CHANGE`, `KICKOFF_INCONSISTENT`; pela canonicalização (por odd): `MISSING_PRICE` (inclui
`price ≤ 1` ativo), `MISSING_LINE`, `UNKNOWN_SELECTION`, `MARKET_MAPPING_CONFLICT`,
`PLAYER_IDENTITY_MISSING`. O payload **continua em raw** (a quarentena aponta para ele); só a normalização
da parte afetada é suspensa. UI: *Sistema → Dados → Quarentena* (inspecionar excerto, resolver/ignorar com nota).

## 8. Armazenamento, backup, export (§49–§52)

- `GET /flywheel/storage`: bytes de SQLite/WAL, raw comprimido vs original, crescimento/dia e projeção
  30/365 d, parquet, cache, backups, exports.
- Backup diário (job `backup`, `data/backups/edgefut-YYYYMMDD-HHMMSS.sqlite3` via `sqlite3.backup()` +
  manifest JSON com `integrity_check`, `schema_version`, contagens). Retenção **7 diários / 4 semanais / 3
  mensais**; remoção só de backups fora da política, nunca de raw.
- Restore (`POST /flywheel/backups/restore`): verifica integridade do ficheiro (ficheiro corrompido ou não-SQLite
  → `unreadable`, nada acontece), faz **cópia de segurança do DB atual** e só depois substitui.
- Export (`POST /flywheel/exports/run`): CSV ou Parquet de `raw` (metadados), `normalized`, `settlement`,
  `timelines`, com carimbo e contagem.

## 9. Números reais no fecho (2026-09-24 19:11 UTC, engine local)

| Métrica | Valor |
|---|---|
| Raw snapshots | **640** (457 com payload, 194 confirmações por referência) · primeiro 2026-09-23 15:27 (backfill) |
| Primeira observação pré-jogo real | 2026-09-24 17:45 UTC |
| Eventos únicos com raw | 381 · 350 prematch · 61 live · 209 finished (oferta vazia pós-jogo) · 20 unknown |
| Linhas normalizadas v1 | **16 095** |
| `marketId` distintos | 460 → MAPPED 24 · OUT_OF_SCOPE 436 · UNKNOWN **0** (ocorrências: 46 605 mapeadas / 391 615 fora de escopo) |
| Cobertura de mapeamento (última hora) | 99,06 % das odds não-out-of-scope · `unknown_share` 0,72 % (antes da reclassificação no boot) |
| Cobertura de alvos (7 d) | **2,3 %** (38/1 645) — T-24h 8,7 %, T-5m 5,0 %, LAST_PRE_KO 4,9 %, T-12h/T-2h/T-30m/T-15m 0 % |
| Lacunas registadas | 0 |
| Quarentena aberta | 0 |
| Seleções liquidadas | **0** — o kickoff mais antigo com preço pré-jogo real é 2026-09-24 19:00 UTC; liquidação exige kickoff + 3 h e resultado |
| Saúde do coletor | HEALTHY · 1 404 pedidos/h · 100 % sucesso · 0 bloqueios · p50 104 ms · staleness 1 min |
| Tamanho | SQLite 119,6 MB (+16 MB WAL) · raw 18,7 MB comprimidos (297 MB originais, 15,9×) · 2,7 MB/dia → 80 MB/30 d · 0,97 GB/ano |
| Backups | 1 (`integrity ok`, schema 5) |

**Leitura honesta**: o dataset tem ~1,5 h de coleta pré-jogo real. Cobertura de alvos baixa é o esperado —
os alvos T-48h…T-6h de jogos que já aconteceram só podem ser cobertos por coletas que o app ainda não
existia para fazer. Estes números só melhoram com o app rodando dias (§46–§48).

## 10. O que o dataset **não** contém (limites)

- Histórico pré-jogo anterior a 2026-09-24 17:45 UTC (não existe; não foi fabricado).
- Odds de mercados fora de escopo (combos, períodos, jogador exótico, …): o raw tem o payload completo, mas não
  há linhas normalizadas — podem ser normalizadas no futuro com um `normalizer_vN` sem tocar na v1.
- Cotações de outras casas.
- Qualquer estatística por jogador (mercados de jogador são capturados, nunca liquidados — §14).
