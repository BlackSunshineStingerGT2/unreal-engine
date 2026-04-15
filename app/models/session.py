from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from app.models.database import Base
from app.config import settings


def build_asyncpg_url(raw_url: str) -> str:
    """Convert a standard postgresql:// URL to asyncpg-compatible format.

    Railway (and many PaaS providers) supply a URL with psycopg2-style
    parameters such as ``sslmode=require``.  asyncpg does not accept
    ``sslmode``; it expects ``ssl=require`` instead.  This function:

    1. Switches the scheme to ``postgresql+asyncpg://``.
    2. Strips any ``sslmode`` parameter from the query string.
    3. Adds ``ssl=require`` when the original URL requested SSL
       (i.e. ``sslmode`` was ``require``, ``verify-ca``, or
       ``verify-full``).
    """
    # Normalise scheme
    if raw_url.startswith("postgresql://") or raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)

    parsed = urlparse(raw_url)
    params = parse_qs(parsed.query, keep_blank_values=True)

    # Detect whether SSL was requested via the psycopg2-style parameter
    sslmode_values = params.pop("sslmode", [])
    ssl_required = any(
        v in ("require", "verify-ca", "verify-full") for v in sslmode_values
    )

    # Preserve an explicit ssl= param if already present; otherwise inject one
    if ssl_required and "ssl" not in params:
        params["ssl"] = ["require"]

    new_query = urlencode({k: v[0] for k, v in params.items()})
    clean = parsed._replace(query=new_query)
    return urlunparse(clean)


db_url = build_asyncpg_url(settings.database_url)

engine = create_async_engine(db_url, echo=False, pool_size=5, max_overflow=10)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
