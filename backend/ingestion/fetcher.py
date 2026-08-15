"""URL fetching + HTML cleaning for product ingestion."""
from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


async def fetch_url(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Return (title, cleaned_text). Raises httpx errors on failure."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                 headers={"User-Agent": UA, "Accept-Language": "en"}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return clean_html(resp.text)


def clean_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header",
                     "form", "button"]):
        tag.decompose()
    # prefer main/article containers when present
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) > 2]
    # drop consecutive duplicates (menus etc.)
    out: list[str] = []
    for ln in lines:
        if not out or out[-1] != ln:
            out.append(ln)
    return title, "\n".join(out)[:16000]
