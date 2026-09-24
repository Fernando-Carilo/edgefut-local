# EdgeFut AI — Fontes de dados

Princípios:

1. Nenhuma API paga.
2. Tudo é cacheado, com rate limit, retry, timeout e circuit breaker.
3. Toda estatística exibida carrega `source`, `sourceUrl`, `collectedAt`,
   `confidence`, `sampleSize`.
4. Se uma fonte bloquear automação, registramos a indisponibilidade e seguimos
   com outra fonte ou importação manual. Não contornamos proteções.
5. Todo insumo tem um **status de frescor** (FRESH / AGING / STALE / EXPIRED)
   calculado pela política do seu tipo (`domain/freshness.py`). Um insumo
   EXPIRED nunca alimenta uma recomendação em silêncio.
6. Quando duas fontes discordam sobre o mesmo jogo, a divergência é gravada
   (`source_conflict`) com a regra que a resolveu, e fica visível na UI.
7. Cada dataset tem um **nível de evidência**: com odds históricas reais o
   modelo pode ser confrontado com o mercado (`BACKTEST_ODDS`); sem odds só há
   evidência probabilística (`MODEL_ONLY`).
8. Em competição `MODEL_ONLY`, **toda** seleção é `MODEL_ONLY` mesmo que a
   Superbet ofereça odd: edge sobre um preço que nunca foi validado é hipótese,
   não valor. Nunca vira VALUE, VALUE CANDIDATE ou watchlist de preço.
9. Toda leitura de histórico passa pela `TemporalFeatureStore` com `as_of`:
   nada posterior à data da análise (ou da janela de replay) é lido.

## 1. Superbet (odds e eventos) — `providers/superbet`

| Item | Valor |
|---|---|
| Tipo | API pública JSON de oferta (a mesma consumida pelo site) |
| Base | `https://production-superbet-offer-br.freetls.fastly.net/v2/pt-BR` |
| Eventos por data | `GET /events/by-date?offerState=prematch&startDate=…&endDate=…&sportId=5` |
| Eventos em andamento | `GET /events/by-date?offerState=live&…` (só `metadata.status == "STARTED"`) |
| Evento completo (odds) | `GET /events/{eventId}` |
| Estrutura competições | `GET /sport/5/tournaments` |
| Autenticação | nenhuma |
| Frequência | eventos: 15 min · odds (jogos < 48h): 5 min · ao vivo: `live_poll_seconds` (mín. 20 s) · closing line: 10 min |
| Cache TTL | eventos 10 min · odds 4 min · torneios 24h · ao vivo `poll − 5 s` |
| Frescor | `odds`: FRESH ≤ 10 min · AGING ≤ 45 min · STALE ≤ 6 h · depois EXPIRED. `odds_live`: 45 s / 2 min / 10 min. `events`: 20 min / 1 h / 6 h |
| Evidência | eventos e odds atuais são reais; **não** há odds históricas da Superbet além dos snapshots que o próprio app grava |
| Evidência acumulada (it. 4) | os snapshots do próprio app formam a **SUPERBET SHADOW VALIDATION** (`validation/superbet.py`): 37.900 snapshots pré-kickoff · 239 eventos em 26 h (2026-09-24); overround mediano 1X2 9,4 %; buckets T-15m…>24h só quando há coleta; closing em 147 eventos. Ver `docs/SUPERBET_VALIDATION.md` |
| Link externo | `https://superbet.bet.br/apostas/futebol/…/{eventId}` (deep link best-effort) |

A página HTML da Superbet é protegida por Cloudflare (403 para clientes não
navegador). **Não** fazemos scraping da página nem usamos Playwright contra a
Superbet. Se o endpoint JSON passar a responder 403/429, o provider registra
`SUPERBET_UNAVAILABLE` e o app opera com os últimos dados em SQLite.

Cada `odds_snapshot` guarda o preço bruto; a análise calcula as probabilidades
justas pelos dois métodos (Shin e multiplicativa) e grava ambas em cada seleção. A closing line é a última odd observada antes
do kickoff (`closing_line`) e serve só para CLV.

### Ao vivo (modo observação) — `collectors/live.py`

O mesmo endpoint, com `offerState=live`, lista os jogos em andamento. O coletor
`live_poll` roda a cada `live_poll_seconds`, com backoff exponencial até 600 s
quando a fonte falha e respeito ao circuit breaker. Do payload usamos apenas o
que a fonte expõe:

