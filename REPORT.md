# Практическая работа №10: Мониторинг и Observability

## Что реализовано

### 1. Метрики — Prometheus + Grafana (8 баллов)

#### `app/metrics.py`
Определены два объекта из библиотеки `prometheus-client`:

- **`REQUEST_COUNT`** — тип `Counter`. Считает общее количество HTTP-запросов.
  Метки (labels): `method` (GET/POST/…), `endpoint` (путь), `status` (200/401/500/…).
  Используется для расчёта **RPS** и **Error Rate**.

- **`REQUEST_LATENCY`** — тип `Histogram`. Записывает время ответа каждого запроса.
  Метки: `method`, `endpoint`.
  Используется для расчёта **p95 latency**.

#### `app/main.py` — `MetricsMiddleware`
Middleware оборачивает каждый запрос: фиксирует время до передачи запроса в эндпоинт и после получения ответа. После каждого запроса:
- увеличивает счётчик `REQUEST_COUNT` (метод, путь, статус-код);
- записывает время выполнения в `REQUEST_LATENCY`.

#### `app/main.py` — эндпоинт `/metrics`
Возвращает все накопленные метрики в текстовом формате Prometheus (text/plain).
Prometheus опрашивает этот эндпоинт каждые 15 секунд.

#### `prometheus.yml`
Конфигурация Prometheus: задание `auth_service`, цель — `host.docker.internal:8000`.
На Linux заменить на `localhost:8000`.

#### `docker-compose.monitoring.yml`
Запускает два контейнера:
- **prometheus** — сбор и хранение метрик (порт 9090);
- **grafana** — визуализация (порт 3000, логин `admin` / пароль `admin`).

#### `grafana/provisioning/`
Grafana при старте автоматически:
- подключает Prometheus как источник данных (`datasources/prometheus.yml`);
- загружает готовый дашборд `auth_service.json` с тремя панелями:
  - **RPS** — запросов в секунду;
  - **Latency p95** — 95-й перцентиль времени ответа;
  - **Error Rate** — доля ответов 5xx от всех запросов.

---

### 2. Структурированные логи (входит в базовые 8 баллов)

#### `app/logging_config.py` — `JSONFormatter`
Каждая запись лога выводится как однострочный JSON:
```json
{
  "timestamp": "2026-05-20T17:05:12.345678+00:00",
  "level": "INFO",
  "logger": "app.main",
  "message": "login attempt",
  "correlation_id": "3f2a1b4c-...",
  "email": "user@example.com"
}
```
Поля `correlation_id` и любые дополнительные значения из `extra` добавляются автоматически. `None`-поля не попадают в вывод.

---

### 3. Correlation ID — +2 балла (дополнительно)

#### `app/main.py` — `CorrelationIDMiddleware`
Для каждого входящего запроса:
1. Берёт `X-Request-ID` из заголовков запроса. Если заголовка нет — генерирует новый UUID.
2. Сохраняет ID в `request.state.correlation_id` (доступно в эндпоинтах).
3. Сохраняет ID в `ContextVar` из `logging_config.py` — `JSONFormatter` берёт его оттуда и добавляет в каждый лог автоматически, без `extra`.
4. Добавляет ID в заголовок ответа `X-Request-ID`.

Благодаря `ContextVar` ID попадает в логи всех уровней (INFO, WARNING, ERROR) без ручной передачи в каждый вызов `logger`.

#### Порядок middleware в `app/main.py`
```python
app.add_middleware(MetricsMiddleware)
app.add_middleware(CorrelationIDMiddleware)
```
В FastAPI/Starlette middleware выполняются в **обратном** порядке добавления, поэтому `CorrelationIDMiddleware` обрабатывается **первой** (устанавливает ID до MetricsMiddleware и эндпоинта).

---

### 4. Тестовые эндпоинты

| Эндпоинт | Что делает |
|----------|-----------|
| `GET /test/error` | Всегда возвращает 500. Нужен для проверки Error Rate в Grafana. |
| `GET /test/slow` | Спит 2 секунды, затем отвечает 200. Нужен для проверки Latency. |

---

## Структура файлов observability

```
auth_service/
├── app/
│   ├── main.py              # MetricsMiddleware, CorrelationIDMiddleware, /metrics, /test/*
│   ├── metrics.py           # REQUEST_COUNT, REQUEST_LATENCY
│   └── logging_config.py    # JSONFormatter, setup_logging, correlation_id_var
├── grafana/
│   └── provisioning/
│       ├── datasources/
│       │   └── prometheus.yml      # автоподключение Prometheus к Grafana
│       └── dashboards/
│           ├── dashboard.yml       # конфиг провайдера дашбордов
│           └── auth_service.json   # дашборд с тремя панелями
├── prometheus.yml                  # конфиг сбора метрик
├── docker-compose.monitoring.yml   # Prometheus + Grafana
└── requirements.txt                # prometheus-client
```

