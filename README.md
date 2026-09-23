# HackAlem — прототип помощника по каталогу ЕКТ

FastAPI хранит гостевые сессии, небольшую выборку товаров ekt.kz, историю сообщений, вложения, предложения и корзины в PostgreSQL. Основной сценарий backend: найти товар по артикулу → создать предложение с количеством → явно подтвердить его → получить сохранённую корзину и ссылку `/cart`. Предложение само по себе корзину не меняет. Подтверждение заново проверяет цену и общий остаток в API ЕКТ; вся запись проходит одной транзакцией. Изменившаяся цена, недостаток товара или недоступность ЕКТ отклоняют подтверждение.

## Устройство и зависимости

- `backend/main.py` — FastAPI, маршруты `/api`, `/health/` и `/ready`; OpenAPI доступен на `/docs`.
- `backend/catalog.py` и `backend/ekt_client.py` — нормализация, поиск по локальной выборке и серверные запросы к ЕКТ с Basic Auth.
- `backend/sessions.py`, `backend/chat.py`, `backend/attachments.py`, `backend/cart.py` — гостевая сессия, чат, файлы и подтверждаемая корзина.
- `backend/models.py`, `backend/migrate.py` — схема PostgreSQL и команда её начального создания.
- `backend/data/` — четыре выбранные карточки из проверки 23 сентября 2026 года и факты об оплате/доставке. Поиск охватывает только загруженную выборку; запрос карточки по неизвестному ID обращается к ЕКТ напрямую.

Нужны Python 3.12+, PostgreSQL 17 и пакеты из `backend/requirements.txt`. `compose.yaml` запускает локальную БД для разработки. OpenAI-модуль поставляется отдельным участником в `backend/assistant/`; если он отсутствует или не настроен, произвольный диалог возвращает `ASSISTANT_UNAVAILABLE`. Серверные сценарии точного артикула и корзины можно проверять без него.

## Запуск на Windows

Все команды ниже выполняются по одной строке в PowerShell. Репозиторий расположен в `C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred`; при другом размещении замените абсолютный путь. Заполните переменные `EKT_API_USERNAME` и `EKT_API_PASSWORD` из серверных учётных данных; не помещайте их во фронтенд и не коммитьте `.env`. Настройки перечислены в `.env.example`, но файл автоматически не загружается: задайте значения через `$env:EKT_API_USERNAME='...'` и `$env:EKT_API_PASSWORD='...'` в том же PowerShell перед запуском. Если ЕКТ отвечает медленно, увеличьте `EKT_API_TIMEOUT_SECONDS` (по умолчанию 2.5); общий лимит подтверждения составляет 8 секунд. `APP_ORIGIN` должен точно совпадать с origin фронтенда, а для HTTPS нужен `SECURE_COOKIE=true`.

1. `docker compose -f 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\compose.yaml' up -d db`
2. `py -3.12 -m venv 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv'`
3. `& 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv\Scripts\python.exe' -m pip install -r 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\requirements.txt'`
4. `Set-Location 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred'; & 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv\Scripts\python.exe' -m backend.migrate`
5. `Set-Location 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred'; & 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv\Scripts\python.exe' -m backend.import_catalog --demo`
6. `Set-Location 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred'; & 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv\Scripts\python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`

Для другой БД задайте `DATABASE_URL=postgresql+asyncpg://...` в окружении перед командами 4–6. Каталог можно расширить ограниченным live-импортом: `Set-Location 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred'; & 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv\Scripts\python.exe' -m backend.import_catalog --max-products 100`. Верхний предел — 500 карточек; импорт прекращается при повторе страницы или отсутствии новых ID. Истёкшие гостевые сессии и вложения удаляются командой `Set-Location 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred'; & 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv\Scripts\python.exe' -m backend.cleanup` (её нужно запускать периодически).

Для фронтенда нужен прокси `/api` и WebSocket Upgrade на этот backend, а также страница `/cart`, читающая `GET /api/cart` с той же cookie. Перед изменяющим запросом фронтенд вызывает `POST /api/session`, хранит выданный `csrf_token` и посылает `X-CSRF-Token`. Cookie HttpOnly, токен сессии не помещается в URL. Формат HTTP и WebSocket описан в `docs/BACKEND_SPEC.md`.

## Проверка и ограничения

После запуска `GET /ready` проверяет PostgreSQL, а `GET /api/products?q=200300285_` — поиск по демовыборке. Для проверки предложения нужны действующий гостевой cookie, `Origin` и CSRF-заголовок; `POST /api/cart/proposals` принимает `{"items":[{"product_id":515291,"quantity":"2"}]}`, затем `POST /api/cart/proposals/{id}/confirm` принимает `{"confirmed":true}`. Подтверждение требует действующих учётных данных ЕКТ. Прямой read-only запрос детали товара 515291 в API ЕКТ был проверен 23 сентября 2026 года; цена и остаток могут измениться.

На локальной PostgreSQL проверены миграция, импорт четырёх карточек, WebSocket/HTTP чат с историей и текстовым подтверждением, а также три точечных теста корзины: два одновременных подтверждения добавляют товар один раз, недостаток остатка в одной строке отклоняет всё предложение, изменившаяся цена отклоняет подтверждение. Реальный путь через API приложения с ЕКТ тоже проверен при `EKT_API_TIMEOUT_SECONDS=7`: карточка 21449 вернула шесть ссылок на документы, для 19457 найден кандидат 21449; предложение не меняло пустую корзину, подтверждение добавило две единицы, повтор вернул тот же результат. Запрос карточки занял 5875 мс, подтверждение — 5555 мс в этой проверке. Повторить тест корзины: `Set-Location 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred'; & 'C:\Users\Daniyar\Documents\HackAlem\hack-63e62cb7-redred\backend\.venv\Scripts\python.exe' -m unittest backend.tests.test_cart -v` (при работающей БД и уже созданной схеме).

Корзина принадлежит прототипу: она не изменяет корзину ekt.kz, не резервирует товар, не создаёт заказ и не принимает оплату. Наличие указано по общему остатку без выбора склада. Для неизвестных единиц и шага продажи покупка заблокирована; они настроены только для проверенных демотоваров. Данные каталога могут содержать противоречия. На странице ЕКТ указаны два разных порога бесплатной доставки — 30 000 и 15 000 ₸; приложение сохраняет предупреждение, а окончательное условие нужно уточнять у продавца. Источники: `https://ekt.kz/api/products`, `https://ekt.kz/api/products/detail`, `https://ekt.kz/checkout-delivery/`. В текущей среде наблюдался разброс времени ответа API ЕКТ от 0.365 до 5.5 секунды; целевая задержка полного диалога не измерена. Начальная команда миграции создаёт схему прототипа; для будущих изменений модели нужны отдельные миграции.
