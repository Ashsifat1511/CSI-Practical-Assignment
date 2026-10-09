"""FastAPI application: wires module routers together. No business logic lives here."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.modules.ack.routes import router as ack_router
from app.modules.audit.service import register_audit_handlers
from app.modules.events.routes import router as events_router
from app.modules.mqtt.routes import router as mqtt_router
from app.modules.state.routes import router as state_router
from app.shared.db import close_pool, init_pool, transaction

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_pool()
    register_audit_handlers()
    yield
    close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="NorthBridge Garments Production API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception):
        # Log details server-side only; never leak SQL, credentials or stack traces to clients.
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"error": "Internal server error"})

    @app.get("/api/health")
    def health():
        with transaction() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    for router in (events_router, state_router, ack_router, mqtt_router):
        app.include_router(router)
    return app


app = create_app()
