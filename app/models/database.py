from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime,
    ForeignKey, BigInteger, JSON, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, timezone

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Channel(Base):
    """Podcast channel registry - the watch list."""
    __tablename__ = "yt_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    subscriber_count = Column(BigInteger, default=0)
    video_count = Column(Integer, default=0)
    category = Column(String(64), default="uap")  # uap, disclosure, science, etc.
    priority = Column(Integer, default=5)  # 1-10, higher = check more often
    active = Column(Boolean, default=True)
    last_checked = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    videos = relationship("Video", back_populates="channel", cascade="all, delete-orphan")


class Video(Base):
    """Individual video/episode record."""
    __tablename__ = "yt_videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(16), unique=True, nullable=False, index=True)
    channel_id = Column(String(64), ForeignKey("yt_channels.channel_id"), nullable=False)
    title = Column(String(512), nullable=False)
    description = Column(Text, default="")
    published_at = Column(DateTime, nullable=False)
    duration_seconds = Column(Integer, default=0)
    tags = Column(JSON, default=list)
    thumbnail_url = Column(String(512), default="")

    # Processing state
    transcript_collected = Column(Boolean, default=False)
    comments_collected = Column(Boolean, default=False)
    analysis_complete = Column(Boolean, default=False)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    channel = relationship("Channel", back_populates="videos")
    transcript = relationship("Transcript", back_populates="video", uselist=False, cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="video", cascade="all, delete-orphan")
    engagement_snapshots = relationship("EngagementSnapshot", back_populates="video", cascade="all, delete-orphan")
    analysis = relationship("VideoAnalysis", back_populates="video", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_yt_videos_published", "published_at"),
        Index("ix_yt_videos_channel_published", "channel_id", "published_at"),
    )


class Transcript(Base):
    """Full transcript for a video."""
    __tablename__ = "yt_transcripts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(16), ForeignKey("yt_videos.video_id"), unique=True, nullable=False)
    language = Column(String(8), default="en")
    full_text = Column(Text, nullable=False)
    segments = Column(JSON, default=list)  # [{start, duration, text}, ...]
    source = Column(String(32), default="auto")  # auto, manual, whisper
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)

    video = relationship("Video", back_populates="transcript")


class Comment(Base):
    """Top-level and reply comments."""
    __tablename__ = "yt_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(String(64), unique=True, nullable=False, index=True)
    video_id = Column(String(16), ForeignKey("yt_videos.video_id"), nullable=False)
    parent_comment_id = Column(String(64), nullable=True)  # null = top-level
    author = Column(String(256), default="")
    author_channel_id = Column(String(64), default="")
    text = Column(Text, nullable=False)
    like_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    published_at = Column(DateTime, nullable=True)
    is_creator_reply = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

    video = relationship("Video", back_populates="comments")

    __table_args__ = (
        Index("ix_yt_comments_video", "video_id"),
        Index("ix_yt_comments_likes", "video_id", "like_count"),
    )


class EngagementSnapshot(Base):
    """Time-series engagement data for velocity tracking."""
    __tablename__ = "yt_engagement_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(16), ForeignKey("yt_videos.video_id"), nullable=False)
    snapshot_at = Column(DateTime, default=utcnow, nullable=False)
    hours_since_publish = Column(Float, nullable=False)
    view_count = Column(BigInteger, default=0)
    like_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    views_per_hour = Column(Float, default=0.0)  # calculated velocity

    video = relationship("Video", back_populates="engagement_snapshots")

    __table_args__ = (
        Index("ix_yt_engagement_video_time", "video_id", "hours_since_publish"),
        UniqueConstraint("video_id", "hours_since_publish", name="uq_video_snapshot_hour"),
    )


class VideoAnalysis(Base):
    """LLM-generated analysis of video content."""
    __tablename__ = "yt_video_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(String(16), ForeignKey("yt_videos.video_id"), unique=True, nullable=False)

    # Topic extraction
    topics = Column(JSON, default=list)  # ["grusch_testimony", "reverse_engineering", ...]
    entities = Column(JSON, default=list)  # people, orgs, locations mentioned
    claims = Column(JSON, default=list)  # specific claims made in the video
    questions_raised = Column(JSON, default=list)  # questions asked by host/guests

    # Sentiment and engagement quality
    sentiment_score = Column(Float, default=0.0)  # -1 to 1
    information_density = Column(Float, default=0.0)  # 0 to 1, how much new info
    community_heat = Column(Float, default=0.0)  # engagement velocity normalized

    # Cross-reference directives
    research_directives = Column(JSON, default=list)  # what Unreal Engine should go find
    related_video_ids = Column(JSON, default=list)  # connections to other collected videos

    summary = Column(Text, default="")
    model_used = Column(String(64), default="claude-sonnet-4-20250514")
    tokens_used = Column(Integer, default=0)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    video = relationship("Video", back_populates="analysis")