---

## Как запустить

### Шаг 1. Запустить базу данных
```bash
docker compose up -d
```

### Шаг 2. Запустить приложение
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Swagger: http://localhost:8000/docs

### Шаг 3. Запустить Prometheus и Grafana
```bash
docker compose -f docker-compose.monitoring.yml up -d
```

Дашборд появится автоматически. Открыть: http://localhost:3000  
Логин: `admin` / Пароль: `admin`

> **Примечание для Linux:** в `prometheus.yml` замените `host.docker.internal:8000` на `localhost:8000`.

---

## Как проверить

### 1. Метрики приложения отдаются
```bash
curl -s http://localhost:8000/metrics | grep http_requests
```
Ожидаемый вывод — строки вида `http_requests_total{...} 0`.

### 2. Prometheus видит сервис
Открыть http://localhost:9090/targets — статус `auth_service` должен быть **UP**.

Можно выполнить запрос прямо в UI Prometheus:
```
http_requests_total
```

### 3. Дашборд Grafana
Открыть http://localhost:3000/dashboards — папка `General`, дашборд **Auth Service Dashboard**.
Три панели: RPS, Latency p95, Error Rate. Обновляется каждые 10 секунд.

### 4. Нагрузочное тестирование

Выполнить в отдельном терминале (сервис должен быть запущен):

```bash
# Нормальные запросы (20 раз)
for i in {1..20}; do curl -s http://localhost:8000/ > /dev/null; done

# Запросы, вызывающие ошибку 500 (5 раз)
for i in {1..5}; do curl -s http://localhost:8000/test/error > /dev/null; done

# Медленные запросы (3 раза, каждый ~2 секунды)
for i in {1..3}; do curl -s http://localhost:8000/test/slow; done
```

После выполнения (через 1–2 минуты, пока Prometheus сделает scrape) в Grafana:
- **RPS** — вырастет во время цикла;
- **Latency p95** — скачок на 2+ секунды при `/test/slow`;
- **Error Rate** — ненулевое значение (~0.2 = 20%) после `/test/error`.

### 5. Логи в формате JSON
В терминале с `uvicorn` каждая строка — JSON. Пример:
```json
{"timestamp": "2026-05-20T17:05:12.345+00:00", "level": "INFO", "logger": "app.main", "message": "login attempt", "correlation_id": "a1b2c3d4-..."}
```

### 6. Correlation ID
```bash
# Свой ID в запросе — тот же ID вернётся в ответе и появится в логах
curl -v -H "X-Request-ID: my-test-id-123" http://localhost:8000/
```
В заголовках ответа: `X-Request-ID: my-test-id-123`.  
В логе: `"correlation_id": "my-test-id-123"`.

```bash
# Без заголовка — генерируется UUID
curl -v http://localhost:8000/
```
В ответе: `X-Request-ID: <сгенерированный-uuid>`.

---

## PromQL-формулы (для понимания)

| Метрика | Формула | Объяснение |
|---------|---------|-----------|
| RPS | `sum(rate(http_requests_total[1m]))` | `rate()` — скорость роста счётчика за 1 минуту (запросов/сек). `sum()` — суммирует по всем эндпоинтам. |
| Latency p95 | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[1m])) by (le))` | Из гистограммы bucket'ов вычисляет перцентиль: 95% запросов выполняются быстрее этого значения. |
| Error Rate | `sum(rate(http_requests_total{status=~"5.."}[1m])) / sum(rate(http_requests_total[1m]))` | Доля ошибочных (5xx) запросов от общего числа. `=~"5.."` — regex: любой статус 500–599. |

---

## Чек-лист для сдачи

- [ ] `GET /metrics` возвращает текст с метриками Prometheus
- [ ] http://localhost:9090/targets — `auth_service` в статусе **UP**
- [ ] http://localhost:3000 — дашборд **Auth Service Dashboard** с тремя панелями
- [ ] Логи в терминале — каждая строка JSON
- [ ] Выполнены команды нагрузки (нормальные / ошибки / медленные)
- [ ] На графиках видны: рост RPS, пик latency, ненулевой error rate
- [ ] *(+2 балла)* В логах поле `correlation_id`
- [ ] *(+2 балла)* В заголовке ответа `X-Request-ID`
