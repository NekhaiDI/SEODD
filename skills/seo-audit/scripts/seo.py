# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "requests>=2.31",
#   "beautifulsoup4>=4.12",
#   "lxml>=5.0",
#   "openpyxl>=3.1",
# ]
# ///
"""
SEO-аудит сайта и сравнение с конкурентами — единая точка входа.

Запуск через uv (зависимости ставятся автоматически):
    uv run seo.py <команда>

Или обычным python (зависимости нужно поставить заранее:
pip install requests beautifulsoup4 lxml openpyxl):
    python3 seo.py <команда>

Команды:
    init                                Создать seo-audit.config.json в текущей папке
    config show                         Показать текущую конфигурацию
    config set-site <url> [--name N]    Задать свой сайт
    config add-competitor <url> [--name N]
                                        Добавить конкурента
    config remove-competitor <имя|url>  Убрать конкурента
    config set <ключ> <значение>        Изменить настройку (max_pages_per_site,
                                        request_delay_seconds и т.д.)
    run [--max-pages N]                 Полный аудит: краулинг -> анализ -> отчёты

Все результаты пишутся в текущую папку:
    seo-audit.config.json            — конфигурация
    seo-audit/data/*.json            — сырые метрики по страницам
    seo-audit/reports/report_*.html  — HTML-отчёт
    seo-audit/reports/report_*.xlsx  — Excel-отчёт
    seo-audit/reports/summary_*.json — машиночитаемая сводка (для агента)
"""
import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seo_kit import settings
from seo_kit.crawler import crawl_site
from seo_kit.analyzer import analyze_pages
from seo_kit.compare import summarize_site, build_comparison
from seo_kit.report import generate_html_report, generate_excel_report, collect_problem_pages


def cmd_init(args):
    if settings.exists() and not args.force:
        print(f"Конфиг уже существует: {settings.config_path()}")
        print("Используй `config show` для просмотра или `init --force` для перезаписи.")
        return 0
    path = settings.save(dict(settings.DEFAULTS))
    print(f"Создан конфиг: {os.path.abspath(path)}")
    print("Свой сайт и конкуренты по умолчанию:")
    _print_sites(settings.load())
    return 0


def _print_sites(cfg):
    print(f"  Твой сайт:  {cfg['my_site']['name']} — {cfg['my_site']['url']}")
    if cfg["competitors"]:
        print("  Конкуренты:")
        for c in cfg["competitors"]:
            print(f"    - {c['name']} — {c['url']}")
    else:
        print("  Конкуренты: (нет)")


def cmd_config_show(args):
    cfg = settings.load()
    print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


def cmd_config_set_site(args):
    cfg = settings.load()
    url = settings.normalize_site_url(args.url)
    name = args.name or settings.name_from_url(url)
    cfg["my_site"] = {"name": name, "url": url}
    settings.save(cfg)
    print(f"Твой сайт: {name} — {url}")
    return 0


def cmd_config_add_competitor(args):
    cfg = settings.load()
    url = settings.normalize_site_url(args.url)
    name = args.name or settings.name_from_url(url)

    def _key(u):
        return u.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")

    existing = {_key(c["url"]) for c in cfg["competitors"]}
    existing.add(_key(cfg["my_site"]["url"]))
    if _key(url) in existing:
        print(f"Уже в списке: {url}")
        return 0

    cfg["competitors"].append({"name": name, "url": url})
    settings.save(cfg)
    print(f"Добавлен конкурент: {name} — {url}")
    _print_sites(cfg)
    return 0


def cmd_config_remove_competitor(args):
    cfg = settings.load()
    target = args.target.strip().lower().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")
    before = len(cfg["competitors"])
    cfg["competitors"] = [
        c for c in cfg["competitors"]
        if c["name"].lower() != target
        and c["url"].lower().replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/") != target
    ]
    if len(cfg["competitors"]) == before:
        print(f"Не нашёл конкурента: {args.target}")
        _print_sites(cfg)
        return 1
    settings.save(cfg)
    print(f"Удалён конкурент: {args.target}")
    _print_sites(cfg)
    return 0


def cmd_config_set(args):
    cfg = settings.load()
    key = args.key
    if key not in settings.DEFAULTS:
        print(f"Неизвестный ключ: {key}")
        print(f"Доступные: {', '.join(sorted(settings.DEFAULTS.keys()))}")
        return 1
    default = settings.DEFAULTS[key]
    raw = args.value
    if isinstance(default, bool):
        value = raw.lower() in ("1", "true", "yes", "да")
    elif isinstance(default, int):
        value = int(raw)
    elif isinstance(default, float):
        value = float(raw)
    elif isinstance(default, list):
        value = [v.strip() for v in raw.split(",") if v.strip()]
    elif isinstance(default, dict):
        print(f"Ключ {key} меняется командами set-site/add-competitor, не через config set.")
        return 1
    else:
        value = raw
    cfg[key] = value
    settings.save(cfg)
    print(f"{key} = {value!r}")
    return 0


