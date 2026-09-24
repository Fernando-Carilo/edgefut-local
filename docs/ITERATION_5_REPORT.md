# EdgeFut AI — Relatório da iteração 5: SUPERBET DATA FLYWHEEL & SPECIALIZED MARKET DISCOVERY

Data de fecho: **2026-09-24 19:11 UTC** (engine local, dados reais, `schema_version 5`).
Baseline: [ITERATION_5_BASELINE.md](ITERATION_5_BASELINE.md). Detalhe técnico:
[SUPERBET_DATASET.md](SUPERBET_DATASET.md), [MARKET_MAPPING.md](MARKET_MAPPING.md),
[DATA_FLYWHEEL.md](DATA_FLYWHEEL.md).

> Regra do spec (§72): **0 VALUE é sucesso** se o dataset for confiável, a coleta robusta, a liquidação
> robusta, o histórico de linha existir e os mercados secundários estiverem a crescer. Este relatório mede
> exatamente isso — não maquia o resto.

## 0. A resposta ao §67 — "Em que mercado vale a pena investir pesquisa?"

**`INSUFFICIENT — ainda sem mercado com evidência para decidir`** (saída literal do relatório semanal).

Motivo mensurável: **0 seleções liquidadas**. O primeiro preço pré-jogo coletado pelo Collector V2 é de
2026-09-24 17:45 UTC e o kickoff mais antigo com preço pré-jogo real é 19:00 UTC do mesmo dia; a liquidação
exige kickoff + 3 h e resultado. Todos os 15 mercados canônicos estão em maturidade `COLLECTING`, estado
`UNPROVEN`, confiança `INSUFFICIENT`, `VALUE OFF`, 0/6 regras de enablement cumpridas. As 8 hipóteses
pré-registadas estão em `CONFIRMING` desde 2026-09-24 18:56 UTC (0 testadas, 0 sobreviventes ao FDR).

O que **já** se sabe com dados reais e sem liquidação (descritivo, N mínimo 30 grupos, mercado completo no
mesmo payload):

| Mercado | Overround mediano | Faixa favorito 1,00–1,50 | Observação |
|---|---|---|---|
| MATCH_RESULT | 9,0 % | — | igual à medição da iteração 4 (9,4 %) |
| DOUBLE_CHANCE | 8,7 % | 8,69 % (113 grupos / 103 jogos) | |
| TOTAL_GOALS | 8,0–8,2 % por linha (1,5…4,5) | 7,98 % (420 / 99) | **T-24h 5,2 %, T-48h 5,0 %** — margem mais baixa longe do kickoff (130 / 66 grupos) |
| TEAM_TOTAL | 7,8–8,0 % por linha | 7,72 % (427 / 73) | T-24h 7,0 %, T-48h 6,9 % |
| CORNERS_TOTAL | 7,9–8,0 % | 7,88 % (268 / 42) | p10–p90 estreito (6,4–8,2): margem quase fixa |
| TEAM_CORNERS | 7,8 % | 7,84 % (238 / 22) | |

Isto não é edge. É a primeira leitura da **estrutura de margem** da Superbet por mercado e por tempo; a
única hipótese que estes dados sugerem (margem de totais menor a T-24h/T-48h) fica para registo **novo** no
`experiment_registry` — não se testa no mesmo dado em que se observou.

## 1. Os números obrigatórios (§71)

