from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, timezone, timedelta

from app.models.database import (
    Subreddit, RedditPost, RedditComment, RedditPostAnalysis, PipelineLog
)
from app.models.session import get_session
from app.config import settings
from app.services.pipeline import Pipeline

reddit_router = APIRouter(prefix="/api/v1/reddit", tags=["reddit"])
pipeline = Pipeline()


# -------------------------------------------------------------------------
# Auth (shared pattern)
# -------------------------------------------------------------------------

async def verify_token(authorization: str = Header(...)):
    if authorization != f"Bearer {settings.pipeline_token}":
        raise HTTPException(status_code=401, detail="Invalid token")


# -------------------------------------------------------------------------
# Subreddit management
# -------------------------------------------------------------------------

@reddit_router.post("/subreddits", dependencies=[Depends(verify_token)])
async def add_subreddit(
    subreddit_name: str,
    category: str = "uap",
    priority: int = 5,
    session: AsyncSession = Depends(get_session),
):
    """Add a subreddit to the watch list."""
    from app.services.reddit import RedditService
    reddit = RedditService()

    try:
        info = await reddit.get_subreddit_info(subreddit_name)
        if not info:
            raise HTTPException(404, "Subreddit not found")

        # Check if exists
        existing = await session.execute(
            select(Subreddit).where(Subreddit.subreddit_name == info["subreddit_name"])
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "Subreddit already registered")

        sub = Subreddit(
            subreddit_name=info["subreddit_name"],
            display_name=info["display_name"],
            description=info["description"],
            subscriber_count=info["subscriber_count"],
            category=category,
            priority=priority,
        )
        session.add(sub)
        await session.commit()

        return {
            "status": "added",
            "subreddit": {
                "name": info["subreddit_name"],
                "display_name": info["display_name"],
                "subscribers": info["subscriber_count"],
            }
        }
    finally:
        await reddit.close()


@reddit_router.get("/subreddits", dependencies=[Depends(verify_token)])
async def list_subreddits(session: AsyncSession = Depends(get_session)):
    """List all registered subreddits."""
    result = await session.execute(
        select(Subreddit).order_by(Subreddit.priority.desc(), Subreddit.subreddit_name)
    )
    subs = result.scalars().all()
    return {
        "count": len(subs),
        "subreddits": [
            {
                "subreddit_name": s.subreddit_name,
                "display_name": s.display_name,
                "category": s.category,
                "priority": s.priority,
                "subscribers": s.subscriber_count,
                "active": s.active,
                "last_checked": s.last_checked.isoformat() if s.last_checked else None,
            }
            for s in subs
        ]
    }


