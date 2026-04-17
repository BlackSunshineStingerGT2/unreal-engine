"""
Reddit client — uses public .json endpoints (no API key required).
Mirrors YouTubeService pattern: async httpx client, methods return dicts.
"""
import asyncio
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

REDDIT_BASE = "https://old.reddit.com"


class RedditService:
    def __init__(self):
        # Use a browser-like user agent — Reddit blocks bot-like agents on .json
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 "
            "UnrealEngine/1.0"
        )
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    # ------------------------------------------------------------------
    # Internal GET with rate-limit guard
    # ------------------------------------------------------------------

    async def _get(self, url: str, params: dict = None) -> Optional[dict]:
        """GET a Reddit .json URL. Returns parsed JSON or None."""
        try:
            resp = await self.client.get(url, params=params or {})
            if resp.status_code == 429:
                logger.warning("Reddit rate limited — backing off 10s")
                await asyncio.sleep(10)
                resp = await self.client.get(url, params=params or {})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Reddit HTTP error: {e}")
            return None
        except Exception as e:
            logger.error(f"Reddit request failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Subreddit info
    # ------------------------------------------------------------------

    async def get_subreddit_info(self, subreddit_name: str) -> Optional[dict]:
        """Fetch subreddit metadata."""
        data = await self._get(f"{REDDIT_BASE}/r/{subreddit_name}/about.json")
        if not data:
            return None

        info = data.get("data", {})
        return {
            "subreddit_name": info.get("display_name", subreddit_name),
            "display_name": info.get("display_name_prefixed", f"r/{subreddit_name}"),
            "description": (info.get("public_description") or "")[:2000],
            "subscriber_count": info.get("subscribers", 0),
        }

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    async def get_new_posts(self, subreddit_name: str, limit: int = 25) -> list[dict]:
        """Fetch newest posts from a subreddit (chronological)."""
        data = await self._get(
            f"{REDDIT_BASE}/r/{subreddit_name}/new.json",
            params={"limit": min(limit, 100), "raw_json": 1},
        )
        if not data:
            return []

        posts = []
        for child in data.get("data", {}).get("children", []):
            p = child.get("data", {})

            # Determine post type
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

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    async def get_post_comments(
        self, subreddit_name: str, post_id: str, limit: int = 200
    ) -> list[dict]:
        """Fetch and flatten comment tree for a post."""
        data = await self._get(
            f"{REDDIT_BASE}/r/{subreddit_name}/comments/{post_id}.json",
            params={"limit": limit, "sort": "top", "raw_json": 1},
        )
        if not data or not isinstance(data, list) or len(data) < 2:
            return []

        comments = []
        self._flatten_comments(
            data[1].get("data", {}).get("children", []),
            comments,
            post_id,
            max_depth=5,
        )
        return comments

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

            result.append({
                "comment_id": comment_id,
                "post_id": post_id,
                "parent_comment_id": parent_id,
                "author": c.get("author", "[deleted]"),
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
