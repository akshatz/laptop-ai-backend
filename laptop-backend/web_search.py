"""Web search for chat answers, used only when the question needs it.

For each question a short prompt asks a model whether answering needs current information from
the web (WEB) or not (NO); only WEB searches SearXNG (same instance Open WebUI uses). The result
snippets (title, URL, text) become numbered sources in the answer's context; for the top
WEB_SEARCH_FETCH_PAGES results the page itself is downloaded and its text used instead. Any failure (model
or SearXNG down, timeout) means answering without the web rather than failing the chat.
WEB_SEARCH=auto (default) decides per question, `always` skips the decision, `off` disables it."""

import asyncio
import os
import re
from datetime import date
from html.parser import HTMLParser

import httpx

from chat_service import DEFAULT_CHAT_MODEL, OLLAMA_BASE_URL

WEB_SEARCH = os.environ.get("WEB_SEARCH", "auto").lower()
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng:8080/search")
DECISION_MODEL = os.environ.get("WEB_SEARCH_DECISION_MODEL", DEFAULT_CHAT_MODEL)
MAX_RESULTS = int(os.environ.get("WEB_SEARCH_RESULTS", "5"))
# The top results' pages are downloaded and their text used instead of SearXNG's one-line
# snippets (a snippet never holds a whole recipe or table). 0 = snippets only.
FETCH_PAGES = int(os.environ.get("WEB_SEARCH_FETCH_PAGES", "3"))
PAGE_CHARS = int(os.environ.get("WEB_SEARCH_PAGE_CHARS", "3000"))
PAGE_TIMEOUT = 8

DECISION_PROMPT = """Today is {today}. Decide whether answering the question below needs a web search.

Answer WEB if it asks about current or recent events, news, prices, rates, scores, weather,
schedules, releases, or facts that may have changed since your training, or about specific
people, places, products or companies you may not know well.
Answer NO for general knowledge, explanations, definitions, maths, coding, writing (letters,
poems, stories), translation, recipes and cooking, ideas and suggestions, greetings, or questions
about the user's own documents or the conversation.

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
    return await with_pages(results)


class _TextExtractor(HTMLParser):
    """Visible text of a page: skips scripts, styles, navigation, headers, footers and forms."""
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "form", "svg", "aside", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def page_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return ""
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    # Very short lines are mostly menus and buttons.
    return "\n".join(line for line in lines if len(line) > 25)


async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    try:
        response = await client.get(url)
        if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
            return ""
        return page_text(response.text)[:PAGE_CHARS]
    except (httpx.HTTPError, UnicodeDecodeError):
        return ""


async def with_pages(results: list[dict]) -> list[dict]:
    """The first FETCH_PAGES results with `content` replaced by their page text (when longer than
    the snippet); a page that fails or is empty keeps its snippet."""
    if not FETCH_PAGES or not results:
        return results
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) laptop-backend/1.0", "Accept-Language": "en-IN,en;q=0.9"}
    async with httpx.AsyncClient(timeout=PAGE_TIMEOUT, follow_redirects=True, headers=headers) as client:
        texts = await asyncio.gather(*(_fetch_page(client, r["url"]) for r in results[:FETCH_PAGES]))
    return [
        {**r, "content": texts[i]} if i < len(texts) and len(texts[i]) > len(r["content"]) else r
        for i, r in enumerate(results)
    ]


def as_context(results: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] {r['title']} ({r['url']})\n{r['content']}" for i, r in enumerate(results, start=1)
    )
