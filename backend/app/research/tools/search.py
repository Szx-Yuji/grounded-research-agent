import time

from tavily import TavilyClient

from app.research import cache, config

_client: TavilyClient | None = None


class SearchError(RuntimeError):
    """A search could not be run. Not the same as a search that found nothing."""


def get_client() -> TavilyClient:
    global _client
    if _client is None:
        api_key = config.TAVILY_API_KEY
        if not api_key:
            raise SearchError("TAVILY_API_KEY environment variable not set")
        _client = TavilyClient(api_key=api_key)
    return _client


def reset_client() -> None:
    global _client
    _client = None


def search_web(
    query: str, max_results: int | None = None, use_cache: bool = True
) -> list[dict]:
    """Top Tavily results. Empty list if nothing matched, SearchError if the
    request could not be made."""
    if max_results is None:
        max_results = config.SEARCH_MAX_RESULTS

    if use_cache:
        hit = cache.get(query, max_results)
        if hit is not None:
            return hit

    client = get_client()
    for attempt in range(1, config.SEARCH_MAX_ATTEMPTS + 1):
        try:
            response = client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
            )
        except Exception:  # noqa: BLE001 - Tavily raises bare Exceptions
            if attempt < config.SEARCH_MAX_ATTEMPTS:
                time.sleep(config.SEARCH_BACKOFF_SECONDS * attempt)
            continue

        if not isinstance(response, dict) or not isinstance(
            response.get("results"), list
        ):
            raise SearchError("Search provider returned an invalid response")
        results = []
        for result in response["results"][:max_results]:
            if not isinstance(result, dict):
                continue
            if not all(
                isinstance(result.get(key, ""), str) for key in ("title", "url")
            ):
                continue
            content = result.get("content") or ""
            if not isinstance(content, str):
                continue
            results.append(
                {
                    "title": result.get("title", "")[:1000],
                    "url": result.get("url", "")[:4000],
                    "content": content[:500],
                }
            )
        if use_cache and results:
            cache.set(query, max_results, results)
        return results

    raise SearchError(f"Search failed after {config.SEARCH_MAX_ATTEMPTS} attempts")
