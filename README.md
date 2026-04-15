# YouTube Intelligence Pipeline
## Codename: Unreal Engine v1

Podcast monitoring, transcript collection, engagement analytics, and LLM-powered
content analysis for ALIENDB / Catastrophic Disclosure.

---

## Architecture

```
YouTube API v3 ──> Channel Poller ──> Video Collector
                                         │
                          ┌──────────────┼──────────────┐
                          ▼              ▼              ▼
                    Transcripts      Comments      Engagement
                     (free)        (API quota)     Snapshots
                          │              │          (time-series)
                          └──────┬───────┘
                                 ▼
                         Unreal Engine (Claude)
                                 │
                     ┌───────────┼───────────┐
                     ▼           ▼           ▼
                  Topics     Claims     Research
                 Entities   Questions   Directives
                                 │
                                 ▼
                         ALIENDB Ingestion
                         (future integration)
```

## Quick Start

### 1. Environment Variables (Railway)

```
DATABASE_URL=postgresql+asyncpg://...
YOUTUBE_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
PIPELINE_TOKEN=your_secret_token
POLLING_INTERVAL_MINUTES=30
ENGAGEMENT_SNAPSHOT_HOURS=1,6,24,72
MAX_COMMENTS_PER_VIDEO=500
```

### 2. Get a YouTube API Key

1. Go to https://console.cloud.google.com
2. Create a project (or use existing)
3. Enable "YouTube Data API v3"
4. Create credentials > API Key
5. (Optional) Restrict key to YouTube Data API v3 only

### 3. Deploy to Railway

```bash
railway login
railway init
railway link
# Set env vars in Railway dashboard
railway up
```

### 4. Seed Channels

After deployment, update `app/seed_channels.py` with your API base URL, then:

```bash
python -m app.seed_channels
```

Or add channels via API:

```bash
curl -X POST "https://your-app.railway.app/api/v1/channels?handle=@WeaponizedPodcast&category=disclosure&priority=10" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## API Reference

All endpoints require `Authorization: Bearer YOUR_TOKEN` header.

### Channel Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/channels` | Add channel (params: handle or channel_id, category, priority) |
| GET | `/api/v1/channels` | List all channels |
| PATCH | `/api/v1/channels/{id}` | Update priority/category/active |
| DELETE | `/api/v1/channels/{id}` | Remove channel |

### Pipeline Controls

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/pipeline/run` | Run full cycle (poll + collect + analyze) |
| POST | `/api/v1/pipeline/poll` | Poll channels for new videos only |
| POST | `/api/v1/pipeline/collect/{video_id}` | Collect transcript + comments for one video |
| POST | `/api/v1/pipeline/analyze/{video_id}` | Force (re)analysis of one video |

### Data Retrieval

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/videos` | List videos (filter: channel_id, analyzed) |
| GET | `/api/v1/videos/{video_id}` | Full detail + analysis + engagement timeline |
| GET | `/api/v1/analytics/topics?days=7` | Trending topics across all content |
| GET | `/api/v1/analytics/directives?days=7` | Research directives from Unreal Engine |
| GET | `/api/v1/logs` | Pipeline operation logs |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check |
| GET | `/` | Service info |

---

## Pipeline Cycle

The scheduler runs every `POLLING_INTERVAL_MINUTES` (default 30):

1. **Poll** all active channels for new videos
2. **Collect transcripts** for videos missing them (free, no quota)
3. **Collect comments** for videos missing them (uses API quota)
4. **Analyze** videos that have transcripts but no analysis (Anthropic API cost)
5. **Snapshot** engagement for videos < 7 days old (tracks velocity)

---

## Costs

- **YouTube API**: Free (10k quota units/day)
- **Transcripts**: Free (youtube-transcript-api, no quota)
- **Anthropic**: ~$0.10-0.30 per video analysis (Sonnet)
- **Railway**: Existing plan + marginal compute

Estimated: $30-60/month at 50-100 channels, ~200 videos/month analyzed.

---

## JSON Schema: Video Analysis Output

```json
{
    "topics": ["grusch_testimony", "reverse_engineering"],
    "entities": [
        {"name": "David Grusch", "type": "person", "context": "whistleblower testimony"}
    ],
    "claims": [
        {"claim": "Program existed since 1947", "source": "guest", "confidence": "medium"}
    ],
    "questions_raised": ["What happened to the Holloman AFB footage?"],
    "research_directives": [
        {
            "directive": "FOIA request for Holloman AFB 1964 records",
            "priority": "high",
            "reasoning": "Multiple sources reference this event"
        }
    ],
    "summary": "Episode discusses...",
    "information_density": 0.7,
    "sentiment_score": 0.4
}
```

---

## Future Integration Points

- **ALIENDB**: Research directives feed into Oracle/Keymaker search
- **Forum monitoring**: Community questions feed back as collection priorities
- **Merit system**: User engagement data weights directive priority
- **Reddit/X pipelines**: Same architecture, different collectors