| # | Métrica | Valor real |
|---|---|---|
| 1 | Raw snapshots totais | **640** (457 com payload + 194 confirmações por referência; 331 do backfill honesto do cache) |
| 2 | Snapshots/dia (janela ativa) | 121 (23/09, backfill) · 519 (24/09) · coletor V2: **440 na última hora**, 90/dia projetado pela série curta |
| 3 | Eventos únicos com raw | **381** |
| 4 | Linhas normalizadas v1 | **16 095** |
| 5 | `marketId` distintos → mapeados / fora de escopo / desconhecidos | **460 → 24 / 436 / 0** (ocorrências 46 605 / 391 615 / 0) |
| 6 | Cobertura de mapeamento | **100 %** após reclassificação no boot; 99,06 % na última hora antes dela (`unknown_share` 0,72 %) |
| 7 | Cobertura de alvos (7 d) | **2,3 %** (38/1 645); por alvo: T-48h 1,9 %, T-24h 8,7 %, T-12h 0 %, T-6h 2,7 %, T-3h 1,4 %, T-2h 0 %, T-1h 0,6 %, T-30m 0 %, T-15m 0 %, T-5m 5,0 %, LAST_PRE_KO 4,9 % |
| 8 | Lacunas de coleta registadas | **0** (motor não esteve desligado > 20 min desde o boot da v5) |
| 9 | Saúde do coletor | **HEALTHY** · 1 404 pedidos/h · sucesso 100 % · 0 bloqueios · 0 429 · 0 5xx · p50 104 ms · parse 100 % · staleness 1 min · 568 eventos futuros |
| 10 | Mudanças de schema detetadas | **0** |
| 11 | Quarentena aberta | **0** |
| 12 | Seleções liquidadas (WON/LOST/VOID) | **0** — INSUFFICIENT |
| 13 | `UNSETTLED_DATA_MISSING` / `UNSUPPORTED` | 0 / 0 (nada elegível ainda) |
| 14 | Eventos com snapshots por mercado (base de liquidação futura) | MATCH_RESULT 127 (421 snaps) · DOUBLE_CHANCE 127 · DNB 120 · TOTAL_GOALS 123 (1 268 snaps) · BTTS · TEAM_TOTAL · CORNERS_TOTAL · … |
| 15 | Overround mediano por mercado | 1X2 9,0 % · DC 8,7 % · totais 8,0–8,2 % · escanteios 7,9 % · gols/equipe 7,8 % |
| 16 | CLV V2 por alvo | INSUFFICIENT (exige `LAST_PRE_KO` + alvo anterior no mesmo jogo; 9 fechamentos até agora) |
| 17 | Eficiência de preço por T (Brier da fair) | INSUFFICIENT (sem liquidação) |
| 18 | Movimentos STEAM / DRIFT / STABLE | INSUFFICIENT (linhas do tempo com ≥ 2 pontos ainda quase todas dentro da mesma hora) |
| 19 | Hipóteses pré-registadas / testadas / SUPPORTED após FDR | **8 / 0 / 0** |
| 20 | Mercados por maturidade | COLLECTING 15 · EARLY 0 · TESTABLE 0 · MATURE 0 |
| 21 | Mercados por edge state | UNPROVEN 15 · COLLECTING 0 · PROMISING 0 · VALIDATED 0 · REJECTED 0 |
| 22 | `VALUE_ENABLED` / `VALUE_ENABLEMENT_CANDIDATE` | **0 / 0** mercados |
| 23 | Staking/Kelly | **DISABLED** ("nenhum mercado com MARKET_EDGE = VALIDATED e VALUE ativo") |
| 24 | Freeze | `INTACT` · 21 modelos · `model_hash 4608dbf2fe2b87e4` · `config_hash 9846d74330b32763` |
| 25 | Armazenamento | SQLite 119,6 MB (+16 MB WAL) · raw 18,7 MB comprimidos / 297 MB originais (15,9×) · 2,7 MB/dia → 80 MB/30 d · 0,97 GB/ano · cache 129,5 MB · parquet 10,4 MB |
| 26 | Backups | 1 (`integrity ok`, schema 5, 106 MB) · retenção 7/4/3 · 1 export (641 KB) |
| 27 | Testes | **201 passed** (183 herdados + 18 novos §68) · ruff limpo nos módulos novos · typecheck/build do frontend verdes |

## 2. Critérios de aceitação (§69) e o fecho do spec

| Critério | Estado | Evidência |
|---|---|---|
| App pode rodar dias em background (Windows) | **Implementado** (validação em Windows real pendente, ver §5) | `lib.rs`: bandeja com saúde/último sync, close-to-tray, autostart opcional (`--tray`), "Sair" encerra sidecar; flags via `apply_background_settings` |
| Superbet snapshots continuam a crescer | **Sim** | 440 raw/última hora, HEALTHY; job `odds` a cada 5 min; `collector_gap` no boot |
| Dados crus nunca são sobrescritos | **Sim, por construção** | triggers SQLite `no_update/no_delete` em raw e normalizada; `UNIQUE(event_id, fetched_at)`; duplicado → quarentena; teste automatizado |
| Snapshots por alvo T-48h…T-5m + closing = último pré-KO, sem interpolação | Sim | `target_for_minutes` janelas não sobrepostas; teste; cobertura mostra os alvos em falta como falta |
| Unknown markets nunca ignorados em silêncio | Sim | 460 marketIds registados; tela *Mercados desconhecidos*; decisão manual com trilha |
| Liquidação por mercado, nunca assume loss | Sim | `UNSETTLED_DATA_MISSING` + `missing_fields`; reavaliação; auditoria + MARKET DATA COVERAGE |
| Line history / CLV V2 / STEAM-DRIFT-STABLE descritivos | Implementado, **INSUFFICIENT** em dados | Superbet Lab (Line movement, Signal vs movement) mostra "INSUFFICIENT" sem inventar |
| Experimentos pré-registados + BH-FDR obrigatório | Sim | 8 hipóteses com `confirmation_start`; `evaluate_experiments(q=0,10)` |
| VALUE_ENABLED=false, RESEARCH_SIGNAL, candidato nunca automático | Sim | `value_enabled_for` no motor; Radar/Dashboard com cartão RESEARCH SIGNAL; toggle humano auditado |
| Staking/Kelly DISABLED enquanto MARKET_EDGE ≠ VALIDATED | Sim | `/bankroll/stake` devolve DISABLED com motivo |
| Backup diário 7/4/3, restore seguro, export CSV/Parquet, storage dashboard | Sim | 1 backup real; restore recusa ficheiro ilegível e faz cópia prévia; export real |
| Quarentena em vez de descarte + UI | Sim | *Sistema → Dados → Quarentena* (0 itens hoje) |
| Schema change detection + alerta | Sim | `schema_issues` no raw; `SCHEMA_CHANGED`; 0 ocorrências |
| Relatórios diário/semanal RESEARCH ONLY | Sim | ambos gerados hoje; semanal responde `INSUFFICIENT` ao §67 |
| Modelo congelado, sem DL/boosting, 1X2 market-aware em PAUSE | Sim | `freeze INTACT`; nenhum treino nesta iteração |
| UI nunca mostra VALUE sem regras | Sim | 0 VALUE em todas as telas; faixa §3 fixa |

