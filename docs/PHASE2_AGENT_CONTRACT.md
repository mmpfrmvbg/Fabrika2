# Фабрика 2.0 — контракт агентов Фазы 2 (согласован с кодом `factory/`)

Источник правды по поведению: реализация в `Fabrika2.0/proekt/factory/` (докстринги модулей, `schema_ddl.py`, оркестратор). Этот текст **подогнан под код**, а не наоборот.

## 1. Судья (Judge) — два разных события FSM

В `state_transitions` заданы **два** события из состояния `ready_for_judge`:

| Событие | Кому применимо (kind) | Целевой статус | Guard / действие |
|--------|------------------------|----------------|------------------|
| `judge_approved` | `atom`, `atm_change` | `ready_for_work` | `guard_has_files_declared` → `action_enqueue_forge` |
| `judge_approved_for_planning` | `vision`, `initiative`, `epic`, `story`, `task` | `planned` | `action_notify_planner` |

Код `factory/agents/judge.py` **только выбирает имя события** по `kind` и вызывает `StateMachine.apply_transition`. Это полноценный путь через FSM, не «одно событие judge_approved для всех».

## 2. Планировщик (Planner) stub и `work_item_files`

Текущий stub создаёт **один** дочерний epic без файлов и не доводит дерево до **atom**. Это **осознанное ограничение** сценария, не баг.

- Guard `guard_has_files_declared` на **st_05** срабатывает для **атомов**, когда Судья применяет `judge_approved`.
- Полный e2e до `done` требует либо полной декомпозиции до atom с заполнением `work_item_files`, либо **фикстурного** сценария «готовый atom с файлами» (см. `--e2e-golden` / `--e2e-chain` в CLI).

## 3. Ревьюер и эскалация: `review_failed` и `sent_to_judge`

- **Ревьюер** при отказе вызывает **только** `review_failed` (переход в `review_rejected`, st_15). Второго перехода у агента нет.
- **`sent_to_judge`** (st_16, `review_rejected` → `ready_for_judge`) применяет **оркестратор** сразу после обработки очереди `REVIEW_INBOX` в том же `tick()` — метод `_escalate_review_rejected_to_judge()`, актор `Role.ORCHESTRATOR`.

Так разделены ответственность агента (одно решение на прогон) и системной маршрутизации.

## 4. Два пути к Архитектору / Судье

Развести явно:

1. **Инициатива Архитектора** (новая задача «с нуля»): в сидере FSM — `architect_submitted` из `draft` → `ready_for_judge`, действие **`action_notify_judge`** (очередь `judge_inbox`). Это **не** `action_notify_architect`.

2. **Планировочный поток** (дети после декомпозиции): после `planner_decomposed` планировщик уведомляет детей через **`action_notify_architect`** → очередь `architect_inbox` (см. `factory/actions.py`).

## 5. База данных в dev / песочнице

При изменении DDL или seed-данных в `schema_ddl.py` проще всего **удалить файл БД** (например `factory.db`) и дать приложению создать схему заново. Отдельные миграции для Fabrika2.0-песочницы **не обязательны** на текущей стадии; позже — при необходимости.

## 6. Транзакции: FSM и прогон агента

- `StateMachine.apply_transition` фиксирует переход в **своей** транзакции (commit внутри).
- Запись прогона (`runs`, `run_steps`, `review_checks` и т.д.) и переход FSM — **последовательные коммиты**, если не используется один общий внешний транзакционный контур (см. комментарий в `factory/agents/_helpers.py`).
- Для **Кузницы** связка «переход + создание run + lease» уже завязана на **`action_start_forge_run`** в рамках одного применения перехода — это эталон для атомарности исполнения forge.
- Для reviewer / judge / planner / architect полная ACID «прогон + переход» зафиксирована как **плановый долг**, не как текущий инвариант.

- **Кузница (`action_start_forge_run`)**: строка в `runs` создаётся **до** вставки `file_locks`, иначе при включённых FK нарушается ссылка `file_locks.run_id → runs.id` (исправлено в коде).

## 7. Кузница (Forge) и QWEN-CODE-CLI

