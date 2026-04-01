"""Схемы валидации входных данных API.

Модуль предоставляет схемы и функции для валидации входных данных API.
При невалидных данных выбрасывается ValueError (HTTP 400).

Version: 1.1.1
"""

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, ValidationError

# Константы для стабильных сообщений об ошибках
ERROR_NAME_TYPE = "Поле 'name' должно быть строкой"
ERROR_NAME_EMPTY = "Имя не может быть пустым"
ERROR_NAME_FORMAT = "Имя должно содержать только буквы, цифры и пробелы"
ERROR_VALUE_TYPE = "Поле 'value' должно быть целым числом"
ERROR_VALUE_RANGE = "Значение должно быть от 0 до 1000"
ERROR_DATA_TYPE = "Входные данные должны быть словарем"
ERROR_DATA_EMPTY = "Входные данные не могут быть пустыми"

# Паттерн для валидации имени
NAME_PATTERN = re.compile(r'^[a-zA-Z0-9а-яА-ЯёЁ\s]+$')


class RequestInput(BaseModel):
    """Модель валидации входного запроса."""

    name: str = Field(..., min_length=1, max_length=100, description="Имя")
    value: int = Field(..., ge=0, le=1000, description="Значение")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Any) -> str:
        """Валидация имени: только буквы, цифры и пробелы."""
        if not isinstance(v, str):
            raise ValueError(ERROR_NAME_TYPE)
        stripped = v.strip()
        if not stripped:
            raise ValueError(ERROR_NAME_EMPTY)
        if not NAME_PATTERN.match(stripped):
            raise ValueError(ERROR_NAME_FORMAT)
        return stripped

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: Any) -> int:
        """Валидация значения: целое число от 0 до 1000."""
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(ERROR_VALUE_TYPE)
        if v < 0 or v > 1000:
            raise ValueError(ERROR_VALUE_RANGE)
        return v


def validate_request(data: dict) -> dict:
    """
    Валидация входных полей запроса.

    Args:
        data: Словарь с данными запроса.

    Returns:
        Валидированные данные.

    Raises:
        ValueError: При невалидных данных (возвращает 400).
    """
    if not isinstance(data, dict):
        raise ValueError(ERROR_DATA_TYPE)

    if not data:
        raise ValueError(ERROR_DATA_EMPTY)

    try:
        validated = RequestInput(**data)
        return validated.model_dump()
    except ValidationError as e:
        if e.errors():
            first_error = e.errors()[0]
            field = first_error.get("loc", ["поле"])[0]
            msg = first_error.get("msg", "Ошибка валидации")
            raise ValueError(f"Ошибка валидации: {field} - {msg}")
        raise ValueError("Ошибка валидации")
    except Exception as e:
        raise ValueError(f"Ошибка валидации: {str(e)}")
