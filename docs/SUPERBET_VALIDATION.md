# EdgeFut AI — Validação Superbet (SUPERBET SHADOW VALIDATION)

Esta é a **única** camada de evidência sobre executabilidade: odds coletadas
pelo próprio app na Superbet, com carimbo de tempo real, sem apostar, sem
login, sem contornar bloqueios. Tudo que vem do football-data é o **RESEARCH
MARKET BENCHMARK** e fica em `docs/MARKET_AWARE_MODELS.md`; os dois nunca se
misturam num mesmo número.

Código: `engine/edgefut/validation/superbet.py` (`SuperbetEvidenceEngine`),
`odds/closing.py` + backfill em `superbet.py`, `validation/shadow.py`
(shadow v2), `db/migrations.py` v4. Rotas: `GET /validation/superbet`
(último relatório persistido; `?live=true` recalcula),
`GET /validation/superbet/selection?event_id&market_key&selection_key[&line]`
(timeline de uma seleção), `GET /validation/shadow`. UI: Validação →
Superbet, Validação → Shadow, Performance → Superbet Lab e Match page →
MARKET vs EDGEFUT.

Dados desta página: relatório `superbet-shadow-v1` gerado em
**2026-09-24 15:48 UTC**, coleta desde 2026-09-23 13:18 UTC (~26 h).
Amostra **pequena** por construção; os números são reportados como pequenos.

## 1. Camadas de dados (§34–35)

| Camada | Tabela / arquivo | Regra |
|---|---|---|
| raw, append-only | `odds_snapshot` (odd, `collected_at`, evento, mercado, seleção, linha) | nunca reescrito; ao vivo (pós-kickoff) marcado e **excluído** da evidência pré-jogo |
| raw | `closing_line` (última odd antes do kickoff por seleção) | preenchido pelo job `closing_lines`; a partir da iteração 4, **backfill** a partir de snapshots existentes para eventos já iniciados |
| processed, versionado | `superbet-shadow-v1` (`shadow_report` persistido + `dataset_version`) | recalculado por job diário `shadow_report`; cada relatório carrega `generated_at` |
| shadow | `shadow_prediction` (+ v4: `hybrid_prob`, `residual_edge_pp`, `required_edge_pp`, `adjusted_edge_pp`, `minutes_to_kickoff`, `closing_odd` na liquidação) | previsão congelada em T; liquidação anexa resultado **e** closing Superbet |

Achado corrigido da auditoria (`ITERATION_4_BASELINE.md`, Achado 1):
`closing_odd` estava `NULL` em 100 % das linhas shadow; agora o `reconcile`
anexa o closing e o backfill recupera closings de eventos passados a partir
dos snapshots (7.952 linhas, 147 eventos hoje).

## 2. Cobertura real

| Métrica | Valor |
|---|---|
| Snapshots pré-kickoff | **37.900** (59.987 ao vivo excluídos) |
| Eventos / seleções | 239 / 16.265 |
| Snapshots por seleção (mediana) | 2 |
| Cadência mediana entre snapshots | 46 min (job `odds` a cada 5 min, mas suspensões da VM interrompem; registrado, não maquiado) |
| Closing capturado | 147 eventos · 7.952 seleções |
| Mercados com mais snapshots | CORRECT_SCORE 11.939 · TOTAL_GOALS 3.905 · ASIAN_HANDICAP 3.539 · HANDICAP 2.918 · TEAM_TOTAL_HOME/AWAY 2.549/2.503 · 1X2 1.852 · BTTS 1.083 |

### Buckets de tempo até o kickoff (§8)

Buckets só existem quando houve coleta real naquele instante
(`collected_at − kickoff_utc`); nada é interpolado.

| Bucket | Eventos | Snapshots |
|---|---|---|
| T-15m | 56 | 196 |
| T-30m | 54 | 188 |
| T-1h | 69 | 258 |
| T-3h | 71 | 325 |
| T-6h | 39 | 226 |
| T-12h | 9 | 59 |
| T-24h | 50 | 219 |
| > 24h | 62 | 381 |

