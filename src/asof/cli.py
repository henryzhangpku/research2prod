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
from .config import Config, find_config, load_config
from .backtest import backtest as run_backtest
from .export import export as do_export
from .sink import publish, rows_from_output
from .probes import check as run_probes
from .promote import PromotionRefused
from .promote import history as read_history
from .promote import promote as do_promote
from .promote import replay as do_replay
from .run import run as run_model

app = typer.Typer(add_completion=False, help="Promote a research model to production")
console = Console()

MODELS = typer.Option(None, "--models", help="Directory of signal modules (default: [project].models in asof.toml)")
DATA = typer.Option(None, "--data", help="Directory of input datasets (default: [project].data)")
PROMOTED = typer.Option(None, "--promoted", help="Directory of promoted artifacts (default: [project].promoted)")


def _dirs(models: Path | None, data: Path | None, promoted: Path | None) -> tuple[Path, Path, Path]:
    """Explicit flags win; otherwise asof.toml says where things are; otherwise
    the conventional names. A researcher in a scaffolded project types none of it."""
    cfg_path = find_config()
    base = cfg_path.parent if cfg_path else Path(".")
    project = _config().project if cfg_path else None
    return (
        models or base / (project.models if project else "signals"),
        data or base / (project.data if project else "data"),
        promoted or base / (project.promoted if project else "promoted"),
    )


def _parse(as_of: str | None) -> date:
    if as_of is None:
        console.print("[yellow]--as-of is required; a model is always priced for a day[/]")
        raise typer.Exit(1)
    try:
        return date.fromisoformat(as_of)
    except ValueError:
        console.print(f"[yellow]{as_of!r} is not a date; use YYYY-MM-DD[/]")
        raise typer.Exit(1) from None


def _rel(path: Path) -> str:
    """Paths as the researcher sees them: relative to where they are standing."""
    try:
        return Path(path).resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _next(text: str) -> None:
    """Every command ends by naming the next one. The gap this project closes
    is not knowing what to do after the research is done; a tool that stops
    talking at exactly that moment reproduces the gap."""
    console.print(f"  [bold]next:[/] {text}\n")


def _config() -> Config | None:
    """The nearest ``asof.toml``, or None -- the probes report its absence."""
    path = find_config()
    if path is None:
        return None
    try:
        return load_config(path)
    except Exception as exc:  # noqa: BLE001 - a broken config must not look like a missing one
        console.print(f"[yellow]{path} could not be read: {exc}[/]")
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
    models, _, _ = _dirs(models, None, None)
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
    models, _, _ = _dirs(models, None, None)
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
    models, data, _ = _dirs(models, data, None)
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
    models, data, _ = _dirs(models, data, None)
    declared = _get(name, models)
    day = _parse(as_of)
    verdicts = run_probes(declared, day, data_dir=data, config=_config())

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
        console.print("\n  [green]ready to promote[/]")
        _next(f"asof backtest {name}   (then: asof promote {name} --as-of {day})")
        return
    console.print("\n  [yellow]promotion would be refused[/]")
    _next("fix the failing probe above, then run the same check again")
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
    models, data, promoted = _dirs(models, data, promoted)
    declared = _get(name, models)
    day = _parse(as_of)
    try:
        artifact = do_promote(declared, day, data_dir=data, promoted_dir=promoted, config=_config())
    except PromotionRefused as refused:
        console.print()
        for verdict in refused.verdicts:
            mark = "[bold green]PASS[/]" if verdict.passed else "[bold red]FAIL[/]"
            console.print(f"  {mark}  [bold]{verdict.probe}[/]  {verdict.detail}")
        console.print("\n  [yellow]refused. nothing was written.[/]")
        _next(f"fix the failing check above, then: asof promote {name} --as-of {day}")
        raise typer.Exit(1) from None

    console.print()
    console.print(
        Panel(
            f"[bold]{artifact.label}[/]\n"
            f"declaration {artifact.fingerprint}\n"
            f"written to {_rel(artifact.path)}",
            expand=False,
            border_style="green",
        )
    )
    _next(f"asof export {name}   (builds the deployable bundle for {artifact.label})")


@app.command()
def history(name: str, promoted: Path = PROMOTED) -> None:
    """Every version ever promoted, oldest first."""
    _, _, promoted = _dirs(None, None, promoted)
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


