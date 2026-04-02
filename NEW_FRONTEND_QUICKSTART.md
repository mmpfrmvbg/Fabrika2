# Быстрый старт нового фронтенда

## 🚀 Запуск

### 1. Запустите API сервер (порт 8000)

```bash
cd D:\projects\osnova-fabrika\fabrika2.0\proekt
python -m factory.api_server
```

Или с опцией dashboard:
```bash
python -m factory --dashboard
```

### 2. Откройте новый фронтенд

**Вариант A:** Откройте `factory-os-refactored.html` в браузере через локальный сервер:

```bash
# Python HTTP server
python -m http.server 8080

# Затем откройте http://localhost:8080/factory-os-refactored.html
```

**Вариант B:** Если API сервер запущен с CORS, откройте напрямую:
```
http://localhost:8000/
```

## ⚙️ Конфигурация

### API Base URL

По умолчанию: `http://127.0.0.1:8000`

Для изменения:
1. **В HTML:** добавьте атрибут на `<html>`:
   ```html
   <html lang="ru" data-theme="dark" data-api-base="http://your-server:8000">
   ```

2. **Через JavaScript:** задайте до загрузки скриптов:
   ```html
   <script>
     window.FACTORY_API_BASE = 'http://your-server:8000';
   </script>
   <script type="module" src="/static/js/main.js"></script>
   ```

### API Key (если требуется)

Для мутаций (POST/PUT/DELETE):
```html
<script>
  window.FACTORY_API_KEY = 'your-api-key-here';
</script>
```

## 📊 Реализованные функции

### ✅ Полностью рабочие

| Функция | Статус |
|---------|--------|
| Detail Panel | ✅ |
| Router Context | ✅ |
| Child Task Modal | ✅ |
| FSM Visualization | ✅ |
| API Client | ✅ |
| Store (pub/sub) | ✅ |
| Chat (SSE) | ✅ |
| Tree навигация | ✅ |
| Journal фильтры | ✅ |
| Forge Queue | ✅ |
| Agents page | ✅ |
| Improvements | ✅ |
| HR page | ✅ |
| Failures page | ✅ |
| Judgements page | ✅ |

### ⏳ В процессе

| Функция | Статус |
|---------|--------|
| Dashboard Charts | ⏳ Заглушка |
| Sidebar Quick Jump | ⏳ Не реализовано |
| Nav Badges | ⏳ Не реализовано |
| Vision Pipeline Bar | ⏳ Не реализовано |
| Bulk Actions | ⏳ Не реализовано |

## 🐛 Известные ограничения

1. **Polling:** 5 секунд (настраивается в `main.js`)
2. **Mock mode:** Не реализован (требуется API)
3. **Accessibility:** Focus states не реализованы
4. **Performance:** Нет lazy loading для больших списков

## 📁 Структура файлов

```
proekt/
├── factory-os-refactored.html    # Новый фронтенд
├── static/
│   ├── css/
│   │   └── factory.css
│   └── js/
│       ├── main.js               # Entry point
│       ├── api/
│       │   └── client.js         # API client
│       ├── state/
│       │   └── store.js          # State management
│       └── components/
│           ├── Dashboard.js
│           ├── Tree.js
│           ├── Journal.js
│           ├── Chat.js
│           ├── Forge.js
│           ├── FSM.js
│           ├── DetailPanel.js    # НОВЫЙ
│           ├── ChildTaskModal.js # НОВЫЙ
│           ├── Analytics.js
│           ├── Others.js
│           └── ui.js
└── factory/
    └── legacy/
        ├── frontend_v1.html      # Legacy (не используется)
        └── README.md
```

## 🔧 Разработка

### Добавление нового компонента

1. Создайте файл в `static/js/components/YourComponent.js`
2. Экспортируйте функцию-компонент:
   ```javascript
   export function YourComponent(container) {
     // Логика
     return () => { /* cleanup */ };
   }
   ```
3. Импортируйте в `main.js`
4. Инициализируйте в `initComponents()`

### Работа с Store

```javascript
import { store, subscribe } from '../state/store.js';

// Подписка на изменения
subscribe((state, changes) => {
  console.log('State updated:', state);
});

// Обновление state
store.update({ yourKey: yourValue });

// Чтение state
const { workItems } = store.state;
```

### API вызовы

```javascript
import { api } from '../api/client.js';

// GET
const data = await api.getWorkItems();

// POST
await api.createVision({ title: 'New Vision' });

// PATCH
await api.patchWorkItem(id, { title: 'Updated' });
```

## 📝 Тестирование

### Ручное тестирование

1. Откройте DevTools Console
2. Проверьте отсутствие ошибок
3. Протестируйте:
   - Навигацию между страницами
   - Открытие Detail Panel
   - Создание Child Task
   - Router Context фильтры
   - FSM визуализацию

### Проверка API

```javascript
// В консоли браузера
await window.store.loadWorkItems();
console.log(window.store.state.workItems);
```

## 🆘 Troubleshooting

### "Нет связи с API"

1. Проверьте что API сервер запущен
2. Проверьте порт (по умолчанию 8000)
3. Проверьте CORS настройки

### Компоненты не рендерятся

1. Проверьте Console на ошибки
2. Убедитесь что `main.js` загружен как module
3. Проверьте пути к файлам

### Child Task Modal не открывается

1. Проверьте что HTML контейнер существует
2. Убедитесь что компонент инициализирован
3. Проверьте `window.openChildTaskModalFromComponent`

---

**Обновлено:** 2026-04-02
**Версия:** 2.0 (refactored)
