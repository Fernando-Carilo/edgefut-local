"""Isola os testes num diretório de dados temporário e desliga rede/scheduler/bootstrap."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="edgefut-test-")
os.environ.setdefault("EDGEFUT_DATA_DIR", _tmp)
os.environ.setdefault("EDGEFUT_SCHEDULER_ENABLED", "false")
os.environ.setdefault("EDGEFUT_AUTOSTART_BOOTSTRAP", "false")
os.environ.setdefault("EDGEFUT_SUPERBET_ENABLED", "false")