- **Реальный агент:** `factory/qwen_cli_runner.run_qwen_cli` вызывает бинарник Qwen с `cwd` = песочница forge (`factory/forge_sandbox.py`); полный текст задачи — `factory/forge_prompt.build_forge_prompt`. После выхода CLI сравнивается baseline и файлы в песочнице → `file_changes`, шаги `file_write`.
- **Канонический режим передачи промпта для реального редактирования файлов:** промпт в **stdin** (`FACTORY_QWEN_PROMPT_VIA=stdin`, по умолчанию в раннере), в терминах CLI это вызов вида `qwen … -p -` с телом промпта на stdin. Режим **argv** (`-p` в аргументах процесса) для forge не считается каноном: длинные промпты и ограничение длины командной строки на Windows.
- **Неинтерактивный subprocess:** без подтверждений пользователем раннер добавляет параметры, без которых CLI часто отвечает текстом без записи на диск (см. `--approval-mode`, `--max-session-turns` в `factory/qwen_cli_runner.py` и `FACTORY_QWEN_EXTRA_ARGS`).
- **MVP-контур с реальным diff:** при `FACTORY_QWEN_DRY_RUN=0` сценарий `python -m factory --e2e-qwen-wet-edit` проверяет, что по объявленному файлу есть запись в `file_changes` (modify) и шаг `file_write`, то есть изменение зафиксировано как артефакт, а не только stdout модели.
- **Wet без артефакта:** если CLI завершился успешно (`ForgeResult.ok`), но после сравнения с baseline нет изменений ни по одному пути с `intent=modify` из `work_item_files`, forge-run завершается как **failed**, событие `run.failed.forge_no_artifact`, переход `forge_failed` (не `forge_completed`). Сухой прогон (`FACTORY_QWEN_DRY_RUN=1`) по-прежнему использует placeholder в песочнице, чтобы был diff.

## E2E: золотой путь

См. `python -m factory --e2e-golden` (только ревью → `done`) и `python -m factory --e2e-chain` (судья → симуляция forge → ревью → `done`) в каталоге `proekt/`.

## Использование этого документа как промпта для внешнего агента

Этот файл можно **вставлять целиком или по разделам** в system-промпт LLM-агента (Планировщик, Судья, Ревьюер и т.д.), чтобы поведение совпадало с кодом. Правила:

1. **Не дублировать** отдельные «промпты Фазы 2» в других репозиториях — держать один канон: этот документ + при необходимости ссылка на конкретный модуль (`factory/agents/judge.py` и т.д.).
2. **Для узкой роли** достаточно вынести в промпт только соответствующий раздел (например, §1 для Судьи, §2 для Планировщика) и одну строку в начале: «Источник правды — реализация в `factory/`, расхождения трактовать в пользу кода».
3. **После изменений FSM/DDL** сначала обновлять код и этот документ, затем прогонять `--e2e-golden` и `--e2e-chain`.

4. Машиночитаемая привязка ролей к §: [`AGENT_PROMPT_CONFIG.yaml`](AGENT_PROMPT_CONFIG.yaml) — общий `meta_header` и поле `section_ids` на роль (planner, architect, judge, reviewer, forge, orchestrator).

## 8. Judge verdicts (`JudgeVerdict`)

**Роль Судьи:** проверить work item (атом, история, эпик и т.д.) по набору **guard-правил** (политики качества, область, согласованность ограничений) и выдать **одно** строго типизированное решение. Реализация guard-логики может быть в коде, в YAML или в LLM; **межагентный контракт** на границе «модель/раннер → FSM» — только структура `JudgeVerdict` ниже.

**Формат ответа:** Судья (или заглушка, имитирующая модель) обязан вернуть **STRICT JSON**, соответствующий схеме `JudgeVerdict`. Любой ответ, который не удаётся разобрать как JSON и валидировать по модели, считается **ошибкой исполнения**: FSM **не** продолжает штатный сценарий «как при успехе»; фиксируется сбой прогона и событие уровня «невалидный выход судьи» (см. код: `judge.invalid_output`).

### 8.1. Поля `JudgeVerdict`

