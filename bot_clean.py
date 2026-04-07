import logging
import os
import smtplib
from pathlib import Path

_env_file = Path(__file__).resolve().parent / ".env"
if _env_file.is_file():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_file)
        if _env_file.stat().st_size == 0:
            import sys

            print(
                "Увага: файл .env порожній на диску — збережіть його в редакторі (Ctrl/Cmd+S).",
                file=sys.stderr,
            )
    except ImportError:
        import warnings

        warnings.warn(
            "Знайдено .env — встановіть python-dotenv (pip install python-dotenv), інакше змінні з файлу не завантажаться.",
            stacklevel=1,
        )
from email.mime.text import MIMEText
from secrets import token_hex

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot_utils import (
    BTN_CANCEL_FLOW,
    BTN_INLINE_END,
    BTN_INLINE_TAKE,
    BTN_LIVE_ADMIN,
    BTN_SHARE_CONTACT_LABEL,
    KEY_FLOW,
    KEY_PENDING_TICKETS,
    KEY_RELAY_PRIVATE,
    KEY_SUPPORT_SESSIONS,
    KEY_TICKET_DRAFT,
    TICKET_MODE_AUTO_CLAIM,
    TICKET_MODE_ONE_SHOT_USERNAME,
    TICKET_MODE_THREADED,
    FLOW_IDLE,
    FLOW_LIVE_REQUEST,
    FLOW_TICKET_PHONE,
    FLOW_TICKET_PHONE_CONFIRM,
    FLOW_TICKET_TEXT,
    MENU_LABELS,
    MIN_APPEAL_TEXT_LENGTH,
    SERVICES_LIST,
    build_live_request_admin_html,
    build_ticket_admin_html,
    escape_html,
    is_appeal_text_valid,
    is_confirm_no,
    is_confirm_yes,
    parse_admin_user_ids,
    relay_bind_private,
    relay_private_key,
    validate_ua_phone,
)

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = '8283530471:AAFsFFhFB9gfzGKVbZztDIYe9sDsOGWsBEg'
TARGET_CHAT_ID = '-1003791029029'  # ID чата, куда бот будет пересылать сообщения (пример)
try:
    ADMIN_CHAT_ID = int(TARGET_CHAT_ID)
except ValueError:
    ADMIN_CHAT_ID = 0

ADMIN_USER_IDS = parse_admin_user_ids(os.environ.get("ADMIN_USER_IDS", ""))

SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def _configure_logging(bot_token: str) -> None:
    """
    Прибирає з консолі рядки httpx на кожен запит (HTTP 200 OK до Telegram API)
    і маскує токен бота, якщо він раптом потрапляє в повідомлення логера.
    """
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if not bot_token:
        return

    class _RedactTokenFilter(logging.Filter):
        def __init__(self, token: str) -> None:
            super().__init__()
            self._token = token

        def filter(self, record: logging.LogRecord) -> bool:
            if record.args:
                record.args = tuple(
                    a.replace(self._token, "<BOT_TOKEN>") if isinstance(a, str) else a for a in record.args
                )
            if isinstance(record.msg, str):
                record.msg = record.msg.replace(self._token, "<BOT_TOKEN>")
            return True

    for handler in logging.getLogger().handlers:
        handler.addFilter(_RedactTokenFilter(bot_token))


_configure_logging(BOT_TOKEN)
logger = logging.getLogger(__name__)

keyboard_main = [
    [KeyboardButton("Правова допомога"), KeyboardButton("Оперативні питання")],
    [KeyboardButton("Адаптивний спорт"), KeyboardButton("Психологічний супровід")],
    [KeyboardButton("Реабілітація та мед супровід"), KeyboardButton("Знижки для захисників в місті Одеса")],
    [KeyboardButton("Інше")],
    # [KeyboardButton(BTN_LIVE_ADMIN)],
]
markup_main = ReplyKeyboardMarkup(keyboard_main, resize_keyboard=True)

keyboard_services = [
    [KeyboardButton("Статус УБД/інвалідність внаслідок війни")],
    [KeyboardButton("ВЛК і МСЕК (тепер - експертні команди з оцінювання функціонування)")],
    [KeyboardButton("Одноразова грошова допомога (ОГД) за поранення або загибель")],
    [KeyboardButton("Грошове забеспечення та борги військової частини")],
    [KeyboardButton("Житло"), KeyboardButton("Соціальні пільги та виплати")],
    [KeyboardButton("Пенсія"), KeyboardButton("⬅️ Назад")],
]
markup_services = ReplyKeyboardMarkup(keyboard_services, resize_keyboard=True)

keyboard_health = [
    [KeyboardButton("Більярд"), KeyboardButton("Настільний теніс")],
    [KeyboardButton("Стрільба з лука"), KeyboardButton("Піклбол")],
    [KeyboardButton("⬅️ Назад")],
]
markup_health = ReplyKeyboardMarkup(keyboard_health, resize_keyboard=True)

markup_phone = ReplyKeyboardMarkup(
    [
        [KeyboardButton(BTN_SHARE_CONTACT_LABEL, request_contact=True)],
        [KeyboardButton(BTN_CANCEL_FLOW)],
    ],
    resize_keyboard=True,
)


