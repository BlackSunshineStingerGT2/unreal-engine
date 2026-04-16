from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://localhost/yt_pipeline"

    # YouTube
    youtube_api_key: str = ""

    # Anthropic
    anthropic_api_key: str = ""

    # Pipeline
    polling_interval_minutes: int = 30
    engagement_snapshot_hours: str = "1,6,24,72"
    max_comments_per_video: int = 500
    transcript_language: str = "en"

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "UnrealEngine/1.0 (UAP Intelligence Pipeline)"
    reddit_polling_interval_minutes: int = 15

    # Auth
    pipeline_token: str = ""

    @property
    def snapshot_intervals(self) -> List[int]:
        return [int(h) for h in self.engagement_snapshot_hours.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
