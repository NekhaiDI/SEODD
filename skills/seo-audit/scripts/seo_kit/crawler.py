# -*- coding: utf-8 -*-
"""
Краулер сайта: обходит внутренние страницы (сначала пробует sitemap.xml,
иначе идёт по внутренним ссылкам в ширину), собирает сырой HTML для
последующего SEO-анализа.
"""
import time
import urllib.parse as up
from collections import deque
from urllib import robotparser

import requests
from bs4 import BeautifulSoup


def _get_robots_parser(base_url, session, timeout):
    rp = robotparser.RobotFileParser()
    robots_url = up.urljoin(base_url, "/robots.txt")
    try:
        resp = session.get(robots_url, timeout=timeout)
        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        else:
            rp = None
    except requests.RequestException:
        rp = None
    return rp


def _try_sitemap_urls(base_url, session, timeout):
    """Пытается найти список URL через sitemap.xml (и вложенные sitemap-индексы)."""
    candidates = [
        up.urljoin(base_url, "/sitemap.xml"),
        up.urljoin(base_url, "/sitemap_index.xml"),
    ]
    found = []
    seen_sitemaps = set()
    queue = deque(candidates)

    while queue:
        sm_url = queue.popleft()
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)
        try:
            resp = session.get(sm_url, timeout=timeout)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.content, "xml")
            # Вложенный индекс sitemap-ов
            for loc in soup.select("sitemap > loc"):
                sm = loc.text.strip()
                if sm and sm not in seen_sitemaps:
                    queue.append(sm)
            # Обычные URL страниц
            for loc in soup.select("url > loc"):
                text = loc.text.strip()
                if text:
                    found.append(text)
        except requests.RequestException:
            continue
        except Exception:
            continue

    page_urls = [u for u in found if not u.lower().endswith(".xml")]
    return list(dict.fromkeys(page_urls))  # уникализируем, сохраняя порядок


def _same_domain(url, domain):
    try:
        return up.urlparse(url).netloc.replace("www.", "") == domain.replace("www.", "")
    except Exception:
        return False


def _normalize_url(url, keep_query_params):
    """Убирает фрагмент (#...) и большинство query-параметров сортировки/фильтров,
    чтобы не плодить дубликаты страниц каталога; параметры пагинации сохраняются."""
    parsed = up.urlparse(url)
    parsed = parsed._replace(fragment="")
    query = up.parse_qs(parsed.query)
    keep = {}
    for k, v in query.items():
        for pattern in keep_query_params:
            if k.upper().startswith(pattern.upper()):
                keep[k] = v
                break
    new_query = up.urlencode(keep, doseq=True)
    parsed = parsed._replace(query=new_query)
    return up.urlunparse(parsed)


def crawl_site(name, base_url, cfg, max_pages=None, verbose=True):
    """
    Обходит сайт и возвращает список словарей:
    {"url", "final_url", "status_code", "html", "depth", "response_time_ms"}
    """
    max_pages = max_pages or cfg["max_pages_per_site"]
    max_depth = cfg["max_depth"]
    delay = cfg["request_delay_seconds"]
    timeout = cfg["request_timeout"]
    retries = cfg["retries"]
    keep_query_params = cfg.get("keep_query_params", ["PAGEN", "PAGE"])

    session = requests.Session()
    session.headers.update({"User-Agent": cfg["user_agent"]})

    domain = up.urlparse(base_url).netloc

    rp = _get_robots_parser(base_url, session, timeout) if cfg["respect_robots_txt"] else None

    def allowed(url):
        if rp is None:
            return True
        try:
            return rp.can_fetch(cfg["user_agent"], url)
        except Exception:
            return True

    visited = set()
    results = []

    # 1. Пытаемся стартовать со списка URL из sitemap.xml
    if verbose:
        print(f"[{name}] Ищу sitemap.xml ...")
    try:
        sitemap_urls = _try_sitemap_urls(base_url, session, timeout)
    except Exception:
        sitemap_urls = []

    queue = deque()
    if sitemap_urls:
        if verbose:
            print(f"[{name}] Найдено {len(sitemap_urls)} URL в sitemap. Использую как основу обхода.")
        for u in sitemap_urls[:max_pages * 3]:
            if _same_domain(u, domain):
                queue.append((_normalize_url(u, keep_query_params), 0))
    else:
        if verbose:
            print(f"[{name}] sitemap.xml не найден/пуст, обхожу по ссылкам с главной страницы.")
        queue.append((_normalize_url(base_url, keep_query_params), 0))

    while queue and len(results) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        if not allowed(url):
            continue

        attempt = 0
        resp = None
        start = time.time()
        while attempt <= retries:
            try:
                resp = session.get(url, timeout=timeout, allow_redirects=True)
                break
            except requests.RequestException:
                attempt += 1
                time.sleep(0.5)
        elapsed = time.time() - start

        if resp is None:
            continue

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            continue

        results.append({
            "url": url,
            "final_url": resp.url,
            "status_code": resp.status_code,
            "html": resp.text,
            "depth": depth,
            "response_time_ms": round(elapsed * 1000, 1),
        })

        if verbose and len(results) % 25 == 0:
            print(f"[{name}] Обработано страниц: {len(results)}")

        # Собираем ссылки дальше, только если не превысили глубину
        if resp.status_code == 200 and depth < max_depth:
            try:
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                        continue
                    abs_url = up.urljoin(resp.url, href)
                    abs_url = _normalize_url(abs_url, keep_query_params)
                    if _same_domain(abs_url, domain) and abs_url not in visited:
                        queue.append((abs_url, depth + 1))
            except Exception:
                pass

        time.sleep(delay)

    if verbose:
        print(f"[{name}] Готово. Всего страниц собрано: {len(results)}")

    return results
