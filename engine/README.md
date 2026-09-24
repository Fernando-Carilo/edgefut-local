# EdgeFut Engine

Motor Python do EdgeFut AI (FastAPI em `127.0.0.1:8765`). Documentação geral no [README da raiz](../README.md).

```bash
uv venv .venv --python 3.12
uv pip install -e ".[dev]" --python .venv/bin/python
.venv/bin/python -m edgefut.main             # API + docs em /docs
.venv/bin/python -m pytest -q                # testes
.venv/bin/python -m PyInstaller edgefut-engine.spec   # sidecar para o Tauri
```

Flags: `--port`, `--no-bootstrap`, `--no-scheduler`, `--reload`.
Variáveis `EDGEFUT_*` (ex.: `EDGEFUT_DATA_DIR`, `EDGEFUT_SCHEDULER_ENABLED=false`) em `edgefut/core/config.py`.
