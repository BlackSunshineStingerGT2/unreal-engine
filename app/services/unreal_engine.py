import json
import logging
from typing import Optional
from anthropic import AsyncAnthropic

from app.config import settings

logger = logging.getLogger(__name__)

ANALYSIS_SYSTEM_PROMPT = """You are the Unreal Engine, an analytical AI that processes UAP/UFO community podcast content.
Your job is to analyze transcripts and extract structured intelligence.

You will receive a podcast transcript with metadata. Extract the following:

1. TOPICS: Key topics discussed (use consistent slug format: "grusch_testimony", "reverse_engineering", "nhi_biologics", etc.)
2. ENTITIES: People, organizations, locations, programs mentioned
3. CLAIMS: Specific factual claims or allegations made (who said what)
4. QUESTIONS: Questions raised by the host, guests, or implied by the discussion
5. RESEARCH_DIRECTIVES: What should be researched further based on this content - things worth crawling for, FOIA targets, documents to find
6. SUMMARY: A concise summary of the episode (3-5 sentences)
7. INFORMATION_DENSITY: Score 0-1 for how much new/substantive information vs. rehashing known content
8. SENTIMENT: Score -1 to 1 for overall tone (negative/skeptical to positive/excited)

Respond ONLY with valid JSON matching this schema:
{
    "topics": ["string"],
    "entities": [{"name": "string", "type": "person|org|location|program", "context": "string"}],
    "claims": [{"claim": "string", "source": "string", "confidence": "high|medium|low"}],
    "questions_raised": ["string"],
    "research_directives": [{"directive": "string", "priority": "high|medium|low", "reasoning": "string"}],
    "summary": "string",
    "information_density": 0.0,
    "sentiment_score": 0.0
}"""

REDDIT_ANALYSIS_SYSTEM_PROMPT = """You are the Unreal Engine, an analytical AI that processes UAP/UFO community Reddit posts.
Your job is to analyze Reddit post content and community discussion to extract structured intelligence.

You will receive a Reddit post with its title, body text, metadata, and top comments. Extract the following:

1. TOPICS: Key topics discussed (use consistent slug format: "grusch_testimony", "reverse_engineering", "nhi_biologics", etc.)
2. ENTITIES: People, organizations, locations, programs mentioned
3. CLAIMS: Specific factual claims or allegations made (who said what)
4. QUESTIONS: Questions raised by the OP or community
5. RESEARCH_DIRECTIVES: What should be researched further based on this content
6. SUMMARY: A concise summary of the post and its discussion (3-5 sentences)
7. INFORMATION_DENSITY: Score 0-1 for how much new/substantive information vs. rehashing
8. SENTIMENT: Score -1 to 1 for overall tone (negative/skeptical to positive/excited)

Note: Reddit posts may include both the original post AND community discussion. Weigh high-upvote comments as significant community signal. Link posts without body text should be analyzed primarily through their comments.

Respond ONLY with valid JSON matching this schema:
{
    "topics": ["string"],
    "entities": [{"name": "string", "type": "person|org|location|program", "context": "string"}],
    "claims": [{"claim": "string", "source": "string", "confidence": "high|medium|low"}],
    "questions_raised": ["string"],
    "research_directives": [{"directive": "string", "priority": "high|medium|low", "reasoning": "string"}],
    "summary": "string",
    "information_density": 0.0,
    "sentiment_score": 0.0
}"""

COMMENT_ANALYSIS_PROMPT = """Analyze these YouTube comments from a UAP/disclosure podcast.
Extract:
1. Top questions the community is asking
2. Key topics generating the most discussion
3. Notable claims or information shared by commenters
4. Overall community sentiment

Respond ONLY with valid JSON:
{
    "community_questions": ["string"],
    "hot_topics": ["string"],
    "notable_comments": [{"text": "string", "likes": 0, "significance": "string"}],
    "sentiment": "string",
    "engagement_quality": 0.0
}"""


