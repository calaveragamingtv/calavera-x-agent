import asyncio
import json

from pathlib import Path
from datetime import datetime, timezone

from scraper import XScraper
from ai.gemini_analyzer import GeminiAnalyzer


USERNAME = "CalaveraGaming"
LIMIT = 10

DATA_DIR = Path("data")
POSTS_FILE = DATA_DIR / "posts.json"
RANKING_FILE = DATA_DIR / "ranking.json"


def save_json(file_path, data):
    file_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


async def main():
    print("Starting X Agent")

    # 1. Scraping
    scraper = XScraper()

    await scraper.authenticate()

    posts = await scraper.get_user_posts(
        username=USERNAME,
        limit=LIMIT
    )

    print(f"Scraped {len(posts)} posts")

    # 2. Guardamos las publicaciones originales
    posts_data = {
        "account": USERNAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(posts),
        "posts": posts
    }

    save_json(POSTS_FILE, posts_data)

    # 3. Análisis con Gemini
    analyzer = GeminiAnalyzer()

    ranking = analyzer.analyze_posts(posts)

    # 4. Guardamos el ranking
    ranking_data = {
        "account": USERNAME,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "model": analyzer.model,
        "total": len(ranking["ranking"]),
        "ranking": ranking["ranking"]
    }

    save_json(RANKING_FILE, ranking_data)

    print("\nRanking generated:")

    for item in ranking["ranking"]:
        print(
            f"#{item['score']} | "
            f"{item['priority']} | "
            f"{item['recommended_action']} | "
            f"{item['post_id']}"
        )

    print("\nX Agent completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
