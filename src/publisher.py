import asyncio
import os
import re

from playwright.async_api import async_playwright

X_USERNAME = "CalaveraGaming"


async def publish_reply():
    auth_token = os.environ.get("X_AUTH_TOKEN")
    ct0 = os.environ.get("X_CT0")
    post_id = os.environ.get("TARGET_POST_ID")
    reply_text = os.environ.get("REPLY_TEXT")

    if not auth_token or not ct0:
        raise RuntimeError("Missing X authentication cookies")

    if not post_id or not re.fullmatch(r"\d+", post_id):
        raise RuntimeError("Invalid TARGET_POST_ID")

    if not reply_text or not reply_text.strip():
        raise RuntimeError("REPLY_TEXT cannot be empty")

    url = f"https://x.com/{X_USERNAME}/status/{post_id}"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            locale="es-AR"
        )

        # Recuperamos la sesión mediante las cookies
        await context.add_cookies([
            {
                "name": "auth_token",
                "value": auth_token,
                "domain": ".x.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax"
            },
            {
                "name": "ct0",
                "value": ct0,
                "domain": ".x.com",
                "path": "/",
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax"
            }
        ])

        page = await context.new_page()

        try:
            print(f"Opening post: {url}")

            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000
            )

            # Esperamos el editor de respuestas
            editor = page.locator(
                '[data-testid="tweetTextarea_0"]'
            )

            await editor.wait_for(
                state="visible",
                timeout=30000
            )

            print("Reply editor found")

            # Escribimos la respuesta
            await editor.fill(reply_text)

            # Botón de publicar respuesta
            publish_button = page.locator(
                '[data-testid="tweetButtonInline"]'
            )

            await publish_button.wait_for(
                state="visible",
                timeout=15000
            )

            await publish_button.click()

            # Esperamos confirmación de X
            toast = page.locator(
                '[data-testid="toast"]'
            )

            try:
                await toast.wait_for(
                    state="visible",
                    timeout=15000
                )
                print("X displayed a confirmation notification")

            except Exception:
                print(
                    "No confirmation notification detected. "
                    "Publication needs verification."
                )
                raise RuntimeError(
                    "Could not verify that the reply was published"
                )

            print("Reply process completed")

        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(publish_reply())
