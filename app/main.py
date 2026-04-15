import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models.session import init_db
from app.api.routes import router, pipeline
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_pipeline_run():
    """Scheduled full pipeline cycle."""
    try:
        results = await pipeline.run_full_cycle()
        logger.info(f"Scheduled pipeline run complete: {results}")
    except Exception as e:
        logger.error(f"Scheduled pipeline run failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized.")

    # Start scheduler
    scheduler.add_job(
        scheduled_pipeline_run,
        "interval",
        minutes=settings.polling_interval_minutes,
        id="pipeline_cycle",
        name="Full Pipeline Cycle",
    )
    scheduler.start()
    logger.info(f"Scheduler started (interval: {settings.polling_interval_minutes}min)")

    yield

    # Shutdown
    scheduler.shutdown()
    await pipeline.close()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="YouTube Intelligence Pipeline",
    description="Podcast monitoring and analysis for ALIENDB / Catastrophic Disclosure",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Lock this down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health():
    return {
        "status": "alive",
        "service": "yt-pipeline",
        "scheduler_running": scheduler.running,
    }


@app.get("/")
async def root():
    return {
        "name": "YouTube Intelligence Pipeline",
        "codename": "Unreal Engine v1",
        "status": "operational",
    }
