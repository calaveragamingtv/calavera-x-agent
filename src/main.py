
import asyncio
import json

from pathlib import Path
from datetime import datetime, timezone

from src.scraper import XScraper
from src.ai.gemini_analyzer import GeminiAnalyzer


# Configuration
USERNAME = "CalaveraGaming"
LIMIT = 10

DATA_DIR = Path("data")
POSTS_FILE = DATA_DIR / "posts.json"
RANKING_FILE = DATA_DIR / "ranking.json"


def save_json(file_path, data):
    file_path.parent.mkdir(parents=True, exist_ok=True)

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


async def main():
    print("Starting X Agent")

    scraper = XScraper()

    try:
        # 1. Authenticate and initialize Playwright
        await scraper.authenticate()

        # 2. Scrape X posts
        posts = await scraper.get_user_posts(
            username=USERNAME,
            limit=LIMIT
        )

        if not posts:
            raise RuntimeError(
                f"No posts found for @{USERNAME}"
            )

        print(f"Scraped {len(posts)} posts")

        # 3. Save original posts
        posts_data = {
            "account": USERNAME,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(posts),
            "posts": posts
        }

        save_json(POSTS_FILE, posts_data)

        print(f"Posts saved to {POSTS_FILE}")

        # 4. Analyze posts with Gemini
        analyzer = GeminiAnalyzer()

        ranking = analyzer.analyze_posts(posts)

        # 5. Save ranking
        ranking_data = {
            "account": USERNAME,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "model": analyzer.model,
            "total": len(ranking["ranking"]),
            "ranking": ranking["ranking"]
        }

        save_json(RANKING_FILE, ranking_data)

        print(f"Ranking saved to {RANKING_FILE}")

        # 6. Display ranking
        print("\nRanking generated:")

        for item in ranking["ranking"]:
            print(
                f"#{item['score']} | "
                f"{item['priority']} | "
                f"{item['recommended_action']} | "
                f"{item['post_id']}"
            )

        print("\nX Agent completed successfully")

    finally:
        await scraper.close()


if __name__ == "__main__":
    asyncio.run(main())
