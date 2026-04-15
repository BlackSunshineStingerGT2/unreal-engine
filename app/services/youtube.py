import httpx
import isodate
import logging
from typing import Optional
from datetime import datetime, timezone
from youtube_transcript_api import YouTubeTranscriptApi

from app.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://www.googleapis.com/youtube/v3"


class YouTubeService:
    """Handles all YouTube Data API v3 interactions."""

    def __init__(self):
        self.api_key = settings.youtube_api_key
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    def _params(self, **kwargs) -> dict:
        kwargs["key"] = self.api_key
        return kwargs

    # -------------------------------------------------------------------------
    # Channel operations
    # -------------------------------------------------------------------------

    async def get_channel_info(self, channel_id: str) -> Optional[dict]:
        """Fetch channel metadata."""
        resp = await self.client.get(
            f"{BASE_URL}/channels",
            params=self._params(
                part="snippet,statistics,contentDetails",
                id=channel_id
            )
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        ch = items[0]
        return {
            "channel_id": ch["id"],
            "name": ch["snippet"]["title"],
            "description": ch["snippet"].get("description", ""),
            "subscriber_count": int(ch["statistics"].get("subscriberCount", 0)),
            "video_count": int(ch["statistics"].get("videoCount", 0)),
            "uploads_playlist": ch["contentDetails"]["relatedPlaylists"]["uploads"],
        }

    async def get_channel_by_handle(self, handle: str) -> Optional[dict]:
        """Lookup channel by @handle."""
        resp = await self.client.get(
            f"{BASE_URL}/channels",
            params=self._params(
                part="snippet,statistics,contentDetails",
                forHandle=handle.lstrip("@")
            )
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        ch = items[0]
        return {
            "channel_id": ch["id"],
            "name": ch["snippet"]["title"],
            "description": ch["snippet"].get("description", ""),
            "subscriber_count": int(ch["statistics"].get("subscriberCount", 0)),
            "video_count": int(ch["statistics"].get("videoCount", 0)),
            "uploads_playlist": ch["contentDetails"]["relatedPlaylists"]["uploads"],
        }

    async def get_latest_videos(
        self, uploads_playlist_id: str, max_results: int = 10
    ) -> list[dict]:
        """Get latest videos from a channel's uploads playlist."""
        resp = await self.client.get(
            f"{BASE_URL}/playlistItems",
            params=self._params(
                part="snippet,contentDetails",
                playlistId=uploads_playlist_id,
                maxResults=max_results
            )
        )
        resp.raise_for_status()
        data = resp.json()

        videos = []
        for item in data.get("items", []):
            snippet = item["snippet"]
            videos.append({
                "video_id": snippet["resourceId"]["videoId"],
                "title": snippet["title"],
                "description": snippet.get("description", ""),
                "published_at": snippet["publishedAt"],
                "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
            })
        return videos

    # -------------------------------------------------------------------------
    # Video details and engagement
    # -------------------------------------------------------------------------

    async def get_video_details(self, video_id: str) -> Optional[dict]:
        """Get full video metadata including duration and stats."""
        resp = await self.client.get(
            f"{BASE_URL}/videos",
            params=self._params(
                part="snippet,statistics,contentDetails",
                id=video_id
            )
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        v = items[0]
        duration = isodate.parse_duration(v["contentDetails"]["duration"])
        stats = v.get("statistics", {})

        return {
            "video_id": v["id"],
            "title": v["snippet"]["title"],
            "description": v["snippet"].get("description", ""),
            "channel_id": v["snippet"]["channelId"],
            "published_at": v["snippet"]["publishedAt"],
            "duration_seconds": int(duration.total_seconds()),
            "tags": v["snippet"].get("tags", []),
            "thumbnail_url": v["snippet"].get("thumbnails", {}).get("high", {}).get("url", ""),
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
        }

    async def get_engagement_stats(self, video_id: str) -> Optional[dict]:
        """Lightweight stats-only fetch for snapshots."""
        resp = await self.client.get(
            f"{BASE_URL}/videos",
            params=self._params(part="statistics", id=video_id)
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return None

        stats = items[0].get("statistics", {})
        return {
            "view_count": int(stats.get("viewCount", 0)),
            "like_count": int(stats.get("likeCount", 0)),
            "comment_count": int(stats.get("commentCount", 0)),
        }

    # -------------------------------------------------------------------------
    # Comments
    # -------------------------------------------------------------------------

    async def get_comments(
        self, video_id: str, max_results: int = 500
    ) -> list[dict]:
        """Fetch top-level comments and their replies."""
        comments = []
        page_token = None
        fetched = 0

        while fetched < max_results:
            batch_size = min(100, max_results - fetched)
            params = self._params(
                part="snippet,replies",
                videoId=video_id,
                maxResults=batch_size,
                order="relevance",
                textFormat="plainText"
            )
            if page_token:
                params["pageToken"] = page_token

            try:
                resp = await self.client.get(
                    f"{BASE_URL}/commentThreads", params=params
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    logger.warning(f"Comments disabled for video {video_id}")
                    return []
                raise

            data = resp.json()

            for thread in data.get("items", []):
                top = thread["snippet"]["topLevelComment"]
                top_snippet = top["snippet"]

                comments.append({
                    "comment_id": top["id"],
                    "parent_comment_id": None,
                    "author": top_snippet.get("authorDisplayName", ""),
                    "author_channel_id": top_snippet.get("authorChannelId", {}).get("value", ""),
                    "text": top_snippet["textDisplay"],
                    "like_count": top_snippet.get("likeCount", 0),
                    "reply_count": thread["snippet"].get("totalReplyCount", 0),
                    "published_at": top_snippet["publishedAt"],
                    "is_creator_reply": False,
                })

                # Collect replies
                if "replies" in thread:
                    for reply in thread["replies"]["comments"]:
                        r_snippet = reply["snippet"]
                        comments.append({
                            "comment_id": reply["id"],
                            "parent_comment_id": top["id"],
                            "author": r_snippet.get("authorDisplayName", ""),
                            "author_channel_id": r_snippet.get("authorChannelId", {}).get("value", ""),
                            "text": r_snippet["textDisplay"],
                            "like_count": r_snippet.get("likeCount", 0),
                            "reply_count": 0,
                            "published_at": r_snippet["publishedAt"],
                            "is_creator_reply": (
                                r_snippet.get("authorChannelId", {}).get("value", "")
                                == thread["snippet"]["channelId"]
                                if "channelId" in thread["snippet"] else False
                            ),
                        })

                fetched += 1

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return comments

    # -------------------------------------------------------------------------
    # Transcripts (uses youtube-transcript-api, no quota cost)
    # -------------------------------------------------------------------------

    def get_transcript(self, video_id: str, language: str = "en") -> Optional[dict]:
        """
        Fetch transcript. Returns segments and full text.
        This is synchronous (youtube-transcript-api is sync).
        """
        try:
            segments = YouTubeTranscriptApi.get_transcript(video_id, languages=[language, "en"])
            full_text = " ".join(seg["text"] for seg in segments)
            return {
                "segments": segments,
                "full_text": full_text,
                "language": language,
                "source": "auto",
            }
        except Exception as e:
            logger.warning(f"Transcript unavailable for {video_id}: {e}")
            return None