class PipelineLog(Base):
    """Operational logging for the pipeline."""
    __tablename__ = "yt_pipeline_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    action = Column(String(64), nullable=False)  # poll, transcript, comments, analyze, snapshot
    target = Column(String(256), default="")  # channel_id or video_id
    status = Column(String(16), default="success")  # success, error, skipped
    detail = Column(Text, default="")
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_pipeline_logs_action_time", "action", "created_at"),
    )


# ==========================================================================
# Reddit models (Phase 2)
# ==========================================================================

class Subreddit(Base):
    """Subreddit watch list — mirrors Channel for Reddit."""
    __tablename__ = "rd_subreddits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    subreddit_name = Column(String(128), unique=True, nullable=False, index=True)
    display_name = Column(String(256), nullable=False)
    description = Column(Text, default="")
    subscriber_count = Column(BigInteger, default=0)
    category = Column(String(64), default="uap")
    priority = Column(Integer, default=5)
    active = Column(Boolean, default=True)
    last_checked = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    posts = relationship("RedditPost", back_populates="subreddit", cascade="all, delete-orphan")


class RedditPost(Base):
    """Individual Reddit post record."""
    __tablename__ = "rd_posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String(16), unique=True, nullable=False, index=True)
    subreddit_name = Column(String(128), ForeignKey("rd_subreddits.subreddit_name"), nullable=False)
    title = Column(String(512), nullable=False)
    author = Column(String(128), default="[deleted]")
    selftext = Column(Text, default="")
    url = Column(String(1024), default="")
    permalink = Column(String(512), default="")
    post_type = Column(String(32), default="self")  # self, link, image, video, crosspost
    flair = Column(String(128), default="")
    score = Column(Integer, default=0)
    upvote_ratio = Column(Float, default=0.0)
    num_comments = Column(Integer, default=0)
    published_at = Column(DateTime, nullable=False)

    # Processing state
    comments_collected = Column(Boolean, default=False)
    analysis_complete = Column(Boolean, default=False)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    subreddit = relationship("Subreddit", back_populates="posts")
    comments = relationship("RedditComment", back_populates="post", cascade="all, delete-orphan")
    analysis = relationship("RedditPostAnalysis", back_populates="post", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_rd_posts_published", "published_at"),
        Index("ix_rd_posts_subreddit_published", "subreddit_name", "published_at"),
    )


class RedditComment(Base):
    """Reddit comment — top-level and replies."""
    __tablename__ = "rd_comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(String(16), unique=True, nullable=False, index=True)
    post_id = Column(String(16), ForeignKey("rd_posts.post_id"), nullable=False)
    parent_comment_id = Column(String(16), nullable=True)
    author = Column(String(128), default="[deleted]")
    body = Column(Text, nullable=False)
    score = Column(Integer, default=0)
    is_op = Column(Boolean, default=False)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    post = relationship("RedditPost", back_populates="comments")

    __table_args__ = (
        Index("ix_rd_comments_post", "post_id"),
        Index("ix_rd_comments_score", "post_id", "score"),
    )


class RedditPostAnalysis(Base):
    """LLM-generated analysis of a Reddit post."""
    __tablename__ = "rd_post_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String(16), ForeignKey("rd_posts.post_id"), unique=True, nullable=False)

    topics = Column(JSON, default=list)
    entities = Column(JSON, default=list)
    claims = Column(JSON, default=list)
    questions_raised = Column(JSON, default=list)

    sentiment_score = Column(Float, default=0.0)
    information_density = Column(Float, default=0.0)
    community_heat = Column(Float, default=0.0)

    research_directives = Column(JSON, default=list)
    related_post_ids = Column(JSON, default=list)

    summary = Column(Text, default="")
    model_used = Column(String(64), default="claude-sonnet-4-20250514")
    tokens_used = Column(Integer, default=0)

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    post = relationship("RedditPost", back_populates="analysis")
