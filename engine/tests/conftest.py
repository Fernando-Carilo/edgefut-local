"""Isola os testes num diretório de dados temporário e desliga rede/scheduler/bootstrap."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="edgefut-test-")
os.environ.setdefault("EDGEFUT_DATA_DIR", _tmp)
os.environ.setdefault("EDGEFUT_SCHEDULER_ENABLED", "false")
os.environ.setdefault("EDGEFUT_AUTOSTART_BOOTSTRAP", "false")
os.environ.setdefault("EDGEFUT_SUPERBET_ENABLED", "false")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _value_enabled_for_legacy_tests(request):
    """Iteração 5: VALUE_ENABLED é False por padrão em produção (RESEARCH_SIGNAL em vez de VALUE).
    Os testes do motor de recomendações verificam a lógica de VALUE/VALUE_CANDIDATE e, por isso, correm
    com o override ligado. Testes marcados com `@pytest.mark.value_disabled` correm com o padrão real."""
    from edgefut.flywheel import governance

    governance.set_test_override(None if request.node.get_closest_marker("value_disabled") else True)
    yield
    governance.set_test_override(None)
