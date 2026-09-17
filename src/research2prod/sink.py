"""Where a signal goes once it is running. The far end of the bridge.

Deploying a job is half of production. The other half is the trading system
that has to consume what the job produces, and that integration is done once,
against a fixed row shape, and never again per signal. The shape is small on
purpose: every consumer needs the key, the score, the date it was priced for
and enough provenance to trace the number back to a certificate.

    as_of, key, score, model, version, fingerprint, published_at

Two sinks ship. ``file`` appends one JSON-lines file per model, which is what
most shops' intake already knows how to read. ``http`` POSTs the batch to a
registry that speaks the same rows. A third kind, ``custom``, names a Python
callable so a shop can route into its own book without forking this module.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

SinkKind = Literal["file", "http", "custom"]


class Sink(BaseModel):
    """Declared once in ``research2prod.toml``; frozen into every bundle."""

    kind: SinkKind = "file"
    #: file: directory for ``<model>.jsonl``. http: the URL to POST to.
    #: custom: ``package.module:function`` taking ``(rows, sink)``.
    target: str = "signals"
    #: http only. Header name whose value is read from ``RESEARCH2PROD_SINK_TOKEN``.
    auth_header: str = "Authorization"


@dataclass(frozen=True)
class SignalRow:
    """The output contract. Consumers integrate against this and nothing else."""

    as_of: str
    key: str
    score: float
    model: str
    version: int
    fingerprint: str
    published_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def rows_from_output(output: Any, *, as_of: date, model: str, version: int, fingerprint: str) -> list[SignalRow]:
    """Turn a model's ``{key: score}`` into contract rows. Anything else is refused."""
    if not isinstance(output, dict):
        raise TypeError("a publishable signal returns {key: score}; got " + type(output).__name__)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for key in sorted(output):
        value = output[key]
        if value is None:
            continue
        rows.append(SignalRow(as_of.isoformat(), str(key), float(value), model, version, fingerprint, stamp))
    return rows


def publish(rows: list[SignalRow], sink: Sink, *, root: Path | str = ".") -> str:
    """Deliver rows to the sink. Returns a one-line receipt for the log."""
    if not rows:
        return "nothing to publish"
    if sink.kind == "file":
        directory = Path(root) / sink.target
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{rows[0].model}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row.as_dict(), sort_keys=True) + "\n")
        return f"appended {len(rows)} row(s) to {path}"
    if sink.kind == "http":
        import os
        import urllib.request

        body = json.dumps([r.as_dict() for r in rows]).encode()
        request = urllib.request.Request(sink.target, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        token = os.environ.get("RESEARCH2PROD_SINK_TOKEN")
        if token:
            request.add_header(sink.auth_header, token)
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - the URL is the operator's
            return f"POST {sink.target} -> {response.status} ({len(rows)} row(s))"
    if sink.kind == "custom":
        module_name, _, func_name = sink.target.partition(":")
        func = getattr(importlib.import_module(module_name), func_name)
        return str(func(rows, sink) or f"delivered {len(rows)} row(s) via {sink.target}")
    raise ValueError(f"unknown sink kind {sink.kind!r}")
