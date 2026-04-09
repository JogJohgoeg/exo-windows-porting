"""
Exo Windows Porting — main CLI entry point.

Commands
--------
  exo-windows-porting info               Show hardware and backend info
  exo-windows-porting run MODEL PROMPT   Single-shot local inference
  exo-windows-porting serve MODEL        Start HTTP inference server
  exo-windows-porting worker ...         Start a distributed pipeline worker
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

app = typer.Typer(
    name="exo-windows-porting",
    help="Zero-Config Distributed LLM Inference — Windows + ROCm/CUDA",
    no_args_is_help=True,
)
console = Console()


# ──────────────────────────────────────────────────────────────────────────────
# info
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def info() -> None:
    """Show detected hardware and available inference backends."""
    from exo_windows_porting.backend.factory import get_backend_factory

    console.print(Panel.fit("[bold]Exo Windows Porting — System Info[/bold]"))

    try:
        factory = get_backend_factory()
        hw = factory.hardware
        info_data = factory.get_backend_info()
    except Exception as exc:
        console.print(f"[red]Hardware detection failed:[/red] {exc}")
        raise typer.Exit(1)

    # Hardware table
    hw_table = Table(title="Hardware", show_header=True)
    hw_table.add_column("Component", style="cyan")
    hw_table.add_column("Detected", style="green")

    hw_table.add_row("NVIDIA GPU", "[green]Yes[/green]" if hw.has_nvidia_gpu else "[dim]No[/dim]")
    for d in hw.nvidia_devices:
        hw_table.add_row(f"  └─ GPU {d['id']}", d["name"])

    hw_table.add_row("AMD GPU", "[green]Yes[/green]" if hw.has_amd_gpu else "[dim]No[/dim]")
    for d in hw.amd_devices:
        hw_table.add_row(f"  └─ {d.get('name', '?')}", d.get("compute_units", ""))

    console.print(hw_table)

    # Backend table
    be_table = Table(title="Inference Backends", show_header=True)
    be_table.add_column("Backend", style="cyan")
    be_table.add_column("Available", style="green")
    be_table.add_column("Selected", style="yellow")

    selected = info_data["selected_backend"]
    for name, available in info_data["available_backends"].items():
        mark = "[green]✓[/green]" if available else "[dim]✗[/dim]"
        sel = "[bold yellow]← active[/bold yellow]" if name == selected else ""
        be_table.add_row(name, mark, sel)

    console.print(be_table)
    console.print(f"\nSelected backend: [bold yellow]{selected}[/bold yellow]")


# ──────────────────────────────────────────────────────────────────────────────
# run
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def run(
    model: str = typer.Argument(..., help="Path to GGUF model file"),
    prompt: str = typer.Argument(..., help="Prompt text to complete"),
    max_tokens: int = typer.Option(512, "--max-tokens", "-n", help="Max tokens to generate"),
    temperature: float = typer.Option(0.7, "--temperature", "-t", help="Sampling temperature"),
    cpu: bool = typer.Option(False, "--cpu", help="Force CPU mode"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
) -> None:
    """Run single-node inference and print the result."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from exo_windows_porting.backend.factory import BackendConfig, get_backend_factory

    config = BackendConfig(
        force_cpu=cpu,
        max_tokens=max_tokens,
        verbose=verbose,
    )
    factory = get_backend_factory(config)

    try:
        backend = factory.create_backend(model, force_cpu=cpu)
    except Exception as exc:
        console.print(f"[red]Failed to load backend:[/red] {exc}")
        raise typer.Exit(1)

    console.print(f"[dim]Backend: {backend.get_backend_name()}[/dim]")
    console.print(f"[dim]Model:   {model}[/dim]")
    console.print()

    async def _generate():
        return await backend.generate(prompt, max_tokens=max_tokens)

    try:
        result = asyncio.run(_generate())
    except Exception as exc:
        console.print(f"[red]Inference failed:[/red] {exc}")
        raise typer.Exit(1)

    console.print(result)


# ──────────────────────────────────────────────────────────────────────────────
# serve
# ──────────────────────────────────────────────────────────────────────────────

@app.command()
def serve(
    model: str = typer.Argument(..., help="Path to GGUF model file"),
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    cpu: bool = typer.Option(False, "--cpu", help="Force CPU mode"),
    workers: int = typer.Option(1, "--workers", help="Number of uvicorn workers"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Start the HTTP inference server (OpenAI-compatible /v1/completions)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        import uvicorn
        from fastapi import FastAPI
    except ImportError:
        console.print("[red]uvicorn/fastapi not installed.[/red] Run: pip install uvicorn fastapi")
        raise typer.Exit(1)

    from exo_windows_porting.backend.factory import BackendConfig, get_backend_factory

    config = BackendConfig(force_cpu=cpu)
    factory = get_backend_factory(config)

    try:
        backend = factory.create_backend(model, force_cpu=cpu)
    except Exception as exc:
        console.print(f"[red]Failed to load backend:[/red] {exc}")
        raise typer.Exit(1)

    # Minimal FastAPI app — wraps the backend
    api = FastAPI(title="Exo Windows Porting API", version="0.1.0")

    from pydantic import BaseModel

    class CompletionRequest(BaseModel):
        prompt: str
        max_tokens: int = 512
        temperature: float = 0.7

    class CompletionResponse(BaseModel):
        text: str
        backend: str

    @api.get("/health")
    async def health():
        return {"status": "ok", "backend": backend.get_backend_name()}

    @api.post("/v1/completions", response_model=CompletionResponse)
    async def completions(req: CompletionRequest):
        text = await backend.generate(req.prompt, max_tokens=req.max_tokens)
        return CompletionResponse(text=text, backend=backend.get_backend_name())

    console.print(
        Panel.fit(
            f"[bold green]Exo API server[/bold green]\n"
            f"Backend : [yellow]{backend.get_backend_name()}[/yellow]\n"
            f"Model   : {model}\n"
            f"Listening on [bold]http://{host}:{port}[/bold]"
        )
    )
    uvicorn.run(api, host=host, port=port, workers=workers, log_level="warning")


# ──────────────────────────────────────────────────────────────────────────────
# worker  (thin alias — full docs are in worker_cli.py)
# ──────────────────────────────────────────────────────────────────────────────

@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def worker(
    ctx: typer.Context,
) -> None:
    """Start a distributed pipeline worker (passes args to exo-worker)."""
    from exo_windows_porting.distributed.worker_cli import main as _worker_main
    import sys as _sys

    # Forward remaining args to the worker CLI parser
    _sys.argv = ["exo-worker"] + ctx.args
    asyncio.run(_worker_main())


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    app()


if __name__ == "__main__":
    main()
