"""Finding the models a researcher has written.

Discovery is by import, not by scanning. A module declares what it declares
when it is imported, and a model nobody imports does not exist -- which keeps
``research2prod list`` an honest answer to "what is in this project" rather than a
guess assembled from filenames.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from .model import REGISTRY, DeclaredModel

#: Where models live unless told otherwise. One directory, no nesting, because
#: a researcher should be able to answer "where do I put it" without reading
#: anything.
DEFAULT_MODEL_DIR = Path("examples")


def load_dir(directory: Path | str = DEFAULT_MODEL_DIR) -> dict[str, DeclaredModel]:
    """Import every module in ``directory`` and return what registered."""
    directory = Path(directory)
    if not directory.exists():
        return dict(REGISTRY)
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        load_file(path)
    return dict(REGISTRY)


def load_file(path: Path | str) -> dict[str, DeclaredModel]:
    """Import one module by path and return the registry afterwards."""
    path = Path(path)
    module_name = f"asof_model_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable file
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return dict(REGISTRY)


def get(name: str, directory: Path | str = DEFAULT_MODEL_DIR) -> DeclaredModel:
    """Look up one model, importing the directory first if it is not loaded."""
    if name not in REGISTRY:
        load_dir(directory)
    if name not in REGISTRY:
        raise KeyError(f"no model named {name!r}; found {sorted(REGISTRY) or 'none'}")
    return REGISTRY[name]