| Campo | Origem no payload | Quando ausente |
|---|---|---|
| Placar | `metadata.homeTeamScore / awayTeamScore` | mostra "—" |
| Minuto / período | `metadata.minutes`, `periodStatus`, `stoppageTime` | mostra só o status |
| Escanteios / cartões | `metadata.homeTeamCorners…`, `…YellowCards`, `…RedCards` | **não** aparecem — nunca são estimados |
| Odds ao vivo | `GET /events/{id}` do jogo acompanhado | movimento em relação ao poll anterior |

A tela Ao Vivo exibe "Odds atualizadas há N s" (idade real desde `updated_at`)
e um aviso **OBSERVATION ONLY**: nenhuma recomendação, edge ou probabilidade de
modelo é calculada durante o jogo. O que é gravado no evento
(`live_status`, `live_minute`, placar, `live_collected_at`) serve para
liquidação e para o Diagnóstico — não para apostas.

### Mapeamento de mercados (marketId Superbet → mercado EdgeFut)

| marketId | Nome Superbet | Mercado EdgeFut | Observação |
|---|---|---|---|
| 547 | Resultado Final | `1X2` | seleções 1 / X / 2 |
| 531 | Dupla Chance | `DOUBLE_CHANCE` | 1X / X2 / 12 |
| 555 | Empate Anula Aposta | `DRAW_NO_BET` | |
| 200734 | Total de Gols | `TOTAL_GOALS` | linha em `specifiers.total` |
| 539 | Ambas as Equipes Marcam | `BTTS` | |
| 200736 | Handicap | `HANDICAP` | linha em `specialBetValue` |
| 530 | Handicap Asiático | `ASIAN_HANDICAP` | |
| 704 | Total de Escanteios | `TOTAL_CORNERS` | |
| 690 | Total de Cartões | `TOTAL_CARDS` | |
| 233527 | Total de Chutes na Trave | — | **não** é finalização; ignorado |
| 233475 | Jogador – Marcar Gol | `PLAYER_TO_SCORE` | |
| 538 | 1º Gol | `FIRST_GOAL` | |
| 200741 | Resultado Correto | `CORRECT_SCORE` | |
| 231194 | Combinações (bet builder) | — | ignorado |

Mercados de finalizações (chutes / chutes no alvo) **não estavam disponíveis**
nos eventos observados; o EdgeFut nunca inventa um mercado ausente. Quando a
Superbet oferecer, o mapeamento entra aqui.

Odds com `status != active` são descartadas.

## 2. football-data.co.uk (histórico de ligas) — `providers/historical/football_data.py`

> **Classificação de evidência (it. 4): RESEARCH MARKET BENCHMARK.** As odds
> `B365*`/`Avg*` são coletadas pelo football-data em instante não registrado
> por partida e não são da Superbet; `PSC*` (Pinnacle closing) só serve para
> CLV e é proibida como feature. Nada derivado destes arquivos prova
> executabilidade — isso é papel da SUPERBET SHADOW VALIDATION acima.

| Item | Valor |
|---|---|
| Tipo | CSV público por temporada |
| URL | `https://www.football-data.co.uk/mmz4281/{season}/{div}.csv` |
| Cobertura | Inglaterra (E0–E3), Espanha (SP1, SP2), Itália (I1, I2), Alemanha (D1, D2), França (F1, F2), Holanda (N1), Portugal (P1), Bélgica (B1), Turquia (T1), Escócia (SC0), Grécia (G1) |
| Campos usados | Date, HomeTeam, AwayTeam, FTHG, FTAG, HTHG, HTAG, HS, AS, HST, AST, HC, AC, HY, AY, HR, AR, HF, AF, Referee, B365H/D/A, Avg>2.5, Avg<2.5, PSCH/PSCD/PSCA (closing) |
| Frequência | diário |
| Frescor | `history`/`stats`: FRESH ≤ 36 h · AGING ≤ 4 d · STALE ≤ 12 d · depois EXPIRED |
| Evidência | **`BACKTEST_ODDS`** — traz odds pré-fechamento e de fechamento reais, então o modelo pode ser confrontado com o mercado (Lab, CLV, **Historical Replay**) |
| Uso | força das equipes (v1 e **v2 opponent-adjusted**), Poisson/Dixon-Coles/Bivariate, corners, cards, shots, ELO de clubes, **backtest e replay com odds reais**, pesos do ensemble por competição, walk-forward do half-life |
| Como baseline de mercado | as odds médias pré-jogo (`odds_h/d/a`, `odds_o25/u25`) com margem removida são o baseline **A · mercado** do replay. É um **proxy**: não são odds da Superbet. Resultado 2022–2026, 11 ligas: o mercado bate todos os modelos em 1X2 e OU 2,5 (ver `ITERATION_3_REPORT.md`) |

