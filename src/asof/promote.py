"""The one key.

Promotion is the whole product in one verb. A researcher who has a model
working runs it, and either gets a versioned production artifact or gets told
which probe stopped them and why. Nothing in between, and no conversation with
an engineer in either branch.

Two rules make the artifact worth having.

A failed probe refuses. There is no flag to force one through, because a gate
with an override is a suggestion, and the whole reason a researcher can be
trusted to promote their own work is that the gate cannot be talked round.

And promotion appends. Re-promoting writes version two; it never edits version
one. Somebody may have run against version one, and a record you can rewrite
is not a record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .model import DeclaredModel
from .probes import Verdict, check

#: Where promoted artifacts land. Committed, not generated at deploy time --
#: the promotion history is meant to be read in the repository.
DEFAULT_PROMOTED_DIR = Path("promoted")


class PromotionRefused(RuntimeError):
    """Raised when a probe failed. Carries the verdicts that caused it."""

    def __init__(self, verdicts: list[Verdict]):
        self.verdicts = verdicts
        failed = ", ".join(v.probe for v in verdicts if not v.passed)
        super().__init__(f"promotion refused by: {failed}")


@dataclass
class Artifact:
    """A promoted version, as written to disk."""

    model: str
    version: int
    path: Path
    fingerprint: str
    promoted_at: str

    @property
    def label(self) -> str:
        return f"{self.model} v{self.version}"


def promote(
    model: DeclaredModel,
    as_of: date,
    *,
    data_dir: Path | str = "data",
    promoted_dir: Path | str = DEFAULT_PROMOTED_DIR,
) -> Artifact:
    """Check, then freeze. Raises :class:`PromotionRefused` if any probe fails."""
    verdicts = check(model, as_of, data_dir=data_dir)
    if not all(v.passed for v in verdicts):
        raise PromotionRefused(verdicts)

    promoted_dir = Path(promoted_dir)
    target = promoted_dir / model.name
    target.mkdir(parents=True, exist_ok=True)
    version = _next_version(target)
    promoted_at = datetime.now(UTC).isoformat(timespec="seconds")

    payload = {
        "model": model.name,
        "version": version,
        "fingerprint": model.spec.fingerprint(),
        "promoted_at": promoted_at,
        "checked_as_of": as_of.isoformat(),
        "spec": model.spec.model_dump(mode="json"),
        "probes": [
            {"probe": v.probe, "passed": v.passed, "detail": v.detail} for v in verdicts
        ],
    }
    path = target / f"v{version}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    return Artifact(
        model=model.name,
        version=version,
        path=path,
        fingerprint=payload["fingerprint"],
        promoted_at=promoted_at,
    )


def history(
    name: str, *, promoted_dir: Path | str = DEFAULT_PROMOTED_DIR
) -> list[dict]:
    """Every promoted version of a model, oldest first."""
    target = Path(promoted_dir) / name
    if not target.exists():
        return []
    versions = sorted(target.glob("v*.json"), key=_version_of)
    return [json.loads(p.read_text(encoding="utf-8")) for p in versions]


def _next_version(target: Path) -> int:
    existing = [_version_of(p) for p in target.glob("v*.json")]
    return max(existing, default=0) + 1


def _version_of(path: Path) -> int:
    return int(path.stem.lstrip("v"))
