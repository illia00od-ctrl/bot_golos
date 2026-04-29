import logging
from telegram import Update
from telegram.ext import ContextTypes

from bot_config import build_config
from bot_utils import (
    KEY_TICKET_DRAFT,
    build_ticket_admin_html,
    escape_html,
)
from utils.markup import main_markup as markup_main
from services.ticket import clear_flow

logger = logging.getLogger(__name__)
_CFG = build_config()

async def get_ticket_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Генерує текстове прев'ю заявки для перевірки користувачем."""
    draft = context.user_data.get(KEY_TICKET_DRAFT) or {}
    category = escape_html(draft.get("service_name", "Загальне питання"))
    body = escape_html(draft.get("message", ""))
    
    q_answers = []
    for k, v in draft.items():
        if k.startswith("answer_"):
            idx = k.replace("answer_", "")
            q_answers.append(f"• Відповідь {int(idx)+1}: {escape_html(str(v))}")
            
    preview = f"<b>Категорія:</b> {category}\n"
    if q_answers:
        preview += "<b>Ваші відповіді:</b>\n" + "\n".join(q_answers) + "\n"
    preview += f"<b>Ваш текст:</b> {body}"
    return preview

async def broadcast_ticket_to_admins(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    html_text: str,
) -> bool:
    # Доставка виконується тільки в робочу групу (TARGET_CHAT_ID).
    if _CFG.admin_chat_id:
        try:
            await context.bot.send_message(
                chat_id=_CFG.admin_chat_id,
                text=html_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.warning("Не вдалося надіслати копію заявки в групу %s: %s", _CFG.admin_chat_id, e)
            return False
        return True
    return False

async def finalize_ticket(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    phone: str | None,
) -> None:
    user = update.effective_user
    draft = context.user_data.get(KEY_TICKET_DRAFT) or {}
    
    category = draft.get("service_name", "Загальне питання")
    body = draft.get("message", "")
    
    # Збираємо відповіді на додаткові питання разом із самими питаннями
    from bot_utils import SERVICE_QUESTIONS
    questions = SERVICE_QUESTIONS.get(category, [])
    q_formatted = []
    
    for k, v in draft.items():
        if k.startswith("answer_"):
            try:
                idx = int(k.replace("answer_", ""))
                q_text = questions[idx] if idx < len(questions) else f"Питання {idx+1}"
                q_formatted.append(f"<b>Q:</b> {q_text}\n<b>A:</b> {v}")
            except (ValueError, IndexError):
                q_formatted.append(f"<b>A:</b> {v}")
    
    if q_formatted:
        body = "<b>Додаткові запитання:</b>\n" + "\n\n".join(q_formatted) + "\n\n<b>Опис ситуації:</b>\n" + body

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
            html_text=html_text,
        )
        if not ok:
            raise RuntimeError("Delivery failed")
        
    except Exception as e:
        logger.exception("Не вдалося надіслати заявку: %s", e)
        await update.effective_message.reply_text(
            "Не вдалося надіслати звернення. Спробуйте ще раз пізніше.",
            reply_markup=markup_main()
        )
        return

    await clear_flow(context)
    await update.effective_message.reply_text("Ваше повідомлення відправлено! Ми зв'яжемося з вами. ✅", reply_markup=markup_main())
