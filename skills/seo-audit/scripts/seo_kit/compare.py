# -*- coding: utf-8 -*-
"""
Строит агрегированные показатели по сайту и сравнивает несколько сайтов.
"""
from collections import Counter


def summarize_site(name, url, pages_data):
    """
    pages_data: список результатов analyze_page для одного сайта.
    Возвращает словарь агрегированных метрик.
    """
    total = len(pages_data)
    if total == 0:
        return {
            "name": name, "url": url, "pages_crawled": 0,
        }

    def pct(cond_fn):
        n = sum(1 for p in pages_data if cond_fn(p))
        return round(100 * n / total, 1)

    titles = [p["title"] for p in pages_data if p["title"]]
    title_counts = Counter(titles)
    duplicate_titles = sum(c for c in title_counts.values() if c > 1)

    descs = [p["meta_description"] for p in pages_data if p["meta_description"]]
    desc_counts = Counter(descs)
    duplicate_descriptions = sum(c for c in desc_counts.values() if c > 1)

    avg_word_count = round(sum(p["word_count"] for p in pages_data) / total, 1)
    avg_title_length = round(sum(p["title_length"] for p in pages_data) / total, 1)
    avg_meta_desc_length = round(sum(p["meta_description_length"] for p in pages_data) / total, 1)
    avg_response_time = round(
        sum((p["response_time_ms"] or 0) for p in pages_data) / total, 1
    )

    broken = [p for p in pages_data if p["status_code"] and p["status_code"] >= 400]

    return {
        "name": name,
        "url": url,
        "pages_crawled": total,

        "pct_missing_title": pct(lambda p: not p["has_title"]),
        "pct_missing_meta_description": pct(lambda p: not p["has_meta_description"]),
        "pct_missing_h1": pct(lambda p: not p["has_h1"]),
        "pct_multiple_h1": pct(lambda p: p["multiple_h1"]),
        "pct_thin_content": pct(lambda p: p["is_thin_content"]),
        "pct_missing_canonical": pct(lambda p: not p["has_canonical"]),
        "pct_noindex": pct(lambda p: p["is_noindex"]),
        "pct_has_schema_org": pct(lambda p: p["has_schema_org"]),
        "pct_product_schema": pct(lambda p: p.get("has_product_schema")),
        "pct_faq_schema": pct(lambda p: p.get("has_faq_schema")),
        "pct_has_og_tags": pct(lambda p: p["has_og_tags"]),
        "pct_images_missing_alt": round(
            100 * sum(p["images_missing_alt"] for p in pages_data)
            / max(1, sum(p["images_total"] for p in pages_data)), 1
        ),

        "duplicate_titles_count": duplicate_titles,
        "duplicate_descriptions_count": duplicate_descriptions,

        "avg_word_count": avg_word_count,
        "avg_title_length": avg_title_length,
        "avg_meta_description_length": avg_meta_desc_length,
        "avg_response_time_ms": avg_response_time,

        "broken_pages_count": len(broken),
        "broken_pages": [p["url"] for p in broken][:50],
    }


def build_comparison(site_summaries):
    """
    site_summaries: список словарей от summarize_site (первый — свой сайт).
    Возвращает структуру для отчёта: метрики построчно + оценка "лучше/хуже".
    """
    metrics = [
        ("pages_crawled", "Страниц просканировано", "neutral"),
        ("pct_missing_title", "% страниц без title", "lower_better"),
        ("pct_missing_meta_description", "% страниц без meta description", "lower_better"),
        ("pct_missing_h1", "% страниц без H1", "lower_better"),
        ("pct_multiple_h1", "% страниц с несколькими H1", "lower_better"),
        ("pct_thin_content", "% страниц с малым объёмом текста", "lower_better"),
        ("pct_missing_canonical", "% страниц без canonical", "lower_better"),
        ("pct_noindex", "% страниц с noindex", "neutral"),
        ("pct_has_schema_org", "% страниц с микроразметкой schema.org", "higher_better"),
        ("pct_product_schema", "% страниц с Product/Offer-разметкой", "higher_better"),
        ("pct_faq_schema", "% страниц с FAQ-разметкой", "higher_better"),
        ("pct_has_og_tags", "% страниц с Open Graph тегами", "higher_better"),
        ("pct_images_missing_alt", "% изображений без alt", "lower_better"),
        ("duplicate_titles_count", "Дублей title (страниц)", "lower_better"),
        ("duplicate_descriptions_count", "Дублей meta description (страниц)", "lower_better"),
        ("avg_word_count", "Средний объём текста (слов)", "higher_better"),
        ("avg_title_length", "Средняя длина title (симв.)", "neutral"),
        ("avg_meta_description_length", "Средняя длина meta description (симв.)", "neutral"),
        ("avg_response_time_ms", "Среднее время ответа (мс)", "lower_better"),
        ("broken_pages_count", "Битых страниц (4xx/5xx)", "lower_better"),
        ("ai_search_bots", "AI-поисковые боты разрешены (robots.txt)", "neutral"),
        ("ai_llms_txt", "llms.txt на сайте", "neutral"),
    ]

    rows = []
    for key, label, direction in metrics:
        values = [s.get(key) for s in site_summaries]
        rows.append({
            "key": key,
            "label": label,
            "direction": direction,
            "values": values,
        })

    return {
        "sites": [{"name": s["name"], "url": s["url"]} for s in site_summaries],
        "rows": rows,
        "raw": site_summaries,
    }