Обязательные:

| Поле | Тип | Описание |
|------|-----|----------|
| `item` | `string` | Ссылочный идентификатор проверяемого объекта, например `atom:wi_abc`, `story:wi_xyz`. |
| `verdict` | `"approved"` \| `"rejected"` | Итог проверки. |
| `checked_guards` | `string[]` | Имена guard-правил, которые были **запущены** на этом прогоне. |
| `all_passed` | `boolean` | `true` только если **все** перечисленные в `checked_guards` правила прошли успешно. |
| `context_refs` | `string[]` | Опциональные ссылки на контекст: `run:…`, `event:…` (может быть пустым массивом). |
| `next_event` | `string` | Имя **следующего события FSM**, которое оркестратор должен применить к work item, например `judge_approved`, `judge_approved_for_planning`, `judge_rejected`. Должно быть согласовано с `verdict` и с `kind`/состоянием в FSM. |

Дополнительно (наблюдаемость, §10):

| Поле | Тип | Описание |
|------|-----|----------|
| `used_event_log` | `boolean` (по умолчанию `false`) | Судья отметил, что учитывал **окно** последних событий единого журнала по данному `work_item` (см. §10). Не влияет на FSM. |

Дополнительно при `verdict === "rejected"` (ожидаются заполненными):

| Поле | Тип | Описание |
|------|-----|----------|
| `failed_guards` | `string[]` | Подмножество `checked_guards` — какие правила не прошли. |
| `rejection_reason_code` | `string` | Короткий машинный код причины: `too_broad`, `out_of_scope`, `conflicting_constraints`, … |
| `suggested_action` | `string` | Рекомендуемое действие для автора/планировщика. Значения вроде `split`, `clarify`, `drop`, `replan` образуют **кандидат на будущий enum**; на текущем этапе допускается любая непустая строка по смыслу. |

При `verdict === "approved"` поля `failed_guards`, `rejection_reason_code`, `suggested_action` могут отсутствовать или быть `null` в транспорте JSON.

### 8.2. Примеры JSON

Одобрение (атом → дальше `judge_approved`):

```json
{
  "item": "atom:wi_atom_001",
  "verdict": "approved",
  "checked_guards": ["guard_has_files_declared", "scope_ok"],
  "all_passed": true,
  "context_refs": ["run:run_judge_01"],
  "next_event": "judge_approved",
  "used_event_log": true
}
```

(`used_event_log` опционально; при отсутствии трактуется как `false`.)

Отклонение:

```json
{
  "item": "story:wi_story_02",
  "verdict": "rejected",
  "checked_guards": ["scope_ok", "decomposition_sane"],
  "all_passed": false,
  "context_refs": [],
  "next_event": "judge_rejected",
  "failed_guards": ["decomposition_sane"],
  "rejection_reason_code": "too_broad",
  "suggested_action": "split"
}
```

### 8.3. Связь с §1

Имена в `next_event` должны совпадать с событиями FSM из §1 (`judge_approved`, `judge_approved_for_planning`, `judge_rejected`). Несогласованный `next_event` при валидном JSON обрабатывается как ошибка валидации на стыке с FSM (см. реализацию).

## 9. Review results (`ReviewResult`)

**Роль ревьюера:** после прогона кузницы оценить артефакты (изменения файлов, шаги прогона, политики) и выдать **одно** строго типизированное решение. Автоматические `review_checks` и LLM-слой могут питать решение; **межагентный контракт** на границе «модель/раннер → FSM» — только структура `ReviewResult`.

**Формат ответа:** ревьюер обязан вернуть **STRICT JSON**, соответствующий схеме `ReviewResult`. Невалидный ответ (не JSON, не проходит Pydantic, несогласованный с FSM) — **ошибка исполнения**: FSM **не** получает штатный переход «успех/отказ по ревью»; фиксируется сбой прогона и событие `review.invalid_output` (см. код).

