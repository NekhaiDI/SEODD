# -*- coding: utf-8 -*-
"""
Краулер сайта: обходит внутренние страницы (сначала пробует sitemap.xml,
иначе идёт по внутренним ссылкам в ширину), отдаёт сырой HTML для
последующего SEO-анализа.

Работает потоково (генератор): вызывающий код анализирует страницу сразу
и выбрасывает HTML, поэтому память не растёт с числом страниц.
"""
import time
import urllib.parse as up
from collections import deque, OrderedDict
from datetime import datetime, timezone
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


def _parse_lastmod(text):
    """'2025-06-25T09:11:41+03:00' -> datetime (tz-aware) или None."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        # fromisoformat разбирает и дату без зоны ('2025-06-25' — самый
        # частый формат lastmod). Naive-датавремя нельзя сравнивать с aware:
        # max()/вычитание упадут, а except выше по стеку молча выбросит
        # весь sitemap. Поэтому всегда приводим к aware.
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:len(fmt) + 2], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _try_sitemap_urls(base_url, session, timeout):
    """
    Ищет sitemap.xml (включая вложенные индексы).

    Возвращает (page_urls, sitemap_info), где sitemap_info описывает саму
    карту: сколько URL, когда обновлялась, из каких под-карт состоит.
    Свежесть карты — самостоятельная SEO-метрика: протухший sitemap
    скармливает поисковикам мёртвые URL.
    """
    candidates = [
        up.urljoin(base_url, "/sitemap.xml"),
        up.urljoin(base_url, "/sitemap_index.xml"),
    ]
    found = []
    lastmods = []
    sub_sitemaps = []
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
            for node in soup.select("sitemap"):
                loc = node.find("loc")
                if not loc:
                    continue
                sm = loc.text.strip()
                lm = node.find("lastmod")
                lm_dt = _parse_lastmod(lm.text if lm else None)
                if lm_dt:
                    lastmods.append(lm_dt)
                sub_sitemaps.append({
                    "url": sm,
                    "lastmod": lm.text.strip() if lm else None,
                })
                if sm and sm not in seen_sitemaps:
                    queue.append(sm)
            # Обычные URL страниц
            for node in soup.select("url"):
                loc = node.find("loc")
                if not loc:
                    continue
                text = loc.text.strip()
                if not text:
                    continue
                found.append(text)
                lm = node.find("lastmod")
                lm_dt = _parse_lastmod(lm.text if lm else None)
                if lm_dt:
                    lastmods.append(lm_dt)
        except requests.RequestException:
            continue
        except Exception:
            continue

    page_urls = [u for u in found if not u.lower().endswith(".xml")]
    page_urls = list(dict.fromkeys(page_urls))  # уникализируем, сохраняя порядок

    info = {
        "found": bool(page_urls),
        "urls_total": len(page_urls),
        "sub_sitemaps": len(sub_sitemaps),
        "lastmod_latest": None,
        "lastmod_oldest": None,
        "age_days": None,
    }
    if lastmods:
        latest, oldest = max(lastmods), min(lastmods)
        info["lastmod_latest"] = latest.isoformat()
        info["lastmod_oldest"] = oldest.isoformat()
        info["age_days"] = (datetime.now(timezone.utc) - latest).days
    return page_urls, info


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


def _path_parts(url):
    """('catalog', 3) — верхнеуровневый раздел и глубина пути URL."""
    try:
        segs = [s for s in up.urlparse(url).path.split("/") if s]
    except Exception:
        segs = []
    return (segs[0] if segs else ""), len(segs)


def order_by_structure(urls):
    """
    Переупорядочивает список URL «сверху вниз по структуре сайта»:
    сначала главная, затем все разделы первого уровня, затем второго и т.д.
    Внутри одного уровня разделы чередуются по кругу, чтобы бюджет обхода
    не съел целиком один большой каталог.

    Зачем: порядок URL в sitemap — это порядок генерации CMS, а не значимость.
    При ограниченном лимите страниц обход «как в карте» даёт перекошенную
    выборку (много глубоких карточек, мало страниц верхнего уровня).
    При обходе без лимита порядок на результат не влияет — только на то,
    насколько осмысленно выглядит незавершённый прогон.
    """
    buckets = OrderedDict()
    for u in urls:
        section, depth = _path_parts(u)
        buckets.setdefault(depth, OrderedDict()).setdefault(section, []).append(u)

    ordered = []
    for depth in sorted(buckets):
        iters = [iter(group) for group in buckets[depth].values()]
        while iters:
            for it in list(iters):
                try:
                    ordered.append(next(it))
                except StopIteration:
                    iters.remove(it)
    return ordered


def crawl_site(name, base_url, cfg, max_pages=None, verbose=True,
               state=None, resume_visited=None, resume_queue=None,
               resume_sitemap_info=None):
    """
    Генератор: обходит сайт и отдаёт по одной странице за раз —
    {"url", "final_url", "status_code", "html", "depth", "response_time_ms",
     "found_on", "page_bytes", "x_robots_tag", "is_redirect", "redirect_to",
     "redirect_chain", "redirect_status"}

    Потоковая отдача нужна, чтобы HTML не накапливался в памяти: на крупном
    сайте страницы по 700 КБ дают гигабайты, если собирать их в список.

    state    — необязательный dict; краулер положит в него ссылки на живые
               visited/queue и sitemap_info, чтобы вызывающий код мог
               сохранять чекпоинты и возобновлять обход.
    resume_* — состояние из чекпоинта: обход продолжится с него, sitemap
               заново не читается.
    """
    # 0 — законный бюджет «ничего не обходить» (resume, когда лимит уже
    # выбран); `max_pages or ...` подменял его дефолтом из конфига.
    if max_pages is None:
        max_pages = cfg["max_pages_per_site"]
    max_depth = cfg["max_depth"]
    delay = cfg["request_delay_seconds"]
    timeout = cfg["request_timeout"]
    retries = cfg["retries"]
    keep_query_params = cfg.get("keep_query_params", ["PAGEN", "PAGE"])
    crawl_order = cfg.get("crawl_order", "structure")

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

    visited = set(resume_visited) if resume_visited else set()
    # Отдельно от visited: URL, уже поставленный в очередь, повторно класть
    # нельзя. Иначе очередь растёт как «страниц × ссылок на странице» —
    # на большом сайте это миллионы записей и те же гигабайты памяти,
    # от которых уходили, отказавшись копить HTML.
    queued = set()
    sitemap_info = {"found": False, "urls_total": 0}

    if resume_queue is not None:
        queue = deque(tuple(item) for item in resume_queue)
        queued = {item[0] for item in queue}
        if resume_sitemap_info:
            # sitemap при продолжении заново не читается: не перенести его
            # данные из чекпоинта — значит потерять свежесть карты в meta
            # и в следующих чекпоинтах.
            sitemap_info = resume_sitemap_info
        if verbose:
            print(f"[{name}] Продолжаю с чекпоинта: {len(visited)} страниц уже обойдено, "
                  f"{len(queue)} в очереди.")
    else:
        # 1. Пытаемся стартовать со списка URL из sitemap.xml
        if verbose:
            print(f"[{name}] Ищу sitemap.xml ...")
        try:
            sitemap_urls, sitemap_info = _try_sitemap_urls(base_url, session, timeout)
        except Exception:
            sitemap_urls, sitemap_info = [], {"found": False, "urls_total": 0}

        queue = deque()
        if sitemap_urls:
            same_domain_urls = [u for u in sitemap_urls if _same_domain(u, domain)]
            if crawl_order == "structure":
                same_domain_urls = order_by_structure(same_domain_urls)
                order_note = "по структуре сайта (сверху вниз)"
            else:
                order_note = "в порядке sitemap"
            if verbose:
                age = sitemap_info.get("age_days")
                age_note = f", обновлён {age} дн. назад" if age is not None else ""
                print(f"[{name}] Найдено {len(sitemap_urls)} URL в sitemap{age_note}. "
                      f"Обхожу {order_note}.")
            # Берём с запасом: часть URL отсеется (robots.txt, не-HTML, дубли).
            for u in same_domain_urls[:max_pages * 3]:
                nu = _normalize_url(u, keep_query_params)
                if nu not in queued:
                    queued.add(nu)
                    queue.append((nu, 0, "sitemap"))
        else:
            if verbose:
                print(f"[{name}] sitemap.xml не найден/пуст, обхожу по ссылкам с главной страницы.")
            start_url = _normalize_url(base_url, keep_query_params)
            queued.add(start_url)
            queue.append((start_url, 0, "start"))

    # Отдаём наружу живые объекты состояния — для чекпоинтов
    if state is not None:
        state["visited"] = visited
        state["queue"] = queue
        state["sitemap_info"] = sitemap_info

    produced = 0
    while queue and produced < max_pages:
        url, depth, found_on = queue.popleft()
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

        # Редиректы: сравниваем то, что запрашивали, с тем, куда пришли.
        # Без этого страница за цепочкой 301 отчитывается как обычная 200.
        history = resp.history or []
        is_redirect = bool(history)

        produced += 1
        yield {
            "url": url,
            "final_url": resp.url,
            "status_code": resp.status_code,
            "html": resp.text,
            "depth": depth,
            "response_time_ms": round(elapsed * 1000, 1),
            "found_on": found_on,
            "page_bytes": len(resp.content),
            "x_robots_tag": resp.headers.get("X-Robots-Tag", ""),
            "is_redirect": is_redirect,
            "redirect_to": resp.url if is_redirect else "",
            "redirect_chain": len(history),
            "redirect_status": history[0].status_code if history else None,
        }

        if verbose and produced % 25 == 0:
            print(f"[{name}] Обработано страниц: {produced}")

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
                    if (_same_domain(abs_url, domain)
                            and abs_url not in visited
                            and abs_url not in queued):
                        # found_on = страница-источник: по ней потом видно,
                        # откуда ведёт ссылка на битый URL
                        queued.add(abs_url)
                        queue.append((abs_url, depth + 1, url))
            except Exception:
                pass

        time.sleep(delay)

    if verbose:
        print(f"[{name}] Готово. Всего страниц собрано: {produced}")
