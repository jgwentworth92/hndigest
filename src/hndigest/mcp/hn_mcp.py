"""HN API client (MCP-style tool interface).

Phase 1 implementation: plain async Python functions wrapping the Hacker News
Firebase API.  A full MCP server wrapper will be added in a future phase.

Functions use a shared ``aiohttp.ClientSession`` for connection pooling.
The caller is responsible for creating and closing the session.
"""

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

HN_BASE_URL = "https://hacker-news.firebaseio.com/v0"
REQUEST_TIMEOUT_SECONDS = 10


async def fetch_top_stories(session: aiohttp.ClientSession) -> list[int]:
    """Fetch the list of current top story IDs from the HN API.

    Args:
        session: An open aiohttp client session for connection reuse.

    Returns:
        A list of integer story IDs, or an empty list on failure.
    """
    url = f"{HN_BASE_URL}/topstories.json"
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        ) as resp:
            resp.raise_for_status()
            data: list[int] = await resp.json()
            logger.info("Fetched %d top story IDs from HN API", len(data))
            return data
    except aiohttp.ClientError as exc:
        logger.error("HTTP error fetching top stories: %s", exc)
        return []
    except TimeoutError:
        logger.error("Timeout fetching top stories from %s", url)
        return []
    except Exception:
        logger.exception("Unexpected error fetching top stories")
        return []


async def fetch_item(
    session: aiohttp.ClientSession, item_id: int
) -> dict[str, Any] | None:
    """Fetch a single HN item by its ID.

    Args:
        session: An open aiohttp client session for connection reuse.
        item_id: The HN item ID to fetch.

    Returns:
        The item dict from the HN API, or None on failure.
    """
    url = f"{HN_BASE_URL}/item/{item_id}.json"
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        ) as resp:
            resp.raise_for_status()
            data: dict[str, Any] | None = await resp.json()
            if data is None:
                logger.warning("HN API returned null for item %d", item_id)
                return None
            logger.debug("Fetched item %d: %s", item_id, data.get("title", ""))
            return data
    except aiohttp.ClientError as exc:
        logger.error("HTTP error fetching item %d: %s", item_id, exc)
        return None
    except TimeoutError:
        logger.error("Timeout fetching item %d from %s", item_id, url)
        return None
    except Exception:
        logger.exception("Unexpected error fetching item %d", item_id)
        return None