class UnrealEngine:
    """The LLM brain that analyzes collected content."""

    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model_fast = "claude-sonnet-4-20250514"

    async def analyze_transcript(
        self,
        transcript: str,
        video_title: str,
        channel_name: str,
        description: str = "",
    ) -> Optional[dict]:
        """Run full analysis on a podcast transcript."""
        # Truncate transcript if too long (keep under 150k tokens roughly)
        max_chars = 400000
        if len(transcript) > max_chars:
            transcript = transcript[:max_chars] + "\n\n[TRANSCRIPT TRUNCATED]"

        user_prompt = f"""PODCAST: {video_title}
CHANNEL: {channel_name}
DESCRIPTION: {description}

TRANSCRIPT:
{transcript}"""

        try:
            response = await self.client.messages.create(
                model=self.model_fast,
                max_tokens=4096,
                system=ANALYSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}]
            )

            raw = response.content[0].text
            # Clean potential markdown fences
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            result = json.loads(raw)
            result["tokens_used"] = response.usage.input_tokens + response.usage.output_tokens
            result["model_used"] = self.model_fast
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Unreal Engine analysis failed: {e}")
            return None

    async def analyze_reddit_post(
        self,
        title: str,
        selftext: str,
        subreddit_name: str,
        post_type: str,
        score: int,
        comments: list[dict] = None,
    ) -> Optional[dict]:
        """Analyze a Reddit post with its top comments."""
        # Build comment block sorted by score
        comments_block = ""
        if comments:
            sorted_comments = sorted(comments, key=lambda c: c.get("score", 0), reverse=True)
            top = sorted_comments[:200]
            comments_block = "\n\nTOP COMMENTS:\n" + "\n\n".join(
                f"[{c.get('score', 0)} pts] {c.get('author', 'anon')}: {c.get('body', '')}"
                for c in top
            )

        user_prompt = f"""SUBREDDIT: r/{subreddit_name}
POST TYPE: {post_type}
SCORE: {score}
TITLE: {title}

{selftext if selftext else '[Link post — no body text]'}{comments_block}"""

        try:
            response = await self.client.messages.create(
                model=self.model_fast,
                max_tokens=4096,
                system=REDDIT_ANALYSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            raw = raw.strip()

            result = json.loads(raw)
            result["tokens_used"] = response.usage.input_tokens + response.usage.output_tokens
            result["model_used"] = self.model_fast
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Reddit analysis JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Reddit post analysis failed: {e}")
            return None

    async def analyze_comments(
        self,
        comments: list[dict],
        video_title: str,
        max_comments: int = 200,
    ) -> Optional[dict]:
        """Analyze comment section for community intelligence."""
        # Sort by likes, take top N
        sorted_comments = sorted(comments, key=lambda c: c.get("like_count", 0), reverse=True)
        top_comments = sorted_comments[:max_comments]

        comments_text = "\n\n".join(
            f"[{c.get('like_count', 0)} likes] {c.get('author', 'anon')}: {c.get('text', '')}"
            for c in top_comments
        )

        user_prompt = f"""VIDEO: {video_title}
TOTAL COMMENTS: {len(comments)}
TOP {len(top_comments)} COMMENTS BY ENGAGEMENT:

{comments_text}"""

        try:
            response = await self.client.messages.create(
                model=self.model_fast,
                max_tokens=2048,
                system=COMMENT_ANALYSIS_PROMPT,
                messages=[{"role": "user", "content": user_prompt}]
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]

            return json.loads(raw.strip())

        except Exception as e:
            logger.error(f"Comment analysis failed: {e}")
            return None

    async def generate_research_directives(
        self,
        recent_analyses: list[dict],
    ) -> list[dict]:
        """
        Cross-reference multiple video analyses to identify
        high-priority research targets for ALIENDB.
        """
        prompt = f"""You have analysis data from {len(recent_analyses)} recent UAP/disclosure podcasts.
Identify the TOP research directives that should be acted on - documents to find, claims to verify,
connections to explore. Prioritize directives that appear across multiple sources.

ANALYSES:
{json.dumps(recent_analyses, indent=2)[:100000]}

Respond ONLY with valid JSON:
{{
    "directives": [
        {{
            "directive": "string",
            "priority": "critical|high|medium",
            "sources": ["video titles that raised this"],
            "action_type": "foia|document_search|person_research|claim_verification|timeline_construction",
            "reasoning": "string"
        }}
    ],
    "emerging_patterns": ["string"],
    "cross_references": ["string"]
}}"""

        try:
            response = await self.client.messages.create(
                model=self.model_fast,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}]
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]

            return json.loads(raw.strip())

        except Exception as e:
            logger.error(f"Research directive generation failed: {e}")
            return []