## 3. O que foi construído (ordem do spec)

1. **§2 Freeze** — `governance.register_freeze/freeze_status`; famílias proibidas; PAUSE 1X2.
2. **§4–§9 Collector V2** — `raw_superbet_snapshot` + `superbet_normalized_v1` (append-only, triggers),
   confirmações por referência, alvos, cobertura, saúde, gaps, quarentena, `market_mapping_registry`,
   backfill honesto do cache HTTP (331 payloads, 206 deles ofertas vazias pós-jogo mantidas como estão).
3. **§10 Canonicalização** — 15 categorias, 24 marketIds `MAPPED`, 18 motivos `OUT_OF_SCOPE` declarados,
   `UNKNOWN` explícito; reclassificação no boot só de `UNKNOWN`.
4. **§11–§13 Settlement V2** — regras por mercado, `VOID`, `UNSETTLED_DATA_MISSING`, `UNSUPPORTED`
   (jogador), auditoria e MARKET DATA COVERAGE.
5. **§15–§20 Research** — Margin Lab (faixa × T × linha), eficiência por T, CLV V2, STEAM/DRIFT/STABLE,
   lead/lag (SIGNAL VS MOVEMENT), tudo com N mínimo e bootstrap por evento.
6. **§21–§30 Discovery + experimentos** — maturidade por mercado, 8 hipóteses pré-registadas, BH-FDR.
7. **§31–§36, §63–§65 Governança** — máquina de estados por mercado, 6 regras de enablement, Required Edge
   V2, confiança por mercado, `VALUE_ENABLED` manual e auditado, staking DISABLED, `RESEARCH_SIGNAL`.
8. **§37–§45 UI** — Data Flywheel, Superbet Lab (3 abas), Pesquisa, Sistema → Dados (6 abas), faixa §3,
   cartão RESEARCH SIGNAL no Radar/Dashboard, saúde do coletor no rodapé, Configurações de background.
9. **§46–§48 Windows always-on** — bandeja, close-to-tray, autostart, "Sair" encerra sidecar, gaps.
10. **§49–§53 Storage** — backup/manifest/retenção, restore com cópia prévia, export, dashboard, quarentena.
11. **§54–§59 Qualidade** — schema change, cobertura de mapeamento, Unknown markets, identidade
    (duplicados, sem id canônico, kickoff inconsistente), `manual_correction`.
12. **§60–§62 Relatórios** — diário e semanal, `RESEARCH ONLY`, resposta ao §67.
13. **§68 Testes** — 18 novos (append-only, dedup, canonicalização, alvos/closing, gaps, quarentena, schema,
    liquidação, CLV V2, estados, freeze, backup/restore/retenção). Correção de portabilidade Windows
    (conexões sqlite3 fechadas com `contextlib.closing` — o CI Windows apanhou o bloqueio de ficheiro).

## 4. Telas (dados reais do engine local)

