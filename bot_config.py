"""
Конфігурація бота з .env / змінних оточення.
Імпортуйте build_config() після зміни os.environ (наприклад у тестах — reload модуля bot_clean).
"""
from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

from bot_utils import parse_admin_user_ids

_ROOT = Path(__file__).resolve().parent
_ENV_FILE = _ROOT / ".env"


def load_dotenv_if_present() -> None:
    if not _ENV_FILE.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(_ENV_FILE)
        if _ENV_FILE.stat().st_size == 0:
            print(
                "Увага: файл .env порожній на диску — збережіть його в редакторі (Ctrl/Cmd+S).",
                file=sys.stderr,
            )
    except ImportError:
        warnings.warn(
            "Знайдено .env — встановіть python-dotenv (pip install python-dotenv), інакше змінні з файлу не завантажаться.",
            stacklevel=1,
        )


load_dotenv_if_present()


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    admin_user_ids: tuple[int, ...]
    admin_chat_id: int
    smtp_server: str
    smtp_port: int
    smtp_user: str
    smtp_password: str


def build_config(environ: dict[str, str] | None = None) -> BotConfig:
    """Зчитує поточне оточення (за замовчуванням os.environ)."""
    env = environ if environ is not None else os.environ
    token = (env.get("BOT_TOKEN") or "").strip()
    raw_target = (env.get("TARGET_CHAT_ID") or "").strip()
    try:
        admin_chat_id = int(raw_target) if raw_target else 0
    except ValueError:
        admin_chat_id = 0
    ids = parse_admin_user_ids(env.get("ADMIN_USER_IDS", ""))
    return BotConfig(
        bot_token=token,
        admin_user_ids=tuple(ids),
        admin_chat_id=admin_chat_id,
        smtp_server=env.get("SMTP_SERVER", "smtp.gmail.com"),
        smtp_port=int(env.get("SMTP_PORT", "587")),
        smtp_user=env.get("SMTP_USER", ""),
        smtp_password=env.get("SMTP_PASSWORD", ""),
    )


def admin_delivery_configured(cfg: BotConfig) -> bool:
    """Є куди доставити заявку: приватні адміни і/або група."""
    return bool(cfg.admin_user_ids) or bool(cfg.admin_chat_id)
