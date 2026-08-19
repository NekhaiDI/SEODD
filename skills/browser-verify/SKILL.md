---
name: browser-verify
description: Этот скилл используется, когда нужно проверить страницу в реальном браузере — выполнить JS на странице, снять скриншот (в т.ч. мобильную версию), замерить скорость и Core Web Vitals, проверить отрендеренные мета-теги и микроразметку SPA-сайта, прогнать axe-аудит доступности, посмотреть cookies/localStorage/сеть. Автоматизация Chrome через DevTools Protocol без внешних зависимостей.
license: Apache-2.0
compatibility: Требуется Chrome/Chromium и Node 22+. npm install не нужен.
metadata:
  authors:
    - Artem Rozumenko <artem_rozumenko@epam.com>
  packaged-by:
    - Дмитрий Нехай
  version: "0.1.0"
---

# Browser Verify

Автоматизация Chrome через Chrome DevTools Protocol. Все взаимодействия —
настоящие CDP-события `Input` (координаты мыши, события клавиш), а не
синтетический JS `.click()`. Без внешних зависимостей: используется
встроенный WebSocket из Node 22.

В составе seo-audit-kit применяется, когда серверного HTML недостаточно:
SPA/JS-рендеринг, замер скорости, мобильная эмуляция, проверка
отрендеренной микроразметки, аудит доступности.

## Скрипты

```bash
SCRIPTS="${CLAUDE_PLUGIN_ROOT}/skills/browser-verify/scripts"

bash "$SCRIPTS/chrome-launcher.sh" start --headless   # запустить Chrome
node "$SCRIPTS/cdp.mjs" <команда> [аргументы]          # выполнить команду
bash "$SCRIPTS/chrome-launcher.sh" stop                # остановить Chrome
```

Chrome запускать перед работой и останавливать после.

## Команды (основные)

### Навигация и осмотр

| Команда | Пример | Назначение |
|---|---|---|
| `navigate <url>` | `navigate "https://example.com"` | Открыть URL, дождаться загрузки |
| `page-info` | `page-info` | Title, URL, viewport, мета |
| `get-meta` | `get-meta` | Мета-теги, OG, Twitter cards, structured data |
| `get-html [--selector s]` | `get-html --selector "#app"` | outerHTML (отрендеренный) |
| `get-text [--selector s]` | `get-text --selector "h1"` | textContent |
| `evaluate <js>` | `evaluate "document.title"` | Выполнить JS на странице |
| `query-all <sel>` | `query-all "img:not([alt])"` | Все совпадения селектора |

### Скриншоты и эмуляция

| Команда | Пример | Назначение |
|---|---|---|
| `screenshot` | `screenshot --output /tmp/page.png` | Снимок страницы |
| `screenshot --full` | `screenshot --full --output /tmp/f.png` | Вся страница, за пределами вьюпорта |
| `emulate <device>` | `emulate mobile` | Эмуляция устройства |
| `viewport <w> <h>` | `viewport 375 812` | Размер вьюпорта |

Устройства: `mobile`, `iphone`, `ipad`, `tablet`, `android`, `desktop`, `laptop`.

### Скорость и качество

| Команда | Пример | Назначение |
|---|---|---|
| `get-performance` | `get-performance` | Тайминги, размеры ресурсов, LCP/FID/CLS |
| `get-network` | `get-network --status error` | Запросы сети, фильтр по статусу |
| `get-console` | `get-console` | Сообщения консоли |
| `inject-axe` | `inject-axe` | Аудит доступности axe-core (WCAG 2.x) |

### Взаимодействие и ожидание

`click`, `type`, `hover`, `press`, `select`, `check`, `scroll`, `wait`,
`wait-visible`, `wait-text`, `wait-url`, `wait-network-idle`, `sleep` —
полный список с примерами в `references/cdp-commands.md`.

## Типовые сценарии для SEO

### Проверка SPA-страницы (пустая в серверном HTML)

```bash
node "$SCRIPTS/cdp.mjs" navigate "https://example.com/catalog"
node "$SCRIPTS/cdp.mjs" wait-network-idle
node "$SCRIPTS/cdp.mjs" get-meta
node "$SCRIPTS/cdp.mjs" evaluate "document.body.innerText.split(/\\s+/).length"
```

### Скорость и Core Web Vitals

```bash
node "$SCRIPTS/cdp.mjs" navigate "https://example.com"
node "$SCRIPTS/cdp.mjs" get-performance
node "$SCRIPTS/cdp.mjs" get-network --status error
```

### Мобильная версия

```bash
node "$SCRIPTS/cdp.mjs" navigate "https://example.com"
node "$SCRIPTS/cdp.mjs" emulate mobile
node "$SCRIPTS/cdp.mjs" screenshot --output /tmp/mobile.png
```

## Примечания

- Скриншоты сохраняются как PNG — ссылаться на путь к файлу как на
  доказательство. **Не читать PNG обратно в контекст без необходимости**:
  просмотренный скриншот едет в каждом следующем ходе сессии. Смотреть
  пиксели только когда вердикт требует визуальной оценки (вёрстка,
  рендеринг); для проверок наличия/состояния использовать `evaluate`,
  DOM-запросы, консоль и сеть — дешевле и точнее.
- Элементы автоматически прокручиваются в зону видимости перед действием.
- Захват консоли/сети — по-сессионный (на каждый `navigate`).
- Windows: `chrome-launcher.sh` — bash-скрипт, запускать из Git Bash.
  Альтернатива — стартовать Chrome вручную:
  `chrome.exe --remote-debugging-port=9222 --user-data-dir=%TEMP%\cdp-profile`,
  после чего `cdp.mjs` работает как обычно.

## Справочник

Расширенные паттерны `evaluate`, QA-инспекция и troubleshooting —
в `references/cdp-commands.md`.
