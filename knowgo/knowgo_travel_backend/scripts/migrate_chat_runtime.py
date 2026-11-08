"""Apply the additive runtime migration, safe to run repeatedly."""
import asyncio

from app.models import Base
from app.models.base import engine, init_db


async def main():
    try:
        await init_db()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
