import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models.session import init_db
from app.api.routes import router, pipeline
from app.api.reddit_routes import reddit_router
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def scheduled_pipeline_run():
    """Scheduled full YouTube pipeline cycle."""
    try:
        results = await pipeline.run_full_cycle()
        logger.info(f"Scheduled pipeline run complete: {results}")
    except Exception as e:
        import traceback
        logger.error(f"Scheduled pipeline run failed: {e}\n{traceback.format_exc()}")


async def scheduled_reddit_run():
    """Scheduled Reddit pipeline cycle."""
    try:
        results = await pipeline.run_reddit_cycle()
        logger.info(f"Scheduled Reddit pipeline run complete: {results}")
    except Exception as e:
        logger.error(f"Scheduled Reddit pipeline run failed: {e}")


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
    scheduler.add_job(
        scheduled_reddit_run,
        "interval",
        minutes=settings.reddit_polling_interval_minutes,
        id="reddit_pipeline_cycle",
        name="Reddit Pipeline Cycle",
    )
    scheduler.start()
    logger.info(
        f"Scheduler started (YT: {settings.polling_interval_minutes}min, "
        f"Reddit: {settings.reddit_polling_interval_minutes}min)"
    )

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
app.include_router(reddit_router)


@app.get("/health")
async def health():
    return {
        "status": "alive",
        "service": "unreal-engine",
        "scheduler_running": scheduler.running,
    }


@app.get("/")
async def root():
    return {
        "name": "Community Intelligence Pipeline",
        "codename": "Unreal Engine v2",
        "status": "operational",
        "sources": ["youtube", "reddit"],
    }


@app.get("/debug/connectivity")
async def debug_connectivity():
    """Test outbound connectivity to YouTube and Reddit APIs."""
    import httpx
    results = {}

    # Test YouTube API key
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={
                    "key": settings.youtube_api_key,
                    "part": "snippet",
                    "forHandle": "YouTube",
                }
            )
            results["youtube"] = {
                "status": resp.status_code,
                "has_items": len(resp.json().get("items", [])) > 0,
                "error": resp.json().get("error", {}).get("message") if resp.status_code != 200 else None,
                "api_key_set": bool(settings.youtube_api_key),
                "api_key_prefix": settings.youtube_api_key[:8] + "..." if settings.youtube_api_key else "EMPTY",
            }
    except Exception as e:
        results["youtube"] = {"error": str(e)}

    # Test Reddit .json access
    try:
        ua = "python:unreal-engine:v2.0 (by /u/OB1Shanobi)"
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                "https://www.reddit.com/r/UFOs/about.json",
                headers={"User-Agent": ua, "Accept": "application/json"},
            )
            results["reddit"] = {
                "status": resp.status_code,
                "has_data": "display_name" in resp.text[:500] if resp.status_code == 200 else False,
            }
    except Exception as e:
        results["reddit"] = {"error": str(e)}

    return results


@app.get("/debug/test-poll")
async def debug_test_poll():
    """Test polling a single channel to diagnose errors."""
    import traceback
    from app.models.session import async_session
    from app.models.database import Channel
    from sqlalchemy import select

    try:
        async with async_session() as session:
            result = await session.execute(
                select(Channel).where(Channel.active == True).limit(1)
            )
            channel = result.scalar_one_or_none()
            if not channel:
                return {"error": "No channels found"}

            info = await pipeline.youtube.get_channel_info(channel.channel_id)
            return {
                "channel": channel.name,
                "channel_id": channel.channel_id,
                "api_response": info,
            }
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}