| | |
|---|---|
| Dashboard com a faixa §3 e contador RESEARCH SIGNAL — `i5_01_dashboard_strip.png` | Data Flywheel: 381 eventos, 598 raw, 16 065 normalizadas, 0 liquidadas, mapeamento 100 %, cobertura 2,2 %, coletor HEALTHY, crescimento por dia — `i5_02_flywheel_top.png` |
| Cobertura por alvo (barras com "0/90" reais) e lacunas (nenhuma) — `i5_03_flywheel_coverage.png` | Mercados: maturidade, overround, edge state — `i5_04_flywheel_markets.png` |
| Superbet Lab: Margin Lab por faixa/T/linha — `i5_05_superbet_lab_margin.png` | Line movement (INSUFFICIENT honesto) — `i5_06_superbet_lab_movement.png` |
| Signal vs movement — `i5_07_superbet_lab_signal.png` | Pesquisa: maturidade e hipóteses — `i5_08_pesquisa.png` |
| Estados de edge: 15 × UNPROVEN, 0 OK / 6 falhas, VALUE OFF, Required edge V2 16–18,5 pp — `i5_09_pesquisa_edge_states.png` | Sistema → Dados: Settlement audit — `i5_10_dados_liquidacao.png` |
| Mercados desconhecidos (0) — `i5_11_dados_mercados.png` · Quarentena (0) — `i5_12_dados_quarentena.png` | Armazenamento/backups/export — `i5_13_dados_armazenamento.png` · Relatório diário — `i5_14_dados_relatorio_diario.png` |
| Configurações: coletor em segundo plano, iniciar com o Windows, notificações, backup — `i5_15_configuracoes_background.png` | Radar com cartão RESEARCH SIGNAL — `i5_16_radar_research_signal.png` |

## 5. O que não foi feito / não pôde ser validado aqui — e porquê

- **Bandeja e autostart em Windows real**: o código Rust compila no CI Windows (`Build Windows installer`) e
  a lógica foi revista, mas fechar-para-bandeja, tooltip de saúde e arranque `--tray` só podem ser
  observados num desktop Windows. O ambiente desta iteração é Linux/headless; a UI foi validada no browser
  (no browser a ponte Tauri é no-op por desenho).
- **Dias de coleta**: o app existe há ~1,5 h em modo V2. Cobertura de alvos 2,3 % é a verdade e não vai
  mudar sem tempo de relógio.
- **Liquidação secundária**: depende de o provider de resultados trazer escanteios/cartões/finalizações; quando
  não trouxer, o estado será `UNSETTLED_DATA_MISSING` (visível na auditoria), nunca `LOST`.
- **Player engine** (§14): não existe; mercados de jogador são capturados para o histórico de preço e
  liquidados como `UNSUPPORTED`.
- **Nenhum modelo foi treinado, ajustado ou substituído** (§2). Nenhum threshold foi relaxado.

## 6. Riscos declarados

- **Dependência da API pública da Superbet**: uma mudança de schema pára a normalização (raw continua),
  dispara `SCHEMA_CHANGED` e o coletor fica `DEGRADED/BROKEN`. Não há fallback automático que "adivinhe"
  campos.
- **Crescimento**: ~1 GB/ano de raw comprimido + SQLite; a política de retenção só toca em backups, nunca em
  raw (§49) — o utilizador decide via export/arquivo.
- **Backfill do cache**: `fetched_at = mtime` do ficheiro. É a melhor estimativa disponível e está carimbada
  com `source_version …/cache-backfill` para poder ser excluída de qualquer análise.
- **Viés de sobrevivência do cache**: só eventos que o app consultou nas iterações 1–4 entraram no backfill.

## 7. O que muda para o utilizador

- Fechar a janela deixa de matar o coletor (com a opção ligada); a bandeja mostra saúde e último sync.
- Aparecem *Data Flywheel*, *Superbet Lab*, *Pesquisa* e *Sistema → Dados*.
- Radar/Dashboard mostram **RESEARCH SIGNAL** em vez de VALUE; staking indica DISABLED com o motivo.
- Backups diários e export em CSV/Parquet.
- Tudo o que a Superbet publica fica guardado — inclusive o que hoje não se usa.

## 8. Próximos passos honestos

1. Deixar o app a correr. Marcos automáticos: `EARLY` a 50 clusters liquidados, `TESTABLE` a 200,
   `MATURE` a 500; hipóteses só contam após 2026-10-24 (30 dias de confirmação).
2. Quando `TOTAL_GOALS`/`CORNERS_TOTAL` chegarem a `TESTABLE`, ler primeiro **Superbet fair vs resultado**
   e **CLV por T** antes de qualquer conversa sobre EdgeFut vs fair.
3. Registar (nova linha) a hipótese "margem de totais a T-24h/T-48h < margem a T-1h", observada hoje.
4. Verificar na primeira sessão Windows: close-to-tray, tooltip, `--tray`, `collector_gap` após reboot.
