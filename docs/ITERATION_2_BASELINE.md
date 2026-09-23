# Iteração 2 — Baseline (estado real antes de qualquer alteração)

Registrado em 2026-09-23 a partir de execução real (não do ROADMAP).

## 1. Testes e build

| Verificação | Comando | Resultado |
|---|---|---|
| Backend | `cd engine && .venv/bin/python -m pytest -q` | **35 passed** em 3.7 s |
| Frontend | `pnpm -r test` | `packages/shared`: 3 passed; `apps/desktop`: 0 testes (`--passWithNoTests`) |
| Typecheck | `pnpm -r typecheck` | OK (`packages/shared`, `apps/desktop`) |
| Build | `pnpm --filter @edgefut/desktop build` | OK (`index` 117 kB, `vendor` 207 kB, `charts` 406 kB) |
| Rust | `cargo check` (iteração 1) | OK |
| Windows | GitHub Actions `build-windows.yml` | OK — `EdgeFutAI-Setup.exe` (155 MB) publicado em `dev-latest` |

Testes existentes (35): `test_odds.py` (implícita, margem multiplicativa, Dupla Chance `FAIR_SUM=2`, movimento),
`test_models.py` (Poisson, Dixon-Coles, ELO, Monte Carlo determinístico, contagens), `test_recommendations.py`
(edge/EV, confidence grades, NO BET, opportunity score, settlement de seleções), `test_normalization.py`
(aliases PT→EN, competições, venue), `test_api.py` (health, settings, simulator, stake, multiples, radar vazio).

Nenhum teste falhando. Nenhum teste de leakage, calibração, identidade de evento ou invariantes matemáticos
gerais existia.

## 2. Providers em funcionamento (execução real, engine com scheduler ligado há ~2 h)

| Provider | Estado | Evidência |
|---|---|---|
| Superbet (oferta pública JSON) | funcionando | 1.117 requisições ok, 22 cache, 0 erros, 0 bloqueios; circuito fechado |
| football-data.co.uk | funcionando | 27 CSVs baixados (10 ligas × 3 temporadas − indisponíveis); 7.489 partidas |
| martj42/international_results | funcionando (cache) | 25.485 partidas em `international_results.parquet` |
| football-data `new/USA.csv` | funcionando | 6.203 partidas (MLS) |
| Ollama | indisponível | esperado; Edge AI responde via templates |
| Player data / escalações | inexistente | nenhum provider; `PLAYER_TO_SCORE` → `LINEUP_UNCERTAINTY` |
| Ao vivo | inexistente | `/live` devolve `available=false` honestamente |

Banco local: 247 eventos, 89 competições, 25.192 snapshots de odds, 44 `prediction_snapshot`, 1.166 `source_log`.

## 3. Exemplos reais (verificados via API)

| Evento | Saída |
|---|---|
| Japão x Uruguai | NÃO ENTRAR — `MODEL_DISAGREEMENT` (Poisson × Dixon-Coles 12,7–13,5 pp no 1X2) |
| Servette (F) x Olympique Lyon (F) | NÃO ENTRAR — `UNSUPPORTED_COMPETITION` |
| Seattle Sounders x Real Salt Lake | mandante confirmado, DQ 79 %, entradas grau B (Under 2.5) |
| Backtest E0 1X2+DC, 3 anos | 420 jogos, 210 apostas, ROI −4,4 %, Brier 0,213, Hit 30 % |

## 4. Limitações e problemas encontrados na auditoria

Ordenados por impacto em **confiabilidade**:

1. **Settlement de snapshots pode nunca ocorrer.** `SuperbetSync.sync_odds` grava `event.settled_at` quando a
   Superbet marca `FINISHED`; `settle_pending` filtra `Event.settled_at IS NULL`, então snapshots desses
   eventos ficam sem `result` para sempre. Hoje: 24 eventos com placar, 0 snapshots liquidados.
2. **Leakage no Backtest Lab.** `use_closing_odds=True` (padrão) usa a odd de *fechamento* para decidir a
   aposta e calcular a probabilidade justa — informação que não existia no momento da previsão.
3. **"Calibração" na Confidence é 1 − Brier normalizado**, não uma calibração de fato; não há ajuste
   isotônico nem reliability diagram por mercado; limiar de 30 amostras é baixo demais.
4. **Freshness só existe para odds** (check binário < 30 min em Data Quality). Forma, ranking, histórico e
   venue não carregam `validUntil`/estado; nada impede o uso de dado expirado.
5. **Identidade de evento = `eventId` da Superbet.** Não existe `canonical_event_id`; a mesma partida vinda de
   outra fonte (resultado no CSV) é casada por nome + janela de 1 dia em `find_result`, sem registro de
   conflito.
6. **Conflitos de fonte não são registrados.** Venue detectado × venue do usuário, placar Superbet × CSV,
   kickoff divergente: a escolha é silenciosa.
7. **Movimento de odds = abertura × atual.** Não há mínima/máxima, movimento em probabilidade implícita,
   nem *closing line* separada da linha de recomendação → CLV nunca é calculado em produção (`clv_pct` só
   no backtest).
8. **Remoção de margem só multiplicativa.** Sem Shin; probabilidades cruas e justas não são guardadas lado a
   lado no snapshot.
9. **Sem model registry.** Versões são strings em `core/versions.py`; parâmetros/janela de treino/métricas
   dos ajustes não são persistidos.
10. **Snapshot mutável em nível de banco.** `_save_snapshot` não sobrescreve (reusa < 6 h), e settlement só
    escreve `result`/`settled_at`, mas não há evento de correção nem proteção contra alteração pós-kickoff.
11. **Scheduler sem histórico de execuções.** Estado em memória (`_state`) com últimos horários e 20 erros;
    sem `startedAt/finishedAt/duration/recordsProcessed` persistidos; `job_sync_history` só roda 24 h após o
    boot (`last_history_sync = null`).
12. **Radar sem contadores de cobertura** (encontrados / com dados / analisados / A / B / NO BET) e Opportunity
    Score v1 com 5 componentes fixos (30/30/25/10/5), sem Quality Gate explícito nem separação
    HIGH PROBABILITY × VALUE.
13. **Cache de análise por TTL (5 min) + "última conhecida até o kickoff"**, sem chave por versão de
    dados/modelo/odds; `refresh=true` recalcula tudo.
14. **Logging sem correlation id**; formato texto; sem `print()` (bom), rotação 5 MB × 3 (bom).
15. **Sem testes de invariantes** (soma 1X2 = 1, fair soma = 1, odds > 1, edge/EV, convergência MC).
16. **Calibração empírica observada:** em amistosos internacionais o modelo tende a Under vs. mercado
    (ex.: Japão x Uruguai Under 2.5 ~70 % × mercado ~57 %). Sem dado settled suficiente para
    confirmar ou corrigir.
17. **Frontend sem testes de componentes** (apenas formatadores em `packages/shared`).

## 5. O que funciona e não deve ser tocado sem necessidade

Providers com cache/retry/circuit breaker, normalização PT→EN, detecção de venue, ELO, Poisson, Dixon-Coles
(MLE com gradiente analítico), Monte Carlo determinístico, engines de contagens, Edge/EV, NO BET, snapshots
antes do jogo, Backtest Lab (exceto o ponto 2), Edge AI via templates, shell Tauri + sidecar, workflow Windows.
