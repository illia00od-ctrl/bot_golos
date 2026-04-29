import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from bot_utils import (
    KEY_FLOW, KEY_TICKET_DRAFT, FLOW_QUESTIONS, FLOW_TICKET_TEXT,
    FLOW_TICKET_PHONE, FLOW_TICKET_PHONE_CONFIRM, FLOW_TICKET_CONFIRM, SERVICE_QUESTIONS,
    MIN_APPEAL_TEXT_LENGTH, BTN_CANCEL_FLOW, escape_html
)
from utils.validators import is_appeal_text_valid, validate_ua_phone, is_confirm_yes, is_confirm_no
from utils import markup as kb
from utils.markup import main_markup, phone_markup

from services.spam_control import is_allowed

logger = logging.getLogger(__name__)

async def clear_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(KEY_FLOW, None)
    context.user_data.pop(KEY_TICKET_DRAFT, None)
    context.user_data.pop("question_index", None)

async def start_service_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, service_name: str) -> None:
    """Викликається після вибору сервісу в меню."""
    if not is_allowed(update.effective_user.id, context.user_data):
        await update.effective_message.reply_text(
            "Ви надіслали занадто багато заявок. Будь ласка, спробуйте пізніше.",
            reply_markup=main_markup()
        )
        return

    context.user_data[KEY_TICKET_DRAFT] = {"service_name": service_name}
    
    questions = SERVICE_QUESTIONS.get(service_name, [])
    if questions:
        context.user_data[KEY_FLOW] = FLOW_QUESTIONS
        context.user_data["question_index"] = 0
        await ask_next_question(update, context)
    else:
        context.user_data[KEY_FLOW] = FLOW_TICKET_TEXT
        await update.effective_message.reply_text(
            f"Опишіть вашу ситуацію одним повідомленням (не менше {MIN_APPEAL_TEXT_LENGTH} символів).",
            reply_markup=kb.cancel_markup(),
        )

async def ask_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Надсилає поточне питання користувачу."""
    draft = context.user_data.get(KEY_TICKET_DRAFT, {})
    service = draft.get("service_name")
    idx = context.user_data.get("question_index", 0)
    questions = SERVICE_QUESTIONS.get(service, [])
    
    if idx < len(questions):
        await update.effective_message.reply_text(
            f"<b>Питання {idx + 1}:</b>\n{questions[idx]}",
            parse_mode="HTML",
            reply_markup=kb.cancel_markup(),
        )
    else:
        context.user_data[KEY_FLOW] = FLOW_TICKET_TEXT
        await update.effective_message.reply_text(
            f"Дякуємо за відповіді! Тепер, будь ласка, опишіть вашу ситуацію детальніше одним повідомленням (не менше {MIN_APPEAL_TEXT_LENGTH} символів).\n\n"
            "Це фінальний крок перед перевіркою заявки.",
            reply_markup=kb.cancel_markup(),
        )

async def process_ticket_logic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from services.delivery import finalize_ticket
    
    text = (update.message.text or "").strip()
    flow = context.user_data.get(KEY_FLOW)

    if flow == FLOW_QUESTIONS:
        if text == BTN_CANCEL_FLOW:
            await clear_flow(context)
            await update.effective_message.reply_text("Заявку скасовано.", reply_markup=main_markup())
            return

        draft = context.user_data.setdefault(KEY_TICKET_DRAFT, {})
        idx = context.user_data.get("question_index", 0)
        draft[f"answer_{idx}"] = text
        context.user_data["question_index"] = idx + 1
        await ask_next_question(update, context)
        return

    if flow == FLOW_TICKET_TEXT:
        if text == BTN_CANCEL_FLOW:
            await clear_flow(context)
            await update.effective_message.reply_text("Заявку скасовано.", reply_markup=main_markup())
            return

        if not is_appeal_text_valid(text):
            current_len = len(text)
            await update.effective_message.reply_text(
                f"Текст занадто короткий ({current_len} з мін. {MIN_APPEAL_TEXT_LENGTH} символів).\n"
                "Будь ласка, опишіть ситуацію детальніше одним повідомленням.",
                reply_markup=kb.cancel_markup()
            )
            return
        
        draft = context.user_data.setdefault(KEY_TICKET_DRAFT, {})
        draft["message"] = text
        user = update.effective_user
        
        # Переходимо до перевірки заявки
        from services.delivery import get_ticket_preview
        preview = await get_ticket_preview(update, context)
        context.user_data[KEY_FLOW] = FLOW_TICKET_CONFIRM
        await update.effective_message.reply_text(
            f"<b>Перевірте вашу заявку:</b>\n\n{preview}\n\n<b>Все правильно?</b>",
            parse_mode="HTML",
            reply_markup=kb.confirm_markup() # Потрібно додати в markup.py
        )
        return

    if flow == FLOW_TICKET_CONFIRM:
        if text == BTN_CANCEL_FLOW:
            await clear_flow(context)
            await update.effective_message.reply_text("Заявку скасовано.", reply_markup=main_markup())
            return
            
        if is_confirm_yes(text):
            user = update.effective_user
            if user.username:
                await finalize_ticket(update, context, phone=None)
            else:
                context.user_data[KEY_FLOW] = FLOW_TICKET_PHONE
                await update.effective_message.reply_text(
                    "Вкажіть номер телефону для зв'язку:",
                    reply_markup=phone_markup()
                )
        elif is_confirm_no(text):
            context.user_data[KEY_FLOW] = FLOW_TICKET_TEXT # Дозволяємо змінити текст
            await update.effective_message.reply_text(
                "Добре, опишіть вашу ситуацію ще раз:",
                reply_markup=ReplyKeyboardRemove()
            )
        else:
            await update.effective_message.reply_text("Будь ласка, натисніть 'Так' або 'Ні'.")
        return

    if flow == FLOW_TICKET_PHONE:
        if text == BTN_CANCEL_FLOW:
            await clear_flow(context)
            await update.effective_message.reply_text("Дію скасовано.", reply_markup=main_markup())
            return
        
        if validate_ua_phone(text):
            draft = context.user_data.setdefault(KEY_TICKET_DRAFT, {})
            draft["pending_phone"] = text
            context.user_data[KEY_FLOW] = FLOW_TICKET_PHONE_CONFIRM
            safe_phone = escape_html(text)
            await update.effective_message.reply_text(
                f"Перевірте номер для заявки:\n<b>{safe_phone}</b>\n\nНапишіть <b>так</b> — підтвердити, або <b>ні</b> — змінити.",
                parse_mode="HTML",
                reply_markup=phone_markup()
            )
            return
        
        await update.effective_message.reply_text(
            "Номер не схожий на український. Спробуйте 0XXXXXXXXX або +380XXXXXXXXX.",
            reply_markup=phone_markup()
        )
        return

    if flow == FLOW_TICKET_PHONE_CONFIRM:
        if text == BTN_CANCEL_FLOW:
            await clear_flow(context)
            await update.effective_message.reply_text("Заявку скасовано.", reply_markup=main_markup())
            return

        if is_confirm_yes(text):
            draft = context.user_data.get(KEY_TICKET_DRAFT) or {}
            phone = draft.get("pending_phone")
            await finalize_ticket(update, context, phone)
            return
        
        if is_confirm_no(text):
            context.user_data[KEY_FLOW] = FLOW_TICKET_PHONE
            await update.effective_message.reply_text(
                "Введіть номер ще раз:",
                reply_markup=phone_markup()
            )
            return
        
        await update.effective_message.reply_text("Будь ласка, напишіть 'так' або 'ні'.")
        return