@app.command()
def backtest(
    name: str,
    start: str = typer.Option(None, "--from", help="First as-of date; default: discovery start"),
    end: str = typer.Option(None, "--to", help="Last as-of date; default: validation end"),
    models: Path = MODELS,
    data: Path = DATA,
) -> None:
    """Run the signal over a date range through the same path production uses."""
    models, data, _ = _dirs(models, data, None)
    cfg = _config()
    if cfg is None:
        console.print("[yellow]no asof.toml found; a backtest needs the project's universe, costs and splits[/]")
        raise typer.Exit(1)
    declared = _get(name, models)
    first = _parse(start) if start else cfg.splits.discovery.start
    last = _parse(end) if end else (cfg.splits.validation.end or date.today())
    try:
        result = run_backtest(declared, cfg, start=first, end=last, data_dir=data)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]{name} could not be backtested: {exc}[/]")
        raise typer.Exit(1) from None

    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False,
                  title=f"[bold]{name}[/] {first} -> {last}  |  {cfg.backtest.portfolio}, q={cfg.backtest.quantile}, {cfg.costs.bps_per_trade:g} bps",
                  title_justify="left")
    for col in ("split", "days", "mean bps", "sharpe", "hit", "max dd", "turnover", "total"):
        table.add_column(col, justify="right" if col != "split" else "left")
    for split, m in result.by_split.items():
        table.add_row(split, str(m.days), f"{m.mean_bps:+.1f}", f"{m.ann_sharpe:.2f}", f"{m.hit_rate:.0%}",
                      f"{m.max_drawdown:.1%}", f"{m.avg_turnover:.2f}", f"{m.total_return:+.1%}")
    console.print()
    console.print(table)
    unrealised = sum(1 for d in result.days if d.net is None)
    if unrealised:
        console.print(f"  [dim]{unrealised} day(s) at the live edge have no realised return yet[/]")
    for gate in result.gates:
        mark = "[bold green]PASS[/]" if gate.passed else "[bold red]FAIL[/]"
        console.print(f"  {mark}  {gate.probe}  {gate.detail}")
    console.print()
    if result.passed:
        _, _, promoted = _dirs(None, None, None)
        _next(f"asof promote {name} --as-of {last}   (freezes v{len(read_history(name, promoted_dir=promoted)) + 1}; the gate re-runs this backtest)")
    else:
        _next("the validation gates in asof.toml are not met; keep researching, or discuss the gates with whoever owns asof.toml")
        raise typer.Exit(1)


@app.command()
def replay(
    name: str,
    version: int = typer.Option(None, "--version", help="Promoted version; default: latest"),
    models: Path = MODELS,
    data: Path = DATA,
    promoted: Path = PROMOTED,
) -> None:
    """Re-run a promoted version on its checked date. Has the world moved?"""
    models, data, promoted = _dirs(models, data, promoted)
    versions = read_history(name, promoted_dir=promoted)
    if not versions:
        console.print(f"[yellow]{name} has never been promoted[/]")
        raise typer.Exit(1)
    chosen = version or versions[-1]["version"]
    report = do_replay(name, chosen, data_dir=data, promoted_dir=promoted, models_dir=models)
    console.print()
    if report["reproduces"]:
        console.print(f"  [green]{name} v{chosen} reproduces its golden output for {report['as_of']}[/]")
        _next("nothing; the promoted answer still stands")
        return
    if report["source_unchanged"]:
        console.print(f"  [yellow]{name} v{chosen} no longer reproduces {report['as_of']} and the source is unchanged: the data was restated[/]")
        console.print(f"  keys that moved: {', '.join(report['moved_keys']) or '(non-dict output)'}")
        _next("tell the data owner; if the restatement is right, promote a new version rather than editing this one")
    else:
        console.print(f"  [yellow]{name} v{chosen} no longer reproduces {report['as_of']}: the source changed since promotion[/]")
        _next(f"asof promote {name} --as-of <date>   (a changed body is a new version, never an edit)")
    raise typer.Exit(1)


@app.command()
def status(models: Path = MODELS, promoted: Path = PROMOTED) -> None:
    """Where every signal is, and what to do next."""
    models, _, promoted = _dirs(models, None, promoted)
    cfg = _config()
    found = registry.load_dir(models)
    console.print()
    if cfg is None:
        console.print("  [yellow]no asof.toml[/]: the project has no ground yet")
        _next("asof new <project>   (or write asof.toml by hand: universe, calendar, costs, splits, contracts)")
        return
    console.print(f"  project [bold]{cfg.project.name}[/]  |  ground {cfg.fingerprint()}  |  {len(cfg.contracts)} data contract(s)")
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", pad_edge=False)
    table.add_column("signal")
    table.add_column("promoted")
    table.add_column("next")
    for name in sorted(found):
        versions = read_history(name, promoted_dir=promoted)
        if not versions:
            table.add_row(f"[bold]{name}[/]", "[dim]never[/]", f"asof check {name} --as-of <date>")
            continue
        latest = versions[-1]
        bundle = promoted / name / f"v{latest['version']}"
        nxt = f"asof replay {name}   (deployed as v{latest['version']})" if bundle.exists() else f"asof export {name}"
        table.add_row(f"[bold]{name}[/]", f"v{latest['version']} on {latest['promoted_at'][:10]}", nxt)
    console.print(table)
    console.print()


