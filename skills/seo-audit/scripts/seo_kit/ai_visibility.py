# -*- coding: utf-8 -*-
"""
Проверки AI-видимости (GEO) на уровне сайта: политика robots.txt по
AI-краулерам и наличие llms.txt.

Ключевое различие (его путают почти все гайды): боты обучения и боты
AI-поиска — разные user-agent'ы. Блокировка GPTBot (обучение) не блокирует
OAI-SearchBot (поиск ChatGPT); ClaudeBot (обучение) != Claude-SearchBot (поиск).
Для видимости в ответах ассистентов критичны именно search/retrieval-боты.
"""
import urllib.parse as up
from urllib import robotparser

import requests

# (user-agent, категория)
# search   — retrieval/citation-боты AI-поиска: их блокировка убирает сайт
#            из ответов ассистентов (критично)
# fetch    — загрузка страницы по запросу пользователя в чате
# training — сбор данных для обучения моделей: блокировка НЕ вредит
#            видимости в AI-поиске (осознанный выбор владельца)
AI_BOTS = [
    ("OAI-SearchBot", "search"),      # поиск ChatGPT
    ("Claude-SearchBot", "search"),   # поиск Claude
    ("PerplexityBot", "search"),      # Perplexity
    ("Bingbot", "search"),            # Bing -> Copilot
    ("Googlebot", "search"),          # Google -> AI Overviews
    ("YandexBot", "search"),          # Яндекс -> Нейро/Алиса
    ("ChatGPT-User", "fetch"),
    ("Claude-User", "fetch"),
    ("Perplexity-User", "fetch"),
    ("GPTBot", "training"),
    ("ClaudeBot", "training"),
    ("Google-Extended", "training"),  # управляет обучением Gemini, не поиском
    ("Applebot-Extended", "training"),
    ("CCBot", "training"),
    ("meta-externalagent", "training"),
]


def check_site(base_url, session=None, timeout=15):
    """
    Возвращает словарь AI-метрик сайта:
    {
      "search_bots_total", "search_bots_allowed", "search_bots_blocked": [...],
      "training_bots_blocked": [...],
      "has_llms_txt", "has_llms_full_txt",
    }
    При недоступном robots.txt боты считаются разрешёнными (как и по стандарту).
    """
    session = session or requests.Session()

    # --- robots.txt ---
    rp = None
    robots_url = up.urljoin(base_url, "/robots.txt")
    try:
        resp = session.get(robots_url, timeout=timeout)
        if resp.status_code == 200:
            rp = robotparser.RobotFileParser()
            rp.parse(resp.text.splitlines())
    except requests.RequestException:
        rp = None

    def bot_allowed(ua):
        if rp is None:
            return True
        try:
            return rp.can_fetch(ua, base_url)
        except Exception:
            return True

    search_blocked = []
    training_blocked = []
    search_total = 0
    for ua, category in AI_BOTS:
        allowed = bot_allowed(ua)
        if category == "search":
            search_total += 1
            if not allowed:
                search_blocked.append(ua)
        elif category == "training" and not allowed:
            training_blocked.append(ua)

    # --- llms.txt / llms-full.txt ---
    def _has_llms(path):
        try:
            r = session.get(up.urljoin(base_url, path), timeout=timeout)
            ct = r.headers.get("Content-Type", "")
            # HTML в ответе — почти наверняка красивая страница 404
            return r.status_code == 200 and "text/html" not in ct
        except requests.RequestException:
            return False

    return {
        "search_bots_total": search_total,
        "search_bots_allowed": search_total - len(search_blocked),
        "search_bots_blocked": search_blocked,
        "training_bots_blocked": training_blocked,
        "has_llms_txt": _has_llms("/llms.txt"),
        "has_llms_full_txt": _has_llms("/llms-full.txt"),
    }
