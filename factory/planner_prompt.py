"""Промпт LLM для декомпозиции Vision → epic → story → task → atom."""

from __future__ import annotations

from typing import Optional


def build_planner_prompt(
    vision_title: str,
    vision_description: str,
    creator_comments: Optional[str] = None,
    project_context: Optional[str] = None,
) -> str:
    comments = (creator_comments or "").strip()
    ctx = (project_context or "").strip()
    parts = [
        "Ты — Планировщик фабрики. Твоя задача — декомпозировать Vision (цель Создателя) "
        "в структурированное дерево задач для последующей работы кузницы (forge). "
        "Ты не пишешь код и не вызываешь инструменты — только возвращаешь один JSON-объект.",
        "",
        "## Входные данные",
        f"vision_title: {vision_title}",
        f"vision_description:\n{vision_description or '(none)'}",
    ]
    if comments:
        parts.extend(["", f"creator_comments:\n{comments}"])
    if ctx:
        parts.extend(["", f"project_context:\n{ctx}"])
    parts.extend(
        [
            "",
            "## Правила декомпозиции",
            "",
            "- Vision → 1–5 Epics (крупные направления работы).",
            "- Каждый Epic → 1–4 Stories (пользовательские сценарии / фичи).",
            "- Каждая Story → 1–5 Tasks (технические задачи).",
            "- Каждый Task → 1–3 Atoms (минимальная единица работы для кузницы).",
            "",
            "Каждый Atom ОБЯЗАН содержать:",
            "- title — что конкретно сделать;",
            "- description — как именно, какой ожидаемый результат;",
            "- files — непустой список объектов с полями:",
            '  path (относительный путь в репозитории),',
            '  action — одно из: create | modify | read,',
            "  details — кратко, что менять или зачем читать файл.",
            "",
            "Atom = одно изменение за один вызов AI-агента в кузнице. Если задача больше — разбей на несколько атомов.",
            "Соблюдай порядок зависимостей: если atom B зависит от A, A должен идти раньше в массиве atoms своего Task.",
            "",
            "## Формат ответа",
            "",
            "Ответ СТРОГО одним JSON-объектом (без Markdown, без пояснений до или после):",
            "",
            "{",
            '  "epics": [',
            "    {",
            '      "title": "...",',
            '      "description": "...",',
            '      "stories": [',
            "        {",
            '          "title": "...",',
            '          "description": "...",',
            '          "tasks": [',
            "            {",
            '              "title": "...",',
            '              "description": "...",',
            '              "atoms": [',
            "                {",
            '                  "title": "...",',
            '                  "description": "...",',
            '                  "files": [',
            '                    {"path": "...", "action": "create|modify|read", "details": "..."}',
            "                  ]",
            "                }",
            "              ]",
            "            }",
            "          ]",
            "        }",
            "      ]",
            "    }",
            "  ]",
            "}",
            "",
            "Требования к JSON: валидный UTF-8 JSON, двойные кавычки для строк, без комментариев и без trailing запятых.",
        ]
    )
    return "\n".join(parts)
