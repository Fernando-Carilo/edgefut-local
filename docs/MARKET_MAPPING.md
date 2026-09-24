# EdgeFut AI — Mapeamento de mercados Superbet (iteração 5)

Fonte de verdade: `engine/edgefut/flywheel/markets.py`. Este documento descreve **o que o código faz**; se
divergir, o código vence e o documento tem de ser corrigido. Nada aqui é inferido a partir de estatística —
todo mapeamento é uma decisão de código (revisada) ou uma decisão manual auditada em `manual_correction`.

## 1. Princípios (§10, §54–§56)

1. **Identificador canônico** por mercado: `canonical_market_id = CATEGORIA[_LADO][_LINHA]` com a linha em
   `_` no lugar do ponto (`TOTAL_GOALS_2_5`, `TEAM_CORNERS_HOME_4_5`, `MATCH_RESULT`). Seleções canônicas:
   `HOME/DRAW/AWAY`, `HOME_DRAW/DRAW_AWAY/HOME_AWAY`, `OVER/UNDER`, `YES/NO`, `player:<id>` ou
   `player:<nome normalizado>` (+ `:OVER` em mercados de jogador com linha).
2. **Três estados** para cada `marketId` visto na oferta: `MAPPED` (tem `MarketSpec`), `OUT_OF_SCOPE` (nome
   casa com um padrão declarado, com **motivo**) ou `UNKNOWN`. `UNKNOWN` **nunca é ignorado em silêncio**:
   fica no `market_mapping_registry` com nome, ocorrências, jogos, seleções de exemplo e specifiers, e aparece
   em *Sistema → Dados → Mercados desconhecidos*.
3. **Ambíguo não se mapeia.** Pela UI só é possível marcar `OUT_OF_SCOPE` / `AMBIGUOUS` / reabrir `UNKNOWN`
   (com motivo ≥ 5 caracteres, gravado em `manual_correction`). Mapear para uma categoria exige alterar
   `MAPPED` em código, com teste.
4. **Decisão manual vence a automática**: `_upsert_registry` nunca sobrescreve uma linha `MAPPED`; a
   reclassificação no boot (`reclassify_unknown`) só toca linhas `UNKNOWN`.
5. **Odds bloqueadas** (`price ≤ 1` com `status != active`) não são odds nem erros: são ignoradas. `price ≤ 1`
   com `status = active` vai para quarentena (`ODD_LE_1`).

## 2. Categorias canônicas (15)

| Categoria | Família de pesquisa (§21) | `kind` | `complete_n` (fair/overround) |
|---|---|---|---|
| `MATCH_RESULT` | 1X2 | 3WAY | 3 |
| `DOUBLE_CHANCE` | 1X2 | DC | 3 (book total 2) |
| `DNB` | 1X2 | 2WAY_TEAM | 2 |
| `TOTAL_GOALS` | OU | OU | 2 |
| `BTTS` | BTTS | YES_NO | 2 |
| `TEAM_TOTAL` | TEAM_TOTAL | TEAM_OU | 2 |
| `CORNERS_TOTAL` | CORNERS | OU | 2 |
| `TEAM_CORNERS` | CORNERS | TEAM_OU | 2 |
| `CARDS_TOTAL` | CARDS | OU | 2 |
| `TEAM_CARDS` | CARDS | TEAM_OU | 2 |
| `SHOTS` | SHOTS | OU / TEAM_OU | 2 |
| `SHOTS_ON_TARGET` | SHOTS | OU / TEAM_OU | 2 |
| `PLAYER_GOAL` | PLAYER | PLAYER_YES | — (capturado, **não liquidado**, §14) |
| `PLAYER_SHOTS` | PLAYER | PLAYER_OU | — |
| `PLAYER_SOT` | PLAYER | PLAYER_OU | — |

`fair_prob` e `overround` só são calculados quando **todas** as seleções do mercado canônico estão no mesmo
payload (mesmo instante) — mercado incompleto fica com `fair_prob = NULL`.

## 3. `MAPPED` — marketId Superbet → spec

