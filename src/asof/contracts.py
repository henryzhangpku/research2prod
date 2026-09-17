"""Data contracts: what the platform promises, and what a signal may rely on.

The researcher declares which datasets a signal reads. The platform declares
what each dataset *is* -- its columns and their types, which column is
knowledge time, who owns it, and how fresh it is. The two declarations meet
at promotion, and the gate refuses a signal that leans on anything the contract
does not promise.

That last part is the one that saves the most time later. A column that exists
in the research extract but is not in the contract is the second most common
way a signal dies on its first production morning (the first is look-ahead,
which the view already makes impossible). Catching it before promotion turns a
midnight page into a lunchtime message.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

ColumnKind = Literal["date", "str", "float", "int", "bool"]


class Column(BaseModel):
    name: str
    kind: ColumnKind
    description: str = ""


class DataContract(BaseModel):
    """One dataset, as promised by whoever owns it."""

    name: str
    #: The team or person who fixes it when it breaks. A contract without an
    #: owner is a hope.
    owner: str = "platform"
    description: str = ""
    #: The column the as-of filter runs on. Where a source is restated after
    #: the fact this must be knowledge time, never event time.
    knowledge_time: str = "date"
    #: Free text, kept in the certificate: "T+0 18:00 America/New_York".
    freshness: str = ""
    columns: list[Column] = Field(default_factory=list)

    @property
    def column_names(self) -> set[str]:
        return {c.name for c in self.columns}

    def kind_of(self, column: str) -> ColumnKind | None:
        return next((c.kind for c in self.columns if c.name == column), None)


# ---------------------------------------------------------------------------
# Checking rows against a contract
# ---------------------------------------------------------------------------


def _conforms(value: Any, kind: ColumnKind) -> bool:
    if value is None or value == "":
        return True  # missing is a data-quality matter, not a type violation
    if kind == "date":
        return isinstance(value, date)
    if kind == "str":
        return isinstance(value, str)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


class ContractViolation(BaseModel):
    column: str
    problem: str


def check_rows(contract: DataContract, rows: list[dict[str, Any]], *, sample: int = 2000) -> list[ContractViolation]:
    """Does this data honour its contract?

    Missing promised columns and mistyped values are violations. Extra columns
    are not -- the platform may carry more than it promises -- but a signal
    that *reads* an extra column is caught by the schema-drift probe, because
    the promise is what production will honour.
    """
    problems: list[ContractViolation] = []
    if not rows:
        return problems
    present = set(rows[0])
    for column in contract.columns:
        if column.name not in present:
            problems.append(ContractViolation(column=column.name, problem="promised column is missing"))
    if contract.knowledge_time not in present:
        problems.append(ContractViolation(column=contract.knowledge_time, problem="knowledge-time column is missing"))
    for column in contract.columns:
        if column.name not in present:
            continue
        bad = next(
            (row[column.name] for row in rows[:sample] if not _conforms(row[column.name], column.kind)),
            None,
        )
        if bad is not None:
            problems.append(
                ContractViolation(
                    column=column.name,
                    problem=f"promised {column.kind}, found {type(bad).__name__} ({bad!r})",
                )
            )
    return problems
