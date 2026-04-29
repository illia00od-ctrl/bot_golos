from telegram import Update
from telegram.ext import ContextTypes
from bot_utils import SERVICES_LIST, KEY_FLOW
from services.ticket import start_service_flow, process_ticket_logic, clear_flow
from utils.markup import main_markup, services_markup, health_markup, phone_markup
from bot_utils import KEY_TICKET_DRAFT, FLOW_TICKET_PHONE, FLOW_TICKET_PHONE_CONFIRM

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await clear_flow(context)
    await update.message.reply_text(
        "Вітаємо! Оберіть розділ у меню нижче.",
        reply_markup=main_markup()
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await clear_flow(context)
    await update.message.reply_text(
        "Дію скасовано. Оберіть пункт у меню нижче або почніть знову.",
        reply_markup=main_markup()
    )

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    
    # Перевірка на головне меню (навігація)
    if text == "⚖️ Правова допомога":
        await update.message.reply_text("Оберіть вид правової допомоги:", reply_markup=services_markup())
        return
    
    if text == "🏅 Адаптивний спорт":
        await update.message.reply_text("Оберіть вид активності:", reply_markup=health_markup())
        return

    if text == "⬅️ Назад":
        await clear_flow(context)
        await update.message.reply_text("Повертаємось до головного меню.", reply_markup=main_markup())
        return

    if text == "🛒 Знижки для захисників в місті Одеса" or text == "❓ Інше":
        await start_service_flow(update, context, text)
        return

    if text in SERVICES_LIST:
        await start_service_flow(update, context, text)
        return

    # Якщо ми вже в якомусь потоці (flow), обробляємо його
    if context.user_data.get(KEY_FLOW):
        await process_ticket_logic(update, context)
        return

    await update.message.reply_text(
        "Не зрозумів повідомлення. Скористайтеся кнопками меню нижче або командою /start.",
        reply_markup=main_markup()
    )

async def handle_non_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробка GIF, стікерів, фото та іншого нетекстового контенту (анти-спам)."""
    await update.message.reply_text(
        "Бот приймає лише текстові повідомлення. Будь ласка, використовуйте кнопки меню або пишіть текст.",
        reply_markup=main_markup()
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обробка кнопки 'Надіслати номер'."""
    flow = context.user_data.get(KEY_FLOW)
    if flow not in (FLOW_TICKET_PHONE, FLOW_TICKET_PHONE_CONFIRM):
        await update.message.reply_text(
            "Зараз бот не очікує номер телефону. Оберіть тему в меню.",
            reply_markup=main_markup(),
        )
        return
    contact = update.message.contact
    if not contact or not contact.phone_number:
        await update.message.reply_text(
            "Не вдалося прочитати номер із контакту. Спробуйте ще раз.",
        )
        return
    
    from services.delivery import finalize_ticket
    context.user_data.setdefault(KEY_TICKET_DRAFT, {}).pop("pending_phone", None)
    await finalize_ticket(update, context, contact.phone_number)

async def cmd_finish_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Скасування заявки через /finish."""
    await clear_flow(context)
    await update.message.reply_text(
        "Дію завершено/скасовано. Очікуючі запити (якщо були) припинено.",
        reply_markup=main_markup()
    )
