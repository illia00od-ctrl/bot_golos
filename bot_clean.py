import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot_config import BotConfig, build_config
from handlers import start, cancel, cmd_finish_support, handle_contact, handle_user_message, handle_non_text_message

# --- Конфігурація ---
_CFG: BotConfig = build_config()
BOT_TOKEN = _CFG.bot_token

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

def _configure_logging(bot_token: str) -> None:
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

def _configure_smtp_logging(smtp_user: str, smtp_pass: str) -> None:
    if not smtp_user or not smtp_pass:
        return

    class _RedactSMTPFilter(logging.Filter):
        def __init__(self, user: str, password: str) -> None:
            super().__init__()
            self._user = user
            self._pass = password

        def filter(self, record: logging.LogRecord) -> bool:
            if record.args:
                record.args = tuple(
                    a.replace(self._user, "<SMTP_USER>").replace(self._pass, "<SMTP_PASS>")
                    if isinstance(a, str) else a for a in record.args
                )
            if isinstance(record.msg, str):
                record.msg = record.msg.replace(self._user, "<SMTP_USER>").replace(self._pass, "<SMTP_PASS>")
            return True

    for handler in logging.getLogger().handlers:
        handler.addFilter(_RedactSMTPFilter(smtp_user, smtp_pass))

_configure_logging(BOT_TOKEN)
_configure_smtp_logging(_CFG.smtp_user, _CFG.smtp_password)
logger = logging.getLogger(__name__)

async def set_commands(application):
    from telegram import BotCommand
    commands = [
        BotCommand("start", "Почати роботу"),
        BotCommand("cancel", "Скасувати дію"),
        BotCommand("finish", "Завершити/Скасувати заявку"),
    ]
    await application.bot.set_my_commands(commands)

def build_application():
    if not BOT_TOKEN:
        raise SystemExit("Не знайдено BOT_TOKEN.")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("finish", cmd_finish_support))
    
    # Регулярний вираз для команди завершення на українській
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

    # Анти-спам для нетекстових повідомлень (GIF, стікери, фото тощо)
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.TEXT & ~filters.COMMAND & ~filters.CONTACT,
            handle_non_text_message,
        )
    )

    return application

if __name__ == "__main__":
    app = build_application()
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_commands(app))
    logger.info("Бот запущено (refactored)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
