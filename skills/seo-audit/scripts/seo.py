# -*- coding: utf-8 -*-
# /// script
# requires-python = ">=3.11"
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
                                        crawl_order, checkpoint_every,
                                        request_delay_seconds и т.д.)
    run [--max-pages N]                 Полный аудит: краулинг всех сайтов -> отчёты
    run [--resume]                      Продолжить прерванный обход с чекпоинта
    run --only <имя> [--max-pages N]    Просканировать один сайт (чанковый режим
                                        для окружений с лимитом длительности хода);
                                        отчёт потом собрать командой report
    report                              Собрать отчёты из последних сохранённых
                                        данных по каждому сайту (без краулинга)
    status                              Показать, по каким сайтам уже есть данные

Все результаты пишутся в текущую папку:
    seo-audit.config.json            — конфигурация
    seo-audit/data/*.json            — сырые метрики по страницам
    seo-audit/reports/report_*.html  — HTML-отчёт
    seo-audit/reports/report_*.xlsx  — Excel-отчёт
    seo-audit/reports/summary_*.json — машиночитаемая сводка (для агента)
"""
import argparse
import csv
import glob
import json
import os
import sys
import urllib.parse as up
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from seo_kit import settings
from seo_kit.crawler import crawl_site
from seo_kit.analyzer import analyze_page
from seo_kit.compare import summarize_site, build_comparison
from seo_kit.report import generate_html_report, generate_excel_report, collect_problem_pages
from seo_kit.ai_visibility import check_site as check_ai_visibility


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

    # Коллизия имён (example.com и example.org дают одно авто-имя "Example")
    # ломает файлы данных — при совпадении берём полный хост.
    taken = {cfg["my_site"]["name"]} | {c["name"] for c in cfg["competitors"]}
    if not args.name and name in taken:
        name = _key(url)

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
    # Опечатка в crawl_order молча включила бы поведение "sitemap"
    if key == "crawl_order" and value not in ("structure", "sitemap"):
        print(f"crawl_order: допустимые значения — structure, sitemap (получено: {value!r})")
        return 1
    cfg[key] = value
    settings.save(cfg)
    print(f"{key} = {value!r}")
    return 0


def _ensure_config():
    if not settings.exists():
        print("Конфиг не найден — создаю с настройками по умолчанию.")
        settings.save(dict(settings.DEFAULTS))
    return settings.load()


def _checkpoint_path(cfg, site_name):
    return os.path.join(cfg["data_dir"], f"{site_name}.checkpoint.json")


def _crawl_and_save(site, cfg, max_pages, timestamp, resume=False):
    """
    Краулит один сайт потоково: анализирует каждую страницу сразу и
    выбрасывает её HTML, поэтому память не зависит от числа страниц
    (иначе сайт из 10 000 страниц по 700 КБ — это гигабайты в оперативке).

    По ходу сохраняет чекпоинты, чтобы обрыв связи на многочасовом
    прогоне не стоил всего обхода: продолжить можно через --resume.
    """
    name, url = site["name"], site["url"]
    print(f"\n=== {name} ({url}) ===")

    threshold = cfg["thin_content_word_threshold"]
    checkpoint_every = int(cfg.get("checkpoint_every", 0) or 0)
    ck_path = _checkpoint_path(cfg, name)

    pages_data = []
    resume_visited = resume_queue = None
    ck_sitemap_info = {}

    if resume and os.path.exists(ck_path):
        with open(ck_path, encoding="utf-8") as f:
            ck = json.load(f)
        pages_data = ck.get("pages_data", [])
        resume_visited = ck.get("visited") or []
        resume_queue = ck.get("queue") or []
        ck_sitemap_info = ck.get("sitemap_info") or {}
        print(f"[{name}] Чекпоинт найден: {len(pages_data)} страниц уже проанализировано.")
    elif resume:
        print(f"[{name}] Чекпоинта нет — начинаю обход с нуля.")

    limit = max_pages or cfg["max_pages_per_site"]
    budget = max(0, limit - len(pages_data))
    state = {}

    def save_checkpoint():
        if not checkpoint_every:
            return
        os.makedirs(cfg["data_dir"], exist_ok=True)
        tmp = ck_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({
                "site": name,
                "url": url,
                "pages_data": pages_data,
                "visited": sorted(state.get("visited") or []),
                "queue": [list(item) for item in (state.get("queue") or [])],
                "sitemap_info": state.get("sitemap_info") or ck_sitemap_info,
            }, f, ensure_ascii=False)
        os.replace(tmp, ck_path)  # атомарно: чекпоинт не бьётся при обрыве

    try:
        if budget:
            for raw_page in crawl_site(name, url, cfg, max_pages=budget, state=state,
                                       resume_visited=resume_visited,
                                       resume_queue=resume_queue,
                                       resume_sitemap_info=ck_sitemap_info or None):
                pages_data.append(analyze_page(raw_page, threshold))
                del raw_page  # HTML освобождается сразу, до следующего запроса
                if checkpoint_every and len(pages_data) % checkpoint_every == 0:
                    save_checkpoint()
                    print(f"[{name}] Чекпоинт: {len(pages_data)} страниц сохранено.")
        else:
            # В чекпоинте уже больше страниц, чем текущий лимит (например,
            # resume без исходного --max-pages) — краулить нечего.
            print(f"[{name}] В чекпоинте уже {len(pages_data)} страниц при лимите {limit} — "
                  f"краулинг пропускаю. Добрать больше: run --only {name} "
                  f"--max-pages <число> --resume")
    except KeyboardInterrupt:
        if checkpoint_every:
            save_checkpoint()
            print(f"\n[{name}] Прервано. Сохранено страниц: {len(pages_data)}")
            print(f"[{name}] Продолжить обход: run --only {name} --resume")
        else:
            print(f"\n[{name}] Прервано. Чекпоинты выключены (checkpoint_every=0) — "
                  f"прогресс не сохранён.")
        raise

    if not pages_data:
        print(f"[{name}] ВНИМАНИЕ: собрано 0 страниц. Возможные причины: "
              f"анти-бот защита (Cloudflare и т.п.), запрет в robots.txt, "
              f"SPA без серверного HTML или сайт недоступен. "
              f"Проверь сайт вручную (curl / браузер).")

    raw_path = os.path.join(cfg["data_dir"], f"{name}_{timestamp}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(pages_data, f, ensure_ascii=False, indent=2)
    print(f"[{name}] Сырые данные сохранены: {raw_path}")

    # Метаданные обхода: свежесть sitemap — самостоятельная SEO-метрика,
    # держим её рядом с данными, а не только в логе.
    # При возобновлении краулер sitemap не перечитывает и кладёт в state
    # пустую заготовку {"found": False}. Она непустая, поэтому проверять
    # её через `or` нельзя — смотрим именно на флаг found.
    sitemap_info = state.get("sitemap_info") or {}
    if not sitemap_info.get("found") and ck_sitemap_info:
        sitemap_info = ck_sitemap_info
    meta_path = os.path.join(cfg["data_dir"], f"{name}_{timestamp}.meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "site": name,
            "url": url,
            "crawled_at": timestamp,
            "pages_crawled": len(pages_data),
            "crawl_order": cfg.get("crawl_order", "structure"),
            "max_pages": limit,
            "sitemap": sitemap_info,
        }, f, ensure_ascii=False, indent=2)

    # Обход дошёл до конца — чекпоинт больше не нужен. Но если краулинг
    # пропущен (budget == 0), обход не завершён: чекпоинт оставляем, чтобы
    # его можно было продолжить с большим --max-pages.
    if budget and os.path.exists(ck_path):
        os.remove(ck_path)

    return pages_data


def _latest_data_file(data_dir, site_name):
    """Последний сохранённый data-файл сайта (таймстамп в имени сортируется лексикографически).

    Файлы *.meta.json отсеиваем: они лежат рядом, попадают в тот же глоб и
    сортируются после основного файла — иначе вместо страниц прочитается
    описание обхода.
    """
    files = sorted(
        f for f in glob.glob(os.path.join(data_dir, f"{site_name}_*.json"))
        if not f.endswith(".meta.json")
    )
    return files[-1] if files else None


def _latest_meta_file(data_dir, site_name):
    files = sorted(glob.glob(os.path.join(data_dir, f"{site_name}_*.meta.json")))
    return files[-1] if files else None


def _load_sitemap_info(cfg, site_name):
    path = _latest_meta_file(cfg["data_dir"], site_name)
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("sitemap") or {}
    except Exception:
        return {}


def _write_broken_links_csv(pages_data, out_path):
    """
    Полный список битых URL с указанием источника ссылки.
    Именно то, что нужно для расстановки редиректов: видно, что чинить —
    sitemap или конкретную страницу, с которой ведёт ссылка.
    """
    broken = [p for p in pages_data
              if p.get("status_code") and p["status_code"] >= 400]
    if not broken:
        return None, 0
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["URL", "Код", "Откуда ссылка", "Раздел"])
        for p in broken:
            path = up.urlparse(p["url"]).path.strip("/").split("/")
            writer.writerow([
                p["url"],
                p["status_code"],
                p.get("found_on") or "",
                path[0] if path and path[0] else "(корень)",
            ])
    return out_path, len(broken)


def _build_reports(cfg, all_pages_data_by_site, timestamp):
    """Сравнение + HTML/Excel/summary из готовых постраничных данных."""
    reports_dir = cfg["reports_dir"]
    os.makedirs(reports_dir, exist_ok=True)

    sites = [cfg["my_site"]] + cfg["competitors"]
    sites = [s for s in sites if s["name"] in all_pages_data_by_site]

    site_summaries = []
    ai_visibility_by_site = {}
    sitemap_by_site = {}
    for site in sites:
        name, url = site["name"], site["url"]
        # AI-видимость: политика robots.txt по AI-ботам + llms.txt (дёшево, HTTP)
        try:
            ai = check_ai_visibility(url, timeout=cfg["request_timeout"])
        except Exception:
            ai = {}
        ai_visibility_by_site[name] = ai

        summary = summarize_site(name, url, all_pages_data_by_site[name])
        sitemap_info = _load_sitemap_info(cfg, name)
        if sitemap_info:
            summary["sitemap_age_days"] = sitemap_info.get("age_days")
            summary["sitemap_urls_total"] = sitemap_info.get("urls_total")
        sitemap_by_site[name] = sitemap_info
        if ai:
            summary["ai_search_bots"] = f"{ai['search_bots_allowed']}/{ai['search_bots_total']}"
            summary["ai_llms_txt"] = "да" if ai["has_llms_txt"] else "нет"
        site_summaries.append(summary)

    comparison = build_comparison(site_summaries)

    html_path = os.path.join(reports_dir, f"report_{timestamp}.html")
    xlsx_path = os.path.join(reports_dir, f"report_{timestamp}.xlsx")
    summary_path = os.path.join(reports_dir, f"summary_{timestamp}.json")

    my_site_name = cfg["my_site"]["name"]
    my_pages = all_pages_data_by_site.get(my_site_name, [])
    generate_html_report(comparison, my_site_pages_data=my_pages, out_path=html_path)
    generate_excel_report(comparison, all_pages_data_by_site, out_path=xlsx_path)

    broken_csv_path = os.path.join(reports_dir, f"broken_links_{timestamp}.csv")
    broken_csv_path, broken_n = _write_broken_links_csv(my_pages, broken_csv_path)

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
        "ai_visibility": ai_visibility_by_site,
        "my_site_problem_pages": collect_problem_pages(my_pages, limit=100),
        "sitemap": sitemap_by_site,
        "reports": {
            "html": os.path.abspath(html_path),
            "xlsx": os.path.abspath(xlsx_path),
            "broken_links_csv": os.path.abspath(broken_csv_path) if broken_csv_path else None,
        },
        "raw_data_dir": os.path.abspath(cfg["data_dir"]),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("\n=== Готово ===")
    print(f"HTML-отчёт:  {os.path.abspath(html_path)}")
    print(f"Excel-отчёт: {os.path.abspath(xlsx_path)}")
    print(f"Сводка JSON: {os.path.abspath(summary_path)}")
    if broken_csv_path:
        print(f"Битые ссылки ({broken_n}) с источниками: {os.path.abspath(broken_csv_path)}")
    return 0


def cmd_run(args):
    cfg = _ensure_config()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(cfg["data_dir"], exist_ok=True)

    sites = [cfg["my_site"]] + cfg["competitors"]

    if args.only:
        target = args.only.strip().lower()
        selected = [
            s for s in sites
            if s["name"].lower() == target or target in s["url"].lower()
        ]
        if not selected:
            print(f"Не нашёл сайт: {args.only}")
            print("Доступные: " + ", ".join(s["name"] for s in sites))
            return 1
        for site in selected:
            _crawl_and_save(site, cfg, args.max_pages, timestamp,
                            resume=getattr(args, "resume", False))
        remaining = [s["name"] for s in sites
                     if not _latest_data_file(cfg["data_dir"], s["name"])]
        print()
        if remaining:
            print(f"Ещё не просканированы: {', '.join(remaining)}")
            print("Просканируй их через `run --only <имя>`, затем собери отчёт: `report`")
        else:
            print("Данные есть по всем сайтам. Собери отчёт командой: `report`")
        return 0

    # Полный прогон: все сайты подряд + отчёт
    all_pages_data_by_site = {}
    for site in sites:
        all_pages_data_by_site[site["name"]] = _crawl_and_save(
            site, cfg, args.max_pages, timestamp,
            resume=getattr(args, "resume", False)
        )
    return _build_reports(cfg, all_pages_data_by_site, timestamp)


def cmd_report(args):
    cfg = _ensure_config()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    sites = [cfg["my_site"]] + cfg["competitors"]
    all_pages_data_by_site = {}
    missing = []
    for site in sites:
        path = _latest_data_file(cfg["data_dir"], site["name"])
        if path is None:
            missing.append(site["name"])
            continue
        with open(path, encoding="utf-8") as f:
            all_pages_data_by_site[site["name"]] = json.load(f)
        print(f"[{site['name']}] Использую данные: {path}")

    if cfg["my_site"]["name"] not in all_pages_data_by_site:
        print(f"Нет данных по твоему сайту ({cfg['my_site']['name']}) — "
              f"сначала просканируй его: `run --only {cfg['my_site']['name']}`")
        return 1
    if missing:
        print(f"ВНИМАНИЕ: нет данных по: {', '.join(missing)} — "
              f"в отчёт не попадут. Досканируй через `run --only <имя>` и "
              f"перезапусти `report`.")

    return _build_reports(cfg, all_pages_data_by_site, timestamp)


def cmd_status(args):
    cfg = _ensure_config()
    sites = [cfg["my_site"]] + cfg["competitors"]
    print("Сохранённые данные по сайтам:")
    for site in sites:
        path = _latest_data_file(cfg["data_dir"], site["name"])
        if path:
            with open(path, encoding="utf-8") as f:
                n = len(json.load(f))
            print(f"  {site['name']}: {n} страниц ({os.path.basename(path)})")
        else:
            print(f"  {site['name']}: нет данных")
        ck = _checkpoint_path(cfg, site["name"])
        if os.path.exists(ck):
            try:
                with open(ck, encoding="utf-8") as f:
                    done = len(json.load(f).get("pages_data", []))
                print(f"      незавершённый обход: {done} страниц "
                      f"(продолжить: run --only {site['name']} --resume)")
            except Exception:
                pass
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

    p_run = sub.add_parser("run", help="Аудит: краулинг -> анализ -> отчёты")
    p_run.add_argument("--max-pages", type=int, default=None,
                       help="Максимум страниц на сайт (переопределяет конфиг)")
    p_run.add_argument("--only", default=None, metavar="САЙТ",
                       help="Просканировать только один сайт (имя или часть URL); "
                            "отчёт потом собрать командой report")
    p_run.add_argument("--resume", action="store_true",
                       help="Продолжить обход с последнего чекпоинта "
                            "(после обрыва длинного прогона)")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="Собрать отчёты из последних сохранённых данных")
    p_report.set_defaults(func=cmd_report)

    p_status = sub.add_parser("status", help="По каким сайтам уже есть данные")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
