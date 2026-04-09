import logging
import smtplib
from email.mime.text import MIMEText
from secrets import token_hex

from telegram import (
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot_config import BotConfig, admin_delivery_configured, build_config
from bot_utils import (
    BTN_CANCEL_FLOW,
    BTN_SHARE_CONTACT_LABEL,
    KEY_FLOW,
    KEY_PENDING_GROUP_NOTIFY,
    KEY_PENDING_TICKETS,
    KEY_TICKET_DRAFT,
    FLOW_IDLE,
    FLOW_TICKET_PHONE,
    FLOW_TICKET_PHONE_CONFIRM,
    FLOW_TICKET_TEXT,
    MENU_LABELS,
    MIN_APPEAL_TEXT_LENGTH,
    SERVICES_LIST,
    build_ticket_admin_html,
    escape_html,
    is_appeal_text_valid,
    is_confirm_no,
    is_confirm_yes,
    validate_ua_phone,
)

# --- Конфігурація (з bot_config: .env завантажується там) ---
_CFG: BotConfig = build_config()
BOT_TOKEN = _CFG.bot_token
ADMIN_USER_IDS: list[int] = list(_CFG.admin_user_ids)
ADMIN_CHAT_ID = _CFG.admin_chat_id
SMTP_SERVER = _CFG.smtp_server
SMTP_PORT = _CFG.smtp_port
SMTP_USER = _CFG.smtp_user
SMTP_PASSWORD = _CFG.smtp_password


def _admin_delivery_configured() -> bool:
    return admin_delivery_configured(_CFG)


# Текст додається лише до повідомлення в групі: без кнопок і без relay з групи.
GROUP_TICKET_INFO_FOOTER = (
    "\n\n"
)


async def _edit_group_notify_copy(
    context: ContextTypes.DEFAULT_TYPE,
    group_notify: tuple[int, int] | None,
    *,
    full_html: str,
) -> None:
    """Оновлює дзеркало заявки в групі (якщо було надіслано)."""
    if not group_notify:
        return
    g_chat, g_mid = group_notify
    try:
        await context.bot.edit_message_text(
            chat_id=g_chat,
            message_id=g_mid,
            text=full_html,
            parse_mode="HTML",
            reply_markup=None,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.warning("Не вдалося оновити групове повідомлення %s/%s: %s", g_chat, g_mid, e)

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

# Текст клієнту після успішної відправки (зв'язок лише поза ботом — за контактами в заявці)
_CLIENT_CONTACT_AFTER_SUBMIT = (
    ""
    ""
)
CLIENT_TICKET_SENT_MESSAGE = "Ваше повідомлення відправлено! Ми зв'яжемося з вами. ✅\n\n" + _CLIENT_CONTACT_AFTER_SUBMIT


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


async def broadcast_ticket_to_admins(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    ticket_id: str,
    html_text: str,
    client_id: int,
) -> bool:
    pending = context.bot_data.setdefault(KEY_PENDING_TICKETS, {})
    admin_msgs: dict[int, int] = {}
    for aid in ADMIN_USER_IDS:
        try:
            m = await context.bot.send_message(
                chat_id=aid,
                text=html_text,
                parse_mode="HTML",
                reply_markup=None,
                disable_web_page_preview=True,
            )
            admin_msgs[aid] = m.message_id
        except Exception as e:
            logger.warning("Не вдалося надіслати звернення адміну %s: %s", aid, e)
    group_notify: tuple[int, int] | None = None
    if ADMIN_CHAT_ID:
        try:
            m = await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=html_text + GROUP_TICKET_INFO_FOOTER,
                parse_mode="HTML",
                reply_markup=None,
                disable_web_page_preview=True,
            )
            group_notify = (ADMIN_CHAT_ID, m.message_id)
        except Exception as e:
            logger.warning("Не вдалося надіслати копію заявки в групу %s: %s", ADMIN_CHAT_ID, e)
    if not admin_msgs and not group_notify:
        return False
    pending[ticket_id] = {
        "client_id": client_id,
        "admin_msgs": admin_msgs,
        "html": html_text,
        KEY_PENDING_GROUP_NOTIFY: group_notify,
    }
    return True



def _client_has_open_support(context: ContextTypes.DEFAULT_TYPE, client_id: int) -> bool:
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
            "У вас уже є заявка в очікуванні. Скасуйте її: натисніть -> /finish, або /cancel — і надішліть нову.",
            reply_markup=markup_main,
        )
        return
    if not _admin_delivery_configured():
        clear_flow(context)
        await update.effective_message.reply_text(
            "Неможливо доставити звернення."
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
    try:
        ok = await broadcast_ticket_to_admins(
            context,
            ticket_id=ticket_id,
            html_text=html_text,
            client_id=user.id,
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
    await update.effective_message.reply_text(CLIENT_TICKET_SENT_MESSAGE)
    await context.bot.send_message(user.id, "Користуйтеся меню нижче:", reply_markup=markup_main)


async def cmd_finish_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скидає очікування заявки (клієнт) або пояснює адміну, що діалогу через бота немає."""
    uid = update.effective_user.id
    if not update.message:
        return
    pending_map: dict = context.bot_data.setdefault(KEY_PENDING_TICKETS, {})

    if uid not in ADMIN_USER_IDS:
        removed_pending = [
            tid for tid, e in list(pending_map.items()) if isinstance(e, dict) and e.get("client_id") == uid
        ]
        for tid in removed_pending:
            entry = pending_map.pop(tid, None)
            if entry:
                html_text = entry.get("html", "")
                cancel_suffix = "\n\n<i>Звернення скасовано клієнтом (/завершити або /finish).</i>"
                for aid, mid in entry.get("admin_msgs", {}).items():
                    try:
                        await context.bot.edit_message_text(
                            chat_id=aid,
                            message_id=mid,
                            text=html_text + cancel_suffix,
                            parse_mode="HTML",
                            reply_markup=None,
                            disable_web_page_preview=True,
                        )
                    except Exception as e:
                        logger.warning("edit after /finish: %s", e)
                await _edit_group_notify_copy(
                    context,
                    entry.get(KEY_PENDING_GROUP_NOTIFY),
                    full_html=html_text + GROUP_TICKET_INFO_FOOTER + cancel_suffix,
                )
                for aid in entry.get("admin_msgs", {}):
                    try:
                        await context.bot.send_message(
                            chat_id=aid,
                            text="Клієнт скасував звернення (/завершити або /finish).",
                        )
                    except Exception as e:
                        logger.warning("notify admin: %s", e)
        if removed_pending:
            await update.message.reply_text("Очікування заявки скасовано.", reply_markup=markup_main)
        else:
            await update.message.reply_text("Немає заявки в очікуванні для скасування.", reply_markup=markup_main)
        return

    await update.message.reply_text(
        "Переписка з клієнтами через бота не ведеться: /завершити або /finish."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    await update.message.reply_text(
        "Вітаємо! Оберіть розділ у меню нижче.\n\n"
        "Якщо заявка «зависла» в очікуванні: натисніть -> /finish ",
        reply_markup=markup_main,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_flow(context)
    await update.message.reply_text(
        "Дію скасовано. Оберіть пункт у меню нижче або почніть знову.",
        reply_markup=markup_main,
    )


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    flow = current_flow(context)
    if flow not in (FLOW_TICKET_PHONE, FLOW_TICKET_PHONE_CONFIRM):
        await update.message.reply_text(
            "Зараз бот не очікує номер телефону. Оберіть тему в меню.",
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


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    user = update.effective_user
    if not user:
        return

    if user.id in ADMIN_USER_IDS:
        await update.message.reply_text(
            ""
        )
        return

    flow = current_flow(context)

    if flow == FLOW_TICKET_TEXT:
        if text in MENU_LABELS:
            await update.message.reply_text(
                "Опишіть ситуацію одним звичайним повідомленням (текстом). Кнопки меню тут не підходять —> або натисніть /cancel.",
            )
            return
        if not is_appeal_text_valid(text):
            await update.message.reply_text(
                f"Опишіть ситуацію детальніше: не менше {MIN_APPEAL_TEXT_LENGTH} символів у повідомленні "
                f"(зараз {len(text)}). Або якщо щось не так, натисніть -> /cancel."
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
                "Будь ласка вкажіть номер телефону для зворотного зв'язку:\n"
                "• натисніть «Надіслати свій номер»;\n"
                "• або введіть номер: 0XXXXXXXXX чи +380XXXXXXXXX.\n\n"
                "Після введення номера вручну бот попросить підтвердити його (так / ні). Скасувати заявку: натисніть -> /cancel.",
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

    if text == "Правова допомога":
        await update.message.reply_text("Оберіть вид правової допомоги:", reply_markup=markup_services)

    elif text in SERVICES_LIST:
        context.user_data[KEY_FLOW] = FLOW_TICKET_TEXT
        context.user_data[KEY_TICKET_DRAFT] = {"service_name": text}
        safe = escape_html(text)
        await update.message.reply_text(
            f"Обрано тему: <b>{safe}</b>.\n\n"
            f"Опишіть ситуацію <b>одним повідомленням</b>. За можливості вкажіть у цьому ж тексті номер телефону для зворотного звʼзку.",
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
            "• приймає звернення й передає їх адміністраторам;\n"
            "\n"
            ""
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
            "BOT_TOKEN=... ADMIN_USER_IDS=id1,id2 (або TARGET_CHAT_ID для групи)"
        )
    if not _admin_delivery_configured():
        logger.warning(
            "Не задано ADMIN_USER_IDS і TARGET_CHAT_ID — заявки клієнтам не доставлятимуться."
        )
    elif not ADMIN_USER_IDS and ADMIN_CHAT_ID:
        logger.info(
            "ADMIN_USER_IDS порожній — копія заявок лише в групу TARGET_CHAT_ID (лише для перегляду). "
            "Приватні адміни — через ADMIN_USER_IDS."
        )
    elif ADMIN_USER_IDS and ADMIN_CHAT_ID:
        logger.info(
            "Звернення: приватним адмінам і копія в групу TARGET_CHAT_ID (лише для перегляду)."
        )

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

    application.add_handler(
        MessageHandler(
            filters.CONTACT & filters.ChatType.PRIVATE,
            handle_contact,
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