## 3. Overround da Superbet (§10)

Overround = Σ(1/odd) / book_total − 1, no **mesmo snapshot**, só para
mercados completos (todas as seleções presentes). `book_total` = 1 para
mercados de vencedor único e **2 para dupla chance** (cada resultado aparece
em duas seleções; sem isso a margem saía como ~118 %). Mínimo de 20 grupos
completos por linha da tabela.

| Mercado | Eventos | Mediana | P10–P90 |
|---|---|---|---|
| TOTAL_GOALS | 231 | 8,52 % | 4,8–11,1 |
| ASIAN_HANDICAP | 121 | 9,94 % | 8,1–11,2 |
| HANDICAP | 125 | 8,16 % | 7,0–11,1 |
| TEAM_TOTAL_HOME | 128 | 8,08 % | 6,7–11,1 |
| TEAM_TOTAL_AWAY | 128 | 7,98 % | 5,3–11,1 |
| TOTAL_CORNERS | 125 | 7,88 % | 6,6–8,2 |
| DRAW_NO_BET | 220 | 10,97 % | 10,3–11,3 |
| BTTS | 229 | 7,95 % | 7,1–8,2 |
| 1X2 | 237 | **9,39 %** | 5,4–11,2 |
| DOUBLE_CHANCE | 220 | 9,03 % | 5,2–10,9 |
| TOTAL_CARDS | 37 | 9,82 % | 8,3–11,5 |

Por linha: OU 2,5 8,34 % · OU 3,5 8,75 % · OU 1,5 9,68 %. Por competição
(≥ 20 grupos): Amistoso Internacional 6,58 % · UEFA Europa Cup (F) 11,17 % ·
FA League Cup (F) 11,83 % · Gaúcho A2 10,96 %. Por faixa de odd do favorito
(1X2): 1,00–1,50 → 9,65 % · 1,50–2,00 → 9,16 % · 2,00–3,00 → 9,29 %. Por
tempo até o kickoff (1X2): > 24h 8,35 % · T-24h 9,66 % · T-6h 8,81 % · T-3h
9,87 % · T-1h 10,42 % · T-30m 9,83 % · T-15m **10,73 %** — a margem do 1X2
**cresce** perto do kickoff nesta amostra.

Consequência direta: a margem entra no `required_edge` de cada seleção
(componente `margin`, teto 15 %). Um edge bruto de +5 pp num 1X2 com 9,4 % de
margem não cobre nem a margem.

## 4. Opening / recomendação / closing e CLV próprio (§9, §26–28)

Para cada seleção: odd de abertura (primeiro snapshot), odd no instante da
recomendação (o snapshot ≤ T usado pelo pipeline), closing (último snapshot
pré-kickoff). **CLV = odd_recomendada / closing_Superbet − 1**. O closing
nunca entra na recomendação (teste
`test_closing_backfill_from_existing_snapshots_and_shadow_clv` + regra §6 no
lado do replay).

| CLV Superbet (shadow com closing) | N | Eventos | CLV médio | % positivo |
|---|---|---|---|---|
| Todas | 314 de 4.931 precificadas | 6 | **−1,3 %** | 1,9 % |
| 1X2 | 104 | 6 | −1,8 % | 1,9 % |
| OU | 132 | 6 | +1,3 % | 2,3 % |
| BTTS | 12 | 6 | 0,0 % | 0 % |
| Outros | 66 | 6 | −6,0 % | 1,5 % |
| Estado `MARKET_OBSERVED` | 124 | 6 | +2,2 % | 2,4 % |
| Estado `OBSERVATION` | 41 | 6 | 0,0 % | 0 % |
| Estado `NO_BET` | 63 | 6 | −6,3 % | 1,6 % |

IC 95 % por evento: **não calculável** com 6 clusters (reportado como `[—, —]`,
`conclusive=false`). Não há uma única recomendação `VALUE` no shadow, então
o "CLV das recomendações" (§28) é vazio — e é assim que aparece.

## 5. Movimento de linha (§27)

8.241 de 16.265 seleções têm ≥ 2 snapshots.

