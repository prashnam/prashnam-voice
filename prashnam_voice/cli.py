from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import (
    ALL_LANG_CODES,
    AUDIO_CACHE_DIR,
    DEFAULT_PACE,
    LANGUAGES,
    PACE_PHRASES,
    parse_langs,
)
from .pipeline import run_pipeline, JobProgress, LangProgress
from .projects import ProjectStore

app = typer.Typer(
    add_completion=False,
    help="prashnam-voice — local English -> Indian-language voice poll generator.",
)
projects_app = typer.Typer(help="Manage on-disk projects (used by the web app).")
app.add_typer(projects_app, name="projects")
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def generate(
    question: str = typer.Option(None, "--question", "-q", help="Poll question in English."),
    option: list[str] = typer.Option(
        None, "--option", "-o", help="A multiple-choice option (repeat for each)."
    ),
    from_json: Path = typer.Option(
        None, "--from-json", help="Load {question, options} from a JSON file."
    ),
    langs: str = typer.Option(
        "all", "--langs", "-l", help=f"Comma-separated codes or 'all'. Codes: {','.join(ALL_LANG_CODES)}"
    ),
    out: Path = typer.Option(Path("./output"), "--out", help="Output root directory."),
    voice: list[str] = typer.Option(
        None, "--voice", help="Override voice for a language, e.g. --voice hi=Aditi (repeatable)."
    ),
    pace: list[str] = typer.Option(
        None,
        "--pace",
        help=(
            f"Pacing. Bare value sets the global default ({'/'.join(PACE_PHRASES)}); "
            "use lang=pace for per-language overrides, e.g. --pace ta=slow. Repeatable."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Translate + synthesize one poll."""
    _setup_logging(verbose)

    if from_json:
        payload = json.loads(from_json.read_text(encoding="utf-8"))
        q = (question or payload.get("question", "")).strip()
        opts = list(option or []) or list(payload.get("options", []))
    else:
        q = (question or "").strip()
        opts = list(option or [])

    if not q:
        console.print("[red]error:[/red] --question is required (or use --from-json).")
        raise typer.Exit(2)
    if not opts:
        console.print("[red]error:[/red] at least one --option is required.")
        raise typer.Exit(2)

    voices: dict[str, str] = {}
    for entry in voice or []:
        if "=" not in entry:
            console.print(f"[red]error:[/red] --voice must be lang=name, got {entry!r}")
            raise typer.Exit(2)
        k, v = entry.split("=", 1)
        if k not in LANGUAGES:
            console.print(f"[red]error:[/red] unknown language in --voice: {k}")
            raise typer.Exit(2)
        voices[k] = v

    paces: dict[str, str] = {}
    default_pace = DEFAULT_PACE
    for entry in pace or []:
        if "=" in entry:
            k, v = entry.split("=", 1)
            if k not in LANGUAGES:
                console.print(f"[red]error:[/red] unknown language in --pace: {k}")
                raise typer.Exit(2)
            if v not in PACE_PHRASES:
                console.print(f"[red]error:[/red] unknown pace {v!r}; use one of {'/'.join(PACE_PHRASES)}")
                raise typer.Exit(2)
            paces[k] = v
        else:
            if entry not in PACE_PHRASES:
                console.print(f"[red]error:[/red] unknown pace {entry!r}; use one of {'/'.join(PACE_PHRASES)}")
                raise typer.Exit(2)
            default_pace = entry

    lang_codes = parse_langs(langs)
    progress = JobProgress()

    def on_update(p: JobProgress) -> None:
        # Single-line status; verbose mode gets its own log lines.
        done = sum(lp.audio_done for lp in p.by_lang.values())
        total = sum(lp.audio_total for lp in p.by_lang.values()) or 1
        translated = sum(1 for lp in p.by_lang.values() if lp.translated)
        sys.stderr.write(
            f"\r[{p.status}] translated {translated}/{len(p.by_lang)} langs | "
            f"audio {done}/{total}     "
        )
        sys.stderr.flush()

    try:
        run_pipeline(
            q, opts, lang_codes, out_root=out, voices=voices,
            paces=paces, default_pace=default_pace,
            progress=progress, on_update=on_update,
        )
    except Exception as exc:
        sys.stderr.write("\n")
        console.print(f"[red]failed:[/red] {exc}")
        raise typer.Exit(1)
    finally:
        sys.stderr.write("\n")

    console.print(f"\n[green]done[/green] in {progress.elapsed_s:.1f}s")
    console.print(f"  output: {progress.out_dir}")
    console.print(f"  translations.json + meta.json + per-language MP3s")


@app.command(name="list-voices")
def list_voices(lang: str = typer.Option(None, "--lang", "-l")):
    """List the default voice mapping for one or all languages."""
    table = Table(title="prashnam-voice default voices")
    table.add_column("Code"); table.add_column("Language"); table.add_column("Voice")
    for code, spec in LANGUAGES.items():
        if lang and code != lang:
            continue
        table.add_row(code, spec.name, spec.voice)
    console.print(table)


@app.command(name="cache-clear")
def cache_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
):
    """Wipe the audio cache."""
    if not AUDIO_CACHE_DIR.exists():
        console.print("Cache is already empty.")
        return
    if not yes:
        confirm = typer.confirm(f"Delete {AUDIO_CACHE_DIR}?")
        if not confirm:
            raise typer.Exit(0)
    shutil.rmtree(AUDIO_CACHE_DIR)
    console.print(f"Removed {AUDIO_CACHE_DIR}.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    out: Path = typer.Option(Path("./output"), "--out"),
    projects_root: Path = typer.Option(
        Path("./projects"), "--projects-root",
        help="Where to keep project directories.",
    ),
):
    """Launch the local web app."""
    import uvicorn  # lazy

    from .server.app import build_app

    fastapi_app = build_app(
        out_root=out.resolve(),
        projects_root=projects_root.resolve(),
    )
    console.print(f"\n  prashnam-voice web app: http://{host}:{port}")
    console.print(f"  projects root: {projects_root.resolve()}\n")
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


# --------------------------------------------------------------------------
# `projects` subcommands
# --------------------------------------------------------------------------


def _store(projects_root: Path) -> ProjectStore:
    return ProjectStore(projects_root.resolve())


@projects_app.command("list")
def projects_list(
    projects_root: Path = typer.Option(Path("./projects"), "--projects-root"),
):
    """List projects under <projects_root>."""
    rows = _store(projects_root).list_projects()
    if not rows:
        console.print("[muted]No projects yet.[/muted]")
        return
    table = Table(title="Projects")
    for col in ("ID", "Name", "Segments", "Langs", "Updated"):
        table.add_column(col)
    for p in rows:
        table.add_row(
            p["id"], p["name"], str(p["segment_count"]),
            ",".join(p["langs"]), p["updated_at"],
        )
    console.print(table)


@projects_app.command("create")
def projects_create(
    name: str = typer.Argument(..., help="Project name."),
    langs: str = typer.Option("all", "--langs", "-l"),
    projects_root: Path = typer.Option(Path("./projects"), "--projects-root"),
):
    """Create a new project."""
    codes = parse_langs(langs)
    proj = _store(projects_root).create(name, codes)
    console.print(f"[green]created[/green] {proj.id}")


@projects_app.command("show")
def projects_show(
    pid: str = typer.Argument(...),
    projects_root: Path = typer.Option(Path("./projects"), "--projects-root"),
):
    """Print a project's segments and current takes."""
    proj = _store(projects_root).load(pid)
    console.print(f"[bold]{proj.name}[/bold] ({proj.id})")
    console.print(f"  langs: {','.join(proj.langs)}")
    console.print(f"  default pace: {proj.default_pace}")
    console.print(f"  segments: {len(proj.segments)}")
    for i, s in enumerate(proj.segments):
        label = "Q" if s.type == "question" else f"O{i}"
        takes = ",".join(f"{l}:{aid}" for l, aid in s.current_takes.items()) or "-"
        console.print(f"  [{label}] {s.english!r}  takes={takes}")


@app.command("batch")
def batch_import(
    csv_path: Path = typer.Argument(..., exists=True, readable=True, dir_okay=False),
    domain: str = typer.Option("poll", "--domain", "-d", help="poll or announcement"),
    langs: str = typer.Option("all", "--langs", "-l"),
    projects_root: Path = typer.Option(Path("./projects"), "--projects-root"),
):
    """Bulk-create projects from a CSV file.

    poll schema:        group_id,type,english[,name,langs]
    announcement schema: group_id,english[,name,langs]
    """
    from .csv_import import import_csv

    codes = parse_langs(langs)
    store = _store(projects_root)
    try:
        result = import_csv(csv_path, store, domain=domain, langs=codes)
    except ValueError as exc:
        console.print(f"[red]error:[/red] {exc}")
        raise typer.Exit(2)

    if result.projects:
        console.print(f"[green]created[/green] {len(result.projects)} project(s):")
        for p in result.projects:
            console.print(f"  • {p.id} — {p.name} ({len(p.segments)} segments)")
    if result.errors:
        console.print(f"[yellow]{len(result.errors)} error(s):[/yellow]")
        for e in result.errors:
            console.print(f"  line {e.line_no}: {e.message}")
    if not result.projects and not result.errors:
        console.print("[muted]Nothing to import.[/muted]")


@projects_app.command("delete")
def projects_delete(
    pid: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
    projects_root: Path = typer.Option(Path("./projects"), "--projects-root"),
):
    """Delete a project (removes its folder)."""
    if not yes and not typer.confirm(f"Delete project {pid}?"):
        raise typer.Exit(0)
    _store(projects_root).delete(pid)
    console.print(f"[green]deleted[/green] {pid}")


@app.command()
def prefetch():
    """Download both model weights ahead of time (~4.5 GB)."""
    _setup_logging(verbose=True)
    from .translator import Translator
    from .tts import TTS

    console.print("[cyan]Downloading translator weights…[/cyan]")
    Translator().close()
    console.print("[cyan]Downloading TTS weights…[/cyan]")
    TTS().close()
    console.print("[green]done[/green] — weights are cached in ~/.cache/huggingface")


if __name__ == "__main__":
    app()
