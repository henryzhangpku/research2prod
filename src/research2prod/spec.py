"""What a researcher declares, and nothing else.

Everything in this module exists so that a model can be described without being
run. That matters twice over.

It is what makes a model reviewable: a reader can see which datasets it touches
and which knobs it has without reading the body, and the promotion gate can
check that the body agreed with the declaration rather than taking it on trust.

And it is what makes the planned visual composer possible. A UI can only
enumerate, wire and validate components that describe themselves. A framework
built on free-form functions cannot grow one later without a rewrite, so the
declaration comes first even though nothing renders it yet.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

#: The types a parameter may take. Deliberately small: every one of these
#: survives a round trip through JSON and through an HTML form field, which is
#: what a composer would have to render it into.
ParamKind = Literal["float", "int", "str", "bool"]


class Input(BaseModel):
    """A dataset the model reads.

    Declaring it is not documentation. The model may only read datasets named
    here, and the declaration probe fails promotion if the body reaches for one
    that is not on the list.
    """

    name: str
    #: What the dataset holds, in the researcher's words rather than the
    #: schema's. A composer shows this next to the field.
    description: str = ""


class Param(BaseModel):
    """A knob, with the value it takes when nobody turns it."""

    name: str
    default: Any
    description: str = ""
    kind: ParamKind = "float"

    @property
    def is_tuned(self) -> bool:
        """Whether this parameter is the kind a sweep would vary."""
        return self.kind in ("float", "int")


class Output(BaseModel):
    """What the model returns for one as-of date."""

    name: str
    description: str = ""


class ModelSpec(BaseModel):
    """The whole declaration. Frozen into the artifact at promotion."""

    name: str
    description: str = ""
    inputs: list[Input] = Field(default_factory=list)
    params: list[Param] = Field(default_factory=list)
    output: Output = Field(default_factory=lambda: Output(name="value"))

    @property
    def input_names(self) -> set[str]:
        return {i.name for i in self.inputs}

    @property
    def param_names(self) -> set[str]:
        return {p.name for p in self.params}

    @property
    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params}

    def fingerprint(self) -> str:
        """A content hash of the declaration.

        Two promotions of the same declaration produce the same fingerprint, so
        a reader can tell at a glance whether version 4 changed the model or
        only re-ran it. It covers the declaration, not the body -- a body
        change with an unchanged declaration is caught by the probes, not here.
        """
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
