# 🚀 Factory OS — Инструкция по запуску

## ✅ Предварительные требования

1. Python 3.10+
2. Установленные зависимости: `pip install -r requirements.txt`
3. Настроенный `.env` файл с API ключами Qwen

---

## 📡 Запуск (ДВА сервера одновременно)

### Терминал 1: API сервер (FastAPI)

```bash
cd D:\projects\osnova-fabrika\fabrika2.0\proekt
python -m factory.api_server
```

**Ожидаемый вывод:**
```
Factory read-only API: http://127.0.0.1:8000  DB=...\factory.db
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000
```

**Не закрывать!**

### Терминал 2: Статический сервер (HTTP)

```bash
cd D:\projects\osnova-fabrika\fabrika2.0\proekt
python -m http.server 8080
```

**Ожидаемый вывод:**
```
Serving HTTP on :: port 8080 (http://[::]:8080/) ...
```

**Не закрывать!**

### Шаг 3: Открыть Factory OS в браузере

```
http://localhost:8080/factory-os-refactored.html
```

---

## 🔧 Как это работает

| Сервер | Порт | Что делает |
|--------|------|------------|
| **API** | `8000` | FastAPI — обрабатывает POST/GET запросы к `/api/*` |
| **Статика** | `8080` | Python HTTP — раздаёт HTML, CSS, JS файлы |

**UI загружается с `:8080`, но все API вызовы идут на `:8000`** (настроено в `static/js/api/client.js`).

**CORS настроен** — API сервер разрешает запросы с `localhost:8080`.

---

## 🧪 Проверка работы

### 1. Проверить API (в отдельном терминале)

```bash
curl http://127.0.0.1:8000/api/stats
```

Ожидаемый ответ: JSON со статистикой фабрики.

### 2. Проверить UI

Откройте DevTools в браузере (F12) → Console.

**Ожидаемые сообщения:**
```
[Factory] Initializing...
[Factory] Initial data loaded
[Factory] Initialized
```

### 3. Проверить Network

DevTools → Network → отфильтруйте по `api/`:

- ✅ `http://127.0.0.1:8000/api/journal` — 200 OK
- ✅ `http://127.0.0.1:8000/api/work-items/tree` — 200 OK
- ✅ `http://127.0.0.1:8000/api/visions` — 200 OK

---

## ⚠️ Troubleshooting

### Ошибка: `API Error: 404 Not Found` на всех запросах

**Причина:** API сервер не запущен.

**Решение:**
1. Откройте **Терминал 1**
2. Запустите: `python -m factory.api_server`
3. Дождитесь `Uvicorn running on http://127.0.0.1:8000`

### Ошибка: `501 Not Implemented`

**Причина:** Статический сервер пытается обработать POST запрос.

**Решение:** Убедитесь что:
1. API сервер запущен на `:8000`
2. `static/js/api/client.js` содержит `const API_BASE = 'http://127.0.0.1:8000';`
3. UI открыт через `http://localhost:8080/factory-os-refactored.html`

### Ошибка: `ReferenceError: showPage is not defined`

**Причина:** `main.js` не загрузился.

**Решение:**
1. Проверьте консоль браузера на ошибки
2. Убедитесь что `<script type="module" src="/static/js/main.js">` в HTML
3. Проверьте что файл `/static/js/main.js` существует

### Ошибка: `database is locked`

**Причина:** Другой процесс держит базу данных.

**Решение:**
```bash
# Завершить все Python процессы
taskkill /F /IM python.exe

# Запустить API сервер заново
python -m factory.api_server
```

---

## 📚 Структура проекта

```
proekt/
├── factory/
│   ├── api_server.py         # FastAPI сервер (порт 8000)
│   ├── chat_service.py       # Chat SSE сервис
│   └── ...
├── static/
│   ├── css/
│   │   └── factory.css       # Стили
│   └── js/
│       ├── api/
│       │   └── client.js     # API клиент (API_BASE = 'http://127.0.0.1:8000')
│       ├── state/
│       │   └── store.js      # State management
│       ├── components/       # UI компоненты
│       └── main.js           # Точка входа
├── factory-os.html           # Оригинальный UI
└── factory-os-refactored.html # Модульный UI
```

---

## 🎯 Что работает

- ✅ Dashboard (KPI, Visions, Journal)
- ✅ Tree задач (раскрытие, фильтры)
- ✅ Journal (фильтры, пагинация)
- ✅ Analytics (KPI, charts)
- ✅ Forge Queue
- ✅ FSM (таблица переходов)
- ✅ Agents
- ✅ Improvements
- ✅ Judgements
- ✅ **Chat с Qwen** (SSE стриминг)
- ✅ Orchestrator controls (start/stop/tick)

---

## 📞 Поддержка

При возникновении проблем:
1. Проверьте что **оба сервера** запущены
2. Проверьте логи API сервера (Терминал 1)
3. Проверьте консоль браузера (F12)
4. Убедитесь что БД не заблокирована
