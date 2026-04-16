"""
Reddit API client — application-only OAuth2.
Mirrors YouTubeService pattern: async httpx client, methods return dicts.
"""
import time
import logging
from typing import Optional
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

REDDIT_AUTH_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"


class RedditService:
    def __init__(self):
        self.client_id = settings.reddit_client_id
        self.client_secret = settings.reddit_client_secret
        self.user_agent = settings.reddit_user_agent
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": self.user_agent},
        )
        self._token: Optional[str] = None
        self._token_expires: float = 0

    async def close(self):
        await self.client.aclose()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    async def _ensure_token(self):
        """Acquire or refresh the OAuth2 application-only token."""
        if self._token and time.time() < self._token_expires:
            return

        resp = await self.client.post(
            REDDIT_AUTH_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.user_agent},
        )
        resp.raise_for_status()
        data = resp.json()

        self._token = data["access_token"]
        # Expire 60s early to avoid edge-case failures
        self._token_expires = time.time() + data.get("expires_in", 3600) - 60
        logger.info("Reddit OAuth token acquired")

    async def _get(self, path: str, params: dict = None) -> dict:
        """Authenticated GET to oauth.reddit.com."""
        await self._ensure_token()
        resp = await self.client.get(
            f"{REDDIT_API_BASE}{path}",
            params=params or {},
            headers={
                "Authorization": f"Bearer {self._token}",
                "User-Agent": self.user_agent,
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Subreddit info
    # ------------------------------------------------------------------

    async def get_subreddit_info(self, subreddit_name: str) -> Optional[dict]:
        """Fetch subreddit metadata."""
        try:
            data = await self._get(f"/r/{subreddit_name}/about")
            info = data.get("data", {})
            return {
                "subreddit_name": info.get("display_name", subreddit_name),
                "display_name": info.get("display_name_prefixed", f"r/{subreddit_name}"),
                "description": (info.get("public_description") or "")[:2000],
                "subscriber_count": info.get("subscribers", 0),
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch subreddit r/{subreddit_name}: {e}")
            return None

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def get_new_posts(self, subreddit_name: str, limit: int = 25) -> list[dict]:
        """Fetch newest posts from a subreddit (chronological)."""
        try:
            data = await self._get(
                f"/r/{subreddit_name}/new",
                params={"limit": min(limit, 100), "raw_json": 1},
            )
            posts = []
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                # Determine post type
                post_type = "self"
                if p.get("is_self"):
                    post_type = "self"
                elif p.get("is_video"):
                    post_type = "video"
                elif p.get("post_hint") == "image":
                    post_type = "image"
                elif p.get("crosspost_parent"):
                    post_type = "crosspost"
                else:
                    post_type = "link"

                posts.append({
                    "post_id": p.get("id", ""),
                    "title": p.get("title", ""),
                    "author": p.get("author", "[deleted]"),
                    "selftext": p.get("selftext", ""),
                    "url": p.get("url", ""),
                    "permalink": p.get("permalink", ""),
                    "post_type": post_type,
                    "flair": p.get("link_flair_text") or "",
                    "score": p.get("score", 0),
                    "upvote_ratio": p.get("upvote_ratio", 0.0),
                    "num_comments": p.get("num_comments", 0),
                    "created_utc": p.get("created_utc", 0),
                })
            return posts
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch posts from r/{subreddit_name}: {e}")
            return []

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    async def get_post_comments(
        self, subreddit_name: str, post_id: str, limit: int = 200
    ) -> list[dict]:
        """Fetch and flatten comment tree for a post."""
        try:
            data = await self._get(
                f"/r/{subreddit_name}/comments/{post_id}",
                params={"limit": limit, "sort": "top", "raw_json": 1},
            )
            # Reddit returns [post_listing, comment_listing]
            if not isinstance(data, list) or len(data) < 2:
                return []

            comments = []
            self._flatten_comments(
                data[1].get("data", {}).get("children", []),
                comments,
                post_id,
                max_depth=5,
            )
            return comments
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch comments for {post_id}: {e}")
            return []

    def _flatten_comments(
        self, children: list, result: list, post_id: str,
        parent_id: str = None, depth: int = 0, max_depth: int = 5,
    ):
        """Recursively flatten Reddit's nested comment tree."""
        if depth > max_depth:
            return
        for child in children:
            if child.get("kind") != "t1":
                continue
            c = child.get("data", {})
            comment_id = c.get("id", "")
            author = c.get("author", "[deleted]")

            result.append({
                "comment_id": comment_id,
                "post_id": post_id,
                "parent_comment_id": parent_id,
                "author": author,
                "body": c.get("body", ""),
                "score": c.get("score", 0),
                "is_op": c.get("is_submitter", False),
                "created_utc": c.get("created_utc", 0),
            })

            # Recurse into replies
            replies = c.get("replies")
            if isinstance(replies, dict):
                reply_children = replies.get("data", {}).get("children", [])
                self._flatten_comments(
                    reply_children, result, post_id,
                    parent_id=comment_id, depth=depth + 1, max_depth=max_depth,
                )
