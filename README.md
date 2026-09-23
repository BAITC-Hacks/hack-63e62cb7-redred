# RedRed — AI-консультант для ekt.kz

Прототип для покупателей ekt.kz: помогает найти электротехнический товар, проверить характеристики, наличие и документы, объяснить выбор аналога и добавить товар в корзину **только после подтверждения**. Корзина пока принадлежит прототипу, а не сайту ekt.kz.

## Состояние на 23 сентября 2026 года

| Часть | Состояние |
| --- | --- |
| Backend | Сессия, локальный поиск, свежая карточка EKT, аналоги, условия, вложения, история, HTTP/WebSocket-чат и подтверждаемая корзина реализованы. |
| AI | `backend/assistant/service.py` подключён к чату; использует OpenAI Responses API, инструменты чтения и проверяемый `AssistantResult`. Ответы после вызова инструментов передаются по частям. Четыре сценария с реальными OpenAI/EKT прошли через HTTP; 10/10 локальных тестов AI прошли. |
| Frontend | React/Vite-проект пока показывает стартовую заглушку. Чата, подключения `/api` и страницы `/cart` нет. |
| Развёртывание | Публичная HTTPS-версия и проверка полного сценария в браузере отсутствуют. |

Проверенный HTTP-путь: вопрос → проверенная карточка/условия/аналог → предложение количества → явное подтверждение → повторная проверка цены и остатка → сохранённая корзина прототипа. Через браузер этот путь ещё не проверен. Схемы API — в [BACKEND_SPEC.md](docs/BACKEND_SPEC.md), архитектурные решения — в [AI_ARCHITECTURE_PLAN.md](docs/AI_ARCHITECTURE_PLAN.md), результаты проверок — в [AI_WORK_REPORT.md](docs/AI_WORK_REPORT.md).

## Структура проекта

```text
backend/  FastAPI, интеграция EKT, AI-сервис, корзина, схема БД и демоданные
frontend/ React/Vite; интерфейс чата ещё предстоит собрать
ai/       диагностические скрипты и сохранённые результаты проверок
tests/    тесты интеграции чата и AI-сервиса
docs/     контракт API, архитектурный план и отчёт
```

Это рабочие зоны трёх участников, а не отдельные сервисы. Локальные `.venv`, `.idea` и `.codex` не входят в приложение. Во время параллельной разработки не переносите модули между зонами без согласования импортов и контрактов.

## Архитектура, технологии и данные

Целевой frontend обращается к FastAPI по `/api` и WebSocket; прокси в Vite пока не настроен. Backend хранит сессии, ограниченную выборку каталога, сообщения, предложения и корзины в PostgreSQL. Свежие детали, цену и остаток он читает из ekt.kz по Basic Auth. AI получает только инструменты чтения и возвращает предложение; корзину меняет лишь серверный обработчик подтверждения. Секреты остаются на backend.