| Mercado | N | Eventos | moveu > 0,5 pp | \|Δ\| mediana | \|Δ\| P90 | Δ líquido médio | abertura (min antes, mediana) | closing (min antes) |
|---|---|---|---|---|---|---|---|---|
| 1X2 | 382 | 138 | 74 % | 0,99 pp | 3,93 pp | +0,01 pp | 356 | 44 |
| TOTAL_GOALS | 960 | 120 | 79 % | 1,13 pp | 4,12 pp | +0,04 pp | 1.113 | 152 |
| BTTS | 247 | 125 | 82 % | 1,32 pp | 5,18 pp | +0,03 pp | 716 | 44 |
| ASIAN_HANDICAP | 728 | 87 | 91 % | 1,63 pp | 4,68 pp | +0,07 pp | 1.751 | 216 |
| DOUBLE_CHANCE | 339 | 136 | 86 % | 1,18 pp | 4,28 pp | −0,02 pp | 266 | 39 |
| DRAW_NO_BET | 248 | 126 | 85 % | 1,54 pp | 5,97 pp | −0,08 pp | 476 | 53 |
| CORRECT_SCORE | 2.497 | 91 | 23 % | 0,17 pp | 0,98 pp | +0,04 pp | 1.321 | 201 |

Correlação (favorito, deriva) no 1X2 = **+0,005** — nesta amostra os
favoritos não encurtam nem alongam de forma sistemática até o kickoff.
Importante: a "abertura" aqui é a **primeira odd que o app viu**, não a odd de
abertura da casa; com ~26 h de coleta, a mediana de 356 min antes do kickoff no
1X2 diz o quanto a janela é curta.

## 6. Teste de viés da Superbet (§11)

Não se assume que a casa erra em favoritos, empates ou underdogs: compara-se a
frequência observada com a probabilidade justa média, por segmento, com IC 95 %
por evento; viés só é declarado se o IC excluir zero.

| Segmento | N | Eventos | Qualidade | Observado | Justa média | Veredicto |
|---|---|---|---|---|---|---|
| 1X2 · HOME | 123 | 123 | EARLY | 51,2 % | 45,5 % | NO BIAS DETECTED |
| 1X2 · DRAW | 123 | 123 | EARLY | 22,8 % | 23,4 % | NO BIAS DETECTED |
| 1X2 · AWAY | 123 | 123 | EARLY | 26,0 % | 31,1 % | NO BIAS DETECTED |
| 1X2 · favorito | 62 | 62 | INSUFFICIENT | 69,4 % | 67,2 % | NO BIAS DETECTED |
| 1X2 · underdog | 123 | 72 | EARLY | 13,8 % | 16,1 % | NO BIAS DETECTED |
| odd 1,00–1,50 | 105 | 68 | EARLY | 67,6 % | 69,3 % | NO BIAS DETECTED |
| odd 1,50–2,00 | 269 | 114 | EARLY | 52,4 % | 53,5 % | NO BIAS DETECTED |
| odd 2,00–3,00 | 254 | 111 | EARLY | 43,7 % | 39,3 % | NO BIAS DETECTED |
| odd 3,00–5,00 | 149 | 100 | EARLY | 22,1 % | 25,6 % | NO BIAS DETECTED |
| odd 5,00+ | 72 | 46 | INSUFFICIENT | 9,7 % | 11,8 % | NO BIAS DETECTED |

Brier da justa Superbet nos mercados liquidados: 1X2 0,1759 · TOTAL_GOALS
0,2522 · BTTS 0,2434 (n 369 / 238 / 242 seleções; 123 / 119 / 121 eventos).
Conclusão honesta: **nenhum viés detectável** com esta amostra; o desvio de
+5,7 pp em HOME é o maior, mas com 123 eventos não separa do ruído.

## 7. Superbet fair como baseline do shadow (§12, §25)

Toda previsão shadow liquidada com preço é comparada com a probabilidade justa
da Superbet no mesmo instante. Só linhas **liquidadas** entram; o híbrido
(`hybrid_prob`) só existe para linhas gravadas após a migração v4 — linhas
antigas **não** são reconstruídas.

