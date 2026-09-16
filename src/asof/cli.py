"""What a researcher types.

The commands are the workflow, in order: see what exists (``list``), read a
declaration without reading the body (``describe``), get an answer for a date
(``run``), ask whether it would survive promotion (``check``), promote it
(``promote``), and read what was promoted before (``history``).

``check`` is the one that matters. It is deliberately available before
``promote`` and identical to the check ``promote`` runs, so a researcher never
discovers a problem at the moment they were trying to ship.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import registry
from .probes import check as run_probes
from .promote import PromotionRefused
from .promote import history as read_history
from .promote import promote as do_promote
from .run import run as run_model

app = typer.Typer(add_completion=False, help="Promote a research model to production")
console = Console()

MODELS = typer.Option(Path("examples"), "--models", help="Directory of model modules")
DATA = typer.Option(Path("data"), "--data", help="Directory of input datasets")
PROMOTED = typer.Option(Path("promoted"), "--promoted", help="Directory of promoted artifacts")


def _parse(as_of: str | None) -> date:
    if as_of is None:
        console.print("[yellow]--as-of is required; a model is always priced for a day[/]")
        raise typer.Exit(1)
    try:
        return date.fromisoformat(as_of)
    except ValueError:
        console.print(f"[yellow]{as_of!r} is not a date; use YYYY-MM-DD[/]")
        raise typer.Exit(1) from None


def _get(name: str, models: Path):
    try:
        return registry.get(name, models)
    except KeyError as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(1) from None


# ---------------------------------------------------------------------------


@app.command("list")
def list_models(models: Path = MODELS) -> None:
    """Every model declared in the models directory."""
    found = registry.load_dir(models)
    if not found:
        console.print(f"[yellow]no models found in {models}[/]")
        raise typer.Exit(1)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("model")
    table.add_column("inputs")
    table.add_column("params", justify="right")
    table.add_column("what it does")
    for name, declared in sorted(found.items()):
        table.add_row(
            f"[bold]{name}[/]",
            ", ".join(sorted(declared.spec.input_names)) or "--",
            str(len(declared.spec.params)),
            declared.spec.description,
        )
    console.print()
    console.print(table)
    console.print()


@app.command()
def describe(name: str, models: Path = MODELS) -> None:
    """The declaration, without reading the body."""
    declared = _get(name, models)
    spec = declared.spec

    console.print()
    console.print(
        Panel(
            f"[bold]{spec.name}[/]\n{spec.description}",
            expand=False,
            border_style="cyan",
            subtitle=f"declaration {spec.fingerprint()}",
            subtitle_align="left",
        )
    )

    inputs = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, title="[bold]inputs[/]", title_justify="left")
    inputs.add_column("name")
    inputs.add_column("what it holds")
    for declared_input in spec.inputs:
        inputs.add_row(declared_input.name, declared_input.description or "[dim]--[/]")
    console.print(inputs)

    params = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False, title="[bold]parameters[/]", title_justify="left")
    params.add_column("name")
    params.add_column("default", justify="right")
    params.add_column("kind")
    params.add_column("what it controls")
    for param in spec.params:
        params.add_row(param.name, str(param.default), param.kind, param.description or "[dim]--[/]")
    console.print(params)
    console.print(f"  returns [bold cyan]{spec.output.name}[/]  {spec.output.description}")
    console.print()


@app.command()
def run(
    name: str,
    as_of: str = typer.Option(None, "--as-of", help="The day being priced, YYYY-MM-DD"),
    models: Path = MODELS,
    data: Path = DATA,
) -> None:
    """Compute one answer for one date."""
    declared = _get(name, models)
    day = _parse(as_of)
    try:
        result = run_model(declared, day, data_dir=data)
    except Exception as exc:  # noqa: BLE001 - the researcher needs the message, not a stack
        console.print(f"[yellow]{name} failed on {day}: {exc}[/]")
        raise typer.Exit(1) from None

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False,
                  title=f"[bold]{name}[/] as of {day}", title_justify="left",
                  caption=f"{len(result.touched_inputs)} input(s) read, bounded at {day}",
                  caption_justify="left")
    table.add_column("key")
    table.add_column(declared.spec.output.name, justify="right")
    output = result.output
    rows = sorted(output.items()) if isinstance(output, dict) else [("value", output)]
    for key, value in rows:
        table.add_row(str(key), f"{value:+.4f}" if isinstance(value, float) else str(value))
    console.print()
    console.print(table)
    console.print()


@app.command()
def check(
    name: str,
    as_of: str = typer.Option(None, "--as-of", help="The day to check against"),
    models: Path = MODELS,
    data: Path = DATA,
) -> None:
    """Would this survive promotion? Same probes, run early."""
    declared = _get(name, models)
    day = _parse(as_of)
    verdicts = run_probes(declared, day, data_dir=data)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False,
                  title=f"[bold]{name}[/] checked against {day}", title_justify="left")
    table.add_column("probe")
    table.add_column("", width=6)
    table.add_column("what it found")
    for verdict in verdicts:
        table.add_row(
            verdict.probe,
            "[bold green]PASS[/]" if verdict.passed else "[bold red]FAIL[/]",
            verdict.detail,
        )
    console.print()
    console.print(table)

    if all(v.passed for v in verdicts):
        console.print("\n  [green]ready to promote[/]\n")
        return
    console.print("\n  [yellow]promotion would be refused[/]\n")
    raise typer.Exit(1)


@app.command()
def promote(
    name: str,
    as_of: str = typer.Option(None, "--as-of", help="The day to check against"),
    models: Path = MODELS,
    data: Path = DATA,
    promoted: Path = PROMOTED,
) -> None:
    """Check, then freeze a versioned production artifact."""
    declared = _get(name, models)
    day = _parse(as_of)
    try:
        artifact = do_promote(declared, day, data_dir=data, promoted_dir=promoted)
    except PromotionRefused as refused:
        console.print()
        for verdict in refused.verdicts:
            mark = "[bold green]PASS[/]" if verdict.passed else "[bold red]FAIL[/]"
            console.print(f"  {mark}  [bold]{verdict.probe}[/]  {verdict.detail}")
        console.print("\n  [yellow]refused. nothing was written.[/]\n")
        raise typer.Exit(1) from None

    console.print()
    console.print(
        Panel(
            f"[bold]{artifact.label}[/]\n"
            f"declaration {artifact.fingerprint}\n"
            f"written to {artifact.path}",
            expand=False,
            border_style="green",
        )
    )
    console.print()


@app.command()
def history(name: str, promoted: Path = PROMOTED) -> None:
    """Every version ever promoted, oldest first."""
    versions = read_history(name, promoted_dir=promoted)
    if not versions:
        console.print(f"[yellow]{name} has never been promoted[/]")
        raise typer.Exit(1)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False,
                  title=f"[bold]{name}[/]", title_justify="left",
                  caption="a new version never edits an older one", caption_justify="left")
    table.add_column("version", justify="right")
    table.add_column("promoted")
    table.add_column("declaration")
    table.add_column("checked against")
    for payload in versions:
        table.add_row(
            f"v{payload['version']}",
            payload["promoted_at"],
            payload["fingerprint"],
            payload["checked_as_of"],
        )
    console.print()
    console.print(table)
    console.print()


if __name__ == "__main__":  # pragma: no cover
    app()
