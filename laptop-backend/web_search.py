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
    """Top results as {title, url, content, published}; empty on any error. The freshest news
    first (last day, then last month), general web results when there is no news; `published` is
    the day a source came out (YYYY-MM-DD) or None."""
    query = await search_query(question)
    raw = []
    for params in ({"categories": "news", "time_range": "day"}, {"categories": "news", "time_range": "month"}, {}):
        raw = await _searxng({"q": query, **params})
        if raw:
            break
    results = []
    for r in raw:
        if r.get("url") and (r.get("content") or r.get("title")):
            results.append({"title": r.get("title", ""), "url": r["url"], "content": r.get("content", ""),
                            "published": _day(r.get("publishedDate"))})
        if len(results) >= MAX_RESULTS:
            break
    return await with_pages(results)


class _TextExtractor(HTMLParser):
    """Visible text of a page: skips scripts, styles, navigation, headers, footers and forms."""
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "form", "svg", "aside", "template"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "table"}

    # Where pages put their publish date (Open Graph, schema.org, common meta names).
    DATE_META = {"article:published_time", "og:published_time", "datepublished", "date", "pubdate",
                 "publish-date", "dc.date", "dc.date.issued"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = 0
        self.published: str | None = None

    def handle_starttag(self, tag, attrs):
        if self.published is None and tag in ("meta", "time"):
            a = dict(attrs)
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            value = a.get("content") if tag == "meta" else a.get("datetime")
            if value and (tag == "time" and a.get("itemprop", "").lower() == "datepublished" or key in self.DATE_META):
                self.published = value
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


def parse_page(html: str) -> tuple[str, str | None]:
    """(visible text, publish date YYYY-MM-DD or None) of a page."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        return "", None
    # JSON-LD ("datePublished": "...") sits in a <script>, which the text skips.
    published = parser.published
    if not published:
        found = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
        published = found.group(1) if found else None
    return _page_text_from(parser), _day(published)


def _day(value: str | None) -> str | None:
    """YYYY-MM-DD from an ISO-ish date (SearXNG's publishedDate, meta tags), else None."""
    found = re.match(r"\s*(\d{4}-\d{2}-\d{2})", value or "")
    return found.group(1) if found else None


def page_text(html: str) -> str:
    return parse_page(html)[0]


def _page_text_from(parser: "_TextExtractor") -> str:
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in "".join(parser.parts).splitlines())
    # Very short lines are mostly menus and buttons.
    return "\n".join(line for line in lines if len(line) > 25)


async def _fetch_page(client: httpx.AsyncClient, url: str) -> tuple[str, str | None]:
    """(page text, publish date) or ("", None)."""
    try:
        response = await client.get(url)
        if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
            return "", None
        text, published = parse_page(response.text)
        return text[:PAGE_CHARS], published
    except (httpx.HTTPError, UnicodeDecodeError):
        return "", None


async def with_pages(results: list[dict]) -> list[dict]:
    """The first FETCH_PAGES results with `content` replaced by their page text (when longer than
    the snippet) and `published` filled from the page when SearXNG had no date; a page that fails
    or is empty keeps its snippet."""
    if not FETCH_PAGES or not results:
        return results
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) laptop-backend/1.0", "Accept-Language": "en-IN,en;q=0.9"}
    async with httpx.AsyncClient(timeout=PAGE_TIMEOUT, follow_redirects=True, headers=headers) as client:
        pages = await asyncio.gather(*(_fetch_page(client, r["url"]) for r in results[:FETCH_PAGES]))
    out = []
    for i, r in enumerate(results):
        if i < len(pages):
            text, published = pages[i]
            r = {**r, "published": r.get("published") or published}
            if len(text) > len(r["content"]):
                r["content"] = text
        out.append(r)
    return out


def as_context(results: list[dict]) -> str:
    """Numbered sources, each with its publish date so the model can tell old figures from today's."""
    return "\n\n".join(
        f"[{i}] {r['title']} ({r['url']}) — "
        + (f"published {r['published']}" if r.get("published") else "publish date unknown")
        + f"\n{r['content']}"
        for i, r in enumerate(results, start=1)
    )
