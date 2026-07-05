"""
Parda Hisob-kitob — Telegram bot (Mini App ochuvchi).

Ishlashi:
  1. /start bosilганda "Hisob-kitobni ochish" tugmasi chiqadi.
  2. Tugma Mini App'ni (index.html) ochadi.
  3. Mini App'da "Natijani yuborish" bosilса, hisob matni shu yerga keladi
     va SQLite bazasiga saqlanadi.

Sozlash: pastdagi BOT_TOKEN va WEBAPP_URL ni to'ldiring.
"""

import os
import sqlite3
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
    MenuButtonWebApp,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ======================= SOZLAMALAR =======================
# BotFather'dan olingan token. Xavfsizlik uchun muhit o'zgaruvchisidan ham o'qiydi.
BOT_TOKEN = os.getenv("BOT_TOKEN", "8201486066:AAGRaSpHcA0S2lRzMBJ5d260xQ_lugNKD48")

# Mini App (index.html) joylashgan HTTPS manzil.
# Masalan GitHub Pages: https://username.github.io/parda-hisob-bot/
# Test uchun ngrok: https://xxxx.ngrok-free.app/
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://SIZNING-SAYTINGIZ.example/index.html")

DB_PATH = os.path.join(os.path.dirname(__file__), "hisobot.db")
# ==========================================================


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calculations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            username   TEXT,
            full_name  TEXT,
            report     TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def save_calc(user, report: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT INTO calculations (user_id, username, full_name, report, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            user.id,
            user.username or "",
            user.full_name,
            report,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    web_app = WebAppInfo(url=WEBAPP_URL)

    # Ekran ostidagi doimiy tugma (web app ochadi)
    reply_kb = ReplyKeyboardMarkup(
        [[KeyboardButton("🪟 Hisob-kitobni ochish", web_app=web_app)]],
        resize_keyboard=True,
    )
    # Xabar ichidagi tugma
    inline_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🪟 Hisob-kitobni ochish", web_app=web_app)]]
    )

    # Chat menyusidagi (pastki chap) tugmani ham Mini App'ga sozlaymiz
    try:
        await context.bot.set_chat_menu_button(
            chat_id=update.effective_chat.id,
            menu_button=MenuButtonWebApp(text="Hisob-kitob", web_app=web_app),
        )
    except Exception:
        pass

    await update.message.reply_text(
        "Assalomu alaykum! 👋\n\n"
        "Bu — parda (tul, parter, zashitniy) uchun mato hisoblovchi bot.\n"
        "Quyidagi tugmani bosing va xona o'lchamlarini kiriting 👇",
        reply_markup=reply_kb,
    )
    await update.message.reply_text("Yoki shu tugma orqali oching:", reply_markup=inline_kb)


async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mini App'dan kelgan hisob natijasi."""
    data = update.effective_message.web_app_data.data
    user = update.effective_user
    save_calc(user, data)
    await update.message.reply_text(
        "✅ Hisob-kitob qabul qilindi va saqlandi:\n\n" + data
    )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Oxirgi 5 ta hisobni ko'rsatadi."""
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT created_at, report FROM calculations WHERE user_id=? "
        "ORDER BY id DESC LIMIT 5",
        (update.effective_user.id,),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("Hozircha saqlangan hisob-kitob yo'q.")
        return

    for created_at, report in rows:
        await update.message.reply_text(f"🕒 {created_at}\n\n{report}")


def main():
    if "BU_YERGA_TOKEN" in BOT_TOKEN:
        raise SystemExit("❗ Avval BOT_TOKEN ni to'ldiring (bot.py yoki muhit o'zgaruvchisi).")

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data)
    )

    print("Bot ishga tushdi. To'xtatish uchun Ctrl+C.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
