from tavily import TavilyClient

from app.research import cache, config
from app.research.events import check_cancelled
from app.runtime.context import provider_call, request_fingerprint, request_timeout

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
    check_cancelled()

    if use_cache:
        hit = cache.get(query, max_results)
        if hit is not None:
            return hit

    client = get_client()
    # A timeout does not prove that the provider did not execute or bill a
    # search. Record the unknown outcome and require an explicit new intent.
    try:
        with provider_call(
            "search",
            operation="tavily.search",
            request_key=request_fingerprint(
                "search", {"query": query, "max_results": max_results}
            ),
        ) as call:
            response = client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
                timeout=request_timeout(30),
            )
            call.succeed()
    except Exception as exc:
        from app.research.errors import ResearchCancelled
        from app.runtime.context import BudgetExceeded

        if isinstance(exc, (BudgetExceeded, ResearchCancelled)):
            raise
        raise SearchError(
            "Search outcome is unknown or unavailable; no automatic replay was attempted"
        ) from None

    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise SearchError("Search provider returned an invalid response")
    results = []
    for result in response["results"][:max_results]:
        if not isinstance(result, dict):
            continue
        if not all(isinstance(result.get(key, ""), str) for key in ("title", "url")):
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
