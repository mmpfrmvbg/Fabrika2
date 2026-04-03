# UI Acceptance Checklist (Final Zero-Stub Sweep)

Цель: единый чеклист приёмки по каждому экрану UI перед релизом.

## 1) Dashboard
- [ ] KPI карточки отображаются без ошибок при пустой аналитике.
- [ ] Таблица `Visions` рендерит как массив `visions` и как `visions.visions`.
- [ ] Journal preview показывает последние 5 записей.
- [ ] Оба чарта (`chart-status`, `chart-activity`) создаются без JS-ошибок.
- [ ] Таблица последних прогонов отображается.

## 2) Journal (Log)
- [ ] Работают фильтры: root/work_item/run/kind/role/search/severity.
- [ ] Пагинация (`←/→`) меняет страницу.
- [ ] Кнопка «Сброс фильтров» очищает все поля.
- [ ] Detail pane открывается/закрывается.

## 3) Tree
- [ ] Expand/Collapse all работают.
- [ ] `hide done` / `hide cancelled` применяются.
- [ ] Run atom запускает `POST /api/work-items/{id}/run`.
- [ ] Bulk archive отправляет `POST /api/bulk/archive`.
- [ ] Router-контекст корректно подсвечивает выбранный узел.

## 4) Forge Queue
- [ ] Список очереди подгружается.
- [ ] Lease bar и таймер рендерятся.
- [ ] File chips (create/modify/delete) отображаются.

## 5) FSM
- [ ] SVG диаграмма рендерится.
- [ ] Таблица transitions загружается.
- [ ] Клик по FSM-статусу переводит на Tree и применяет `FACTORY_TREE_STATUS_FILTER`.

## 6) Agents / Judgements / Failures / Improvements / HR
- [ ] Все страницы открываются из sidebar без ошибок.
- [ ] Таблицы/карточки заполняются данными API.
- [ ] На пустом API-ответе нет падения рендера.

## 7) Autonomous Mode
- [ ] Отображается активный Vision и прогресс.
- [ ] ETA считается от реального `created_at` vision (если есть).
- [ ] Пауза/продолжить переключает оркестратор через store actions.
- [ ] Кнопка «Спросить Qwen» открывает чат.

## 8) Vision Creator
- [ ] Модалка открывается и закрывается.
- [ ] `submit` создаёт vision и запускает auto-decompose best-effort.
- [ ] Оценка сложности обновляется без сетевых вызовов и без ошибок.

## 9) Global UX
- [ ] Connection banner показывает offline/online.
- [ ] Toast уведомления работают для success/error.
- [ ] Переключение между Autonomous/Developer режимами стабильно.
- [ ] Нет `TODO`/`заглушка` комментариев в `static/js/autonomous/*`, `VisionCreator`, `FSM`, `AutonomousMode`, `main.js`.
