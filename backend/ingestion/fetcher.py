"""URL fetching + HTML cleaning for product ingestion.

JS-rendered e-commerce pages (91APP, Shopify, etc.) often carry the real
product data in JSON-LD blocks and og:/meta tags rather than in server-side
body text — so we extract those FIRST and prepend them to the cleaned text.
This also mirrors what AI search crawlers can actually see.
"""
from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
UA_CRAWLER = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

META_KEYS = ("og:title", "og:description", "og:type", "description", "keywords",
             "price", "product:", "twitter:title", "twitter:description")

MIN_USEFUL_CHARS = 300

# Link-shim wrappers (Messenger/FB copy-paste links etc.) redirect server-side
# fetches to an interstitial warning page, not the target — unwrap them first.
WRAPPER_HOSTS = {"l.messenger.com", "l.facebook.com", "lm.facebook.com", "m.facebook.com",
                 "l.instagram.com", "l.threads.net", "out.reddit.com", "away.vk.com",
                 "slack-redir.net", "t.umblr.com", "href.li"}
TRACKING_PARAMS = ("fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref_", "spm")


def unwrap_url(url: str) -> str:
    """Resolve known redirector/wrapper URLs to their real target + strip tracking params."""
    for _ in range(3):
        p = urlparse(url)
        host = p.netloc.lower()
        qs = parse_qs(p.query)
        candidate = None
        if host in ("www.google.com", "google.com") and p.path == "/url":
            candidate = (qs.get("q") or qs.get("url") or [None])[0]
        elif host in WRAPPER_HOSTS or host.startswith("l.") or p.path == "/l.php" \
                or p.path.startswith("/flx/warn"):
            for key in ("u", "url", "target", "dest", "to", "q"):
                vals = qs.get(key) or []
                if vals and vals[0].startswith(("http://", "https://")):
                    candidate = vals[0]
                    break
        if not candidate or candidate == url:
            break
        url = candidate
    # strip tracking params
    p = urlparse(url)
    if p.query:
        kept = [(k, v) for k, v in parse_qs(p.query, keep_blank_values=True).items()
                if not (k.lower().startswith("utm_") or k.lower() in TRACKING_PARAMS)]
        flat = [(k, x) for k, vs in kept for x in vs]
        url = urlunparse(p._replace(query=urlencode(flat)))
    return url


async def fetch_page(url: str, timeout: float = 20.0) -> dict:
    """Return {"title", "text", "images": [urls]}.

    Tries a browser UA first, then a crawler UA. Returns the best (longest)
    text even if thin — the ingestion service decides whether it's extractable.
    Also collects product image URLs (page tags + raw-HTML scan + site
    adapters) for the vision-extraction fallback.
    Raises the last httpx error only if every attempt failed at HTTP level.
    """
    url = unwrap_url(url)
    best: tuple[str, str, str] = ("", "", "")  # title, text, html
    last_exc: Exception | None = None
    for ua in (UA, UA_CRAWLER):
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout,
                                         headers={"User-Agent": ua,
                                                  "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            title, text = clean_html(resp.text)
            if len(text) > len(best[1]):
                best = (title, text, resp.text)
            if len(text) >= MIN_USEFUL_CHARS:
                break
        except httpx.HTTPError as e:
            last_exc = e
            continue
    if not best[1] and last_exc is not None:
        raise last_exc
    title, text, html = best
    images = collect_image_urls(html, url)
    extra_imgs, extra_text = await _site_adapter(url, timeout)
    for u in extra_imgs:
        if u not in images:
            images.append(u)
    if extra_text:
        text = (text + "\n\nSite data:\n" + extra_text)[:16000]
    return {"title": title, "text": text, "images": images[:12]}


async def fetch_url(url: str, timeout: float = 20.0) -> tuple[str, str]:
    """Back-compat wrapper: (title, cleaned_text)."""
    page = await fetch_page(url, timeout)
    return page["title"], page["text"]


IMG_EXT_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.(?:jpg|jpeg|png|webp)", re.I)
SKIP_IMG_HINTS = ("icon", "logo", "sprite", "avatar", "button", "1x1", "pixel")


def collect_image_urls(html: str, base_url: str) -> list[str]:
    """Product-image candidates: og/twitter meta, <img> tags, raw-HTML scan."""
    from urllib.parse import urljoin
    out: list[str] = []

    def add(u: str | None) -> None:
        if not u:
            return
        u = urljoin(base_url, u.strip())
        low = u.lower()
        if not low.startswith("http"):
            return
        if any(h in low for h in SKIP_IMG_HINTS):
            return
        if u not in out:
            out.append(u)

    try:
        soup = BeautifulSoup(html, "html.parser")
        for m in soup.find_all("meta"):
            key = (m.get("property") or m.get("name") or "").lower()
            if key in ("og:image", "og:image:secure_url", "twitter:image"):
                add(m.get("content"))
        for img in soup.find_all("img"):
            for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                add(img.get(attr))
    except Exception:
        pass
    for u in IMG_EXT_RE.findall(html or "")[:40]:
        add(u)
    return out


async def _site_adapter(url: str, timeout: float) -> tuple[list[str], str]:
    """Site-specific enrichment for JS-heavy shops. Currently: PChome 24h."""
    m = re.search(r"24h\.pchome\.com\.tw/prod/([A-Z0-9-]+)", url, re.I)
    if not m:
        return [], ""
    pid = m.group(1)
    imgs: list[str] = []
    extra: list[str] = []
    headers = {"User-Agent": UA, "Referer": "https://24h.pchome.com.tw/"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        try:
            r = await client.get(
                f"https://ecapi-cdn.pchome.com.tw/ecshop/prodapi/v2/prod?id={pid}&fields=Id,Name,Pic,Price")
            for item in (r.json() or {}).values():
                pic = (item or {}).get("Pic") or {}
                for k in ("B", "S"):
                    if pic.get(k):
                        imgs.append("https://cs-a.ecimg.tw" + pic[k])
                if item.get("Name"):
                    extra.append(str(item["Name"]))
        except Exception:
            pass
        try:  # JSONP-style desc endpoint carries slogan/statement text
            r = await client.get(
                f"https://ecapi-cdn.pchome.com.tw/ecshop/prodapi/v2/prod/{pid}/desc&fields=Meta,Kword,Stmt,Slogan&_callback=x")
            mjson = re.search(r"x\((\{.*\})\);", r.text, re.S)
            if mjson:
                data = json.loads(mjson.group(1))
                for item in data.values():
                    for k in ("Slogan", "Stmt", "Kword"):
                        v = (item or {}).get(k)
                        if v and str(v).strip():
                            extra.append(str(v).strip())
        except Exception:
            pass
    return imgs, "\n".join(extra)


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
