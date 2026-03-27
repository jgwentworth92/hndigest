"""Web content fetching and extraction (MCP-style tool interface).

Phase 2 implementation: plain async Python functions for fetching URLs and
extracting article text from HTML.  A full MCP server wrapper will be added
in a future phase.

Functions use a shared ``aiohttp.ClientSession`` for connection pooling.
The caller is responsible for creating and closing the session.
"""

import logging

import aiohttp
import trafilatura

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; hndigest/0.1; "
    "+https://github.com/hndigest/hndigest)"
)

_PAYWALL_INDICATORS = [
    "paywall",
    "subscribe to read",
    "subscription required",
    "premium content",
    "members only",
    "sign in to continue",
    "create an account to read",
]


async def fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    timeout: int = 10,
) -> tuple[str, str]:
    """Fetch a URL and return its HTML content.

    Handles redirects automatically.  Returns a status string indicating
    success or the kind of failure encountered.

    Args:
        session: An open aiohttp client session for connection reuse.
        url: The URL to fetch.
        timeout: Request timeout in seconds.

    Returns:
        A tuple of (html_content, status) where status is one of
        "success", "failed", "timeout", or "paywall".
    """
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        ) as resp:
            if resp.status == 402:
                logger.info("Paywall detected (HTTP 402) for %s", url)
                return ("", "paywall")

            if resp.status == 403:
                body = await resp.text()
                body_lower = body.lower()
                if any(indicator in body_lower for indicator in _PAYWALL_INDICATORS):
                    logger.info("Paywall detected (HTTP 403) for %s", url)
                    return ("", "paywall")
                logger.error("HTTP 403 Forbidden for %s", url)
                return ("", "failed")

            if resp.status >= 400:
                logger.error("HTTP %d error fetching %s", resp.status, url)
                return ("", "failed")

            html = await resp.text()
            logger.info("Fetched %d bytes from %s", len(html), url)
            return (html, "success")

    except TimeoutError:
        logger.error("Timeout fetching %s", url)
        return ("", "timeout")
    except aiohttp.ClientError as exc:
        logger.error("HTTP error fetching %s: %s", url, exc)
        return ("", "failed")
    except Exception:
        logger.exception("Unexpected error fetching %s", url)
        return ("", "failed")


def extract_article_text(html: str) -> str:
    """Extract article text from raw HTML using trafilatura.

    Args:
        html: Raw HTML content of a web page.

    Returns:
        The extracted article text, or an empty string if extraction fails.
    """
    try:
        text = trafilatura.extract(html)
    except Exception:
        logger.exception("Trafilatura extraction error")
        return ""

    if text:
        logger.info("Extracted %d characters of article text", len(text))
        return text

    logger.info("Trafilatura returned no text for input of %d bytes", len(html))
    return ""