Formato "new" (`https://www.football-data.co.uk/new/{country}.csv` — BRA, ARG,
MEX, USA, …) contém apenas placar e odds; usado para gols/1X2, sem corners/cards.

### Mapeamento competição Superbet → football-data

| tournamentId | Nome Superbet | Código |
|---|---|---|
| 106 | Premier League | E0 |
| 27 | Championship | E1 |
| 98 | LaLiga | SP1 |
| 191 | LaLiga 2 | SP2 |
| 104 | Série A (Itália) | I1 |
| 244 | Série B (Itália) | I2 |
| 245 | Bundesliga | D1 |
| 50 | Bundesliga 2 | D2 |
| 100 | Ligue 1 | F1 |
| 143 | Ligue 2 | F2 |
| 256 | Eredivisie | N1 |
| 142 | Primeira Liga | P1 |
| 324 | Pro League (Bélgica) | B1 |
| 323 | Super Lig | T1 |
| 4 | Premiership (Escócia) | SC0 |
| 1698 | Brasileiro – Série A | new/BRA |
| 897 | MLS | new/USA |

## 3. International results (seleções) — `providers/historical/international_results.py`

| Item | Valor |
|---|---|
| Tipo | CSV público (CC0) |
| URL | `https://raw.githubusercontent.com/martj42/international_results/master/results.csv` |
| Campos | date, home_team, away_team, home_score, away_score, tournament, city, country, **neutral** |
| Cobertura | todos os jogos de seleções masculinas desde 1872 |
| Uso | ELO de seleções, forma, H2H, **detecção de campo neutro**, settlement de resultados |
| Frescor | mesma política de `history`; `results` (para liquidação): FRESH ≤ 6 h · AGING ≤ 1 d · STALE ≤ 3 d |
| Evidência | **`MODEL_ONLY`** — não há odds históricas; o modelo nunca foi confrontado com o mercado nesse dataset. Todo jogo de seleções carrega esse rótulo (e todas as suas seleções ficam no estado `MODEL_ONLY`) até existirem ≥ 30 previsões liquidadas na competição (`SETTLED`) |
| Tipo de torneio | `tournament_type(tournament)` → FRIENDLY / QUALIFIER / TOURNAMENT / NATIONS_LEAGUE / CONTINENTAL / OTHER; amistosos pesam 0,6 no `international-strength-v1`; o replay de seleções reporta Brier por tipo (N 15.956 desde 2010; lift vs ingênuo de +12 % a +25 %) |
| Limitações | sem escanteios, cartões ou finalizações → mercados de corners/cards/shots ficam `LOW_DATA` para seleções; sem odds → nunca há VALUE em seleções até o shadow mode acumular preço da Superbet liquidado |

### Conflitos entre fontes — `quality/conflicts.py`

O mesmo jogo pode aparecer na Superbet e num dataset histórico com horário,
nomes, mando ou placar diferentes. O `CanonicalEventResolver` casa os dois pelo
par de times canônicos + janela de horário, e o Source Conflict Engine grava
cada divergência em `source_conflict` (campo, valor A, valor B, fonte
vencedora, regra). Regras usadas:

| Regra | Quando |
|---|---|
| `user_override` | o usuário definiu manualmente (ex.: mando) |
| `dataset_flag` | flag explícita do dataset (ex.: `neutral` do international_results) |
| `prefer_curated_history` | placar/resultado: o dataset curado prevalece sobre o feed da casa |
| `prefer_superbet` | horário/odds: o feed da Superbet prevalece por ser mais recente |
| `listing_kept_unconfirmed` | mando: a listagem é mantida, mas marcado `UNCONFIRMED` |
| `tolerance_window` | diferença de horário dentro da tolerância |
| `canonical_teams` | nomes distintos resolvidos por alias para o mesmo time |

Na página do jogo aparece "N divergências de dados resolvidas"; clicar abre
cada conflito com os dois valores e a regra aplicada. Nada é resolvido em
silêncio.

## 4. Venue / campo neutro — `features/venue.py`

Ordem de resolução:

1. Override manual do usuário (`PATCH /events/{id}/venue`).
2. Clube em liga nacional (competição mapeada): mandante confirmado por
   convenção da liga — `confidence 0.9`.
3. Seleções: consulta `international_results` para o mesmo par/data quando o
   jogo já ocorreu (settlement) — para jogos futuros, o venue fica
   `UNCONFIRMED`. O modelo aplica **metade** da vantagem de mandante e o
   Confidence Engine penaliza.
4. Competições internacionais de clubes em fase final (finais em sede única)
   são marcadas `UNCONFIRMED`.

O sistema nunca assume "o primeiro time joga em casa" sem registrar o grau de
confiança dessa afirmação.

## 5. Normalização de nomes — `normalization/teams.py`

- Remoção de acentos, sufixos `(F)`, `(Sub-21)`, `FC`, `SC`, etc.
- Dicionário PT-BR → EN para seleções (`Japão → Japan`, `Uruguai → Uruguay`).
- Aliases para clubes no formato football-data (`Manchester City → Man City`).
- Fallback fuzzy (`difflib`) com limiar 0.88; abaixo disso, `UNMATCHED` e o
  pipeline devolve `LOW_DATA` em vez de casar errado.

## 6. Fontes ainda não integradas (roadmap)

- Estatísticas de jogadores (minutos, xG, chutes) — apenas quando houver
  fonte pública permitida; até então o Player Engine fica desativado com aviso.
- Árbitros por partida futura — football-data traz o árbitro só para jogos
  passados; não inventamos árbitro para jogos futuros.
- Escalações confirmadas — sem fonte; o aviso `LINEUP_UNCERTAINTY` é sempre
  exibido para mercados de jogador.

### Player Engine — arquitetura pronta, sem provider (`providers/player/`)

Existe apenas o contrato: `PlayerProvider` (abstrato), `PlayerStats`
(por 90 min, observadas — nunca estimadas), `Availability`
(`AVAILABLE / DOUBTFUL / INJURED / SUSPENDED / UNKNOWN`) e um registro cuja
propriedade `available` é `False`. Enquanto for `False`, todo mercado de jogador
recebe `NO BET · LINEUP_UNCERTAINTY` e a UI mostra **PLAYER DATA UNAVAILABLE**.
Um provider real pode ser plugado sem mexer no pipeline; o Diagnóstico lista o
componente "Jogadores / escalações" como indisponível por design.

## 6a. Coverage Map (`GET /validation/coverage`, Validação → Coverage Map)

Por dataset: competição(ões) Superbet mapeadas, N de partidas, primeira e
última data, % com resultado, % com odds, % com finalizações, escanteios,
cartões, % com dados de jogadores (sempre 0 — sem fonte permitida), qualidade
da amostra (INSUFFICIENT / EARLY / MODERATE / STRONG) e **`value_capable`**
(só datasets com odds históricas podem, em princípio, gerar VALUE). Hoje: 11
ligas europeias `value_capable` (E0, SP1, F1, N1, D1, SC0, I1, P1, B1, T1
STRONG; SP2 MODERATE), USA/MLS e INTL sem odds → nunca VALUE.

## 6b. Fontes V2 — o que a tela "Fontes" mostra

Cada fonte tem um card gerado por `quality/health.py::source_cards`:

- status (HEALTHY / DEGRADED / STALE / UNAVAILABLE) e frescor da última coleta;
- `last_ok`, latência média, taxa de erro e requisições nas últimas 24 h (de
  `source_log`);
- **Fornece / Não fornece** — lista explícita do que a fonte entrega (ex.:
  Superbet: eventos, odds por mercado, status, placar final; football-data:
  resultados, finalizações, escanteios, cartões, odds B365) e do que não
  entrega (ex.: international_results: escanteios, cartões, odds).

Abaixo dos cards, a tabela de datasets traz, por competição, o tamanho, a
última atualização e a coluna **Odds / evidência** (`BACKTEST_ODDS` ou
`MODEL_ONLY`).

## 7. Cache e limites

| Recurso | TTL cache | Rate limit |
|---|---|---|
| Superbet eventos | 10 min | 1 req / 2 s |
| Superbet evento (odds) | 4 min | 1 req / 2 s |
| football-data CSV | 24 h | 1 req / 3 s |
| international_results | 24 h | 1 req / 10 s |

Diretórios: `data/raw` (CSVs originais), `data/processed` (Parquet),
`data/cache` (HTTP JSON).