def cmd_run(args):
    if not settings.exists():
        print("Конфиг не найден — создаю с настройками по умолчанию.")
        settings.save(dict(settings.DEFAULTS))
    cfg = settings.load()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    data_dir = cfg["data_dir"]
    reports_dir = cfg["reports_dir"]
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(reports_dir, exist_ok=True)

    sites_to_crawl = [cfg["my_site"]] + cfg["competitors"]
    threshold = cfg["thin_content_word_threshold"]

    all_pages_data_by_site = {}
    site_summaries = []

    for site in sites_to_crawl:
        name, url = site["name"], site["url"]
        print(f"\n=== {name} ({url}) ===")
        pages = crawl_site(name, url, cfg, max_pages=args.max_pages)
        pages_data = analyze_pages(pages, threshold)

        raw_path = os.path.join(data_dir, f"{name}_{timestamp}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(pages_data, f, ensure_ascii=False, indent=2)
        print(f"[{name}] Сырые данные сохранены: {raw_path}")

        all_pages_data_by_site[name] = pages_data
        site_summaries.append(summarize_site(name, url, pages_data))

    comparison = build_comparison(site_summaries)

    html_path = os.path.join(reports_dir, f"report_{timestamp}.html")
    xlsx_path = os.path.join(reports_dir, f"report_{timestamp}.xlsx")
    summary_path = os.path.join(reports_dir, f"summary_{timestamp}.json")

    my_site_name = cfg["my_site"]["name"]
    generate_html_report(
        comparison,
        my_site_pages_data=all_pages_data_by_site[my_site_name],
        out_path=html_path,
    )
    generate_excel_report(comparison, all_pages_data_by_site, out_path=xlsx_path)

    # Машиночитаемая сводка — компактная, чтобы агент мог прочитать её целиком
    summary = {
        "generated_at": timestamp,
        "my_site": cfg["my_site"],
        "competitors": cfg["competitors"],
        "comparison": {
            "sites": comparison["sites"],
            "rows": comparison["rows"],
        },
        "site_summaries": comparison["raw"],
        "my_site_problem_pages": collect_problem_pages(
            all_pages_data_by_site[my_site_name], limit=100
        ),
        "reports": {
            "html": os.path.abspath(html_path),
            "xlsx": os.path.abspath(xlsx_path),
        },
        "raw_data_dir": os.path.abspath(data_dir),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== Готово ===")
    print(f"HTML-отчёт:  {os.path.abspath(html_path)}")
    print(f"Excel-отчёт: {os.path.abspath(xlsx_path)}")
    print(f"Сводка JSON: {os.path.abspath(summary_path)}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="SEO-аудит сайта и сравнение с конкурентами",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Создать конфиг в текущей папке")
    p_init.add_argument("--force", action="store_true", help="Перезаписать существующий конфиг")
    p_init.set_defaults(func=cmd_init)

    p_config = sub.add_parser("config", help="Просмотр и изменение конфигурации")
    config_sub = p_config.add_subparsers(dest="config_command", required=True)

    p_show = config_sub.add_parser("show", help="Показать конфигурацию")
    p_show.set_defaults(func=cmd_config_show)

    p_set_site = config_sub.add_parser("set-site", help="Задать свой сайт")
    p_set_site.add_argument("url")
    p_set_site.add_argument("--name", default=None)
    p_set_site.set_defaults(func=cmd_config_set_site)

    p_add = config_sub.add_parser("add-competitor", help="Добавить конкурента")
    p_add.add_argument("url")
    p_add.add_argument("--name", default=None)
    p_add.set_defaults(func=cmd_config_add_competitor)

    p_rm = config_sub.add_parser("remove-competitor", help="Убрать конкурента")
    p_rm.add_argument("target", help="Имя или URL конкурента")
    p_rm.set_defaults(func=cmd_config_remove_competitor)

    p_set = config_sub.add_parser("set", help="Изменить настройку")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_set.set_defaults(func=cmd_config_set)

    p_run = sub.add_parser("run", help="Полный аудит: краулинг -> анализ -> отчёты")
    p_run.add_argument("--max-pages", type=int, default=None,
                       help="Максимум страниц на сайт (переопределяет конфиг)")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
