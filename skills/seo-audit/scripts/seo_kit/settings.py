# -*- coding: utf-8 -*-
"""
Конфигурация SEO-аудита: читается из JSON-файла в рабочей папке проекта.

Файл создаётся командой `seo.py init` и правится командами
`seo.py config ...` — руками его редактировать не обязательно.
"""
import json
import os
import urllib.parse as up

CONFIG_FILENAME = "seo-audit.config.json"

# Дефолтная конфигурация (кейс datastream.by — замени через `seo.py config set-site`)
DEFAULTS = {
    "my_site": {"name": "Datastream", "url": "https://datastream.by/"},
    "competitors": [
        {"name": "Telestream", "url": "https://www.telestream.by/"},
        {"name": "Netland", "url": "https://netland.by/"},
        {"name": "Techlink", "url": "https://techlink.by/"},
    ],
    # Максимум страниц на один сайт за один запуск.
    # Жёсткого потолка нет: краулер работает потоково, память не растёт
    # с числом страниц. Ограничение здесь — только время обхода
    # (примерно 1.5 сек на страницу при задержке 0.6 сек).
    "max_pages_per_site": 600,
    # Порядок обхода при ограниченном лимите страниц:
    #   "structure" — сверху вниз по структуре сайта (главная -> разделы ->
    #                 подразделы), разделы чередуются. Даёт представительную
    #                 выборку: верхнеуровневые страницы не теряются.
    #   "sitemap"   — в порядке sitemap.xml (порядок генерации CMS).
    # При обходе без лимита на результат не влияет.
    "crawl_order": "structure",
    # Как часто сохранять чекпоинт (страниц). 0 — не сохранять.
    # Нужен для длинных прогонов: обрыв на 8000-й странице не должен
    # стоить всего обхода.
    "checkpoint_every": 500,
    # Максимальная глубина обхода от главной страницы (по кликам)
    "max_depth": 6,
    # Задержка между запросами к одному сайту (секунды).
    # Не ставь меньше 0.3-0.5 — можно словить бан по IP на чужом сайте.
    "request_delay_seconds": 0.6,
    # Таймаут одного HTTP-запроса (секунды)
    "request_timeout": 15,
    # Повторные попытки при сетевой ошибке
    "retries": 2,
    "user_agent": "Mozilla/5.0 (compatible; SEOCompetitorBot/1.0; +local-analysis-tool)",
    # Уважать ли robots.txt (рекомендуется не выключать)
    "respect_robots_txt": True,
    # Порог "тонкого" контента, слов
    "thin_content_word_threshold": 150,
    # Query-параметры, которые сохраняются при нормализации URL (пагинация Bitrix и т.п.)
    "keep_query_params": ["PAGEN", "PAGE"],
    # Куда складывать результаты (относительно рабочей папки)
    "data_dir": "seo-audit/data",
    "reports_dir": "seo-audit/reports",
}


def config_path(workdir="."):
    return os.path.join(workdir, CONFIG_FILENAME)


def load(workdir="."):
    """Читает конфиг из рабочей папки; отсутствующие ключи добираются из DEFAULTS."""
    path = config_path(workdir)
    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        cfg.update(user_cfg)
    return cfg


def save(cfg, workdir="."):
    path = config_path(workdir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def exists(workdir="."):
    return os.path.exists(config_path(workdir))


def name_from_url(url):
    """Techlink из https://techlink.by/ — имя по умолчанию, если не задали."""
    netloc = up.urlparse(url).netloc or url
    host = netloc.replace("www.", "").split(":")[0]
    base = host.split(".")[0] if "." in host else host
    return base.capitalize() or host


def normalize_site_url(url):
    """Приводит ввод пользователя к полному URL: techlink.by -> https://techlink.by/"""
    url = url.strip()
    if not url:
        raise ValueError("Пустой URL")
    if "://" not in url:
        url = "https://" + url
    parsed = up.urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"Не похоже на адрес сайта: {url!r}")
    # Гарантируем закрывающий слэш у корня
    if not parsed.path:
        parsed = parsed._replace(path="/")
    return up.urlunparse(parsed)
