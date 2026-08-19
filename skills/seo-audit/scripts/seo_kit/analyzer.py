# -*- coding: utf-8 -*-
"""
Извлекает SEO-метрики из одной HTML-страницы.
"""
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup


def _clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def analyze_page(page, thin_content_word_threshold=150):
    """
    page: {"url", "final_url", "status_code", "html", "depth", "response_time_ms"}
    Возвращает словарь SEO-метрик по странице.
    """
    html = page.get("html", "") or ""
    soup = BeautifulSoup(html, "html.parser")

    # Title
    title_tag = soup.find("title")
    title = _clean_text(title_tag.text) if title_tag else ""

    # Meta description
    meta_desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    meta_description = _clean_text(meta_desc_tag.get("content", "")) if meta_desc_tag else ""

    # Meta robots
    meta_robots_tag = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    meta_robots = _clean_text(meta_robots_tag.get("content", "")) if meta_robots_tag else ""

    # Canonical
    canonical_tag = soup.find("link", attrs={"rel": re.compile("canonical", re.I)})
    canonical = canonical_tag.get("href", "").strip() if canonical_tag else ""

    # H1
    h1_tags = soup.find_all("h1")
    h1_texts = [_clean_text(h.get_text()) for h in h1_tags]

    # H2 (для представления о структуре)
    h2_count = len(soup.find_all("h2"))

    # Изображения без alt
    imgs = soup.find_all("img")
    imgs_missing_alt = sum(1 for img in imgs if not img.get("alt", "").strip())

    # Ссылки
    links = soup.find_all("a", href=True)
    internal_links = 0
    external_links = 0
    page_domain = urlparse(page.get("final_url") or page.get("url") or "").netloc.replace("www.", "")
    for a in links:
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        try:
            link_domain = urlparse(href).netloc
        except Exception:
            link_domain = ""
        if not link_domain:
            internal_links += 1
        elif link_domain.replace("www.", "") == page_domain:
            internal_links += 1
        else:
            external_links += 1

    # Текст страницы (видимый) и количество слов
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    visible_text = _clean_text(soup.get_text(separator=" "))
    word_count = len(visible_text.split()) if visible_text else 0

    # Structured data (schema.org) — просто факт наличия
    has_schema_org = bool(
        soup.find("script", attrs={"type": "application/ld+json"})
        or soup.find(attrs={"itemtype": re.compile("schema.org", re.I)})
    )

    # Open Graph
    has_og_tags = bool(soup.find("meta", attrs={"property": re.compile("^og:", re.I)}))

    return {
        "url": page.get("url"),
        "final_url": page.get("final_url"),
        "status_code": page.get("status_code"),
        "depth": page.get("depth"),
        "response_time_ms": page.get("response_time_ms"),

        "title": title,
        "title_length": len(title),
        "has_title": bool(title),

        "meta_description": meta_description,
        "meta_description_length": len(meta_description),
        "has_meta_description": bool(meta_description),

        "meta_robots": meta_robots,
        "is_noindex": "noindex" in meta_robots.lower(),

        "canonical": canonical,
        "has_canonical": bool(canonical),
        "canonical_mismatch": bool(canonical) and canonical.rstrip("/") != (page.get("final_url") or "").rstrip("/"),

        "h1_count": len(h1_texts),
        "h1_text": h1_texts[0] if h1_texts else "",
        "has_h1": len(h1_texts) > 0,
        "multiple_h1": len(h1_texts) > 1,
        "h2_count": h2_count,

        "images_total": len(imgs),
        "images_missing_alt": imgs_missing_alt,

        "internal_links": internal_links,
        "external_links": external_links,

        "word_count": word_count,
        "is_thin_content": word_count < thin_content_word_threshold,

        "has_schema_org": has_schema_org,
        "has_og_tags": has_og_tags,
    }


def analyze_pages(pages, thin_content_word_threshold=150):
    return [analyze_page(p, thin_content_word_threshold) for p in pages]
