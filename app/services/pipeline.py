import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import (
    Channel, Video, Transcript, Comment,
    EngagementSnapshot, VideoAnalysis, PipelineLog
)
from app.models.session import async_session
from app.services.youtube import YouTubeService
from app.services.unreal_engine import UnrealEngine

logger = logging.getLogger(__name__)


class Pipeline:
    """Orchestrates the full collection and analysis pipeline."""

    def __init__(self):
        self.youtube = YouTubeService()
        self.brain = UnrealEngine()

    async def close(self):
        await self.youtube.close()

    # -------------------------------------------------------------------------
    # Channel polling
    # -------------------------------------------------------------------------

    async def poll_channel(self, session: AsyncSession, channel: Channel) -> list[dict]:
        """Check a channel for new videos."""
        start = time.time()
        new_videos = []

        try:
            info = await self.youtube.get_channel_info(channel.channel_id)
            if not info:
                await self._log(session, "poll", channel.channel_id, "error", "Channel not found")
                return []

            # Update channel stats
            channel.subscriber_count = info["subscriber_count"]
            channel.video_count = info["video_count"]
            channel.last_checked = datetime.now(timezone.utc)

            # Get latest videos
            uploads_playlist = info["uploads_playlist"]
            latest = await self.youtube.get_latest_videos(uploads_playlist, max_results=10)

            # Check which are new
            existing_ids = set()
            if latest:
                result = await session.execute(
                    select(Video.video_id).where(
                        Video.video_id.in_([v["video_id"] for v in latest])
                    )
                )
                existing_ids = set(result.scalars().all())

            for v in latest:
                if v["video_id"] not in existing_ids:
                    # Get full details
                    details = await self.youtube.get_video_details(v["video_id"])
                    if details:
                        video = Video(
                            video_id=details["video_id"],
                            channel_id=channel.channel_id,
                            title=details["title"],
                            description=details["description"],
                            published_at=datetime.fromisoformat(
                                details["published_at"].replace("Z", "+00:00")
                            ),
                            duration_seconds=details["duration_seconds"],
                            tags=details.get("tags", []),
                            thumbnail_url=details.get("thumbnail_url", ""),
                        )
                        session.add(video)
                        new_videos.append(details)

                        # Create initial engagement snapshot
                        hours_since = (
                            datetime.now(timezone.utc) - video.published_at
                        ).total_seconds() / 3600
                        snapshot = EngagementSnapshot(
                            video_id=details["video_id"],
                            hours_since_publish=round(hours_since, 2),
                            view_count=details["view_count"],
                            like_count=details["like_count"],
                            comment_count=details["comment_count"],
                            views_per_hour=details["view_count"] / max(hours_since, 0.1),
                        )
                        session.add(snapshot)

            await session.commit()
            duration_ms = int((time.time() - start) * 1000)
            await self._log(
                session, "poll", channel.channel_id, "success",
                f"Found {len(new_videos)} new videos", duration_ms
            )

        except Exception as e:
            logger.error(f"Error polling channel {channel.channel_id}: {e}")
            await self._log(session, "poll", channel.channel_id, "error", str(e))

        return new_videos

    async def poll_all_channels(self) -> dict:
        """Poll all active channels for new content."""
        async with async_session() as session:
            result = await session.execute(
                select(Channel).where(Channel.active == True).order_by(Channel.priority.desc())
            )
            channels = result.scalars().all()

            total_new = 0
            for channel in channels:
                new = await self.poll_channel(session, channel)
                total_new += len(new)
                # Small delay to respect rate limits
                await asyncio.sleep(0.5)

            return {"channels_checked": len(channels), "new_videos": total_new}

    # -------------------------------------------------------------------------
    # Transcript collection
    # -------------------------------------------------------------------------

    async def collect_transcript(self, session: AsyncSession, video: Video) -> bool:
        """Collect transcript for a video."""
        start = time.time()

        try:
            # Sync call wrapped for async context
            result = await asyncio.to_thread(
                self.youtube.get_transcript, video.video_id
            )

            if not result:
                await self._log(
                    session, "transcript", video.video_id,
                    "skipped", "No transcript available"
                )
                return False

            transcript = Transcript(
                video_id=video.video_id,
                language=result["language"],
                full_text=result["full_text"],
                segments=result["segments"],
                source=result["source"],
                token_count=len(result["full_text"].split()) * 1.3,  # rough estimate
            )
            session.add(transcript)
            video.transcript_collected = True
            await session.commit()

            duration_ms = int((time.time() - start) * 1000)
            await self._log(
                session, "transcript", video.video_id, "success",
                f"Collected {len(result['segments'])} segments", duration_ms
            )
            return True

        except Exception as e:
            logger.error(f"Transcript collection failed for {video.video_id}: {e}")
            await self._log(session, "transcript", video.video_id, "error", str(e))
            return False

    # -------------------------------------------------------------------------
    # Comment collection
    # -------------------------------------------------------------------------

    async def collect_comments(self, session: AsyncSession, video: Video) -> int:
        """Collect comments for a video."""
        start = time.time()

        try:
            raw_comments = await self.youtube.get_comments(video.video_id)

            # Check existing comments
            existing = set()
            if raw_comments:
                result = await session.execute(
                    select(Comment.comment_id).where(
                        Comment.comment_id.in_([c["comment_id"] for c in raw_comments])
                    )
                )
                existing = set(result.scalars().all())

            count = 0
            for c in raw_comments:
                if c["comment_id"] not in existing:
                    comment = Comment(
                        comment_id=c["comment_id"],
                        video_id=video.video_id,
                        parent_comment_id=c.get("parent_comment_id"),
                        author=c.get("author", ""),
                        author_channel_id=c.get("author_channel_id", ""),
                        text=c["text"],
                        like_count=c.get("like_count", 0),
                        reply_count=c.get("reply_count", 0),
                        published_at=datetime.fromisoformat(
                            c["published_at"].replace("Z", "+00:00")
                        ) if c.get("published_at") else None,
                        is_creator_reply=c.get("is_creator_reply", False),
                    )
                    session.add(comment)
                    count += 1

            video.comments_collected = True
            await session.commit()

            duration_ms = int((time.time() - start) * 1000)
            await self._log(
                session, "comments", video.video_id, "success",
                f"Collected {count} new comments", duration_ms
            )
            return count

        except Exception as e:
            logger.error(f"Comment collection failed for {video.video_id}: {e}")
            await self._log(session, "comments", video.video_id, "error", str(e))
            return 0

    # -------------------------------------------------------------------------
    # Engagement snapshots
    # -------------------------------------------------------------------------

    async def take_engagement_snapshot(self, session: AsyncSession, video: Video) -> bool:
        """Take a point-in-time engagement snapshot."""
        try:
            stats = await self.youtube.get_engagement_stats(video.video_id)
            if not stats:
                return False

            hours_since = (
                datetime.now(timezone.utc) - video.published_at
            ).total_seconds() / 3600

            snapshot = EngagementSnapshot(
                video_id=video.video_id,
                hours_since_publish=round(hours_since, 2),
                view_count=stats["view_count"],
                like_count=stats["like_count"],
                comment_count=stats["comment_count"],
                views_per_hour=stats["view_count"] / max(hours_since, 0.1),
            )
            session.add(snapshot)
            await session.commit()
            return True

        except Exception as e:
            logger.error(f"Engagement snapshot failed for {video.video_id}: {e}")
            return False

    # -------------------------------------------------------------------------
    # LLM Analysis
    # -------------------------------------------------------------------------

    async def analyze_video(self, session: AsyncSession, video: Video) -> bool:
        """Run Unreal Engine analysis on a video."""
        start = time.time()

        try:
            # Need transcript
            if not video.transcript:
                result = await session.execute(
                    select(Transcript).where(Transcript.video_id == video.video_id)
                )
                transcript = result.scalar_one_or_none()
                if not transcript:
                    await self._log(
                        session, "analyze", video.video_id,
                        "skipped", "No transcript"
                    )
                    return False
            else:
                transcript = video.transcript

            # Get channel name
            ch_result = await session.execute(
                select(Channel.name).where(Channel.channel_id == video.channel_id)
            )
            channel_name = ch_result.scalar_one_or_none() or "Unknown"

            # Run analysis
            analysis_data = await self.brain.analyze_transcript(
                transcript=transcript.full_text,
                video_title=video.title,
                channel_name=channel_name,
                description=video.description,
            )

            if not analysis_data:
                await self._log(session, "analyze", video.video_id, "error", "LLM returned None")
                return False

            # Store analysis
            analysis = VideoAnalysis(
                video_id=video.video_id,
                topics=analysis_data.get("topics", []),
                entities=analysis_data.get("entities", []),
                claims=analysis_data.get("claims", []),
                questions_raised=analysis_data.get("questions_raised", []),
                sentiment_score=analysis_data.get("sentiment_score", 0.0),
                information_density=analysis_data.get("information_density", 0.0),
                research_directives=analysis_data.get("research_directives", []),
                summary=analysis_data.get("summary", ""),
                model_used=analysis_data.get("model_used", ""),
                tokens_used=analysis_data.get("tokens_used", 0),
            )
            session.add(analysis)
            video.analysis_complete = True
            await session.commit()

            duration_ms = int((time.time() - start) * 1000)
            await self._log(
                session, "analyze", video.video_id, "success",
                f"Topics: {analysis_data.get('topics', [])}", duration_ms
            )
            return True

        except Exception as e:
            logger.error(f"Analysis failed for {video.video_id}: {e}")
            await self._log(session, "analyze", video.video_id, "error", str(e))
            return False

    # -------------------------------------------------------------------------
    # Full pipeline run
    # -------------------------------------------------------------------------

    async def run_full_cycle(self) -> dict:
        """Execute a complete pipeline cycle."""
        logger.info("Starting full pipeline cycle")
        results = {
            "channels_polled": 0,
            "new_videos": 0,
            "transcripts_collected": 0,
            "comments_collected": 0,
            "videos_analyzed": 0,
            "snapshots_taken": 0,
        }

        # 1. Poll all channels
        poll_result = await self.poll_all_channels()
        results["channels_polled"] = poll_result["channels_checked"]
        results["new_videos"] = poll_result["new_videos"]

        async with async_session() as session:
            # 2. Collect transcripts for videos missing them
            uncollected = await session.execute(
                select(Video).where(Video.transcript_collected == False).limit(20)
            )
            for video in uncollected.scalars().all():
                if await self.collect_transcript(session, video):
                    results["transcripts_collected"] += 1
                await asyncio.sleep(1)

            # 3. Collect comments for videos missing them
            uncommented = await session.execute(
                select(Video).where(Video.comments_collected == False).limit(20)
            )
            for video in uncommented.scalars().all():
                count = await self.collect_comments(session, video)
                if count > 0:
                    results["comments_collected"] += count
                await asyncio.sleep(1)

            # 4. Analyze videos with transcripts but no analysis
            unanalyzed = await session.execute(
                select(Video).where(
                    Video.transcript_collected == True,
                    Video.analysis_complete == False
                ).limit(10)
            )
            for video in unanalyzed.scalars().all():
                if await self.analyze_video(session, video):
                    results["videos_analyzed"] += 1
                await asyncio.sleep(2)  # Rate limit LLM calls

            # 5. Take engagement snapshots for recent videos (< 7 days)
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            recent = await session.execute(
                select(Video).where(Video.published_at > cutoff)
            )
            for video in recent.scalars().all():
                if await self.take_engagement_snapshot(session, video):
                    results["snapshots_taken"] += 1
                await asyncio.sleep(0.5)

        logger.info(f"Pipeline cycle complete: {results}")
        return results

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------

    async def _log(
        self, session: AsyncSession, action: str, target: str,
        status: str, detail: str = "", duration_ms: int = 0
    ):
        log = PipelineLog(
            action=action, target=target, status=status,
            detail=detail, duration_ms=duration_ms
        )
        session.add(log)
        await session.commit()