| marketId | Nome Superbet observado | Categoria | kind | lado (fallback) |
|---|---|---|---|---|
| 547 | Resultado Final | MATCH_RESULT | 3WAY | — |
| 531 | Dupla Chance | DOUBLE_CHANCE | DC | — |
| 555 | Empate Anula Aposta | DNB | 2WAY_TEAM | — |
| 200734 | Total de Gols | TOTAL_GOALS | OU | — |
| 539 | Ambas as Equipes Marcam | BTTS | YES_NO | — |
| 544 | *Equipe* – Total de Gols | TEAM_TOTAL | TEAM_OU | HOME |
| 535 | *Equipe* – Total de Gols | TEAM_TOTAL | TEAM_OU | AWAY |
| 704 | Total de Escanteios | CORNERS_TOTAL | OU | — |
| 713 | *Equipe* – Total de Escanteios | TEAM_CORNERS | TEAM_OU | HOME |
| 733 | *Equipe* – Total de Escanteios | TEAM_CORNERS | TEAM_OU | AWAY |
| 690 | Total de Cartões | CARDS_TOTAL | OU | — |
| 700 | *Equipe* – Total de Cartões | TEAM_CARDS | TEAM_OU | HOME |
| 708 | *Equipe* – Total de Cartões | TEAM_CARDS | TEAM_OU | AWAY |
| 201590 | Total de Finalizações | SHOTS | OU | — |
| 201591 / 201592 | *Equipe* – Total de Finalizações | SHOTS | TEAM_OU | HOME / AWAY |
| 200702 | Total de Finalizações no Alvo | SHOTS_ON_TARGET | OU | — |
| 200703 / 200704 | *Equipe* – Finalizações no Alvo | SHOTS_ON_TARGET | TEAM_OU | HOME / AWAY |
| 233475, 236226 | Jogador – Marcar Gol | PLAYER_GOAL | PLAYER_YES | — |
| 236218, 232338 | Jogador – Finalizações | PLAYER_SHOTS | PLAYER_OU | — |
| 236220 | Jogador – Finalizações no Alvo | PLAYER_SOT | PLAYER_OU | — |

**Lado da equipe (`TEAM_OU`)**: primeiro pelo nome do mercado (`"Al Ahly - Total de Gols"` → casa se o nome
da equipe da casa aparece e o da visitante não; `side_source = NAME`); se o nome não decide, usa o lado
padrão do `marketId` (`side_source = ID`); se ambos os nomes aparecem e não há fallback →
`MARKET_MAPPING_CONFLICT` (quarentena). Linha: `specifiers.total|hcp|handicap`, senão o primeiro número do
nome (`"Mais de 9,5"` → 9.5).

**Identidade de jogador**: `specifiers.player_id` → `player:<id>` (`identity_confidence = STRONG`);
só nome → `player:<nome normalizado>` (`NAME_ONLY`); nenhum → `PLAYER_IDENTITY_MISSING` (quarentena).
Mercados de jogador são **capturados** para a evidência de preço, mas `settle_canonical` devolve
`UNSUPPORTED` (não há engine de jogador nesta iteração, §14).

## 4. `OUT_OF_SCOPE` — padrões por nome (ordem importa; o primeiro que casa ganha)

