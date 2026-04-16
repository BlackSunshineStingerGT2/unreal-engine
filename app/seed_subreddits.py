"""
Seed script to populate the subreddit registry with core UAP communities.
Run after deployment: python -m app.seed_subreddits

Update names as needed. Priority 1-10 (10 = highest priority, checked first).
"""
import asyncio
import httpx
from app.config import settings

# Format: (subreddit_name, category, priority)
SEED_SUBREDDITS = [
    # Tier 1 - Primary UAP Communities
    ("UFOs", "disclosure", 10),           # r/UFOs — largest UAP subreddit
    ("UAP", "disclosure", 9),             # r/UAP — focused on UAP terminology
    ("ufo", "disclosure", 8),             # r/ufo — secondary UFO sub

    # Tier 2 - Specialized
    ("UFOscience", "science", 8),         # r/UFOscience — technical/scientific analysis
    ("UFOB", "disclosure", 7),            # r/UFOB — curated UAP content
    ("HighStrangeness", "community", 7),  # r/HighStrangeness — paranormal + UAP crossover
    ("aliens", "community", 7),           # r/aliens — broader alien discussion

    # Tier 3 - Adjacent / Cross-reference
    ("experiencers", "community", 6),     # r/experiencers — contact/experience reports
    ("Skydentify", "science", 5),         # r/Skydentify — identification requests
    ("UFObelievers", "community", 4),     # r/UFObelievers
]

API_BASE = "http://localhost:8000"


async def seed():
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {settings.pipeline_token}"}

        for name, category, priority in SEED_SUBREDDITS:
            try:
                resp = await client.post(
                    f"{API_BASE}/api/v1/reddit/subreddits",
                    params={
                        "subreddit_name": name,
                        "category": category,
                        "priority": priority,
                    },
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