def ticket_inline_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(BTN_INLINE_TAKE, callback_data=f"claim:{ticket_id}")],
            [InlineKeyboardButton(BTN_INLINE_END, callback_data=f"close:{ticket_id}")],
        ]
    )


def take_only_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    """Лише «Взяти» — для заявки за темою з @username."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BTN_INLINE_TAKE, callback_data=f"claim:{ticket_id}")]]
    )


def end_only_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(BTN_INLINE_END, callback_data=f"close:{ticket_id}")]])


def send_email(subject, body, to_email):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = to_email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        logger.info("Email відправлено на %s", to_email)
        return True
    except Exception as e:
        logger.exception("Помилка email: %s", e)
        return False


def clear_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(KEY_FLOW, None)
    context.user_data.pop(KEY_TICKET_DRAFT, None)


def current_flow(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get(KEY_FLOW) or FLOW_IDLE


def find_session_by_ticket(bot_data: dict, ticket_id: str) -> tuple[int | None, dict | None]:
    sessions: dict = bot_data.get(KEY_SUPPORT_SESSIONS, {})
    for client_id, data in list(sessions.items()):
        if isinstance(data, dict) and data.get("ticket_id") == ticket_id:
            return int(client_id), data
    return None, None


async def broadcast_ticket_to_admins(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    ticket_id: str,
    html_text: str,
    client_id: int,
    ticket_mode: str,
) -> bool:
    pending = context.bot_data.setdefault(KEY_PENDING_TICKETS, {})
    admin_msgs: dict[int, int] = {}
    if ticket_mode == TICKET_MODE_ONE_SHOT_USERNAME:
        kb = take_only_keyboard(ticket_id)
    elif ticket_mode == TICKET_MODE_AUTO_CLAIM:
        kb = None
    else:
        kb = ticket_inline_keyboard(ticket_id)
    targets = list(ADMIN_USER_IDS)
    if ADMIN_CHAT_ID:
        targets.append(ADMIN_CHAT_ID)
    for target in targets:
        try:
            m = await context.bot.send_message(
                chat_id=target,
                text=html_text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            admin_msgs[target] = m.message_id
        except Exception as e:
            logger.warning("Не вдалося надіслати звернення адміну %s: %s", target, e)
    if not admin_msgs:
        return False
    pending[ticket_id] = {
        "client_id": client_id,
        "admin_msgs": admin_msgs,
        "html": html_text,
        "mode": ticket_mode,
    }
    return True


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    data = q.data
    if data.startswith("claim:"):
        await handle_claim(update, context)
    elif data.startswith("close:"):
        await handle_close(update, context)
    else:
        await q.answer()


async def handle_claim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    ticket_id = q.data.split(":", 1)[1]
    if q.from_user.id not in ADMIN_USER_IDS:
        await q.answer("Доступ лише для адміністраторів.", show_alert=True)
        return
    pending_map: dict = context.bot_data.get(KEY_PENDING_TICKETS, {})
    entry = pending_map.get(ticket_id)
    if not entry:
        await q.answer("Це звернення вже недійсне або взято іншим адміністратором.", show_alert=True)
        return
    client_id = entry["client_id"]
    admin_msgs: dict[int, int] = entry["admin_msgs"]
    html_text = entry["html"]
    mode = entry.get("mode", TICKET_MODE_THREADED)
    taker_id = q.from_user.id

    recipient_chat_id = q.message.chat.id
    if recipient_chat_id not in admin_msgs:
        await q.answer("Це звернення не належить цьому чату або не отримано вами.", show_alert=True)
        return

    if mode == TICKET_MODE_AUTO_CLAIM:
        await q.answer(
            "Це звернення без кнопок: натисніть «Відповісти» на пост із заявкою, щоб взяти його в роботу.",
            show_alert=True,
        )
        return

    del pending_map[ticket_id]
    winner_mid = admin_msgs[recipient_chat_id]
    relay_bind_private(context.bot_data, taker_id, winner_mid, client_id)

    if mode == TICKET_MODE_ONE_SHOT_USERNAME:
        taken_footer = (
            "\n\n✅ <b>Ви взяли це звернення.</b> Відповідь клієнту — лише через «Відповісти» на це повідомлення."
        )
        for aid, mid in admin_msgs.items():
            try:
                if aid == taker_id:
                    await context.bot.edit_message_text(
                        chat_id=aid,
                        message_id=mid,
                        text=html_text + taken_footer,
                        parse_mode="HTML",
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
                else:
                    await context.bot.edit_message_text(
                        chat_id=aid,
                        message_id=mid,
                        text=html_text + "\n\n<i>Звернення взято іншим адміністратором.</i>",
                        parse_mode="HTML",
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
            except Exception as e:
                logger.warning("Не вдалося оновити повідомлення %s/%s: %s", aid, mid, e)
        await q.answer("Звернення призначено вам.")
        try:
            await context.bot.send_message(
                chat_id=client_id,
                text="Вашу заявку прийняв адміністратор. Відповідь буде в цьому чаті з ботом.",
            )
            await context.bot.send_message(chat_id=client_id, text="Користуйтеся меню нижче:", reply_markup=markup_main)
        except Exception as e:
            logger.exception("Повідомлення клієнту після claim: %s", e)
        return

    sessions = context.bot_data.setdefault(KEY_SUPPORT_SESSIONS, {})
    sessions[client_id] = {
        "admin_id": taker_id,
        "ticket_id": ticket_id,
        "thread_with_buttons": True,
    }

    taken_footer = (
        f"\n\n✅ <b>Ви взяли це звернення.</b> Відповідайте клієнту через «Відповісти» на це повідомлення "
        f"або на наступні від бота в цьому чаті."
    )
    for aid, mid in admin_msgs.items():
        try:
            if aid == taker_id:
                await context.bot.edit_message_text(
                    chat_id=aid,
                    message_id=mid,
                    text=html_text + taken_footer,
                    parse_mode="HTML",
                    reply_markup=end_only_keyboard(ticket_id),
                    disable_web_page_preview=True,
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=aid,
                    message_id=mid,
                    text=html_text + "\n\n<i>Звернення взято іншим адміністратором. Дії не потрібні.</i>",
                    parse_mode="HTML",
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.warning("Не вдалося оновити повідомлення %s/%s: %s", aid, mid, e)

    await q.answer("Звернення призначено вам.")
    try:
        await context.bot.send_message(
            chat_id=client_id,
            text="Ваше звернення прийняв адміністратор. Ви можете писати повідомлення — їх отримає саме він. "
            "Завершити розмову: кнопка «Завершити звернення», /завершити або /finish.",
            reply_markup=end_only_keyboard(ticket_id),
        )
        await context.bot.send_message(chat_id=client_id, text="Меню:", reply_markup=markup_main)
    except Exception as e:
        logger.exception("Повідомлення клієнту після claim: %s", e)


async def handle_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    ticket_id = q.data.split(":", 1)[1]
    uid = q.from_user.id
    pending_map: dict = context.bot_data.get(KEY_PENDING_TICKETS, {})
    sessions: dict = context.bot_data.setdefault(KEY_SUPPORT_SESSIONS, {})

    entry = pending_map.get(ticket_id)
    client_id: int | None = None
    assigned_admin: int | None = None

    if entry:
        client_id = entry["client_id"]
        admin_msgs = entry["admin_msgs"]
        html_text = entry["html"]
        if uid != client_id and uid not in ADMIN_USER_IDS:
            await q.answer("Немає прав завершити це звернення.", show_alert=True)
            return
        del pending_map[ticket_id]
        done = "\n\n<i>Звернення завершено.</i>"
        for aid, mid in admin_msgs.items():
            try:
                await context.bot.edit_message_text(
                    chat_id=aid,
                    message_id=mid,
                    text=html_text + done,
                    parse_mode="HTML",
                    reply_markup=None,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning("Редагування після close: %s", e)
        await q.answer("Звернення завершено.")
        try:
            if uid == client_id:
                for aid in admin_msgs:
                    await context.bot.send_message(
                        chat_id=aid,
                        text="Клієнт завершив звернення (ще до призначення адміністратора).",
                    )
            else:
                await context.bot.send_message(
                    chat_id=client_id,
                    text="Адміністратор завершив звернення до того, як його було призначено. За потреби надішліть нове через меню.",
                    reply_markup=markup_main,
                )
        except Exception as e:
            logger.warning("Сповіщення після close (pending): %s", e)
        return

    c_id, sess = find_session_by_ticket(context.bot_data, ticket_id)
    if c_id is None or not sess:
        await q.answer("Це звернення вже закрите або недійсне.", show_alert=True)
        return
    client_id = c_id
    assigned_admin = sess.get("admin_id")
    if uid != client_id and uid != assigned_admin:
        await q.answer("Завершити може лише клієнт або адміністратор, який взяв звернення.", show_alert=True)
        return

    sessions.pop(client_id, None)
    await q.answer("Звернення завершено.")

    if uid == client_id:
        try:
            if assigned_admin:
                await context.bot.send_message(
                    chat_id=assigned_admin,
                    text="Клієнт натиснув «Завершити звернення». Діалог у цьому тикеті закрито.",
                )
            await context.bot.send_message(
                chat_id=client_id,
                text="Звернення завершено. За потреби створіть нове через меню.",
                reply_markup=markup_main,
            )
        except Exception as e:
            logger.exception("Після close (клієнт): %s", e)
    else:
        try:
            await context.bot.send_message(
                chat_id=client_id,
                text="Адміністратор завершив звернення. За потреби ви можете надіслати нове через меню.",
                reply_markup=markup_main,
            )
        except Exception as e:
            logger.exception("Після close (адмін): %s", e)


def _client_has_open_support(context: ContextTypes.DEFAULT_TYPE, client_id: int) -> bool:
    if client_id in context.bot_data.get(KEY_SUPPORT_SESSIONS, {}):
        return True
    for e in context.bot_data.get(KEY_PENDING_TICKETS, {}).values():
        if isinstance(e, dict) and e.get("client_id") == client_id:
            return True
    return False


async def finalize_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    phone: str | None,
) -> None:
    user = update.effective_user
    if user and _client_has_open_support(context, user.id):
        await update.effective_message.reply_text(
            "У вас уже є активне звернення або заявка в очікуванні. Завершіть його командою /завершити або /finish "
            "(або кнопкою «Завершити звернення», якщо вона є), /cancel, або дочекайтеся відповіді адміністратора.",
            reply_markup=markup_main,
        )
        return
    if not ADMIN_USER_IDS:
        clear_flow(context)
        await update.effective_message.reply_text(
            "Неможливо доставити звернення: не налаштовано список адміністраторів (ADMIN_USER_IDS). "
            "Зверніться до підтримки сервісу.",
            reply_markup=markup_main,
        )
        return
    draft = context.user_data.get(KEY_TICKET_DRAFT) or {}
    category = draft.get("service_name", "Загальне питання")
    body = draft.get("message", "")
    if not user:
        return
    ticket_id = token_hex(8)
    html_text = build_ticket_admin_html(
        category,
        user.id,
        user.full_name or "",
        user.username,
        body,
        phone,
    )
    ticket_mode = TICKET_MODE_ONE_SHOT_USERNAME if user.username else TICKET_MODE_AUTO_CLAIM
    try:
        ok = await broadcast_ticket_to_admins(
            context,
            ticket_id=ticket_id,
            html_text=html_text,
            client_id=user.id,
            ticket_mode=ticket_mode,
        )
        if not ok:
            raise RuntimeError("no admin reachable")
    except Exception as e:
        logger.exception("Не вдалося надіслати заявку адмінам: %s", e)
        await update.effective_message.reply_text(
            "Не вдалося надіслати звернення. Спробуйте ще раз за кілька хвилин або натисніть /cancel і почніть спочатку.",
            reply_markup=markup_main,
        )
        return

    clear_flow(context)
    thanks = (
        "Дякуємо! Заявку отримано й передано адміністраторам.\n\n"
    )
    if ticket_mode == TICKET_MODE_ONE_SHOT_USERNAME:
        await update.effective_message.reply_text(thanks)
    else:
        await update.effective_message.reply_text(
            thanks + "\n\n",
        )
    await context.bot.send_message(user.id, "Користуйтеся меню нижче:", reply_markup=markup_main)


async def finalize_live_request(update: Update, context: ContextTypes.DEFAULT_TYPE, body: str) -> None:
    user = update.effective_user
    if user and _client_has_open_support(context, user.id):
        await update.effective_message.reply_text(
            "У вас уже є активне звернення або заявка в очікуванні. Завершіть попереднє: натисніть команду /finish ",
            reply_markup=markup_main,
        )
        return
    if not ADMIN_USER_IDS:
        clear_flow(context)
        await update.effective_message.reply_text(
            "Неможливо надіслати запит: не налаштовано ADMIN_USER_IDS.",
            reply_markup=markup_main,
        )
        return
    if not user:
        return
    ticket_id = token_hex(8)
    html_text = build_live_request_admin_html(
        user.id,
        user.full_name or "",
        user.username,
        body,
    )
    live_mode = TICKET_MODE_THREADED if user.username else TICKET_MODE_AUTO_CLAIM
    try:
        ok = await broadcast_ticket_to_admins(
            context,
            ticket_id=ticket_id,
            html_text=html_text,
            client_id=user.id,
            ticket_mode=live_mode,
        )
        if not ok:
            raise RuntimeError("no admin reachable")
    except Exception as e:
        logger.exception("Не вдалося надіслати запит: %s", e)
        await update.effective_message.reply_text(
            "Не вдалося надіслати запит. Спробуйте ще раз за кілька хвилин.",
            reply_markup=markup_main,
        )
        return

    clear_flow(context)
    if live_mode == TICKET_MODE_THREADED:
        await update.effective_message.reply_text(
            "Запит передано адміністраторам.\n\n",
            reply_markup=end_only_keyboard(ticket_id),
        )
    else:
        await update.effective_message.reply_text(
            "Запит передано адміністраторам.\n\n"
            "",
        )
    await context.bot.send_message(user.id, "Користуйтеся меню нижче:", reply_markup=markup_main)


async def cmd_finish_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скидає очікування / діалог (клієнт або адмін)."""
    uid = update.effective_user.id
    if not update.message:
        return
    pending_map: dict = context.bot_data.setdefault(KEY_PENDING_TICKETS, {})
    sessions: dict = context.bot_data.setdefault(KEY_SUPPORT_SESSIONS, {})

    if uid not in ADMIN_USER_IDS:
        removed_pending = [
            tid for tid, e in list(pending_map.items()) if isinstance(e, dict) and e.get("client_id") == uid
        ]
        for tid in removed_pending:
            entry = pending_map.pop(tid, None)
            if entry:
                html_text = entry.get("html", "")
                for aid, mid in entry.get("admin_msgs", {}).items():
                    try:
                        await context.bot.edit_message_text(
                            chat_id=aid,
                            message_id=mid,
                            text=html_text + "\n\n",
                            parse_mode="HTML",
                            reply_markup=None,
                            disable_web_page_preview=True,
                        )
                    except Exception as e:
                        logger.warning("edit after /finish: %s", e)
                for aid in entry.get("admin_msgs", {}):
                    try:
                        await context.bot.send_message(
                            chat_id=aid,
                            text=".",
                        )
                    except Exception as e:
                        logger.warning("notify admin: %s", e)
        sess = sessions.pop(uid, None)
        if sess:
            aid = sess.get("admin_id")
            if aid:
                try:
                    await context.bot.send_message(
                        chat_id=aid,
                        text="",
                    )
                except Exception as e:
                    logger.warning("notify admin session: %s", e)
        if removed_pending or sess:
            await update.message.reply_text("Звернення скинуто.", reply_markup=markup_main)
        else:
            await update.message.reply_text("Немає активного звернення для завершення.", reply_markup=markup_main)
        return

    client_to_notify: int | None = None
    for cid, s in list(sessions.items()):
        if isinstance(s, dict) and s.get("admin_id") == uid:
            client_to_notify = int(cid)
            sessions.pop(cid, None)
            break
    if client_to_notify is not None:
        try:
            await context.bot.send_message(
                chat_id=client_to_notify,
                text="",
                reply_markup=markup_main,
            )
        except Exception as e:
            logger.exception("Клієнту після /finish адміна: %s", e)
        await update.message.reply_text("Діалог із клієнтом завершено.")
        return

    await update.message.reply_text(
        ""
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    await update.message.reply_text(
        "Вітаємо! Оберіть розділ у меню нижче.\n\n"
        "",
        reply_markup=markup_main,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    await update.message.reply_text(
        "Дію скасовано. Оберіть пункт у меню нижче або почніть знову.",
        reply_markup=markup_main,
    )


async def try_auto_claim_from_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Перший Reply адміна на пост AUTO_CLAIM — без inline-кнопок."""
    msg = update.effective_message
    if not msg or not msg.reply_to_message:
        return False
    admin_id = update.effective_user.id
    rid = msg.reply_to_message.message_id
    pending_map: dict = context.bot_data.get(KEY_PENDING_TICKETS, {})
    for tid, entry in list(pending_map.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("mode") != TICKET_MODE_AUTO_CLAIM:
            continue
        if entry.get("admin_msgs", {}).get(admin_id) != rid:
            continue
        pending_map.pop(tid, None)
        client_id = int(entry["client_id"])
        admin_msgs: dict[int, int] = entry["admin_msgs"]
        html_text = entry["html"]
        relay_bind_private(context.bot_data, admin_id, rid, client_id)
        sessions = context.bot_data.setdefault(KEY_SUPPORT_SESSIONS, {})
        sessions[client_id] = {
            "admin_id": admin_id,
            "ticket_id": tid,
            "thread_with_buttons": False,
        }
        taken_footer = (
            "\n\n"
        )
        for aid, mid in admin_msgs.items():
            try:
                if aid == admin_id:
                    await context.bot.edit_message_text(
                        chat_id=aid,
                        message_id=mid,
                        text=html_text + taken_footer,
                        parse_mode="HTML",
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
                else:
                    await context.bot.edit_message_text(
                        chat_id=aid,
                        message_id=mid,
                        text=html_text + "\n\n<i></i>",
                        parse_mode="HTML",
                        reply_markup=None,
                        disable_web_page_preview=True,
                    )
            except Exception as e:
                logger.warning("Не вдалося оновити повідомлення %s/%s (auto-claim): %s", aid, mid, e)
        try:
            await context.bot.send_message(
                chat_id=client_id,
                text=(
                    ""
                ),
                reply_markup=markup_main,
            )
        except Exception as e:
            logger.exception("Клієнту після auto-claim: %s", e)
        text_body = (msg.text or "").strip()
        if text_body:
            try:
                await context.bot.send_message(
                    chat_id=client_id,
                    text=f"📩 Повідомлення від адміністратора:\n\n{text_body}",
                    reply_markup=markup_main,
                )
            except Exception as e:
                logger.exception("Relay після auto-claim: %s", e)
        return True
    return False


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = current_flow(context)
    if flow not in (FLOW_TICKET_PHONE, FLOW_TICKET_PHONE_CONFIRM):
        await update.message.reply_text(
            "",
            reply_markup=markup_main,
        )
        return
    contact = update.message.contact
    if not contact or not contact.phone_number:
        await update.message.reply_text(
            "Не вдалося прочитати номер із контакту. Спробуйте ще раз або введіть номер вручну.",
        )
        return
    context.user_data.setdefault(KEY_TICKET_DRAFT, {}).pop("pending_phone", None)
    await finalize_ticket(update, context, contact.phone_number)


async def handle_admin_private_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.reply_to_message:
        return
    uid = update.effective_user.id
    if uid not in ADMIN_USER_IDS:
        return
    rid = update.message.reply_to_message.message_id
    key = relay_private_key(uid, rid)
    client_id = context.bot_data.get(KEY_RELAY_PRIVATE, {}).get(key)
    if client_id is None:
        await try_auto_claim_from_reply(update, context)
        return
    text = update.message.text or ""
    if not text.strip():
        return
    sess = context.bot_data.get(KEY_SUPPORT_SESSIONS, {}).get(client_id)
    tid = sess.get("ticket_id", "") if isinstance(sess, dict) else ""
    thread_buttons = isinstance(sess, dict) and sess.get("thread_with_buttons", True)
    kb = end_only_keyboard(tid) if (tid and thread_buttons) else None
    try:
        await context.bot.send_message(
            chat_id=client_id,
            text=f"📩 Повідомлення від адміністратора:\n\n{text}",
            reply_markup=kb,
        )
    except Exception as e:
        logger.exception("Relay до користувача %s: %s", client_id, e)


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    user = update.effective_user
    if not user:
        return

    if user.id in ADMIN_USER_IDS:
        await update.message.reply_text(
            ""
            ""
        )
        return

    sess = context.bot_data.get(KEY_SUPPORT_SESSIONS, {}).get(user.id)
    if isinstance(sess, dict) and sess.get("admin_id"):
        thread_buttons = sess.get("thread_with_buttons", True)
        if text in MENU_LABELS:
            via_btn = ", кнопка «Завершити звернення»" if thread_buttons else ""
            await update.message.reply_text(
                ""
                f"{via_btn}), потім знову зможете користуватися меню."
            )
            return
        admin_id = sess["admin_id"]
        ticket_id = sess.get("ticket_id", "")
        line = f"💬 Повідомлення від клієнта:\n\n{text}"
        reply_kb = end_only_keyboard(ticket_id) if (ticket_id and thread_buttons) else None
        try:
            m = await context.bot.send_message(
                chat_id=admin_id,
                text=line,
                reply_markup=reply_kb,
            )
            relay_bind_private(context.bot_data, admin_id, m.message_id, user.id)
        except Exception as e:
            logger.exception("Пересилання адміну: %s", e)
            await update.message.reply_text("Не вдалося передати повідомлення. Спробуйте пізніше.")
            return
        await update.message.reply_text("Повідомлення передано адміністратору.", reply_markup=markup_main)
        return

    flow = current_flow(context)

    if flow == FLOW_TICKET_TEXT:
        if text in MENU_LABELS:
            await update.message.reply_text(
                "Опишіть ситуацію одним звичайним повідомленням (текстом). Кнопки меню тут не підходять — або натисніть /cancel.",
            )
            return
        if not is_appeal_text_valid(text):
            await update.message.reply_text(
                f"Опишіть ситуацію детальніше: не менше {MIN_APPEAL_TEXT_LENGTH} символів у повідомленні "
                f"(зараз {len(text)}). Або /cancel."
            )
            return
        draft = context.user_data.setdefault(KEY_TICKET_DRAFT, {})
        draft["message"] = text
        if user.username:
            await finalize_ticket(update, context, phone=None)
        else:
            context.user_data[KEY_FLOW] = FLOW_TICKET_PHONE
            await update.message.reply_text(
                "У вашому профілі Telegram немає публічного нікнейму (@username).\n\n"
                "Вкажіть номер телефону для зворотного зв'язку:\n"
                "• натисніть «Надіслати свій номер»;\n"
                "• або введіть номер: 0XXXXXXXXX чи +380XXXXXXXXX.\n\n"
                "Після введення номера вручну бот попросить підтвердити його (так / ні). Скасувати заявку: кнопка нижче або /cancel.",
                reply_markup=markup_phone,
            )
        return

    if flow == FLOW_TICKET_PHONE:
        if text == BTN_CANCEL_FLOW:
            clear_flow(context)
            await update.message.reply_text(
                "Заявку скасовано. Оберіть дію в меню нижче.",
                reply_markup=markup_main,
            )
            return
        if validate_ua_phone(text):
            draft = context.user_data.setdefault(KEY_TICKET_DRAFT, {})
            draft["pending_phone"] = text.strip()
            context.user_data[KEY_FLOW] = FLOW_TICKET_PHONE_CONFIRM
            safe_phone = escape_html(text.strip())
            await update.message.reply_text(
                f"Перевірте номер для заявки:\n<b>{safe_phone}</b>\n\n"
                "Напишіть <b>так</b> — надіслати заявку з цим номером.\n"
                "Напишіть <b>ні</b> — ввести номер знову.",
                parse_mode="HTML",
                reply_markup=markup_phone,
            )
            return
        await update.message.reply_text(
            "Номер не схожий на український. Спробуйте 0XXXXXXXXX або +380XXXXXXXXX, кнопку «Надіслати свій номер» або /cancel.",
            reply_markup=markup_phone,
        )
        return

    if flow == FLOW_TICKET_PHONE_CONFIRM:
        if text == BTN_CANCEL_FLOW:
            clear_flow(context)
            await update.message.reply_text(
                "Заявку скасовано. Оберіть дію в меню нижче.",
                reply_markup=markup_main,
            )
            return
        if is_confirm_yes(text):
            draft = context.user_data.get(KEY_TICKET_DRAFT) or {}
            pending = draft.get("pending_phone")
            if not pending:
                clear_flow(context)
                await update.message.reply_text(
                    "Не знайдено номера в чернетці. Почніть заявку знову.",
                    reply_markup=markup_main,
                )
                return
            await finalize_ticket(update, context, pending)
            return
        if is_confirm_no(text):
            context.user_data[KEY_FLOW] = FLOW_TICKET_PHONE
            context.user_data.setdefault(KEY_TICKET_DRAFT, {}).pop("pending_phone", None)
            await update.message.reply_text(
                "Введіть номер ще раз (0XXXXXXXXX або +380...) або надішліть контакт кнопкою нижче.",
                reply_markup=markup_phone,
            )
            return
        await update.message.reply_text(
            "Напишіть <b>так</b>, щоб підтвердити номер, або <b>ні</b>, щоб змінити його.",
            parse_mode="HTML",
            reply_markup=markup_phone,
        )
        return

    if flow == FLOW_LIVE_REQUEST:
        if text in MENU_LABELS:
            await update.message.reply_text(
                "Надішліть одне текстове повідомлення зі змістом звернення або натисніть /cancel."
            )
            return
        if not is_appeal_text_valid(text):
            await update.message.reply_text(
                f"Опишіть звернення детальніше: не менше {MIN_APPEAL_TEXT_LENGTH} символів "
                f"(зараз {len(text)}). Або /cancel."
            )
            return
        await finalize_live_request(update, context, text)
        return

    if text == BTN_LIVE_ADMIN:
        if current_flow(context) not in (FLOW_IDLE, ""):
            await update.message.reply_text(
                "Спочатку завершіть поточний крок або натисніть /cancel, щоб почати спочатку."
            )
            return
        context.user_data[KEY_FLOW] = FLOW_LIVE_REQUEST
        await update.message.reply_text(
            "Опишіть одним повідомленням, з якого питання потрібна допомога адміністратора. "
            "Меню зникло з екрана, щоб не заважати набору тексту.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    if text == "Правова допомога":
        await update.message.reply_text("Оберіть вид правової допомоги:", reply_markup=markup_services)

    elif text in SERVICES_LIST:
        context.user_data[KEY_FLOW] = FLOW_TICKET_TEXT
        context.user_data[KEY_TICKET_DRAFT] = {"service_name": text}
        safe = escape_html(text)
        await update.message.reply_text(
            f"Обрано тему: <b>{safe}</b>.\n\n"
            f"Опишіть ситуацію <b>одним повідомленням</b>. За бажання вкажіть у цьому ж тексті номер телефону для зворотного дзвінка.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )

    elif text == "⬅️ Назад":
        clear_flow(context)
        await update.message.reply_text("Повертаємось до головного меню.", reply_markup=markup_main)

    elif text == "Адаптивний спорт":
        await update.message.reply_text("Оберіть вид активності:", reply_markup=markup_health)

    elif text == "Інше":
        info_text = (
            "🤖 <b>Що вміє цей бот</b>\n"
            "• показує меню послуг та довідкову інформацію;\n"
            "• приймає звернення й передає їх адміністраторам у приватні чати з ботом;\n"
            "• доставляє вам відповідь адміністратора в цей чат;\n"
            "• за потреби може працювати з електронною поштою (якщо це налаштовано на сервері)."
        )
        await update.message.reply_text(info_text, parse_mode="HTML", reply_markup=markup_main)

    elif text == "Знижки для захисників в місті Одеса":
        info_text = (
            "🏎️ <b>Паркування</b>\n"
            "  1) Бескоштовне паркування на паркомісцях в центрі міста (але треба оформити звернувшись до Департаменту Транспорту по вул.Богдана Хмельницького 18)\n"
            "  2) ТРЦ Гагарін Плаза - безкоштовно\n"
            "  3) ТРЦ Острів- безкоштовно\n\n"
            "🍔 <b>Кафе та ресторани</b>\n"
            "  1) Клара Захарівна - 30%\n"
            "  2) RedLine - 10%\n"
            "  3) Veteran Pizza - 25%\n"
            "  4) Lviv Croissant - 15%\n"
            "  5) Ресторан Olio Pizza - 10%\n"
            "  6) Ресторан Zucchini - 10%\n"
            "  7) Пекарня Bulochki - 21%\n\n"
            "🧸 <b>Дитячі товари</b>\n"
            "  1) Будинок іграшок - 10%\n\n"
            "⛽ <b>Паливо (Через додаток Армія+)</b>\n"
            "  1) ОККО - 5 грн знижки на пальне за літр, 20% на кафе\n"
            "  2) WOG - 5 грн знижки на пальне за літр, 20% на кафе, 10% на маркет\n"
            "  3) Укрнафта - 3 грн знижки на пальне за літр, 20% на кафе, 10% на маркет\n\n"
            "🏨 <b>Готелі</b>\n"
            "  1) La Gioconda Boutique Hotel - 20%\n"
            "  2) Лавандія - безкоштовний квиток на вхід\n"
            "  3) Optima Hotel & Resorts - 10%\n\n"
            " 👕 <b>Одяг та аксесуари</b>\n"
            "  1) RiotDivision - 50%\n"
            "  2) Марафон - 30%\n"
            "  3) Gorgany - 15%\n"
            "  4) Мілітарист - 15%\n"
            "  5) Core company - 20%\n"
            "  6) Puma - 15%\n"
            "  7) Adidas - 15%\n\n"
            "👨‍⚕️ <b>Медичні послуги</b>\n"
            "  1) Смартлаб - 10%\n"
            "  2) Люксоптика - бескоштовна діагностика зору\n"
            "  3) Діла - 50%\n"
            "  4) Аналізи CSD LAB - 100%\n"
            "  5) Артмедіуз - 50%\n"
            "  6) Стоматологія Антарес - 15%\n"
            "  7) Масаж Andreev Massage Studio - 30%\n"
            "  8) Стоматологія Boiko Dent - 15%\n"
            "  9) Офтальмологічний центр GlazCo\n"
            "  10) Медичний центр EvaClinic\n\n"
            "📽️ <b>Дозвілля</b>\n"
            "  1) Аквапарк Гаваї - 50%\n"
            "  2) Frisor Барбершоп - 20%\n"
            "  3) Планета Кіно - 20%\n"
            "  4) Multiplex (через додаток Армія+) 60 грн на всі квитки\n"
            "  5) Бритва Барбершоп - 20%\n\n"
            "🛠️ <b>Товари для дому</b>\n"
            "  1) Jysk - 5%\n"
            "  2) DniproM - 20%\n"
            "  3) Столичнв ювелірна фабрика - 7%\n"
            "  4) Estro - 10%\n\n"
            "🍜 <b>Продукти харчування</b>\n"
            "  1) Риба, морепродукти, ікра Katsal Fish House - 10%\n"
            "  2) Спортивне харчування SFS Odessa - 15% та можна розрахуватись за програмою Ветеранський спорт\n"
            "  3) Спортивне харчування та вітаміни Biotus - 10%\n"
            "  4) Спортивне харчування Belok.ua - 15%\n\n"
            "👶 <b>Для дітей</b>\n"
            "  1) Антошка - 10%\n"
            "  2) Дитяча кімната Країна мрій - бескоштовно\n"
            "  3) Дитяча кімната Fly Kids - 50%\n"
            "  4) Товари для дітей pakkids.ua - 10%\n"
            "  5) Біопарк - 50%\n\n"
            "💐 <b>Інше</b>\n"
            "  1) Квіти My Flowers Odessa - 20%\n"
            "  2) Ножі ручної роботи SavaKnife - 10%\n"
            "  3) Ламінат та шпалери Domivka - 10%\n\n"
            "💪 <b>Тренажерні зали та спорт</b>\n"
            "  1) Фітнес клуб Rio - 30%\n"
            "  2) Спортклуб Lure (700 грн по УБД)\n"
            "  3) Боксерський клуб 12 унцій (бескоштовні групові тренування)\n"
            "  4) Тренажерний зал Апгрейд - 35%\n"
            "  5) Тренажерний зал Health Factory - безкоштовно\n\n"
            "✂️ <b>Послуги</b>\n"
            "  1) Інтернет провайдер Тенет - 50%\n"
            "  2) Хімчистка Kims - безкоштовно військовий одяг\n"
            "  3) Ателье Берегиня.ua - 10%\n"
            "  4) Спа комплекс Дюківські лазні - 50%\n\n"
        )
        await update.message.reply_text(info_text, parse_mode="HTML", reply_markup=markup_main)

    elif text in ("Більярд", "Настільний теніс", "Стрільба з лука", "Піклбол"):
        await update.message.reply_text(
            "Розклад і деталі — у каналі адаптивного спорту:\nhttps://t.me/adaptivesportOD"
        )

    else:
        await update.message.reply_text(
            "Не зрозумів повідомлення. Скористайтеся кнопками меню нижче або командою /start."
        )


def build_application():
    if not BOT_TOKEN:
        raise SystemExit(
            "Не знайдено BOT_TOKEN. Додайте його в .env (і збережіть файл) або в оточення: "
            "BOT_TOKEN=... ADMIN_USER_IDS=id1,id2"
        )
    if not ADMIN_USER_IDS:
        logger.warning("ADMIN_USER_IDS порожній — звернення до адміністраторів не доставлятимуться.")
    if ADMIN_CHAT_ID != 0:
        logger.info("TARGET_CHAT_ID задано — звернення також надсилатимуться в цю групу.")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("finish", cmd_finish_support))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.TEXT
            & filters.Regex(r"(?i)^/завершити(@[A-Za-z0-9_]+)?\s*$"),
            cmd_finish_support,
        )
    )

    application.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^(claim|close):"))

    application.add_handler(
        MessageHandler(
            filters.CONTACT & filters.ChatType.PRIVATE,
            handle_contact,
        )
    )

    if ADMIN_USER_IDS:
        application.add_handler(
            MessageHandler(
                filters.User(user_id=ADMIN_USER_IDS)
                & filters.REPLY
                & filters.TEXT
                & ~filters.COMMAND,
                handle_admin_private_reply,
            )
        )

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
            handle_user_message,
        )
    )

    return application


if __name__ == "__main__":
    app = build_application()
    logger.info("Бот запущено...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
