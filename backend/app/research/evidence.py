"""One source representation for public web results and private KB chunks."""

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit

from app.services.knowledge_retrieval import Evidence


def collect_sources(
    search_results: dict[str, list[dict[str, Any]]],
    knowledge_evidence: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Deduplicate sources without placing knowledge material in public search."""
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in knowledge_evidence or []:
        source = Evidence.model_validate(item).model_dump(mode="json")
        if source["source_id"] not in seen:
            seen.add(source["source_id"])
            sources.append(source)

    for results in search_results.values():
        for result in results:
            url = result.get("url", "")
            if not isinstance(url, str):
                continue
            try:
                parsed_url = urlsplit(url)
            except ValueError:
                continue
            if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                continue
            source_id = "web-" + hashlib.sha256(url.encode()).hexdigest()[:16]
            if source_id in seen:
                continue
            source = Evidence(
                source_id=source_id,
                kind="web",
                content=result.get("content", ""),
                title=result.get("title", ""),
                url=url,
                document_id=None,
                chunk_index=None,
            )
            seen.add(source_id)
            sources.append(source.model_dump(mode="json"))
    return sources


def format_evidence(sources: list[dict[str, Any]]) -> str:
    """JSON keeps source metadata separate from instructions in source text."""
    return json.dumps(sources, ensure_ascii=False)
