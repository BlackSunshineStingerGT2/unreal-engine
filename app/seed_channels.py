"""
Seed script to populate the channel registry with core UAP podcast channels.
Run after deployment: python -m app.seed_channels

Update handles/IDs as needed. Priority 1-10 (10 = highest priority, checked first).
"""
import asyncio
import httpx
from app.config import settings

# Format: (handle_or_id, category, priority)
SEED_CHANNELS = [
    # Tier 1 - Primary UAP Disclosure
    ("@WeaponizedPodcast", "disclosure", 10),      # Jeremy Corbell & George Knapp
    ("@ThatUFOPodcast", "disclosure", 9),           # Andy McGrillen
    ("@JesseMichels", "disclosure", 9),             # American Alchemy
    ("@rosscoulthart", "disclosure", 9),            # Ross Coulthart
    ("@DarkJournalist", "disclosure", 9),           # Daniel Liszt
    ("@MergedPodcast", "disclosure", 8),            # Merged
    ("@ChrisLehto", "disclosure", 8),               # Chris Lehto

    # Tier 2 - Adjacent / Analytical
    ("@TheHillTV", "politics", 7),                  # The Hill (Rising)
    ("@NewsNation", "news", 7),                     # NewsNation
    ("@caborama", "disclosure", 7),                 # Koncrete / Danny Jones
    ("@JoeRogan", "general", 6),                    # JRE (UAP episodes)
    ("@4biddenknowledge", "disclosure", 6),          # Billy Carson

    # Tier 3 - Science / Technical
    ("@SCU_Anomalous", "science", 7),               # Scientific Coalition for UAP Studies
    ("@TheSOLFoundation", "science", 8),            # Sol Foundation

    # Tier 4 - Community / Commentary
    ("@UFOJoe101", "community", 5),                 # Joe Murgia
    ("@UAPJason", "community", 5),                  # Jason Guillemette
]

API_BASE = "http://localhost:8000"


async def seed():
    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {settings.pipeline_token}"}

        for handle, category, priority in SEED_CHANNELS:
            try:
                resp = await client.post(
                    f"{API_BASE}/api/v1/channels",
                    params={
                        "handle": handle,
                        "category": category,
                        "priority": priority,
                    },
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    print(f"  Added: {data['channel']['name']} ({handle})")
                elif resp.status_code == 409:
                    print(f"  Already exists: {handle}")
                else:
                    print(f"  Failed: {handle} - {resp.status_code} {resp.text}")
            except Exception as e:
                print(f"  Error: {handle} - {e}")

            await asyncio.sleep(0.5)  # Rate limit


if __name__ == "__main__":
    print("Seeding channel registry...")
    asyncio.run(seed())
    print("Done.")
