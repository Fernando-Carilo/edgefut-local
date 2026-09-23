# EdgeFut AI

Assistente **local** (Windows desktop) de análise quantitativa de futebol com referência às odds da Superbet.
Coleta dados públicos, roda modelos estatísticos, simula, compara com o mercado e recomenda uma entrada — ou diz, com motivo, **NÃO ENTRAR**.

> O EdgeFut **não realiza apostas**, não faz login, não armazena credenciais de casas de apostas e não contorna bloqueios. O motor Python escuta apenas em `127.0.0.1`.

---

## O que ele faz

```
COLETA → NORMALIZAÇÃO → QUALIDADE DOS DADOS → FEATURES → MODELOS → SIMULAÇÃO →
PROBABILIDADE → ODD JUSTA → COMPARAÇÃO SUPERBET → EDGE → RISCO → RECOMENDAÇÃO ou NO BET → EXPLICAÇÃO
```

| Camada | O que entra nesta versão |
|---|---|
| **Fontes** | Superbet (oferta pública JSON: eventos, 14 mercados, odds, movimento), football-data.co.uk (17 ligas: gols, finalizações, escanteios, cartões, odds de fechamento), martj42/international_results (seleções, cidade/país/campo neutro) |
| **Normalização** | Nomes PT→EN (países e clubes), mapeamento torneio→dataset, detecção **MANDANTE / VISITANTE / CAMPO NEUTRO** (não assume que o primeiro time listado é mandante) |
| **Features** | Força com janelas 5/10/20 e peso de recência; splits geral/casa/fora; ataque/defesa relativos; H2H com peso menor que a forma |
| **Modelos** | `elo-v1`, `goals-poisson-v1`, `goals-dixon-coles-v1` (decaimento temporal), `mc-v1` (10k–100k simulações, seed = eventId), `corners-v1`, `cards-v1`, `shots-v1` |
| **Odds** | Probabilidade implícita, remoção de margem só com conjunto completo (Dupla Chance normalizada para soma 2), abertura × atual, % e direção |
| **Decisão** | Edge (pp), EV, Confidence Engine (A/B/C/D), Opportunity Score 0–100 (nunca pela odd), **NO BET** com 9 motivos |
| **Avaliação** | `prediction_snapshot` gravado antes do jogo e liquidado depois; Backtest Lab walk-forward; Hit rate, Brier, Log loss, ROI, Yield, Drawdown |
| **Explicação** | ExplanationEngine por templates + **Edge AI** (perguntas respondidas só com os números da análise); Ollama local opcional para reescrever |
| **UI** | Início, Radar, Jogos, Partida (com **Ver fontes**), Melhores Entradas, Ao Vivo (honestamente indisponível), Múltiplas (com correlação), Backtest Lab, Histórico, Favoritos, Fontes, Modelos, Performance, Configurações |

Todo número na tela carrega `source, sourceUrl, collectedAt, confidence, sampleSize`. Não há dados mock: o que não existe aparece como indisponível.

---

## Estrutura

```
apps/desktop/          React + TypeScript + Vite + Tailwind (janela Tauri 2)
  src-tauri/           shell Rust: inicia o sidecar Python e espera /health
packages/contracts/    tipos TS espelhando os schemas Pydantic do engine
packages/shared/       formatadores (pt-BR) e matemática de odds
engine/                Python 3.12 · FastAPI · SQLAlchemy · DuckDB/Parquet · pandas/scipy
  edgefut/api          rotas (health, events, radar, entries, tools, sources, settings…)
  edgefut/providers    http client (cache, rate-limit, retry, circuit breaker), Superbet, football-data, international_results, SourceResolver
  edgefut/normalization times, países, competições
  edgefut/features     força, forma, H2H, local do jogo, qualidade dos dados
  edgefut/models       ELO, Poisson, Dixon-Coles, escanteios/cartões/finalizações
  edgefut/simulation   Monte Carlo
  edgefut/odds         implícita, margem, movimento
  edgefut/recommendations edge, confiança, NO BET, múltiplas
  edgefut/backtesting  Lab, liquidação, métricas, performance
  edgefut/explanations templates, Edge AI, Ollama
  tests/               pytest
data/                  raw · processed (Parquet) · cache · sqlite (ignorado no git)
docs/                  ARCHITECTURE · DATA_SOURCES · MODELS · ROADMAP
scripts/               dev.ps1 · install.ps1 · build-windows.ps1 · dev.sh
```

---

## Rodando no Windows

### 1. Instalar dependências (uma vez)

```powershell
.\scripts\install.ps1
```

Instala (via winget, se faltar) Python 3.12, uv, Node LTS, pnpm, Rust e os Build Tools do Visual Studio; cria a venv em `engine\.venv` e roda `pnpm install`.

### 2. Desenvolvimento

```powershell
.\scripts\dev.ps1
```

Sobe o engine em `http://127.0.0.1:8765` (com reload) e a janela Tauri apontando para o Vite em `http://127.0.0.1:1420`.
No primeiro boot o app cria o banco, baixa os datasets públicos, valida a Superbet e carrega os próximos eventos — tudo visível na tela de preparação.

### 3. Gerar o instalador

```powershell
.\scripts\build-windows.ps1
```

