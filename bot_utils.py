"""
Чисті функції та константи для бота: тестування без Telegram API.
"""
from __future__ import annotations

import html
import re
from typing import Any, MutableMapping, Optional

# Мінімальна довжина тексту звернення (символів після strip)
MIN_APPEAL_TEXT_LENGTH = 15

def is_appeal_text_valid(text: str) -> bool:
    return len((text or "").strip()) >= MIN_APPEAL_TEXT_LENGTH

def is_confirm_yes(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in ("так", "yes", "y", "да", "ok", "ок", "✅ так", "✅")

def is_confirm_no(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in ("ні", "no", "n", "не", "❌ ні", "❌")

# --- Стани сценарію (user_data['flow']) ---
FLOW_IDLE = ""
FLOW_TICKET_TEXT = "ticket_text"
FLOW_TICKET_PHONE = "ticket_phone"
FLOW_TICKET_PHONE_CONFIRM = "ticket_phone_confirm"
FLOW_QUESTIONS = "ticket_questions"
FLOW_TICKET_CONFIRM = "ticket_confirm"

# --- Ключі user_data ---
KEY_FLOW = "flow"
KEY_TICKET_DRAFT = "ticket_draft"

# --- Ключ bot_data ---
KEY_ADMIN_POST_TO_USER = "admin_post_to_user"  # legacy (група): message_id -> user_id
KEY_RELAY_PRIVATE = "relay_private"  # "admin_id:message_id" -> client_user_id
KEY_PENDING_TICKETS = "pending_tickets"  # ticket_id -> { client_id, admin_msgs, group_notify?, ... }
KEY_PENDING_GROUP_NOTIFY = "group_notify"  # (chat_id, message_id) — копія заявки в групі (лише інфо)

# --- Кнопки (узгоджені між обробниками) ---
BTN_CANCEL_FLOW = "❌ Скасувати заявку"
BTN_SHARE_CONTACT_LABEL = "📱 Надіслати свій номер"
BTN_SKIP_PHONE = "⏭ Пропустити"

SERVICES_LIST = [
    "🧠 Психологічний супровід",
    "🏥 Реабілітація та мед супровід",
    "🚨 Оперативні питання",
    "📄 Статус УБД/інвалідність внаслідок війни",
    "🏛️ ВЛК і МСЕК (тепер - експертні команди з оцінювання функціонування)",
    "💰 Одноразова грошова допомога (ОГД) за поранення або загибель",
    "💳 Грошове забеспечення та борги військової частини",
    "🏠 Житло",
    "🏥 Соціальні пільги та виплати",
    "🎓 Пенсія",
    "🛒 Знижки для захисників в місті Одеса",
    "❓ Інше",
    "🏸 Більярд",
    "🏓 Настільний теніс",
    "🏹 Стрільба з лука",
    "🤾 Піклбол",
]

SERVICE_QUESTIONS = {
    "⚖️ Правова допомога": [
        "Опишіть правову проблему (коротко)",
        "Чи є у вас документи, які треба проаналізувати? (так/ні)",
    ],
    "🏅 Адаптивний спорт": [
        "Який вид спорту вас цікавить?",
        "Чи маєте ви медичні обмеження? (так/ні)",
    ],
    "🏥 Реабілітація та мед супровід": [
        "Опишіть, яку реабілітацію чи медичну підтримку ви шукаєте",
        "Чи є у вас вже діагностовані захворювання? (так/ні)",
    ],
    "🛒 Знижки для захисників в місті Одеса": [
        "Які саме знижки вас цікавлять? (транспорт, ресторани, тощо)",
    ],
    "🧠 Психологічний супровід": ["Опишіть, яка саме підтримка вам потрібна"],
    "🚨 Оперативні питання": ["Опишіть питання, яке треба вирішити"],
    "📄 Статус УБД/інвалідність внаслідок війни": ["Укажіть ваш статус та які документи потрібні"],
    "🏛️ ВЛК і МСЕК (тепер - експертні команди з оцінювання функціонування)": ["Опишіть, яку допомогу ви шукаєте"],
    "💰 Одноразова грошова допомога (ОГД) за поранення або загибель": ["Опишіть ваш випадок та потрібну суму"],
    "💳 Грошове забеспечення та борги військової частини": ["Опишіть вашу фінансову ситуацію"],
    "🏠 Житло": ["Опишіть вашу потребу у житлі"],
    "🏥 Соціальні пільги та виплати": ["Яка саме пільга вас цікавить?"],
    "🎓 Пенсія": ["Укажіть ваш вік та тип пенсії"],
    "❓ Інше": ["Опишіть ваше питання"],
    "🏸 Більярд": ["Бажаєте забронювати час чи просто дізнатися деталі?"],
    "🏓 Настільний теніс": ["Укажіть ваш рівень підготовки"],
    "🏹 Стрільба з лука": ["Чи маєте ви власний лук?"],
    "🤾 Піклбол": ["Коли вам зручно відвідати тренування?"],
}

# Усі підписи кнопок меню (щоб не приймати їх за текст заявки)
MENU_LABELS = frozenset(
    {
        "⚖️ Правова допомога",
        "🚨 Оперативні питання",
        "🏅 Адаптивний спорт",
        "🧠 Психологічний супровід",
        "🏥 Реабілітація та мед супровід",
        "🛒 Знижки для захисників в місті Одеса",
        "❓ Інше",
        "⬅️ Назад",
        "🏸 Більярд",
        "🏓 Настільний теніс",
        "🏹 Стрільба з лука",
        "🤾 Піклбол",
        BTN_CANCEL_FLOW,
        BTN_SKIP_PHONE,
        *SERVICES_LIST,
    }
)

def escape_html(text: str) -> str:
    return html.escape(text or "", quote=True)

def parse_admin_user_ids(raw: str) -> list[int]:
    """ADMIN_USER_IDS=123,456,789 → [123, 456, 789]"""
    if not raw or not raw.strip():
        return []
    out: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out

def relay_private_key(admin_id: int, message_id: int) -> str:
    return f"{admin_id}:{message_id}"

def relay_bind_private(
    bot_data: MutableMapping[str, Any],
    admin_id: int,
    message_id: int,
    client_user_id: int,
) -> None:
    m: dict[str, int] = bot_data.setdefault(KEY_RELAY_PRIVATE, {})
    m[relay_private_key(admin_id, message_id)] = client_user_id

def format_user_line_html(user_id: int, full_name: str, username: Optional[str]) -> str:
    name = escape_html(full_name or "—")
    lines = [f"👤 Від: {name}"]
    if username:
        un = escape_html(username)
        lines.append(f"🔗 @{un} — <a href=\"https://t.me/{username}\">t.me/{un}</a>")
    else:
        lines.append("\n⚠️ У профілі немає публічного нікнейму (@username)")
        lines.append(
            f"\n🔗 <a href=\"tg://user?id={user_id}\">Посилання для зв'язку в Telegram</a>"
        )
    lines.append(f"🆔 ID: <code>{user_id}</code>")
    return "\n".join(lines)

def digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def validate_ua_phone(raw: str) -> bool:
    """
    Приймає номери виду +380XXXXXXXXX, 380XXXXXXXXX, 0XXXXXXXXX (9 цифр після 0).
    """
    d = digits_only(raw)
    if len(d) == 12 and d.startswith("380"):
        rest = d[3:]
        return len(rest) == 9 and rest.isdigit()
    if len(d) == 10 and d.startswith("0"):
        return d[1:].isdigit() and len(d[1:]) == 9
    return False

def build_ticket_admin_html(
    category: str,
    user_id: int,
    full_name: str,
    username: Optional[str],
    body: str,
    phone: Optional[str],
) -> str:
    user_block = format_user_line_html(user_id, full_name, username)
    body_esc = escape_html(body)
    cat_esc = escape_html(category)
    parts = [
        f"📩 <b>НОВА ЗАЯВКА: \n{cat_esc}</b>",
        "",
        user_block,
        "",
        f"📝 <b>Текст звернення:</b>",
        body_esc,
    ]
    if phone:
        parts.extend(["", f"📱 <b>Телефон:</b> <code>{escape_html(phone)}</code>"])
    parts.extend(
        [
            "",
        ]
    )
    return "\n".join(parts)

def relay_bind_admin_message(
    bot_data: MutableMapping[str, Any],
    admin_message_id: int,
    user_id: int,
) -> None:
    """
    Legacy: одна група — message_id → user_id.
    """
    mapping: dict[int, int] = bot_data.setdefault(KEY_ADMIN_POST_TO_USER, {})
    mapping[admin_message_id] = user_id

def register_ticket_admin_post(
    bot_data: MutableMapping[str, Any],
    admin_message_id: int,
    *,
    relay_admin_id: int,
    user_id: int,
    category: str,
    full_name: str,
    username: Optional[str],
    body: str,
    phone: Optional[str],
) -> str:
    """Приватний relay (admin user id + message_id → клієнт) + HTML поста."""
    relay_bind_private(bot_data, relay_admin_id, admin_message_id, user_id)
    return build_ticket_admin_html(
        category, user_id, full_name, username, body, phone
    )
