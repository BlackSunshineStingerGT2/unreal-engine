from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, timezone, timedelta

from app.models.database import (
    Channel, Video, Transcript, Comment,
    EngagementSnapshot, VideoAnalysis, PipelineLog
)
from app.models.session import get_session
from app.config import settings
from app.services.pipeline import Pipeline

router = APIRouter(prefix="/api/v1", tags=["pipeline"])
pipeline = Pipeline()


# -------------------------------------------------------------------------
# Auth
# -------------------------------------------------------------------------

async def verify_token(authorization: str = Header(...)):
    if authorization != f"Bearer {settings.pipeline_token}":
        raise HTTPException(status_code=401, detail="Invalid token")


# -------------------------------------------------------------------------
# Channel management
# -------------------------------------------------------------------------

@router.post("/channels", dependencies=[Depends(verify_token)])
async def add_channel(
    channel_id: str = None,
    handle: str = None,
    category: str = "uap",
    priority: int = 5,
    session: AsyncSession = Depends(get_session),
):
    """Add a channel to the watch list."""
    from app.services.youtube import YouTubeService
    yt = YouTubeService()

    try:
        if handle:
            info = await yt.get_channel_by_handle(handle)
        elif channel_id:
            info = await yt.get_channel_info(channel_id)
        else:
            raise HTTPException(400, "Provide channel_id or handle")

        if not info:
            raise HTTPException(404, "Channel not found")

        # Check if exists
        existing = await session.execute(
            select(Channel).where(Channel.channel_id == info["channel_id"])
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "Channel already registered")

        channel = Channel(
            channel_id=info["channel_id"],
            name=info["name"],
            description=info["description"],
            subscriber_count=info["subscriber_count"],
            video_count=info["video_count"],
            category=category,
            priority=priority,
        )
        session.add(channel)
        await session.commit()

        return {
            "status": "added",
            "channel": {
                "id": info["channel_id"],
                "name": info["name"],
                "subscribers": info["subscriber_count"],
                "videos": info["video_count"],
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to add channel: {str(e)}")
    finally:
        await yt.close()


@router.get("/channels", dependencies=[Depends(verify_token)])
async def list_channels(session: AsyncSession = Depends(get_session)):
    """List all registered channels."""
    result = await session.execute(
        select(Channel).order_by(Channel.priority.desc(), Channel.name)
    )
    channels = result.scalars().all()
    return {
        "count": len(channels),
        "channels": [
            {
                "channel_id": ch.channel_id,
                "name": ch.name,
                "category": ch.category,
                "priority": ch.priority,
                "subscribers": ch.subscriber_count,
                "videos": ch.video_count,
                "active": ch.active,
                "last_checked": ch.last_checked.isoformat() if ch.last_checked else None,
            }
            for ch in channels
        ]
    }


@router.patch("/channels/{channel_id}", dependencies=[Depends(verify_token)])
async def update_channel(
    channel_id: str,
    priority: Optional[int] = None,
    category: Optional[str] = None,
    active: Optional[bool] = None,
    session: AsyncSession = Depends(get_session),
):
    """Update channel settings."""
    result = await session.execute(
        select(Channel).where(Channel.channel_id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Channel not found")

    if priority is not None:
        channel.priority = priority
    if category is not None:
        channel.category = category
    if active is not None:
        channel.active = active

    await session.commit()
    return {"status": "updated", "channel_id": channel_id}


@router.delete("/channels/{channel_id}", dependencies=[Depends(verify_token)])
async def remove_channel(
    channel_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Remove a channel from the watch list."""
    result = await session.execute(
        select(Channel).where(Channel.channel_id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(404, "Channel not found")

    await session.delete(channel)
    await session.commit()
    return {"status": "removed", "channel_id": channel_id}


# -------------------------------------------------------------------------
# Pipeline controls
# -------------------------------------------------------------------------

@router.post("/pipeline/run", dependencies=[Depends(verify_token)])
async def run_pipeline():
    """Trigger a full pipeline cycle."""
    import traceback
    try:
        results = await pipeline.run_full_cycle()
        return {"status": "complete", "results": results}
    except Exception as e:
        return {"status": "error", "detail": str(e), "traceback": traceback.format_exc()}


@router.post("/pipeline/poll", dependencies=[Depends(verify_token)])
async def poll_channels():
    """Poll all channels for new videos only."""
    results = await pipeline.poll_all_channels()
    return {"status": "complete", "results": results}


@router.post("/pipeline/collect/{video_id}", dependencies=[Depends(verify_token)])
async def collect_video(
    video_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger collection for a specific video."""
    result = await session.execute(
        select(Video).where(Video.video_id == video_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(404, "Video not found. Poll the channel first.")

    results = {}

    if not video.transcript_collected:
        results["transcript"] = await pipeline.collect_transcript(session, video)

    if not video.comments_collected:
        count = await pipeline.collect_comments(session, video)
        results["comments_collected"] = count

    if not video.analysis_complete and video.transcript_collected:
        results["analyzed"] = await pipeline.analyze_video(session, video)

    return {"status": "complete", "video_id": video_id, "results": results}


@router.post("/pipeline/analyze/{video_id}", dependencies=[Depends(verify_token)])
async def analyze_video(
    video_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Force re-analysis of a video."""
    result = await session.execute(
        select(Video).where(Video.video_id == video_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(404, "Video not found")

    # Delete existing analysis if any
    existing = await session.execute(
        select(VideoAnalysis).where(VideoAnalysis.video_id == video_id)
    )
    old = existing.scalar_one_or_none()
    if old:
        await session.delete(old)
        video.analysis_complete = False
        await session.commit()

    success = await pipeline.analyze_video(session, video)
    return {"status": "analyzed" if success else "failed", "video_id": video_id}


# -------------------------------------------------------------------------
# Data retrieval
# -------------------------------------------------------------------------

@router.get("/videos", dependencies=[Depends(verify_token)])
async def list_videos(
    channel_id: Optional[str] = None,
    analyzed: Optional[bool] = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List collected videos with filters."""
    query = select(Video).order_by(desc(Video.published_at))

    if channel_id:
        query = query.where(Video.channel_id == channel_id)
    if analyzed is not None:
        query = query.where(Video.analysis_complete == analyzed)

    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    videos = result.scalars().all()

    return {
        "count": len(videos),
        "videos": [
            {
                "video_id": v.video_id,
                "channel_id": v.channel_id,
                "title": v.title,
                "published_at": v.published_at.isoformat(),
                "duration_seconds": v.duration_seconds,
                "transcript_collected": v.transcript_collected,
                "comments_collected": v.comments_collected,
                "analysis_complete": v.analysis_complete,
            }
            for v in videos
        ]
    }


@router.get("/videos/{video_id}", dependencies=[Depends(verify_token)])
async def get_video_detail(
    video_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get full video detail including analysis."""
    result = await session.execute(
        select(Video).where(Video.video_id == video_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(404, "Video not found")

    # Get analysis
    analysis_result = await session.execute(
        select(VideoAnalysis).where(VideoAnalysis.video_id == video_id)
    )
    analysis = analysis_result.scalar_one_or_none()

    # Get engagement snapshots
    snap_result = await session.execute(
        select(EngagementSnapshot)
        .where(EngagementSnapshot.video_id == video_id)
        .order_by(EngagementSnapshot.hours_since_publish)
    )
    snapshots = snap_result.scalars().all()

    # Comment stats
    comment_count = await session.execute(
        select(func.count()).select_from(Comment).where(Comment.video_id == video_id)
    )

    response = {
        "video_id": video.video_id,
        "channel_id": video.channel_id,
        "title": video.title,
        "description": video.description,
        "published_at": video.published_at.isoformat(),
        "duration_seconds": video.duration_seconds,
        "tags": video.tags,
        "comments_count": comment_count.scalar(),
        "engagement_timeline": [
            {
                "hours": s.hours_since_publish,
                "views": s.view_count,
                "likes": s.like_count,
                "comments": s.comment_count,
                "views_per_hour": round(s.views_per_hour, 2),
            }
            for s in snapshots
        ],
    }

    if analysis:
        response["analysis"] = {
            "topics": analysis.topics,
            "entities": analysis.entities,
            "claims": analysis.claims,
            "questions_raised": analysis.questions_raised,
            "research_directives": analysis.research_directives,
            "summary": analysis.summary,
            "sentiment_score": analysis.sentiment_score,
            "information_density": analysis.information_density,
            "model_used": analysis.model_used,
            "tokens_used": analysis.tokens_used,
        }

    return response


@router.get("/analytics/topics", dependencies=[Depends(verify_token)])
async def get_trending_topics(
    days: int = Query(default=7, le=90),
    session: AsyncSession = Depends(get_session),
):
    """Get trending topics across all analyzed videos."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    result = await session.execute(
        select(VideoAnalysis, Video)
        .join(Video, VideoAnalysis.video_id == Video.video_id)
        .where(Video.published_at > cutoff)
    )

    topic_counts = {}
    for analysis, video in result.all():
        for topic in (analysis.topics or []):
            if topic not in topic_counts:
                topic_counts[topic] = {"count": 0, "videos": []}
            topic_counts[topic]["count"] += 1
            topic_counts[topic]["videos"].append(video.title)

    sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1]["count"], reverse=True)

    return {
        "period_days": days,
        "topics": [
            {"topic": topic, "mentions": data["count"], "videos": data["videos"]}
            for topic, data in sorted_topics[:30]
        ]
    }


@router.get("/analytics/directives", dependencies=[Depends(verify_token)])
async def get_research_directives(
    days: int = Query(default=7, le=90),
    session: AsyncSession = Depends(get_session),
):
    """Get aggregated research directives from recent analyses."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    result = await session.execute(
        select(VideoAnalysis)
        .join(Video, VideoAnalysis.video_id == Video.video_id)
        .where(Video.published_at > cutoff)
    )

    all_directives = []
    for analysis in result.scalars().all():
        for d in (analysis.research_directives or []):
            all_directives.append(d)

    return {
        "period_days": days,
        "directive_count": len(all_directives),
        "directives": all_directives,
    }


@router.get("/logs", dependencies=[Depends(verify_token)])
async def get_pipeline_logs(
    action: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(default=50, le=500),
    session: AsyncSession = Depends(get_session),
):
    """View pipeline operation logs."""
    query = select(PipelineLog).order_by(desc(PipelineLog.created_at))

    if action:
        query = query.where(PipelineLog.action == action)
    if status:
        query = query.where(PipelineLog.status == status)

    result = await session.execute(query.limit(limit))
    logs = result.scalars().all()

    return {
        "count": len(logs),
        "logs": [
            {
                "action": log.action,
                "target": log.target,
                "status": log.status,
                "detail": log.detail,
                "duration_ms": log.duration_ms,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]
    }
