from telegram import KeyboardButton, ReplyKeyboardMarkup
from bot_utils import BTN_SHARE_CONTACT_LABEL, BTN_CANCEL_FLOW

def cancel_markup():
    keyboard = [[KeyboardButton(BTN_CANCEL_FLOW)]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def main_markup():
    keyboard = [
        [KeyboardButton("⚖️ Правова допомога"), KeyboardButton("🚨 Оперативні питання")],
        [KeyboardButton("🏅 Адаптивний спорт"), KeyboardButton("🧠 Психологічний супровід")],
        [KeyboardButton("🏥 Реабілітація та мед супровід"), KeyboardButton("🛒 Знижки для захисників в місті Одеса")],
        [KeyboardButton("❓ Інше")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def services_markup():
    keyboard = [
        [KeyboardButton("📄 Статус УБД/інвалідність внаслідок війни")],
        [KeyboardButton("🏛️ ВЛК і МСЕК (тепер - експертні команди з оцінювання функціонування)")],
        [KeyboardButton("💰 Одноразова грошова допомога (ОГД) за поранення або загибель")],
        [KeyboardButton("💳 Грошове забеспечення та борги військової частини")],
        [KeyboardButton("🏠 Житло"), KeyboardButton("🏥 Соціальні пільги та виплати")],
        [KeyboardButton("🎓 Пенсія"), KeyboardButton("⬅️ Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def health_markup():
    keyboard = [
        [KeyboardButton("🏸 Більярд"), KeyboardButton("🏓 Настільний теніс")],
        [KeyboardButton("🏹 Стрільба з лука"), KeyboardButton("🤾 Піклбол")],
        [KeyboardButton("⬅️ Назад")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def phone_markup():
    keyboard = [
        [KeyboardButton(BTN_SHARE_CONTACT_LABEL, request_contact=True)],
        [KeyboardButton(BTN_CANCEL_FLOW)],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def confirm_markup():
    keyboard = [
        [KeyboardButton("✅ Так"), KeyboardButton("❌ Ні")],
        [KeyboardButton(BTN_CANCEL_FLOW)],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