@app.command()
def export(
    name: str,
    version: int = typer.Option(None, "--version", help="Promoted version; default: latest"),
    models: Path = MODELS,
    promoted: Path = PROMOTED,
) -> None:
    """Lay out the deployable bundle for a promoted version. No code is generated."""
    models, _, promoted = _dirs(models, None, promoted)
    cfg = _config()
    if cfg is None:
        console.print("[yellow]no asof.toml found; a bundle carries the project ground with it[/]")
        raise typer.Exit(1)
    try:
        bundle = do_export(name, config=cfg, version=version, models_dir=models,
                           promoted_dir=promoted, config_path=find_config() or "asof.toml")
    except (KeyError, FileNotFoundError, ValueError) as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(1) from None

    files = sorted(p.relative_to(bundle.path).as_posix() for p in bundle.path.rglob("*") if p.is_file())
    console.print()
    console.print(Panel(
        f"[bold]{name} v{bundle.version}[/] is deployable\n{_rel(bundle.path)}\n\n" + "\n".join(f"  {f}" for f in files),
        expand=False, border_style="green",
    ))
    console.print(f"  sink: [bold]{cfg.sink.kind}[/] -> {cfg.sink.target}   schedule: [bold]{cfg.schedule}[/] UTC")
    _next(
        f"commit and push {_rel(bundle.path)}; the workflow runs it on schedule. "
        f"To run it now: python {_rel(bundle.path)}/run.py --as-of <date>"
    )


@app.command()
def serve(
    name: str,
    as_of: str = typer.Option(None, "--as-of", help="The day to price; default today"),
    version: int = typer.Option(None, "--version", help="Promoted version to attribute; default latest"),
    models: Path = MODELS,
    data: Path = DATA,
    promoted: Path = PROMOTED,
) -> None:
    """Price one day with a promoted signal and publish it to the sink."""
    models, data, promoted = _dirs(models, data, promoted)
    cfg = _config()
    if cfg is None:
        console.print("[yellow]no asof.toml found; serving needs the sink and calendar[/]")
        raise typer.Exit(1)
    versions = read_history(name, promoted_dir=promoted)
    if not versions:
        console.print(f"[yellow]{name} has never been promoted; serve only publishes promoted signals[/]")
        _next(f"asof promote {name} --as-of <date>")
        raise typer.Exit(1)
    payload = versions[-1] if version is None else next((p for p in versions if p["version"] == version), None)
    if payload is None:
        console.print(f"[yellow]{name} v{version} was never promoted[/]")
        raise typer.Exit(1)
    day = _parse(as_of) if as_of else date.today()
    declared = _get(name, models)
    result = run_model(declared, day, data_dir=data)
    rows = rows_from_output(result.output, as_of=day, model=name, version=payload["version"],
                            fingerprint=payload["fingerprint"])
    receipt = publish(rows, cfg.sink)
    console.print(f"\n  {name} v{payload['version']} priced {day}: {len(rows)} row(s)  ->  {receipt}")
    _next(f"asof replay {name}   (any time, to confirm the promoted answer still reproduces)")


@app.command()
def new(
    name: str,
    directory: Path = typer.Option(None, "--dir", help="Where to create it; default ./<name>"),
) -> None:
    """Start a project: ground, contracts, a sample panel, one working signal, and tests."""
    from .scaffold import scaffold

    target = directory or Path(name)
    if target.exists() and any(target.iterdir()):
        console.print(f"[yellow]{target} exists and is not empty[/]")
        raise typer.Exit(1)
    created = scaffold(name, target)
    console.print()
    console.print(Panel(
        f"[bold]{name}[/] is ready at {target}\n\n" + "\n".join(f"  {f}" for f in created),
        expand=False, border_style="green",
    ))
    _next(f"cd {target}  &&  asof status   (then write your signal in signals/, next to the example)")


if __name__ == "__main__":  # pragma: no cover
    app()
