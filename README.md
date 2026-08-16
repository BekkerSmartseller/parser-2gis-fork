<p align="center">
  <a href="#%E2%84%B9%EF%B8%8F-%D0%BE%D0%BF%D0%B8%D1%81%D0%B0%D0%BD%D0%B8%D0%B5">
    <img alt="Logo" width="128" src="https://user-images.githubusercontent.com/20641837/174094285-6e32eb04-7feb-4a60-bddf-5a0fde5dba4d.png"/>
  </a>
</p>
<h1 align="center">Parser2GIS</h1>

<p align="center">
  <a href="https://github.com/Eroloft/parser-2gis-new/actions/workflows/tests.yml"><img src="https://github.com/Eroloft/parser-2gis-new/actions/workflows/tests.yml/badge.svg" alt="Tests"/></a>
  <img src="https://img.shields.io/badge/python-3.8%20%E2%80%93%203.13-blue" alt="Поддерживаемые версии Python"/>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-LGPLv3-green" alt="Лицензия: LGPLv3"/></a>
  <a href="https://github.com/interlark/parser-2gis"><img src="https://img.shields.io/badge/fork%20of-interlark%2Fparser--2gis-orange" alt="Форк interlark/parser-2gis"/></a>
</p>

> ### 🍴 Это форк
> Проект является форком [**parser-2gis**](https://github.com/interlark/parser-2gis)
> (© Andy Trofimov, лицензия LGPLv3). Оригинальная разработка принадлежит автору;
> здесь добавлены модификации — см. раздел [«Изменения форка»](#-изменения-форка)
> и [CHANGELOG](CHANGELOG.md). Это независимый форк, не одобрен и не поддерживается
> оригинальным автором.

**Parser2GIS** - парсер сайта [2GIS](https://2gis.ru/) с помощью браузера [Google Chrome](https://google.com/chrome).

<img alt="Скриншот веб-дашборда Parser2GIS" src="docs/screenshot.png"/>

## ℹ️ Описание

Парсер для автоматического сбора базы адресов и контактов предприятий, которые работают на территории
России <img width="18px" src="https://user-images.githubusercontent.com/20641837/183511175-3d47f0f0-4e3f-45d2-8495-95d0612a8a8c.svg"/>, Казахстана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183511625-20420aef-59c3-426d-a112-654d2caf0dda.svg"/>, Беларуси <img width="18px" src="https://user-images.githubusercontent.com/20641837/183511940-ce088ad1-d97f-4fa1-849a-9b887ad481c5.svg"/>,
Азербайджана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512176-1f6795a1-ceac-4865-a29f-b5720ce5115e.svg"/>, Киргизии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512234-286ca403-5194-4a6d-a59e-59201140078a.svg"/>, Узбекистана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512333-7ec1f36d-07fe-450d-b6f1-eed59a3b69c8.svg"/>, Чехии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512458-5a5d9531-a8f0-4624-99da-7069cde84926.svg"/>, Египта <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512581-71fa2106-8cc1-43cc-a680-b3ff420acb8a.svg"/>, Италии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512763-0b438e5b-3ff0-4717-a826-0baac9207167.svg"/>, Саудовской Аравии <img width="18px" src="https://user-images.githubusercontent.com/20641837/183512980-427a985a-df1b-42c8-90bb-2c61692b6654.svg"/>, Кипра <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513128-4367d2b1-feb9-4efe-bc57-73a15d178ef2.svg"/>, Объединенных Арабских Эмиратов <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513374-9afef8c7-923e-4a18-9cd8-c69645b99377.svg"/>, Чили <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513576-7209ce90-a04a-4258-9832-ef210198c3c4.svg"/>, Катара <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513757-143ee2bf-b66c-4766-bbe1-db896a33eac1.svg"/>, Омана <img width="18px" src="https://user-images.githubusercontent.com/20641837/183513865-27509b74-b08f-4d92-b83b-a0d3aaabe155.svg"/>, Бахрейна <img width="18px" src="https://user-images.githubusercontent.com/20641837/183514076-3b6c9496-7c95-4452-8ee1-8723d98f876d.svg"/>, Кувейта <img width="18px" src="https://user-images.githubusercontent.com/20641837/183514240-7eff8632-5cd2-46ac-bed4-e483bb2df5f0.svg"/>.

## ✨ Особенности
- 💰 Абсолютно бесплатный
- 🤖 Успешно обходит анти-бот блокировки на территории РФ
- 🖥️ Работает под Windows, Linux и MacOS
- 📄 Четыре выходных формата: CSV таблица, XLSX таблица, JSON список и **HTML-страница**
- 🌐 **Современный веб-интерфейс** в браузере — единственный UI (запуск без аргументов)
- 🔗 **Генератор ссылок** прямо в вебе: город + рубрика → готовый URL 2GIS
- 🧹 **Фильтры результатов:** без франшиз (1 филиал на организацию), только с телефоном / WhatsApp / соцсетями / e-mail / сайтом, по рейтингу и отзывам
- 💬 **HTML-страница с кнопками WhatsApp и 2GIS** — открыл и сразу пишешь клиентам
- ✨ **Чистый вид** вывода — только нужные колонки, без мусора
- ⚙️ Расширенные настройки (лимит RAM, задержка кликов, кодировка, CSV-опции) в той же панели

## 🚀 Установка
> Для работы парсера необходимо установить браузер [Google Chrome](https://google.com/chrome).

### Установка этого форка из исходников
Дистрибутив форка называется **`parser-2gis-new`** (import-пакет остался `parser_2gis`).
  ```bash
  git clone <этот-репозиторий>
  cd parser-2gis-new
  python -m venv .venv
  pip install -e .
  ```
  Запуск (доступны обе команды — `parser-2gis-new` и `parser-2gis`):
  - `parser-2gis-new` — **веб-интерфейс** в браузере (по умолчанию);
  - `parser-2gis-new -i <URL> -o out.csv -f csv` — CLI без браузера.

  > Десктоп-GUI (tkinter/PySimpleGUI) в этом форке удалён — весь интерфейс перенесён в браузер.

### Оригинальный проект (PyPI)
  ```bash
  pip install parser-2gis        # CLI оригинального parser-2gis
  pip install parser-2gis[gui]   # с десктоп-GUI
  ```

### 🧵 Конкурентный парсинг и очередь запросов

- **Конкурентный парсинг:** несколько задач одновременно (лимит `max_concurrent` в расширенных настройках), задачи сверх лимита **встают в очередь** и выполняются по мере освобождения Chrome, у каждой свой `job_id`.
- **/api/jobs** — список задач и их статусы; класс/результаты/скачивание доступны по `job_id`.

## 📖 Документация
Описание работы доступно на [вики](https://github.com/interlark/parser-2gis/wiki).

## 🔧 Изменения форка

Форк сохраняет всю функциональность оригинала и добавляет:

- **Модернизация:** переход на `pydantic` v2, поддержка Python 3.12/3.13, обновлённые сборочные зависимости.
- **Веб-движок: Flask заменён на Litestar 2** (ASGI + uvicorn). API автоматически документируется через OpenAPI/Swagger/ReDoc (`/schema/swagger`, `/schema/redoc`, `/schema/openapi.json`).
- **HTTP-клиент: requests заменён на httpx**.
- **Фикс парсинга:** в headless-режиме карта 2GIS (WebGL) не инициализировалась, из-за чего страница отправляла вырождённый viewport и API отвечал `400 «Bound is incorrect»`. Исправлено через новый headless-режим Chrome и принудительный размер вьюпорта.
- **Веб-интерфейс — единственный UI.** Десктоп-GUI (tkinter/PySimpleGUI) удалён, все его функции перенесены в браузер. Запуск без аргументов открывает дашборд. Litestar + uvicorn стали основными зависимостями.
- **Генератор ссылок в вебе:** выбор страны, города (мультивыбор) и рубрики (поиск по 1785 рубрикам) → готовые URL 2GIS добавляются в список направлений.
- **Расширенные настройки в вебе:** лимит RAM браузера, задержка кликов, «точные совпадения», кодировка, CSV-опции (рубрики, комментарии, пустые колонки, дубликаты, колонок на поле).
- **Фильтры результатов:** дедуп франшиз (1 филиал на организацию), только с телефоном/WhatsApp/соцсетями/e-mail/сайтом, по рейтингу и количеству отзывов. Работают для всех форматов; доступны в CLI (`--filters.*`) и в вебе.
- **Чистый вид CSV/XLSX** (`--writer.csv.clean`) — только основные читаемые колонки.
- **Новый формат HTML** (`-f html`) — самодостаточная страница с карточками и кнопками WhatsApp / звонок / 2GIS / соцсети и поиском.
- **Улучшенный XLSX** — кликабельные ссылки, авто-ширина колонок, заморозка шапки, автофильтр.

Полный список — в [CHANGELOG.md](CHANGELOG.md).

### 🆕 Дополнительные модификации (форк Eroloft/parser-2gis-new)

Данный форк дополнительно расширяет базовый набор изменений и добавляет:

- **Расширенный сбор данных 2GIS:**
  - остановки общественного транспорта (`links.nearest_stations` — названия и расстояния), фото (`external_content`), атрибуты/цены (`attribute_groups`, включая «средний чек» по ключевым словам), e-mail для отправки (строка или объект `{"allowed": ...}`);
  - раздел рубрикатора верхнего уровня, основная рубрика и подрубрики, ID рубрик;
  - ID организации и ID записи в 2GIS, мобильный телефон, почтовый индекс, рейтинги/отзывы организации, ссылку на отзывы;
  - комментарии контактов и расписания — в отдельные колонки, без «склейки» со значениями.
- **CSV/XLSX:** логичный порядок колонок, «Остановки (все)» со списком «Название (расстояние, м)» и отдельная колонка минимального расстояния, «чистый вид» по умолчанию выключен.
- **HTML:** карточки с кнопками «Отзывы», «Фото», мобильным телефоном, индексом и разделом.
- **Веб-интерфейс:** язык по умолчанию — русский, сохранение настроек панели в localStorage (ссылки, фильтры, параметры, расширенные настройки), переводы кнопок, исправлены кнопки переключения колонок, исключена дублирующая псевдо-рубрика «Без рубрики» из списка выбора.
- **Надёжность:** исправлена валидация `email_for_sending` (2GIS присылает `dict` вместо строки), авто-поиск Chromium/headless-shell через Playwright и Brave для запуска без установленного Google Chrome.
- **Координатный (пространственный) поиск:** парсер принимает URL без городского префикса с параметром `?m=lon,lat/zoom` (например `https://2gis.ru/search/фитнес клуб?m=45.51,58.37/12`), что заставляет 2GIS искать **по кругу вокруг заданной точки** и расширяет охват за пределы одного города (например «в Шарье и области»).
- **Умное определение города (fallback-цепочка):** город филиала определяется не только по явному полю `city`, но и через регионы федерального значения (Москва/СПб/Севастополь), город-центр муниципального округа («Гурьевский муниципальный округ» → «Гурьевск») и населённые пункты с очисткой префикса (`пос.`/`пгт`/`г.`). Организации в посёлках и городских округах больше не теряют город.
- **Серверный режим:** флаги `--web-host <HOST>` (адрес прослушивания, по умолчанию `127.0.0.1`) и `--web-no-browser` (не открывать браузер) — для запуска веб-интерфейса на сервере/в Docker.
- **Толерантность к данным:** битые/аномальные контакты (без типа или значения, контакты одним объектом вместо списка) больше не роняют запись.
- **Геокодинг адреса** (`POST /api/geocode`): поиск адреса через 2GIS UI, перехват `markers/clustered`
  (новый UI), возврат координат + `id` объекта 2GIS (для маршрутов).
- **Маршруты через 2GIS** (`POST /api/route`): итинерарий (авто/ОТ/пешком/вело) парсится из
  серверного HTML 2GIS (SSR) — отдельный routing API не нужен. Точки `lon,lat;ID` для точной
  привязки, в ответе `segments` и все `variants`. Поддерживается **метро** (Москва/СПб и др.).
  См. раздел «🚌 Маршруты (routing): правило заполнения параметров».
- **Все филиалы сети** (`--parser.collect-branches`, по умолчанию вкл): когда сеть в выдаче
  показана одной карточкой (например при рубричном поиске), парсер дополнительно открывает
  страницу `/branches/{network_id}` и собирает **все** филиалы. Входные ссылки вида
  `/branches/{id}` и `/branches/{id}/firm/{firm}/...` также поддерживаются.

Автор этих модификаций: **BekkerSmartseller** — форк/дальнейшая разработка на базе `Eroloft/parser-2gis-new`.



## 🌐 HTTP API (веб-дашборд)

Сервер (порт 8666) построен на **Litestar 2** (ASGI, запускается под **uvicorn**). Он предоставляет REST API для запуска и мониторинга парсинга, в т.ч. нескольких задач параллельно.

**Автоматическая документация API (OpenAPI):**
- Swagger UI: `http://127.0.0.1:8666/schema/swagger`
- ReDoc: `http://127.0.0.1:8666/schema/redoc`
- OpenAPI JSON: `http://127.0.0.1:8666/schema/openapi.json`


### Запуск задачи

```http
POST /api/start
Content-Type: application/json
```

Тело запроса:

| Поле | Тип | Описание |
|---|---|---|
| `urls` | string[] | Список URL поисковой выдачи 2GIS (обязательно) |
| `max_records` | int | Лимит записей с одного URL (по умолчанию из конфига, обычно 100) |
| `max_concurrent` | int | Сколько Chrome-задач можно выполнять одновременно (по умолчанию 3). Задачи сверх лимита **встают в очередь** |
| `headless` | bool | Скрытый браузер (по умолчанию true) |
| `clean` | bool | «Чистый вид» CSV (только основные колонки) |
| `filters` | object | Фильтры результатов (см. ниже) |
| `advanced` | object | Расширенные настройки (см. ниже) |

Ответ: `{"ok": true, "job_id": "ab12cd34ef56"}`.

### Фильтры (`filters`)

```json
{
  "dedup_franchises": true,      // один филиал на организацию
  "dedup_across_niches": true,   // одно заведение один раз между нишами
  "require_phone": false,        // только с телефоном
  "require_whatsapp": false,
  "require_social": false,
  "require_email": false,
  "require_website": false,
  "min_rating": 4.0,             // рейтинг не ниже
  "min_reviews": 10              // отзывов не меньше
}
```

### Расширенные настройки (`advanced`)

```json
{
  "disable_images": true,        // быстрее
  "start_maximized": false,
  "skip_404_response": true,
  "delay_between_clicks": 0,     // мс
  "columns_per_entity": 3,
  "memory_limit": 2048,          // МБ для V8
  "add_rubrics": false,
  "add_comments": false,
  "remove_empty_columns": true,
  "remove_duplicates": true,
  "encoding": "utf8",
  "collect_branches": true      // все филиалы сетей (/branches/)
}
```

### Автообновление справочников (cities/rubrics)

Справочники городов и рубрик обновляются из `data.2gis.com` автоматически:

- **при запуске** веб-сервера и **раз в сутки** (фоновый поток);
- **вручную** — `POST /api/refresh`.

Обновлённые файлы сохраняются в `user_path/refdata/` и используются сразу
(кэш загрузчиков сбрасывается). Управление переменными окружения:

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `P2GIS_REFDATA_REFRESH` | `1` | `0` — отключить автообновление (запуск + расписание) |
| `P2GIS_REFDATA_INTERVAL_HOURS` | `24` | Через сколько часов справочники считаются устаревшими |
| `P2GIS_REFDATA_CHECK_MINUTES` | `60` | Как часто фоновый поток проверяет расписание |

### Мониторинг и результаты

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/jobs` | Все задачи: `[{id, status, count}]`. Статусы: `queued`, `running`, `done`, `stopped`, `error` |
| GET | `/api/status?job_id=ID&cursor=0` | Прогресс задачи: `{status, running, count, logs[], cursor}`. Без `job_id` — последняя задача |
| GET | `/api/results?job_id=ID` | Массив записей задачи `{records: [...]}` |
| GET | `/api/download?format=csv&job_id=ID` | Скачать результат: `csv`, `xlsx`, `json`, `html` |
| POST | `/api/stop` | Остановить задачу (тело `{"job_id": ID}`) |
| POST | `/api/clear` | Очистить результат (тело `{"job_id": ID}`) |
| GET | `/api/generator` | Данные для конструктора ссылок: `{countries, cities, rubrics}` (города = base + добавленные) |
| POST | `/api/cities` | **Добавить город** в справочник, если отсутствует. Тело: `{"name": "Шарья", "code"?: "sharya", "domain"?: "ru", "country_code"?: "ru"}`. `code` необязателен — генерируется транслитом. Идемпотентно по `code`/имени. Ответ: `{"ok": true, "city": {name, code, domain, country_code}}` |
| GET | `/api/cities` | Список городов (базовый справочник + добавленные через API) |
| GET | `/api/history` | Сохранённые парсинги: `{items: [{id, created_at, urls, count}]}` |
| GET | `/api/history/HID/results` | Записи из истории |
| GET | `/api/history/HID/download?format=csv` | Скачать из истории |
| POST | `/api/history/merge` | Объединить записи истории (тело `{"ids": [HID,...]}`) |
| DELETE | `/api/history/HID` | Удалить запись истории |
| POST | `/api/refresh` | **Обновить справочники** 2GIS (cities.json/rubrics.json из data.2gis.com). Ответ: `{ok, status: ok\|skipped\|busy\|error, cities, rubrics, updated_at}` |
| POST | `/api/geocode` | **Геокодинг адреса** через 2GIS. Тело: `{"query": "...", "city"?: "...", "lat"?: N, "lon"?: N}`. Ответ: `{"ok": true, "lat", "lon", "name", "address", "id"}` — `id` нужен для точной привязки точек маршрута |
| POST | `/api/route` | **Маршрут** через 2GIS (авто/ОТ/пешком/вело). Тело: `{"from_lat", "from_lon", "to_lat", "to_lon", "transport_mode"?: "car|transit|walk|bike", "city"?, "from_id"?, "to_id"?}`. Ответ: `{"ok", "mode", "duration_s", "distance_m", "segments", "variants"}`. Подробно — раздел «🚌 Маршруты» |

### Пример (curl)

```bash
# Запуск параллельного парсинга (лимит 3 Chrome; 5-я задача встанет в очередь)
curl -X POST http://127.0.0.1:8666/api/start -H 'Content-Type: application/json' -d '{
  "urls": ["https://2gis.ru/kazan/search/Фитнес-клубы/rubricId/268/filters/sort=name"],
  "max_records": 100,
  "max_concurrent": 3
}'
# → {"ok": true, "job_id": "a1b2c3d4e5f6"}

# Статус
curl "http://127.0.0.1:8666/api/status?job_id=a1b2c3d4e5f6"

# Результаты
curl "http://127.0.0.1:8666/api/results?job_id=a1b2c3d4e5f6"

# Скачать CSV
curl "http://127.0.0.1:8666/api/download?format=csv&job_id=a1b2c3d4e5f6" -o result.csv

# Геокодинг адреса (вернёт координаты + id для маршрута)
curl -X POST http://127.0.0.1:8666/api/geocode -H 'Content-Type: application/json' -d '{
  "query": "Московский проспект 273", "city": "Калининград"
}'
# -> {"ok": true, "lat": 54.71, "lon": 20.51,
#     "name": "Московский проспект, 273", "address": null, "id": "111222333444"}

# Маршрут на общественном транспорте (точки с ID из геокодинга)
curl -X POST http://127.0.0.1:8666/api/route -H 'Content-Type: application/json' -d '{
  "from_lat": 54.71, "from_lon": 20.51,
  "to_lat": 54.72, "to_lon": 20.53,
  "transport_mode": "transit", "city": "kaliningrad",
  "from_id": "111222333444", "to_id": "555666777888"
}'
```

### Замечания

- `max_records` — это лимит числа *кликов* по позициям выдачи. Если 2GIS не вернул JSON на какую-то позицию (анти-бот/сеть), она пропускается — итог может быть чуть меньше лимита. При необходимости задавайте `max_records` с запасом или увеличивайте ретраи.
- Каждая задача использует собственный Chrome; одновременно открыто не более `max_concurrent` браузеров.

## 📜 Лицензия и авторство

- Оригинальный проект: **parser-2gis** — © **Andy Trofimov** (interlark@gmail.com),
  https://github.com/interlark/parser-2gis
- Лицензия: **GNU LGPLv3** (см. [LICENSE](LICENSE)) — сохранена без изменений.
- Модификации в этом форке распространяются на тех же условиях LGPLv3.


---

## 🏙 Добавление городов (расширение справочника)

По умолчанию список городов берётся из `parser_2gis/data/cities.json`. Если нужного города там нет
(например небольшой город вроде Шарьи), его можно добавить через API — город будет сохранён в
`~/.local/share/parser-2gis/cities_custom.json` и попадёт в выдачу `/api/generator` и `/api/cities`.

```bash
# код города необязателен — будет сгенерирован транслитом (Шарья -> sharya)
curl -X POST http://127.0.0.1:8666/api/cities \
  -H 'Content-Type: application/json' \
  -d '{"name": "Шарья", "code": "sharya", "domain": "ru"}'
# -> {"ok": true, "city": {"name": "Шарья", "code": "sharya", "domain": "ru", "country_code": "ru"}}
```

- Идемпотентно: повторный вызов с тем же `code` или именем вернёт существующий город без дублей.
- После добавления город сразу доступен в `/api/generator` (конструктор ссылок и интеграции).


---

## 📍 Координатный (пространственный) поиск

По умолчанию парсер ищет в пределах **одного города** — URL вида
`https://2gis.ru/<city>/search/<запрос>`. Но 2GIS умеет искать **по кругу вокруг
заданной точки**: если передать URL **без городского префикса** с параметром
`?m=lon,lat/zoom`, выдача расширяется на весь видимый участок карты (город
+ окрестности, а при малом zoom — целый регион).

Формат (обратите внимание: **сначала долгота, потом широта**):

```
https://2gis.ru/search/<запрос>?m=<долгота>,<широта>/<zoom>
```

- `zoom` управляет радиусом поиска: меньше число = шире круг (например `12` —
  город с окрестностями, `9.5` — регион).
- Такой URL можно передать как в `urls` при запуске через `/api/start`, так и
  через CLI (`-i "https://2gis.ru/search/фитнес клуб?m=45.51,58.37/12"`).
- Координаты центра обычно берут из геокодера (по названию города).

Пример: вместо `https://2gis.ru/sharya/search/фитнес клуб` (1 результат в Шарье)

```
https://2gis.ru/search/фитнес клуб?m=45.519306,58.37257/12
```

вернёт фитнес-клубы по всей округе Шарьи.


---

## 🚌 Маршруты (routing): правило заполнения параметров

Маршруты строятся через веб-интерфейс 2GIS (`POST /api/route`): открывается страница
`directions` в Chrome (headless), 2GIS рендерит итинерарий на сервере (SSR), парсер
извлекает карточки маршрутов из DOM. Отдельный routing API 2GIS для этого не нужен
(эндпоинты `routing.api.2gis.ru` / `public-transport.api.2gis.ru` больше не используются
веб-клиентом). Если 2GIS не смог построить маршрут — эндпоинт отвечает `404`.

### `POST /api/route`

| Поле | Тип | Обязательно | Описание |
|---|---|---|---|
| `from_lat` / `from_lon` | number | да | Координаты точки А |
| `to_lat` / `to_lon` | number | да | Координаты точки Б |
| `transport_mode` | string | да | `car` / `transit` / `walk` / `bike` |
| `city` | string | нет | Название города (кириллица) или готовый латинский slug (`kaliningrad`) |
| `from_id` / `to_id` | string | нет | ID точек 2GIS (из `/api/geocode`) — точная привязка к объекту |

### Как координаты и ID превращаются в URL 2GIS

```
https://2gis.ru/{city}/directions/{tab}points/{lon,lat[;ID]|lon,lat[;ID]}?m={mid_lon},{mid_lat}/{zoom}
```

- Точка в URL: `lon,lat` (сначала долгота, потом широта). При наличии ID —
  `lon,lat;ID` (ID берётся из `/api/geocode`, поле `id`/`geometry_id`).
  Без ID 2GIS привяжет точку по координатам — маршрут тоже построится, но привязка
  грубее (точка привяжется к ближайшему объекту).
- Таб по режиму:

| `transport_mode` | Таб в URL |
|---|---|
| `transit` | `tab/bus/` |
| `car` | `tab/car/` |
| `walk` | `tab/pedestrian/` |
| `bike` | `tab/bike/` |

- `?m=lon,lat/zoom` — середина маршрута и зум по дальности (опциональный якорь карты;
  2GIS сам пересчитывает его после загрузки).

Пример (ОТ, точки с ID):

```
https://2gis.ru/kaliningrad/directions/tab/bus/points/
  20.51,54.71;111222333444|20.53,54.72;555666777888?m=20.520000,54.715000/15
```

### Типовой поток: геокод → маршрут

```bash
# 1) геокодинг адреса (возвращает координаты + id)
curl -X POST http://127.0.0.1:8666/api/geocode -H 'Content-Type: application/json' \
  -d '{"query": "Московский проспект 273", "city": "Калининград"}'
# -> {"ok": true, "lat": 54.71, "lon": 20.51,
#     "name": "Московский проспект, 273", "address": ..., "id": "111222333444"}

# 2) маршрут с точными ID точек
curl -X POST http://127.0.0.1:8666/api/route -H 'Content-Type: application/json' \
  -d '{"from_lat": 54.71, "from_lon": 20.51,
       "to_lat": 54.72, "to_lon": 20.53,
       "transport_mode": "transit", "city": "kaliningrad",
       "from_id": "111222333444", "to_id": "555666777888"}'
```

### Ответ `/api/route`

```json
{
  "ok": true,
  "mode": "transit",
  "duration_s": 3660,
  "distance_m": null,
  "walk_duration_s": 1380,
  "transfers": 0,
  "segments": [
    {"type": "walk", "mode": "walk", "route": "", "duration_s": null, ...},
    {"type": "bus", "mode": "bus", "route": "28", "duration_s": 960, ...},
    {"type": "walk", "mode": "walk", "route": "", "duration_s": null, ...}
  ],
  "variants": [
    {"duration_s": 3660, "transfers": 0, "segments": [...]},
    {"duration_s": 4020, "transfers": 1, "segments": [...]}
  ]
}
```

- `segments` — участки маршрута (`walk` / `bus` / `trolleybus` / `tram` /
  `shuttle_bus` / `suburban_train` / `river_transport` / `cable_car`), у ОТ —
  номер маршрута в `route`.
- `variants` — все варианты маршрута (для ОТ 2GIS предлагает несколько),
  первый продублирован в полях верхнего уровня.
- Для `car` / `walk` / `bike` заполняются `duration_s`, `distance_m` и `note`
  (например «с учётом пробок»), `segments` пуст.
- `distance_m` для ОТ отсутствует (в карточке 2GIS нет дистанции) — `null`.
