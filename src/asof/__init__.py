"""Promote a research model to a production job without an engineer in between.

A researcher declares what data a model needs, what its knobs are, and how to
compute one answer for one date. Everything else -- point-in-time correctness,
determinism, versioning, the audit record -- belongs to the framework.

The name is the guarantee. A model is handed a view bounded by its as-of date
and has no way to reach past it, so look-ahead is a structural impossibility
rather than a bug found six months later in production.
"""

from __future__ import annotations

from .data import AsOfView, Dataset, Frame, UndeclaredInput, load_csv
from .model import REGISTRY, DeclaredModel, model
from .probes import Verdict, check
from .promote import Artifact, PromotionRefused, history, promote
from .run import RunResult, run
from .spec import Input, ModelSpec, Output, Param

__version__ = "0.1.0"

__all__ = [
    "REGISTRY",
    "Artifact",
    "AsOfView",
    "Dataset",
    "DeclaredModel",
    "Frame",
    "Input",
    "ModelSpec",
    "Output",
    "Param",
    "PromotionRefused",
    "RunResult",
    "UndeclaredInput",
    "Verdict",
    "check",
    "history",
    "load_csv",
    "model",
    "promote",
    "run",
]