**Согласование с FSM:** в seed переходы из `in_review` называются **`review_passed`** (→ `done`) и **`review_failed`** (→ `review_rejected`). В поле `next_event` **обязательно** используются именно эти имена: для `verdict === "approved"` — только `review_passed`, для `verdict === "rejected"` — только `review_failed`. Синонимы вроде `review_approved` / `review_rejected` в JSON **не** принимаются.

**Поле `run_id`:** идентификатор прогона **кузницы** (`runs.id` последнего успешного `implement`-прогона по данному work item), который ревьюируется. В JSON допускается число — при разборе оно приводится к строке. Если отдельного forge-run в БД нет (упрощённые сценарии вроде `--e2e-golden` только с ревью), заглушка использует стабильное значение `seed:<work_item_id>`; при наличии реального implement-run значение **должно** с ним совпадать (проверка в коде).

### 9.1. Поля `ReviewResult`

| Поле | Тип | Описание |
|------|-----|----------|
| `item` | `string` | Ссылка на объект, например `atom:wi_abc`. |
| `run_id` | `string` (в JSON может быть number → string) | `runs.id` прогона кузницы под ревью (см. выше). |
| `verdict` | `"approved"` \| `"rejected"` | Итог ревью. |
| `checked_artifacts` | `string[]` | Что проверялось (`file_changes`, `run_steps`, `sandbox_diff`, …). |
| `all_passed` | `boolean` | `true` только если все проверки прошли. |
| `issues` | `Issue[]` | Список замечаний; при `approved` — без элементов с `severity: "high"`. |
| `context_refs` | `string[]` | Ссылки `run:…`, `event:…`. |
| `next_event` | `string` | `review_passed` или `review_failed` — строго по `verdict`. |

**`Issue`:**

| Поле | Тип |
|------|-----|
| `code` | `string` |
| `severity` | `"low"` \| `"medium"` \| `"high"` |
| `message` | `string` |

Правила согласованности (дублируются в коде): при `approved` — `all_passed === true`, нет issues с `severity === "high"`. При `rejected` — `all_passed === false`, хотя бы один элемент в `issues`.

### 9.2. Примеры JSON

Одобрение:

```json
{
  "item": "atom:wi_atom_001",
  "run_id": "run_forge_01",
  "verdict": "approved",
  "checked_artifacts": ["file_changes", "run_steps", "sandbox_diff"],
  "all_passed": true,
  "issues": [],
  "context_refs": ["run:run_forge_01", "event:334"],
  "next_event": "review_passed"
}
```

Отклонение:

```json
{
  "item": "atom:wi_atom_001",
  "run_id": "run_forge_01",
  "verdict": "rejected",
  "checked_artifacts": ["file_changes", "run_steps"],
  "all_passed": false,
  "issues": [
    {
      "code": "scope_exceeded",
      "severity": "high",
      "message": "Changes exceed declared atom scope"
    }
  ],
  "context_refs": ["run:run_forge_01"],
  "next_event": "review_failed"
}
```

## 10. Судья и окно единого журнала (наблюдаемость)

**Цель:** судья принимает решение не только по «сырому» выходу прогона, но с учётом **краткого контекста** из единого журнала фабрики по **текущему** `work_item`.

**Не тянем** весь глобальный лог: в промпт (и в артефакты прогона) попадает **окно** последних **N** событий (по умолчанию **20**), отфильтрованных по `work_item_id` и связанным `run_id` этого work item.

**Источник данных:** таблица `event_log` (и при необходимости — unified read-model `/api/journal` с тем же фильтром).

**Поля событий в окне (стабильный контракт для промпта):**

| Поле | Смысл |
|------|--------|
| `event_time` | Метка времени (ISO). |
| `actor_role` | Роль источника: `forge`, `judge`, `reviewer`, `orchestrator`, … |
| `event_type` | Значение из whitelist `EventType` / строка из журнала. |
| `message` | Короткое текстовое сообщение (одна строка в листинге промпта). |

**Формат блока в промпте** (заголовок фиксирован):

```markdown
### Recent factory events for this work item

- [timestamp] [actor] [event_type]: short message
- ...
```

**JudgeVerdict:** опциональное поле `used_event_log` — `true`, если модель/раннер реально опирался на этот блок при формировании JSON (см. §8.1).
