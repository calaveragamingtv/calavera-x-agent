import os
import re

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


class XScraper:

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def authenticate(self):
        auth_token = os.getenv("X_AUTH_TOKEN")
        ct0 = os.getenv("X_CT0")

        if not auth_token or not ct0:
            raise RuntimeError("Missing X authentication cookies")

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )

        self.context = await self.browser.new_context(
            viewport={"width": 1365, "height": 900},
            locale="en-US"
        )

        await self.context.add_cookies([
            {
                "name": "auth_token",
                "value": auth_token,
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax"
            },
            {
                "name": "ct0",
                "value": ct0,
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "httpOnly": False,
                "sameSite": "Lax"
            }
        ])

        self.page = await self.context.new_page()
        self.page.set_default_timeout(15000)

        print("Playwright browser initialized")
        
        # Temporary authentication diagnostic
        print("Testing X session...")
        
        response = await self.page.goto(
            "https://x.com/home",
            wait_until="domcontentloaded",
            timeout=60000
        )
        
        print(
            "X HOME HTTP STATUS:",
            response.status if response else "No response"
        )
        
        print("X HOME URL:", self.page.url)
        
        print(
            "AUTH COOKIE PRESENT:",
            bool(await self.context.cookies("https://x.com"))
        )
        
        print("PAGE TITLE:", await self.page.title())

    async def get_user_posts(self, username, limit=10):
        if self.page is None:
            raise RuntimeError("Call authenticate() before scraping")

        if limit < 1:
            raise ValueError("limit must be greater than zero")

        username = username.lstrip("@")

        profile_url = f"https://x.com/{username}"

        print(f"Opening X profile: @{username}")

        response = await self.page.goto(
            profile_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        if response and response.status in (401, 403):
            raise RuntimeError(
                f"X rejected the profile request: HTTP {response.status}"
            )

        try:
            await self.page.locator(
                'article[data-testid="tweet"]'
            ).first.wait_for(timeout=30000)

        except PlaywrightTimeoutError as error:
            current_url = self.page.url

            if "/login" in current_url or "/i/flow/login" in current_url:
                raise RuntimeError(
                    "X redirected to login. Authentication cookies were rejected."
                ) from error

            raise RuntimeError(
                "Could not find posts on the X profile. "
                "The page may be blocked or its layout may have changed."
            ) from error

        posts_by_id = {}
        no_new_posts_rounds = 0

        while len(posts_by_id) < limit:
            articles = self.page.locator(
                'article[data-testid="tweet"]'
            )

            article_count = await articles.count()
            previous_count = len(posts_by_id)

            for index in range(article_count):
                article = articles.nth(index)

                try:
                    post = await self._extract_post(article, username)

                    if post:
                        posts_by_id[post["id"]] = post

                    if len(posts_by_id) >= limit:
                        break

                except Exception as error:
                    print(f"Could not extract post: {error}")

            if len(posts_by_id) >= limit:
                break

            if len(posts_by_id) == previous_count:
                no_new_posts_rounds += 1
            else:
                no_new_posts_rounds = 0

            if no_new_posts_rounds >= 4:
                break

            await self.page.mouse.wheel(0, 1200)
            await self.page.wait_for_timeout(1500)

        posts = list(posts_by_id.values())[:limit]

        print(f"Successfully scraped {len(posts)} posts from @{username}")

        return posts

    async def _extract_post(self, article, username):
        text_locator = article.locator(
            '[data-testid="tweetText"]'
        )

        if await text_locator.count() == 0:
            return None

        text = await text_locator.first.inner_text()

        time_locator = article.locator("time").first

        if await time_locator.count() == 0:
            return None

        created_at = await time_locator.get_attribute("datetime")

        link = await time_locator.locator(
            "xpath=.."
        ).get_attribute("href")

        if not link:
            return None

        match = re.search(r"/status/(\d+)", link)

        if not match:
            return None

        post_id = match.group(1)

        return {
            "id": post_id,
            "text": text,
            "url": f"https://x.com/{username}/status/{post_id}",
            "created_at": created_at,
            "replies": await self._extract_metric(article, "reply"),
            "reposts": await self._extract_metric(article, "retweet"),
            "likes": await self._extract_metric(article, "like"),
            "views": await self._extract_metric(article, "view")
        }

    async def _extract_metric(self, article, metric):
        locator = article.locator(
            f'[data-testid="{metric}"]'
        )

        if await locator.count() == 0:
            return 0

        label = await locator.first.get_attribute("aria-label")
        value = await locator.first.inner_text()

        raw_value = label or value

        if not raw_value:
            return 0

        match = re.search(r"([\d,.]+)\s*([KMB])?", raw_value, re.I)

        if not match:
            return 0

        number = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").upper()

        multiplier = {
            "K": 1_000,
            "M": 1_000_000,
            "B": 1_000_000_000
        }.get(suffix, 1)

        return int(number * multiplier)

    async def close(self):
        if self.browser:
            await self.browser.close()

        if self.playwright:
            await self.playwright.stop()
