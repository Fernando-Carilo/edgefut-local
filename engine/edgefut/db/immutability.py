"""Imutabilidade de snapshots de previsão.

Depois de gravado, um `prediction_snapshot` nunca tem seus campos de previsão
alterados (`PREDICTION_FIELDS`). Só `result`, `settled_at` e `closing_odds` são
escritos posteriormente (liquidação / closing line). Uma tentativa de escrita em
campo protegido levanta `ImmutableSnapshotError`.

Correção de resultado (ex.: placar revisado pela fonte) é permitida, mas gera um
`snapshot_correction` com valor antigo, novo, motivo e fonte.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .models import PREDICTION_FIELDS, SHADOW_PREDICTION_FIELDS, PredictionSnapshot, ShadowPrediction, SnapshotCorrection

log = logging.getLogger(__name__)


class ImmutableSnapshotError(RuntimeError):
    pass


def _guard(session: Session, flush_context, instances) -> None:  # noqa: ANN001
    for obj in session.dirty:
        if isinstance(obj, PredictionSnapshot):
            fields, label = PREDICTION_FIELDS, "snapshot"
        elif isinstance(obj, ShadowPrediction):
            fields, label = SHADOW_PREDICTION_FIELDS, "shadow prediction"
        else:
            continue
        state = inspect(obj)
        changed = [a.key for a in state.attrs if a.key in fields and a.history.has_changes()]
        if changed:
            raise ImmutableSnapshotError(f"{label} {obj.id}: campos de previsão são imutáveis ({', '.join(sorted(changed))})")
    for obj in session.deleted:
        if isinstance(obj, ShadowPrediction):
            raise ImmutableSnapshotError(f"shadow prediction {obj.id}: registro append-only, não pode ser apagado")


_installed = False


def install_snapshot_guard() -> None:
    global _installed
    if _installed:
        return
    event.listen(Session, "before_flush", _guard)
    _installed = True


def correct_result(session: Session, snap: PredictionSnapshot, new_result: dict, *, reason: str, source: str | None) -> SnapshotCorrection:
    """Substitui `result` registrando a correção. A previsão não é tocada."""
    old = snap.result
    corr = SnapshotCorrection(snapshot_id=snap.id, field="result", old_value=old, new_value=new_result, reason=reason, source=source)
    snap.result = new_result
    snap.settled_at = datetime.utcnow()
    session.add(corr)
    session.flush()
    log.info("correção de resultado no snapshot %s (%s): %s → %s", snap.id, reason, (old or {}).get("hg"), new_result.get("hg"))
    return corr
