import asyncio
import json

from pathlib import Path
from datetime import datetime, timezone

from scraper import XScraper


USERNAME = "CalaveraGaming"
LIMIT = 10

DATA_FILE = Path("data/posts.json")


async def main():
    scraper = XScraper()

    await scraper.authenticate()

    posts = await scraper.get_user_posts(
        username=USERNAME,
        limit=LIMIT
    )

    output = {
        "account": USERNAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(posts),
        "posts": posts
    }

    DATA_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            output,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(f"Successfully saved {len(posts)} posts")


if __name__ == "__main__":
    asyncio.run(main())
