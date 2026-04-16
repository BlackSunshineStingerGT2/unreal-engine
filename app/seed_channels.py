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
    ("@UFOJoe101", "disclosure", 9),                # Joe Murgia

    # Tier 2 - Science / Technical
    ("@SCU_Anomalous", "science", 7),               # Scientific Coalition for UAP Studies
    ("@TheSOLFoundation", "science", 8),            # Sol Foundation
    ("@theangryastronaut", "science", 7),           # The Angry Astronaut

    # Tier 3 - Community / Commentary
    ("@UAPJason", "community", 5),                  # Jason Guillemette
    ("@CristinaG", "disclosure", 7),                # Cristina Gomez (Strange Paradigms)
    ("@psicoactivopodcast", "disclosure", 7),       # Psicoactivo Podcast

    # Internal — ally channels, full technical analysis & platform support
    ("@UfosAroundTheWorld", "ally", 10),            # UFOs Around The World (internal project)
    ("@AcrossThePondYT", "ally", 10),               # Across The Pond Podcast
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