@reddit_router.patch("/subreddits/{subreddit_name}", dependencies=[Depends(verify_token)])
async def update_subreddit(
    subreddit_name: str,
    priority: Optional[int] = None,
    category: Optional[str] = None,
    active: Optional[bool] = None,
    session: AsyncSession = Depends(get_session),
):
    """Update subreddit settings."""
    result = await session.execute(
        select(Subreddit).where(Subreddit.subreddit_name == subreddit_name)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Subreddit not found")

    if priority is not None:
        sub.priority = priority
    if category is not None:
        sub.category = category
    if active is not None:
        sub.active = active

    await session.commit()
    return {"status": "updated", "subreddit_name": subreddit_name}


@reddit_router.delete("/subreddits/{subreddit_name}", dependencies=[Depends(verify_token)])
async def remove_subreddit(
    subreddit_name: str,
    session: AsyncSession = Depends(get_session),
):
    """Remove a subreddit from the watch list."""
    result = await session.execute(
        select(Subreddit).where(Subreddit.subreddit_name == subreddit_name)
    )
    sub = result.scalar_one_or_none()
    if not sub:
        raise HTTPException(404, "Subreddit not found")

    await session.delete(sub)
    await session.commit()
    return {"status": "removed", "subreddit_name": subreddit_name}


# -------------------------------------------------------------------------
# Pipeline controls
# -------------------------------------------------------------------------

@reddit_router.post("/pipeline/run", dependencies=[Depends(verify_token)])
async def run_reddit_pipeline():
    """Trigger a full Reddit pipeline cycle."""
    results = await pipeline.run_reddit_cycle()
    return {"status": "complete", "results": results}


@reddit_router.post("/pipeline/poll", dependencies=[Depends(verify_token)])
async def poll_subreddits():
    """Poll all subreddits for new posts only."""
    results = await pipeline.poll_all_subreddits()
    return {"status": "complete", "results": results}


@reddit_router.post("/pipeline/collect/{post_id}", dependencies=[Depends(verify_token)])
async def collect_post(
    post_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Manually collect comments and analyze a specific post."""
    result = await session.execute(
        select(RedditPost).where(RedditPost.post_id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post not found. Poll the subreddit first.")

    results = {}

    if not post.comments_collected:
        count = await pipeline.collect_reddit_comments(session, post)
        results["comments_collected"] = count

    if not post.analysis_complete and post.comments_collected:
        results["analyzed"] = await pipeline.analyze_reddit_post(session, post)

    return {"status": "complete", "post_id": post_id, "results": results}


@reddit_router.post("/pipeline/analyze/{post_id}", dependencies=[Depends(verify_token)])
async def analyze_post(
    post_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Force re-analysis of a Reddit post."""
    result = await session.execute(
        select(RedditPost).where(RedditPost.post_id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post not found")

    # Delete existing analysis
    existing = await session.execute(
        select(RedditPostAnalysis).where(RedditPostAnalysis.post_id == post_id)
    )
    old = existing.scalar_one_or_none()
    if old:
        await session.delete(old)
        post.analysis_complete = False
        await session.commit()

    success = await pipeline.analyze_reddit_post(session, post)
    return {"status": "analyzed" if success else "failed", "post_id": post_id}


# -------------------------------------------------------------------------
# Data retrieval
# -------------------------------------------------------------------------

@reddit_router.get("/posts", dependencies=[Depends(verify_token)])
async def list_posts(
    subreddit_name: Optional[str] = None,
    analyzed: Optional[bool] = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List collected Reddit posts with filters."""
    query = select(RedditPost).order_by(desc(RedditPost.published_at))

    if subreddit_name:
        query = query.where(RedditPost.subreddit_name == subreddit_name)
    if analyzed is not None:
        query = query.where(RedditPost.analysis_complete == analyzed)

    query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    posts = result.scalars().all()

    return {
        "count": len(posts),
        "posts": [
            {
                "post_id": p.post_id,
                "subreddit_name": p.subreddit_name,
                "title": p.title,
                "author": p.author,
                "post_type": p.post_type,
                "flair": p.flair,
                "score": p.score,
                "upvote_ratio": p.upvote_ratio,
                "num_comments": p.num_comments,
                "published_at": p.published_at.isoformat(),
                "comments_collected": p.comments_collected,
                "analysis_complete": p.analysis_complete,
            }
            for p in posts
        ]
    }


@reddit_router.get("/posts/{post_id}", dependencies=[Depends(verify_token)])
async def get_post_detail(
    post_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Get full post detail including analysis."""
    result = await session.execute(
        select(RedditPost).where(RedditPost.post_id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(404, "Post not found")

    # Get analysis
    analysis_result = await session.execute(
        select(RedditPostAnalysis).where(RedditPostAnalysis.post_id == post_id)
    )
    analysis = analysis_result.scalar_one_or_none()

    # Comment count
    comment_count = await session.execute(
        select(func.count()).select_from(RedditComment).where(RedditComment.post_id == post_id)
    )

    response = {
        "post_id": post.post_id,
        "subreddit_name": post.subreddit_name,
        "title": post.title,
        "author": post.author,
        "selftext": post.selftext[:2000] if post.selftext else "",
        "url": post.url,
        "permalink": post.permalink,
        "post_type": post.post_type,
        "flair": post.flair,
        "score": post.score,
        "upvote_ratio": post.upvote_ratio,
        "num_comments": post.num_comments,
        "comments_in_db": comment_count.scalar(),
        "published_at": post.published_at.isoformat(),
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


# -------------------------------------------------------------------------
# Analytics
# -------------------------------------------------------------------------

@reddit_router.get("/analytics/topics", dependencies=[Depends(verify_token)])
async def get_trending_topics(
    days: int = Query(default=7, le=90),
    session: AsyncSession = Depends(get_session),
):
    """Get trending topics across analyzed Reddit posts."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await session.execute(
        select(RedditPostAnalysis, RedditPost)
        .join(RedditPost, RedditPostAnalysis.post_id == RedditPost.post_id)
        .where(RedditPost.published_at > cutoff)
    )

    topic_counts = {}
    for analysis, post in result.all():
        for topic in (analysis.topics or []):
            if topic not in topic_counts:
                topic_counts[topic] = {"count": 0, "posts": []}
            topic_counts[topic]["count"] += 1
            topic_counts[topic]["posts"].append(post.title)

    sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1]["count"], reverse=True)

    return {
        "period_days": days,
        "topics": [
            {"topic": topic, "mentions": data["count"], "posts": data["posts"]}
            for topic, data in sorted_topics[:30]
        ]
    }
