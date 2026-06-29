from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import ingestion, goals, assumptions, engine, earmarks, chat
from app.auth.router import router as auth_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Daily NAV cron
    scheduler = BackgroundScheduler()
    from app.cron.nav_refresh import refresh_navs
    scheduler.add_job(refresh_navs, "cron", hour=6, minute=0, id="nav_refresh")
    scheduler.start()

    yield

    scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Goal-Based MF Tracker API",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin, "http://localhost:3000", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(ingestion.router)
    app.include_router(goals.router)
    app.include_router(assumptions.router)
    app.include_router(engine.router)
    app.include_router(earmarks.router)
    app.include_router(chat.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app


app = create_app()
