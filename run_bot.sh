#!/usr/bin/env bash
# Запуск бота з каталогу проєкту (підхоплює .env через python-dotenv у bot_clean.py)
set -e
cd "$(dirname "$0")"
exec python3 bot_clean.py
