from datetime import datetime, timedelta
import logging
from bot_config import build_config

logger = logging.getLogger(__name__)
_CFG = build_config()

# Параметри з конфігурації або за замовчуванням
SPAM_WINDOW = timedelta(seconds=600)  # 10 хвилин
MAX_TICKETS = 5

def is_allowed(user_id: int, user_data: dict) -> bool:
    """Перевіряє, чи не перевищив користувач ліміт заявок."""
    now = datetime.utcnow()
    timestamps = user_data.get("_ticket_timestamps", [])
    
    # Фільтруємо старі timestamps
    timestamps = [t for t in timestamps if now - t < SPAM_WINDOW]
    
    if len(timestamps) >= MAX_TICKETS:
        logger.warning("User %s blocked by anti-spam", user_id)
        return False
    
    timestamps.append(now)
    user_data["_ticket_timestamps"] = timestamps
    return True