`backend/data/demo_products.json` содержит четыре выбранные карточки из проверки API 23 сентября 2026 года. Это ограниченная выборка, а не полный каталог; цена и остаток в ней не считаются актуальными. `backend/data/purchase_terms.json` содержит подтверждённые сведения об оплате и доставке с предупреждением о противоречивых порогах бесплатной доставки. Источники: [API каталога](https://ekt.kz/api/products), [детальная карточка](https://ekt.kz/api/products/detail?id=515291), [условия покупки](https://ekt.kz/checkout-delivery/).

Технологии: Python 3.12+, FastAPI, SQLAlchemy, asyncpg, PostgreSQL 17, httpx, React, TypeScript, Vite и OpenAI Responses API (`gpt-6-sol`). `compose.yaml` поднимает только PostgreSQL; Nginx и Docker-запуск всего приложения ещё не добавлены. API ekt.kz, модель OpenAI, библиотеки, исходные данные каталога и страницы ekt.kz — внешние материалы, не созданные командой во время хакатона.

## Локальный запуск на Windows

Из корня репозитория, по одной команде в PowerShell. AI-модуль и проверки находятся в официальном репозитории. 23 сентября 2026 года новый клон прошёл создание `.venv`, установку зависимостей, миграцию, импорт демокаталога и четыре реальных HTTP-сценария на отдельной PostgreSQL (порт 25433). WebSocket и JPEG/PDF также прошли сквозные проверки в этом клоне. Пользовательский сценарий в браузере ещё не проверен.

```powershell
docker compose up -d db
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
$env:EKT_API_USERNAME = '<логин от партнёра>'
$env:EKT_API_PASSWORD = '<пароль от партнёра>'
$env:OPENAI_API_KEY = '<серверный ключ OpenAI>'
$env:OPENAI_MODEL = 'gpt-6-sol'
.\.venv\Scripts\python.exe -m backend.migrate
.\.venv\Scripts\python.exe -m backend.import_catalog --demo
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

В другом терминале:

```powershell
cd frontend
npm ci
npm run dev
```

Сейчас `http://localhost:5173` показывает стартовую страницу, а не чат. API доступен отдельно на `http://127.0.0.1:8000`.

`APP_ORIGIN` по умолчанию — `http://localhost:5173`, `DATABASE_URL` соответствует локальному контейнеру. При другом адресе фронтенда установите `APP_ORIGIN` до запуска backend. Для HTTPS нужен `SECURE_COOKIE=true`. `.env.example` перечисляет переменные, но backend сам не загружает `.env`; задавайте значения в окружении процесса. Реальные ключи, пароли и `.env` не добавляйте в Git. Демонстрационный каталог можно загрузить без EKT-ключа, но подтверждение корзины требует живого API ekt.kz.

## Воспроизводимая проверка

После запуска backend: `GET http://127.0.0.1:8000/health/` возвращает `{"status":"ok"}`, `/ready` проверяет PostgreSQL, `/docs` показывает маршруты. `GET /api/products?q=200300285_` ищет по демовыборке. Для мутаций сначала вызовите `POST /api/session`, затем передавайте полученные cookie, `X-CSRF-Token` и корректный `Origin`. `POST /api/cart/proposals` принимает `{"items":[{"product_id":515291,"quantity":"2"}]}`; подтверждение — `POST /api/cart/proposals/{id}/confirm` с `{"confirmed":true}`. Отдельная страница `/cart` во frontend ещё не реализована.

Тесты корзины при работающей БД и созданной схеме:

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_cart -v
.\.venv\Scripts\python.exe -m unittest tests.test_assistant_service tests.test_chat_transport -v
```

AI-диагностика из корня репозитория (первые три команды требуют `OPENAI_API_KEY` и расходуют API-кредиты; последняя требует EKT Basic Auth):

```powershell
python ai\evaluate_hypotheses.py --output ai\reports\openai-gpt-6-sol.json
python ai\evaluate_dialogue.py --output ai\reports\openai-gpt-6-sol-dialogue.json
python ai\measure_ttft.py --output ai\reports\openai-gpt-6-sol-ttft.json
python ai\catalog_probe.py --pages 1 --details 6 --output ai\reports\ekt-catalog-probe.json
```

Сохранённые отчёты находятся в `ai/reports/`. Ранний EKT probe завершился с `partial` и таймаутами. Интеграционный отчёт `backend-ai-live.json` содержит реальные HTTP-ответы backend и длительности отдельных запросов; это ограниченный прогон, не измерение p95.

Живая проверка AI через HTTP без запуска браузера (реальные OpenAI/EKT, расходует API-кредиты):

```powershell
.\.venv\Scripts\python.exe -m ai.verify_backend --prepare-db --output ai/reports/backend-ai-live.json
.\.venv\Scripts\python.exe -m ai.smoke_service
.\.venv\Scripts\python.exe -m ai.smoke_attachments
.\.venv\Scripts\python.exe -m ai.smoke_ws
```

Запускайте эти команды на тестовой БД: `--prepare-db` создаёт схему и импортирует демовыборку; первый скрипт создаёт собственную сессию и удаляет её после проверки. `smoke_service` проверяет несколько потоковых ответов с локальными карточками. `smoke_attachments` загружает публичное JPEG-изображение товара ekt.kz, создаёт тестовый PDF в памяти и проверяет оба файла через чат. `smoke_ws` проверяет реальный поток WebSocket, итоговый текст и карточку товара. Скрипты читают локальный `.env`, сервер по-прежнему использует окружение процесса. Если Windows не позволяет занять порт 5432, для отдельной тестовой БД можно использовать:

```powershell
docker run --detach --name redred-ai-check-db --publish 127.0.0.1:25432:5432 --env POSTGRES_DB=hackalem --env POSTGRES_USER=hackalem --env POSTGRES_PASSWORD=hackalem postgres:17-alpine
$env:DATABASE_URL = 'postgresql+asyncpg://hackalem:hackalem@127.0.0.1:25432/hackalem'
```

AI делает максимум два вызова модели и получает до трёх свежих карточек за сообщение. После вызова инструментов `emit` передаёт части текстового поля из потокового ответа OpenAI; итоговый структурированный результат проверяется отдельно. Для ответа без инструментов текст пока приходит одним событием. Таймаут обычного сообщения остаётся 8 секунд, вложений — 20 секунд. DOCX/XLSX разбираются локально с ограничением объёма; формулы не исполняются. JPEG и PDF передаются модели. Сквозная проверка JPEG дала описание фото без точной привязки к артикулу; из PDF модель извлекла артикул и получила карточку из EKT. Контент вложений отправляется в OpenAI; `store=false` отключает сохранение Responses, но не является обещанием отсутствия всех журналов у провайдера.

Использованные материалы OpenAI Docs: [вызов инструментов](https://developers.openai.com/api/docs/guides/function-calling), [структурированные ответы](https://developers.openai.com/api/docs/guides/structured-outputs), [входные файлы](https://developers.openai.com/api/docs/guides/file-inputs).

## Ограничения

- Корзина принадлежит прототипу: она не изменяет корзину ekt.kz, не резервирует товар и не оформляет заказ. Предоставленный партнёром API доступен только для чтения. Пункт кейса о переходе к оформлению на ekt.kz требует отдельной интеграции.
- Frontend-чат, `/cart` и развёртывание для судей ещё не завершены. Для вложений проверены локальное извлечение DOCX/XLSX и сквозные запросы JPEG/PDF с моделью. По одному фото точный артикул надёжно определить не удалось; требуется уточнение клиента. Старые DOC/XLS не поддерживаются.
- Поиск ограничен импортированной выборкой. Наличие указано по общему остатку без выбора склада. Для неизвестной единицы или шага продажи покупка блокируется.
- Публичные условия доставки содержат два разных порога бесплатной доставки; точное условие нужно подтвердить у продавца. Единая схема сертификатов для всего каталога не установлена.
- URL развёрнутой версии пока отсутствует.