1. roda os testes do engine e o empacota com PyInstaller (`edgefut-engine.exe`, sidecar do Tauri) e valida `/health`;
2. compila o frontend (`tsc` + `vite build`);
3. gera o NSIS installer e copia para **`dist\EdgeFutAI-Setup.exe`**.

O usuário final não precisa de terminal: o instalador traz o engine embutido e o app abre pronto.

### 4. Download sem compilar (GitHub Actions)

O workflow [`build-windows.yml`](.github/workflows/build-windows.yml) roda os mesmos passos num runner Windows a cada push e publica o instalador:

- como **artifact** `EdgeFutAI-Setup` do run (aba *Actions*);
- como asset da pré-release **`dev-latest`** em [Releases](https://github.com/Fernando-Carilo/edgefut-local/releases/tag/dev-latest) (pushes em `main`/`cursor/**`), ou de uma release `vX.Y.Z` ao publicar uma tag.

### Linux/macOS (só desenvolvimento)

```bash
./scripts/dev.sh     # engine + Vite; abra http://127.0.0.1:1420
```

---

## Engine (API local)

```bash
cd engine
uv venv .venv --python 3.12 && uv pip install -e ".[dev]" --python .venv/bin/python
.venv/bin/python -m edgefut.main            # http://127.0.0.1:8765/docs
.venv/bin/python -m pytest -q               # testes
```

Principais rotas: `GET /health`, `GET /events?window=48h`, `GET /events/{id}/analysis?simulations=50000`,
`GET /radar`, `GET /entries`, `GET /dashboard`, `POST /chat`, `POST /multiples/evaluate`, `POST /simulator`,
`POST /bankroll/stake`, `POST /backtest`, `GET /performance`, `GET /history`, `GET /sources`, `GET /models`, `GET|PUT /settings`.

Configuração por variáveis `EDGEFUT_*` (ver `engine/edgefut/core/config.py`). O host é fixo em loopback e não pode ser alterado.

---

## Como ler uma análise

- **MANDANTE / MANDANTE NÃO CONFIRMADO / CAMPO NEUTRO** — em amistosos e torneios de seleções a vantagem de mando só é aplicada integralmente quando confirmada; não confirmada usa 50 % (configurável); neutro remove. Você pode corrigir manualmente em "Definir local".
- **Divergência (pp)** — distância entre Poisson e Dixon-Coles no 1X2. Acima de 10 pp o jogo vira `NO BET · MODELOS DIVERGEM`.
- **Edge** = probabilidade do modelo − probabilidade justa do mercado (em pontos percentuais). **EV** = prob × odd − 1.
- **Confiança A/B/C/D** — só A e B entram no Radar; C fica em observação; D nunca é recomendado.
- **Opportunity Score** — 30 % qualidade dos dados, 30 % confiança, 25 % edge, 10 % estabilidade da odd, 5 % calibração histórica. Nunca a odd.
- **Ver fontes** — origem, URL, data de coleta, confiança e amostra de cada estatística, mais os checks de qualidade e os componentes da confiança.

Motivos de NO BET: `LOW_DATA`, `LOW_CONFIDENCE`, `NO_EDGE`, `MODEL_DISAGREEMENT`, `UNRELIABLE_SOURCE`, `SMALL_SAMPLE`, `LINEUP_UNCERTAINTY`, `EXTREME_ODDS_MOVEMENT`, `UNSUPPORTED_COMPETITION`.

---

## Validação com jogos reais (iteração 1)

| Jogo | Resultado do pipeline |
|---|---|
| **Japão x Uruguai** (Amistoso Internacional) | mandante não confirmado → 50 % da vantagem; ELO 1933 × 1869; Poisson 61/25/13 vs Dixon-Coles 49/35/16 → **NO BET · MODEL_DISAGREEMENT (12,7 pp)**; 51 mercados; snapshot gravado |
| **Servette (F) x Olympique Lyon (F)** (UEFA) | **NO BET · UNSUPPORTED_COMPETITION** — não há fonte pública de histórico para futebol feminino; qualidade 17 %, confiança D |
| **Seattle Sounders x Real Salt Lake** (MLS) | mandante confirmado; qualidade 79 %; confiança B; recomendações em gols com edge > 10 pp |
| Backtest E0 2025/26, 1X2, Dixon-Coles, odds de fechamento | 420 jogos, ROI −4,4 %, Brier 0,213 — mostrado sem maquiagem |

---

## Segurança e ética

- Escuta apenas `127.0.0.1` (nunca `0.0.0.0`); CORS restrito à janela Tauri e ao Vite local.
- Nenhum login, nenhuma credencial de casa de apostas, nenhuma automação de aposta.
- Não contorna CAPTCHA/Cloudflare/anti-bot: a Superbet é consultada só pela API pública de oferta; páginas HTML protegidas não são raspadas. Bloqueios são registrados (`source_log`) e o app segue com o que tem.
- Rate-limit, cache em disco, retry, timeout e circuit breaker em todo acesso HTTP.
- Nenhuma API paga.

---

## Documentação

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — pipeline, módulos, contratos, segurança
- [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) — fontes, endpoints, mapeamento de mercados e competições
- [docs/MODELS.md](docs/MODELS.md) — fórmulas, parâmetros e versões dos modelos
- [docs/ROADMAP.md](docs/ROADMAP.md) — fases, estado e próximos passos

## Licença

Uso pessoal. Este software é uma ferramenta de análise e não constitui recomendação financeira.
