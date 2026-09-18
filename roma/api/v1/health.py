"""Process-liveness endpoint."""


def mount_health_route(app) -> None:
    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "active_calls": len(getattr(app.state, "calls", {}) or {})}


__all__ = ["mount_health_route"]