| Motivo | Padrão (regex, case-insensitive) | Exemplos reais |
|---|---|---|
| `COMBO_MARKET` | `;` · `super odds` · `&`, `… ou … vence`, `resultado final e/ou`, `vencer ou ambas` | bet-builder (`"Peñarol - Vencer; Mais de 1.5"`), "Super Odds" |
| `PERIOD_MARKET` | `1º/2º tempo`, `intervalo`, `em cada tempo`, `nos dois tempos` | "Resultado Final 1º Tempo" |
| `TIME_WINDOW` | `primeiros N`, `próximos N`, `próximo minuto`, `de mm:ss a` | "Gol nos primeiros 10 minutos" |
| `CORRECT_SCORE` | `resultado/placar correto`, `múltiplos placares` | 200741 |
| `HANDICAP` | `handicap` | 200736, 530 (asiático) |
| `EXACT_OR_RANGE` | `número exato`, `faixa de`, `ímpar/par`, `asiático` | "Total de Gols Asiático" (529) |
| `SEQUENCE_MARKET` | `Nº gol/escanteio/cartão…`, `corrida`, `último escanteio`, `método do gol` | 538 "1º Gol" |
| `TEAM_COMPARISON` | `equipe com mais…`, `(1X2)` em stats, `cada equipe` | "Escanteios (1X2)" |
| `RED_CARDS_OR_WOODWORK` | `vermelh`, `trave` | 233527 "Chutes na Trave" |
| `FOULS_OFFSIDES` | `faltas`, `impedimento` | |
| `OTHER_STAT` | `pênaltis`, `arremessos laterais`, `tiros de meta`, `último gol`, `de fora da área`, `cada canto` | 230900–230902 "Arremessos Laterais" |
| `REFEREE_VAR` | `\bVAR\b`, `árbitro` | 237082/237083/240046 |
| `TEAM_TO_SCORE` | `- marcar gol$`, `marcar gols consecutivos` | 2528/2530 "Noruega - Marcar Gol" |
| `WIN_VARIANT` | `vencer de virada`, `ficar à frente`, `vencer a partida`, `- vence$`, `qualquer equipe vence`, `vence o restante`, `se classificar`, `vencer ou qualquer equipe` | 1135–1137, 536, 936, 232513–232516, 231009/231011 |
| `PLAYER_EXOTIC` | `jogador`/`duelo`/`assistência`/`desarme`/`cabeça`… | mercados de jogador fora das 3 categorias |
| `BTTS_VARIANT` / `DC_VARIANT` / `TOTAL_VARIANT` | variantes de BTTS, dupla chance e total | "Ambas Marcam 2+", "Total de Gols Asiático" |

Os padrões existem porque foram **observados** na oferta (baseline §3 e produção). Um nome novo que não case
com nada continua `UNKNOWN` até decisão.

## 5. Números reais (2026-09-24, após 1 h de Collector V2 + backfill do cache)

- `marketId` distintos vistos: **460** → `MAPPED` 24 · `OUT_OF_SCOPE` 436 · `UNKNOWN` 0.
- Cobertura por **ocorrência** (odds): `MAPPED` 24 811 (**98,9 %** do que não é out-of-scope) · `UNKNOWN`
  2 532 (1,05 %) antes da reclassificação; após os padrões `REFEREE_VAR`/`TEAM_TO_SCORE`/`WIN_VARIANT` e a
  correção de `arremessos laterais` (plural), **100 % / 0 %**.
- Maiores volumes fora de escopo: `PERIOD_MARKET` 117 987 · `COMBO_MARKET` 74 653 · `PLAYER_EXOTIC` 45 284 ·
  `CORRECT_SCORE` 14 973 · `SEQUENCE_MARKET` 11 475 · `EXACT_OR_RANGE` 11 468.
- Um evento típico da Superbet publica ~1 500 odds; ~90 % são combos, períodos, jogadores e sequências. Isso
  é a oferta real, não um defeito do mapeamento.

## 6. Como acrescentar um mercado

1. Confirmar na tela *Mercados desconhecidos* (nome, seleções de exemplo, specifiers, nº de jogos).
2. Se pertence a uma das 15 categorias: adicionar `MAPPED[<marketId>] = MarketSpec(...)` e um caso em
   `tests/test_flywheel_collector.py::test_canonicalization_*`. Se exige categoria nova, isso é decisão de
   iteração (afeta liquidação, discovery e UI) — não se faz "por conveniência".
3. Se não pertence: adicionar um padrão com **motivo** em `OUT_OF_SCOPE_PATTERNS`, ou marcar manualmente pela
   UI (a decisão fica em `manual_correction`).
4. Reiniciar o engine: `reclassify_unknown` aplica o código novo às linhas ainda `UNKNOWN`. Linhas raw e
   normalizadas antigas **não** são reescritas (append-only): a cobertura histórica fica como estava.
