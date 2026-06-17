"""FastAPI application factory and Typer CLI entry point for memory-layer."""

import typer

cli = typer.Typer(name="memory-layer", help="memory-layer: AI memory layer CLI.")


@cli.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, help="Enable auto-reload (dev only)."),
) -> None:
    """Start the memory-layer HTTP server."""
    import uvicorn

    uvicorn.run(
        "memory_layer.adapters.rest.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


def create_app():
    """FastAPI application factory — wires routes, middleware, and lifecycle hooks."""
    from fastapi import FastAPI

    app = FastAPI(
        title="memory-layer",
        version="0.1.0",
        description="Framework-agnostic AI memory layer for production agentic systems.",
    )

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    return app


if __name__ == "__main__":
    cli()
