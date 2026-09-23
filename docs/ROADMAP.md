# EdgeFut AI — Roadmap

Fases conforme a especificação. Estado desta iteração marcado.

| Fase | Escopo | Estado |
|---|---|---|
| 0 | Foundation: desktop abre, API local, SQLite | ✅ engine + frontend; Tauri shell criado (build Windows via `scripts/build-windows.ps1`) |
| 1 | Provider Superbet: eventos reais, odds reais, histórico de odds | ✅ API pública de oferta, 14 mercados mapeados, snapshots de odds |
| 2 | Dados históricos, Team Strength, ELO | ✅ football-data.co.uk + international_results; strength-v1; elo-v1 |
| 3 | Poisson, Dixon-Coles, Monte Carlo | ✅ goals-poisson-v1, goals-dixon-coles-v1, mc-v1 |
| 4 | Radar, Página do Jogo, Odds, Edge, Confiança, NO BET | ✅ |
| 5 | Corners, Cards, Shots | ✅ engines v1 (dependem de HC/AC/HS/HST no histórico — ligas football-data) |
| 6 | Jogadores | ⏳ Player Engine desativado até existir fonte pública permitida; mercados de jogador exibem `LINEUP_UNCERTAINTY` |
| 7 | Backtesting, Performance, Calibração | ✅ Lab walk-forward com odds de fechamento; métricas Brier/LogLoss/ROI/Yield/Drawdown/CLV |
| 8 | Edge AI | ✅ Q&A grounded por templates; Ollama opcional |
| 9 | Empacotamento Windows | ✅ scripts PowerShell + sidecar PyInstaller; binário final requer máquina Windows |

## Próximos passos (após esta iteração)

1. **Ao Vivo**: a API Superbet expõe `offerState=live`; hoje a tela indica
   indisponibilidade em vez de exibir dados não implementados.
2. **Player Engine**: integrar fonte pública de minutos/xG/chutes quando
   houver uma permitida; até então NO BET em mercados de jogador.
3. **Árbitros**: expandir cards-v1 com árbitro quando a escala for pública.
4. **Shin margin removal** como alternativa à normalização multiplicativa.
5. **Calibração isotônica** por competição usando snapshots settled (> 300).
6. **Bivariate Poisson** como terceiro modelo de gols.
7. **Importação manual de CSV** do usuário pela UI (Fontes → Importar).
8. **Auto-update** do desktop (Tauri updater) e assinatura do instalador.

## Critérios de saída da iteração 1 (entregável)

- [x] projeto compilando (engine: pytest; frontend: `tsc` + `vite build`)
- [x] frontend funcionando
- [x] backend funcionando em 127.0.0.1
- [x] banco local (SQLite + Parquet/DuckDB)
- [x] SuperbetProvider inicial
- [x] Data Source Manager (SourceResolver + tela Fontes)
- [x] evento real (Japão x Uruguai · Servette (F) x Lyon (F))
- [x] tela de jogo
- [x] implied probability + remoção de margem
- [x] ELO inicial
- [x] Poisson inicial (+ Dixon-Coles)
- [x] Monte Carlo
- [x] probabilidades 1X2 / Over-Under / BTTS / placares
- [x] Confidence Engine inicial
- [x] Edge Engine
- [x] botão "Ver fontes"
- [x] design premium
- [x] README completo
- [x] scripts Windows
