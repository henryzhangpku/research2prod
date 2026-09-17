"""The entire API a researcher learns.

One decorator and one function signature. That is the whole surface, and
keeping it that small is the point of the project: every additional concept a
researcher has to hold is a reason to go back to writing a notebook and
handing it to somebody else.

    @model(
        name="momentum",
        inputs=["prices"],
        params=[Param("lookback_days", 252, kind="int")],
    )
    def momentum(ctx):
        prices = ctx.frame("prices")
        ...
        return {"AAPL": 0.12}

``ctx`` is an :class:`~research2prod.data.AsOfView`. It answers ``ctx.as_of``,
``ctx.frame(name)`` and ``ctx.param(name)``, and it cannot be persuaded to
return a row after the as-of date.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .data import AsOfView
from .spec import Input, ModelSpec, Output, Param


@dataclass
class DeclaredModel:
    """A spec and the body that implements it, kept together."""

    spec: ModelSpec
    fn: Callable[[AsOfView], Any]

    @property
    def name(self) -> str:
        return self.spec.name

    def __call__(self, ctx: AsOfView) -> Any:
        return self.fn(ctx)


#: Every model declared in this process, in declaration order. A module is
#: imported once and registers whatever it declares; nothing is discovered by
#: scanning names, so a model that is never imported simply does not exist.
REGISTRY: dict[str, DeclaredModel] = {}


def model(
    *,
    name: str,
    description: str = "",
    inputs: list[Input | str] | None = None,
    params: list[Param] | None = None,
    output: Output | str | None = None,
) -> Callable[[Callable[[AsOfView], Any]], DeclaredModel]:
    """Declare a model and register it.

    ``inputs`` accepts bare strings for the common case where the name is the
    whole story. A composer wants the descriptions eventually; a researcher
    getting a first model running does not want to write them yet, and a
    framework that insists reads as ceremony.
    """

    def decorate(fn: Callable[[AsOfView], Any]) -> DeclaredModel:
        spec = ModelSpec(
            name=name,
            description=description or (fn.__doc__ or "").strip().split("\n")[0],
            inputs=[Input(name=i) if isinstance(i, str) else i for i in (inputs or [])],
            params=list(params or []),
            output=(
                Output(name=output)
                if isinstance(output, str)
                else output or Output(name="value")
            ),
        )
        declared = DeclaredModel(spec=spec, fn=fn)
        REGISTRY[name] = declared
        return declared

    return decorate
