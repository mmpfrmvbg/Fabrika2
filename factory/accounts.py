"""Совместимость: исторически AccountManager жил в accounts.py.

Фаза 1: контракт требует `factory/config.py` для AccountManager.
"""

from .config import AccountExhaustedError, AccountManager

__all__ = ["AccountExhaustedError", "AccountManager"]
