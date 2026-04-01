# Unified Journal (единый операционный журнал)

## Зачем

Фабрика пишет факты в разные таблицы SQLite (`event_log`, `run_steps`, `file_changes`, `comments`, …). Для человека и для будущих агентов (Судья, Архитектор, HR) нужна **одна хронологическая лента** с предсказуемым форматом строк и стабильными ключами, без новой write-таблицы и без дублирования записей в БД.

Read-model строится **на чтении** (projection): те же данные, что уже пишут FSM, forge и оркестратор.

## Источники

| Источник | `source_type` | Примечание |
|----------|---------------|------------|
| `event_log` | `event` | FSM, очередь, run lifecycle, forge-аудит и т.д. |
| `run_steps` | `run_step` | Шаги прогона (`step_kind`, `payload`) |
| `file_changes` | `file_change` | Путь, `change_type`, хеши, `diff_summary` |
| `comments` | `comment` | `source_id` с префиксом `c:{id}` |
| `architect_comments` | `comment` | `source_id` `a:{id}`, `kind` = `architect_comment`, `payload.journal_origin` |
| `decisions` | `comment` | `source_id` `d:{id}`, `kind` = `decision`, роль из `decision_role` |

Таблица `runs` **не дублируется** отдельными строками: границы прогона отражаются через `event_log` и `run_steps` / `file_changes`.

## Формат записи (unified entry)

Каждая строка ответа API:

| Поле | Описание |
|------|----------|
| `ts` | Время (ISO из БД) |
| `source_type` | `event` \| `comment` \| `run_step` \| `file_change` |
| `source_id` | Идентификатор в источнике (с префиксами для комментариев) |
| `source_key` | Стабильный ключ `source_type:source_id` |
| `work_item_id` | Задача или `null` |
| `run_id` | Прогон или `null` |
| `kind` | Нормализованный тип (`transition`, `forge_started`, `file_change`, `run_step_llm_reply`, …) |
| `title` | Короткий заголовок |
| `summary` | Текст для ленты |
| `status_before` / `status_after` | Если извлекаются из payload события FSM |
| `role` | Роль (`forge`, `judge`, …) или `null` |
| `path` | Путь файла для `file_change` |
| `payload` | Полный JSON-объект без потери полей исходной строки |

## Сортировка и tie-break

- Канон: **`ts` DESC** (новые сверху).
- При равном `ts`: ранг `source_type` (comment → event → file_change → run_step), затем **`source_id` DESC** (строковое сравнение).

## Фильтры API

`GET /api/journal` (read-only):

- `work_item_id` — задача и связанные прогоны (как для `event_log`).
- `run_id` — всё по прогону.
- `root_id` — **поддерево** от любого узла (рекурсия по `parent_id`), не только vision.
- `kind` — точное совпадение нормализованного `kind`.
- `role` — без учёта регистра.
- `limit` (1…500), `offset`.

Дополнительные алиасы:

- `GET /api/work-items/<id>/journal`
- `GET /api/runs/<id>/journal`
- `GET /api/root/<id>/journal` — то же, что `root_id` (поддерево от якоря).

Ответ: `{ items, total, limit, offset, order, sort }`.

## Ограничения

- Пагинация с большим `offset` опирается на выборку до `cap` строк **из каждой** таблицы (`min(50000, offset+limit+2000)`). Если в одной таблице очень много записей, теоретически возможны пропуски на дальних страницах; для типичных объёмов фабрики этого достаточно.
- При фильтрах `kind` / `role` подсчёт `total` проходит по объединённым строкам (до 100k на источник).

## База для Судьи / Архитектора / HR

- Стабильный `source_key` и полный `payload` дают **воспроизводимый контекст**.
- Фильтры по задаче, поддереву и прогону позволяют узко вытаскивать релевантные фрагменты для LLM без обхода всех таблиц вручную.
- Один контракт ленты упрощает политики («что видит Судья») и регрессионные проверки.
