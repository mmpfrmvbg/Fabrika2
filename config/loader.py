"""Модуль для загрузки конфигурации из JSON файлов."""

import json


def load_config(path):
    """Загружает конфигурацию из JSON файла.

    Args:
        path: Путь к JSON файлу.

    Returns:
        dict: Распарсенное содержимое файла.

    Raises:
        FileNotFoundError: Если файл не существует.
    """
    with open(path, 'r') as f:
        return json.load(f)
