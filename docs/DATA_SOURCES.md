# EdgeFut AI — Fontes de dados

Princípios:

1. Nenhuma API paga.
2. Tudo é cacheado, com rate limit, retry, timeout e circuit breaker.
3. Toda estatística exibida carrega `source`, `sourceUrl`, `collectedAt`,
   `confidence`, `sampleSize`.
4. Se uma fonte bloquear automação, registramos a indisponibilidade e seguimos
   com outra fonte ou importação manual. Não contornamos proteções.

## 1. Superbet (odds e eventos) — `providers/superbet`

| Item | Valor |
|---|---|
| Tipo | API pública JSON de oferta (a mesma consumida pelo site) |
| Base | `https://production-superbet-offer-br.freetls.fastly.net/v2/pt-BR` |
| Eventos por data | `GET /events/by-date?offerState=prematch&startDate=…&endDate=…&sportId=5` |
| Evento completo (odds) | `GET /events/{eventId}` |
| Estrutura competições | `GET /sport/5/tournaments` |
| Autenticação | nenhuma |
| Frequência | eventos: 15 min · odds (jogos < 48h): 5 min |
| Cache TTL | eventos 10 min · odds 4 min · torneios 24h |
| Link externo | `https://superbet.bet.br/apostas/futebol/…/{eventId}` (deep link best-effort) |

A página HTML da Superbet é protegida por Cloudflare (403 para clientes não
navegador). **Não** fazemos scraping da página nem usamos Playwright contra a
Superbet. Se o endpoint JSON passar a responder 403/429, o provider registra
`SUPERBET_UNAVAILABLE` e o app opera com os últimos dados em SQLite.

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

| Item | Valor |
|---|---|
| Tipo | CSV público por temporada |
| URL | `https://www.football-data.co.uk/mmz4281/{season}/{div}.csv` |
| Cobertura | Inglaterra (E0–E3), Espanha (SP1, SP2), Itália (I1, I2), Alemanha (D1, D2), França (F1, F2), Holanda (N1), Portugal (P1), Bélgica (B1), Turquia (T1), Escócia (SC0), Grécia (G1) |
| Campos usados | Date, HomeTeam, AwayTeam, FTHG, FTAG, HTHG, HTAG, HS, AS, HST, AST, HC, AC, HY, AY, HR, AR, HF, AF, Referee, B365H/D/A, Avg>2.5, Avg<2.5, PSCH/PSCD/PSCA (closing) |
| Frequência | diário |
| Uso | força das equipes, Poisson/Dixon-Coles, corners, cards, shots, ELO de clubes, **backtest com odds reais de fechamento** |

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
| Limitações | sem escanteios, cartões ou finalizações → mercados de corners/cards/shots ficam `LOW_DATA` para seleções |

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

## 7. Cache e limites

| Recurso | TTL cache | Rate limit |
|---|---|---|
| Superbet eventos | 10 min | 1 req / 2 s |
| Superbet evento (odds) | 4 min | 1 req / 2 s |
| football-data CSV | 24 h | 1 req / 3 s |
| international_results | 24 h | 1 req / 10 s |

Diretórios: `data/raw` (CSVs originais), `data/processed` (Parquet),
`data/cache` (HTTP JSON).
