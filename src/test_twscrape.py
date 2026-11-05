
import os
import asyncio

# Seleccionar backend antes de importar twscrape
os.environ.setdefault("TWS_HTTP_BACKEND", "curl")

from twscrape import API


USERNAME = "CalaveraGaming"
LIMIT = 10


async def main():
    auth_token = os.getenv("X_AUTH_TOKEN")
    ct0 = os.getenv("X_CT0")
    account_username = os.getenv("X_ACCOUNT_USERNAME")

    if not auth_token or not ct0 or not account_username:
        raise RuntimeError("Missing X authentication secrets")

    # Base de datos independiente para no reutilizar bloqueos anteriores
    api = API("twscrape_test.db")

    cookies = f"auth_token={auth_token}; ct0={ct0}"

    await api.pool.add_account_cookies(
        username=account_username,
        cookies=cookies
    )

    print(f"Searching X user: @{USERNAME}")

    user = await api.user_by_login(USERNAME)

    if not user:
        raise RuntimeError(f"User not found: @{USERNAME}")

    print(f"User found: {user.username}")
    print(f"User ID: {user.id}")
    print(f"\nLast {LIMIT} posts:\n")

    count = 0

    async for tweet in api.user_tweets(user.id, limit=LIMIT):
        count += 1

        print("=" * 60)
        print(f"ID: {tweet.id}")
        print(f"Date: {tweet.date}")
        print(f"Text: {tweet.rawContent}")
        print(f"Likes: {tweet.likeCount}")
        print(f"Replies: {tweet.replyCount}")
        print(f"Reposts: {tweet.retweetCount}")
        print(f"Views: {tweet.viewCount}")
        print(f"URL: https://x.com/{USERNAME}/status/{tweet.id}")

    print("=" * 60)
    print(f"\nSuccessfully retrieved {count} posts")


if __name__ == "__main__":
    asyncio.run(main())