| Família | N | N efetivo | Brier Superbet | Brier EdgeFut | Δ (EF − SB) | Vencedor |
|---|---|---|---|---|---|---|
| 1X2 | 84 | 5 | 0,1785 | 0,2200 | +0,0414 | INCONCLUSIVE |
| OU | 110 | 5 | 0,1844 | 0,1962 | +0,0117 | INCONCLUSIVE |
| BTTS | 10 | 5 | 0,2690 | 0,2121 | −0,0569 | INCONCLUSIVE |
| Outros | 40 | 5 | 0,0912 | 0,0912 | +0,0001 | INCONCLUSIVE |
| **Todas** | **244** | **5 jogos · N efetivo 10** | **0,1706** | **0,1878** | **+0,0172** | **INCONCLUSIVE** |

Ponto: a Superbet justa está **melhor** do que o EdgeFut em 1X2 e OU nas 5
partidas liquidadas — a mesma direção do replay histórico — mas 5 eventos não
autorizam conclusão em nenhum sentido.

Painel shadow por família (§25): 1X2 56 eventos · 1.554 previsões · 86
liquidadas · 0 VALUE · 174 OBSERVATION; OU 55 · 2.104 · 110 · 0 · 276; BTTS
55 · 194 · 10 · 0 · 24; Outros 55 · 1.067 · 55 · 0 · 1; Escanteios 1 · 12 · 0.
Total: **4.931 previsões · 261 liquidadas (5 jogos) · VALUE 0 · OBSERVATION 481**.

## 8. Shadow report v2 (§46–48)

`GET /validation/shadow` acrescenta `market_aware` (tabela acima, com CLV n
244 ≈ −0,1 %, veredicto `INSUFFICIENT DATA`), `confidence_validation` (Brier /
hit / ROI OOS por grau A/B/C/D — A n 42 (1 jogo), B n 162 (4 jogos), D n 40
(5 jogos); grau C ausente; só graus com N ≥ 30 entram e o N efetivo tem de
atingir o mínimo `EARLY` da governança → `INSUFFICIENT DATA`) e
`opportunity_validation` (bins fixos 0–40 n 76, 40–55 n 29, 55–70 n 126, 70+
n 13 → `INSUFFICIENT DATA`). Nenhuma nota é atribuída como validada, e nunca
se escolhe o bin com maior ROI como regra.

## 9. Performance por tempo até o kickoff (§26)

`time_to_kickoff` no relatório: **todos os buckets com N 0**. As linhas shadow
liquidadas até agora foram gravadas antes da coluna `minutes_to_kickoff`
existir (migração v4); as novas linhas já a carregam (ex.: Girona × Albacete,
51 linhas com `minutes_to_kickoff` 1.636). O painel vai preencher sozinho
conforme os jogos liquidam — nada é estimado retroativamente.

## 10. Regra de parada (§40) e o que o Superbet Lab mostra

Enquanto `SHADOW SETTLED N` efetivo for `INSUFFICIENT` (hoje 10) e o CLV não
tiver IC, nenhuma promoção da camada de value é possível
(`evaluate_market_aware_promotion` → `NO EVIDENCE OF MARKET EDGE`, ou
`CHALLENGER BEATS MARKET · CLV UNKNOWN` se algum challenger vencer o mercado no
holdout — o que não aconteceu). O Superbet Lab (Performance) e a aba Superbet
(Validação) mostram exatamente estes dados, com *Raw N* e *Effective N*, e o
botão "Recalcular agora" (`?live=true`).

## 11. Limites declarados

- 26 h de coleta; 5 jogos liquidados; 6 eventos com closing e recomendação.
- Odds ao vivo excluídas; nada de player/lineup.
- Cadência irregular (VM suspendida) — os buckets refletem o que foi coletado.
- Nenhuma automação de aposta, login ou contorno de bloqueio; se a Superbet
  bloquear, o coletor registra indisponibilidade e a evidência **para** de
  crescer (não é inventada).
