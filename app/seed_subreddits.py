"""
Seed script to populate the subreddit registry with core UAP communities.
Run after deployment: python -m app.seed_subreddits

Update names as needed. Priority 1-10 (10 = highest priority, checked first).
"""
import asyncio
import httpx
from app.config import settings

# Format: (subreddit_name, category, priority, credibility)
# credibility: 1-10 editorial trust score (None = not yet rated)
SEED_SUBREDDITS = [
    # Tier 1 - Primary UAP Communities
    ("UFOs", "disclosure", 10, None),           # r/UFOs — largest UAP subreddit
    ("UAP", "disclosure", 9, None),             # r/UAP — focused on UAP terminology
    ("ufo", "disclosure", 8, None),             # r/ufo — secondary UFO sub

    # Tier 2 - Specialized
    ("UFOscience", "science", 8, None),         # r/UFOscience — technical/scientific analysis
    ("UFOB", "disclosure", 7, None),            # r/UFOB — curated UAP content
    ("HighStrangeness", "community", 7, None),  # r/HighStrangeness — paranormal + UAP crossover
    ("aliens", "community", 7, None),           # r/aliens — broader alien discussion

    # Tier 3 - Adjacent / Cross-reference
    ("experiencers", "community", 6, None),     # r/experiencers — contact/experience reports
    ("Skydentify", "science", 5, None),         # r/Skydentify — identification requests
    ("UFObelievers", "community", 4, None),     # r/UFObelievers
]

API_BASE = "http://localhost:8000"


async def seed():
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {settings.pipeline_token}"}

        for name, category, priority, credibility in SEED_SUBREDDITS:
            try:
                params = {
                    "subreddit_name": name,
                    "category": category,
                    "priority": priority,
                }
                if credibility is not None:
                    params["credibility"] = credibility
                resp = await client.post(
                    f"{API_BASE}/api/v1/reddit/subreddits",
                    params=params,
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"  Added: {data['subreddit']['display_name']}")
                elif resp.status_code == 409:
                    print(f"  Already exists: r/{name}")
                else:
                    print(f"  Failed: r/{name} - {resp.status_code} {resp.text}")
            except Exception as e:
                print(f"  Error: r/{name} - {e}")

            await asyncio.sleep(0.5)


if __name__ == "__main__":
    print("Seeding subreddit registry...")
    asyncio.run(seed())
    print("Done.")
