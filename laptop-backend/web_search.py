"""Web search for chat answers, used only when the question needs it.

For each question a short prompt asks a model whether answering needs current information from
the web (WEB) or not (NO); only WEB searches SearXNG (same instance Open WebUI uses). The result
snippets (title, URL, text) become numbered sources in the answer's context. Any failure (model
or SearXNG down, timeout) means answering without the web rather than failing the chat.
WEB_SEARCH=auto (default) decides per question, `always` skips the decision, `off` disables it."""

import os
from datetime import date

import httpx

from chat_service import DEFAULT_CHAT_MODEL, OLLAMA_BASE_URL

WEB_SEARCH = os.environ.get("WEB_SEARCH", "auto").lower()
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080/search")
DECISION_MODEL = os.environ.get("WEB_SEARCH_DECISION_MODEL", DEFAULT_CHAT_MODEL)
MAX_RESULTS = int(os.environ.get("WEB_SEARCH_RESULTS", "5"))

DECISION_PROMPT = """Today is {today}. Decide whether answering the question below needs a web search.

Answer WEB if it asks about current or recent events, news, prices, rates, scores, weather,
schedules, releases, or facts that may have changed since your training, or about specific
people, places, products or companies you may not know well.
Answer NO for general knowledge, explanations, definitions, maths, coding, writing, translation,
greetings, or questions about the user's own documents or the conversation.

Question: {question}

Answer with one word, WEB or NO:"""


async def needs_web_search(question: str) -> bool:
    if WEB_SEARCH == "off":
        return False
    if WEB_SEARCH == "always":
        return True
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=30) as client:
            response = await client.post("/api/generate", json={
                "model": DECISION_MODEL,
                "prompt": DECISION_PROMPT.format(today=date.today().isoformat(), question=question),
                "stream": False,
                "options": {"temperature": 0, "num_predict": 3},
            })
            response.raise_for_status()
    except httpx.HTTPError:
        return False
    return response.json().get("response", "").strip().upper().startswith("WEB")


QUERY_PROMPT = """Today is {today}. Write one short web search query (3-8 keywords) that finds
current information for the question below. Fix spelling mistakes, keep names and places, drop
filler words like "what is your view on". Reply with the query only, no quotes.

Question: {question}

Query:"""


async def search_query(question: str) -> str:
    """Keyword query for the question (the raw sentence matched words like "your", 2026-10-20);
    the question itself on any failure."""
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=30) as client:
            response = await client.post("/api/generate", json={
                "model": DECISION_MODEL,
                "prompt": QUERY_PROMPT.format(today=date.today().isoformat(), question=question),
                "stream": False,
                "options": {"temperature": 0, "num_predict": 24},
            })
            response.raise_for_status()
    except httpx.HTTPError:
        return question
    lines = response.json().get("response", "").strip().splitlines()
    query = lines[0].strip().strip('"\'') if lines else ""
    return query or question


async def _searxng(params: dict) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(SEARXNG_URL, params={**params, "format": "json"})
            response.raise_for_status()
    except httpx.HTTPError:
        return []
    return response.json().get("results", [])


async def search(question: str) -> list[dict]:
    """Top results as {title, url, content}; empty on any error. Recent news first (last month),
    general web results when there is no news."""
    query = await search_query(question)
    raw = await _searxng({"q": query, "categories": "news", "time_range": "month"})
    if not raw:
        raw = await _searxng({"q": query})
    results = []
    for r in raw:
        if r.get("url") and (r.get("content") or r.get("title")):
            results.append({"title": r.get("title", ""), "url": r["url"], "content": r.get("content", "")})
        if len(results) >= MAX_RESULTS:
            break
    return results


def as_context(results: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] {r['title']} ({r['url']})\n{r['content']}" for i, r in enumerate(results, start=1)
    )
