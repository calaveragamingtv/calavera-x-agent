import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from twscrape import API


USERNAME = "CalaveraGaming"
LIMIT = 10

DATA_FILE = Path("data/posts.json")
DB_FILE = "accounts.db"


async def scrape_posts():
    auth_token = os.environ["X_AUTH_TOKEN"]
    ct0 = os.environ["X_CT0"]

    api = API(DB_FILE)

    # Registrar la sesión mediante cookies
    await api.pool.add_account(
        username="scraper_account",
        password="",
        email="",
        email_password="",
        cookies={
            "auth_token": auth_token,
            "ct0": ct0
        }
    )

    # Obtener usuario
    user = await api.user_by_login(USERNAME)

    if not user:
        raise RuntimeError(f"No se encontró el usuario {USERNAME}")

    # Obtener publicaciones
    tweets = []

    async for tweet in api.user_tweets(user.id, limit=LIMIT):
        tweets.append({
            "id": str(tweet.id),
            "text": tweet.rawContent,
            "url": f"https://x.com/{USERNAME}/status/{tweet.id}",
            "created_at": tweet.date.isoformat(),
            "replies": tweet.replyCount,
            "reposts": tweet.retweetCount,
            "likes": tweet.likeCount,
            "views": tweet.viewCount
        })

        if len(tweets) >= LIMIT:
            break

    # Guardar JSON
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "account": USERNAME,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(tweets),
        "posts": tweets
    }

    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print(f"Se guardaron {len(tweets)} publicaciones.")

    await api.pool.close()


if __name__ == "__main__":
    asyncio.run(scrape_posts())
