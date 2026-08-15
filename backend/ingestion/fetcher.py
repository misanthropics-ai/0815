"""URL fetching + HTML cleaning for product ingestion.

JS-rendered e-commerce pages (91APP, Shopify, etc.) often carry the real
product data in JSON-LD blocks and og:/meta tags rather than in server-side
body text — so we extract those FIRST and prepend them to the cleaned text.
This also mirrors what AI search crawlers can actually see.
"""
from __future__ import annotations

import json
import re

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

META_KEYS = ("og:title", "og:description", "og:type", "description", "keywords",
             "price", "product:", "twitter:title", "twitter:description")


async def fetch_url(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Return (title, cleaned_text). Raises httpx errors on failure."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                 headers={"User-Agent": UA,
                                          "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return clean_html(resp.text)


def _jsonld_blocks(soup: BeautifulSoup) -> list[str]:
    out = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (tag.string or tag.get_text() or "").strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
            out.append(json.dumps(data, ensure_ascii=False)[:3500])
        except Exception:
            out.append(raw[:2500])
    return out[:3]


def _meta_lines(soup: BeautifulSoup) -> list[str]:
    lines, seen = [], set()
    for m in soup.find_all("meta"):
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").lower()
        content = (m.get("content") or "").strip()
        if not key or not content:
            continue
        if any(k in key for k in META_KEYS) and key not in seen:
            seen.add(key)
            lines.append(f"{key}: {content[:300]}")
    return lines[:15]


def clean_html(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""

    # harvest structured data BEFORE stripping scripts
    ld_blocks = _jsonld_blocks(soup)
    meta_lines = _meta_lines(soup)

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "header",
                     "form", "button"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text(separator="\n")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if len(ln) > 2]
    out: list[str] = []
    for ln in lines:
        if not out or out[-1] != ln:
            out.append(ln)
    body = "\n".join(out)

    parts = []
    if title:
        parts.append(f"Page title: {title}")
    if meta_lines:
        parts.append("Page metadata:\n" + "\n".join(meta_lines))
    if ld_blocks:
        parts.append("Structured data (JSON-LD):\n" + "\n\n".join(ld_blocks))
    if body:
        parts.append("Page text:\n" + body)
    return title, "\n\n".join(parts)[:16000]
