"""
Rayyon Pardalar — Telegram bot + CRM API server (v3, rollar bilan).

ROLLAR
  mijoz      — hisob-kitob, o'z buyurtmalari, mahsulotlar
  admin      — o'ziga biriktirilgan va o'zi yaratgan buyurtmalarni boshqaradi,
               qolganlarini faqat ko'radi
  bosh_admin — hamma narsa (buyurtma biriktirish, hodim tasdiqlash, hisobotlar)
  tikuvchi   — unga task biriktiriladi; tikuv haqi 10 000 so'm/metr

RO'YXATDAN O'TISH
  Yangi odam /start bossa: "Mijozmisiz yoki hodim?" — mijoz darhol tasdiqlanadi,
  hodim (admin/tikuvchi/bosh admin) so'rovi BOSH ADMINga boradi (tasdiq/rad).

BUYURTMA BOSQICHLARI
  buyurtma_olindi → olchov → zaklad → kesildi → tikilmoqda → tayyor → yakunlandi (+bekor)
"""

import os
import io
import re
import csv
import json
import hmac
import hashlib
import logging
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, time as dtime
from urllib.parse import parse_qsl, quote

from aiohttp import web
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, WebAppInfo, MenuButtonWebApp,
)
from telegram.error import Conflict, NetworkError, TimedOut
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
log = logging.getLogger("parda-bot")

# ======================= SOZLAMALAR =======================
# Token muhit o'zgaruvchisidan (Railway) yoki lokal secrets_local.py dan olinadi.
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    try:
        from secrets_local import BOT_TOKEN as BOT_TOKEN   # gitignored (lokal)
    except Exception:
        pass
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://asadulloabdullayev.github.io/parda-hisob-bot/")

BOSH_ADMIN_ID = int(os.getenv("BOSH_ADMIN_ID", "2090652095"))   # egasi — har doim bosh_admin
TIKUV_NARX = 10000              # tikuvchi haqi: so'm / metr

# Railway PORT beradi; lokalda 8081. Railway o'zi HTTPS domen beradi (tunnel kerak emas).
API_PORT = int(os.getenv("PORT", "8081"))
RAILWAY_DOMAIN = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
IS_CLOUD = bool(RAILWAY_DOMAIN)
DEV_MODE = os.getenv("DEV_MODE", "0" if IS_CLOUD else "1") == "1"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Bulut: baza Volume'da (/data). Lokal: papkada.
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "hisobot.db"))
CLOUDFLARED = os.path.join(BASE_DIR, "cloudflared.exe")

STATUSLAR = ["buyurtma_olindi", "olchov", "zaklad", "kesildi",
             "tikilmoqda", "tayyor", "yakunlandi", "bekor"]
STATUS_LABEL = {
    "buyurtma_olindi": "📥 Buyurtma olindi",
    "olchov":     "📏 O'lchovga borildi",
    "zaklad":     "💰 Zaklad berildi",
    "kesildi":    "✂️ Kesildi",
    "tikilmoqda": "🧵 Tikilmoqda",
    "tayyor":     "✅ Tayyor",
    "yakunlandi": "🏁 Yakunlandi",
    "bekor":      "❌ Bekor",
}
ROL_LABEL = {"mijoz": "🛍 Mijoz", "admin": "🛠 Admin",
             "bosh_admin": "👑 Bosh admin", "tikuvchi": "🧵 Tikuvchi"}
API_URL = ""
PHOTO_CACHE = {}
# ==========================================================


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY, username TEXT, full_name TEXT, chat_id INTEGER,
        created_at TEXT);
    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        full_name TEXT, text TEXT, status TEXT DEFAULT 'buyurtma_olindi',
        created_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS comments(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, user_id INTEGER,
        name TEXT, text TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS history(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, status TEXT,
        by_name TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS photos(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, data TEXT,
        by_name TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT,
        price REAL, qty REAL, photo_file_id TEXT, added_by INTEGER, created_at TEXT);
    CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER,
        assignee_id INTEGER, assignee_name TEXT, text TEXT,
        deadline TEXT, status TEXT DEFAULT 'ochiq', result TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS calculations(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
        full_name TEXT, report TEXT, created_at TEXT);
    """)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS order_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, name TEXT,
        unit TEXT DEFAULT 'm', warehouse TEXT DEFAULT 'Asosiy', qty REAL DEFAULT 0,
        price REAL DEFAULT 0, discount REAL DEFAULT 0, created_at TEXT);
    CREATE TABLE IF NOT EXISTS seams(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER,
        tikuvchi_id INTEGER, tikuvchi_name TEXT, work TEXT, fee REAL DEFAULT 0,
        status TEXT DEFAULT 'biriktirildi', note TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS cf_defs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, pos INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS cf_vals(
        order_id INTEGER, def_id INTEGER, value TEXT,
        PRIMARY KEY(order_id, def_id));
    CREATE TABLE IF NOT EXISTS calls(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, type TEXT,
        by_name TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, name TEXT,
        amount REAL DEFAULT 0, by_name TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS product_comments(
        id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, user_id INTEGER,
        name TEXT, text TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT, tikuvchi_id INTEGER, tikuvchi_name TEXT,
        amount REAL DEFAULT 0, note TEXT, by_name TEXT, created_at TEXT);
    """)
    # yangi ustunlar (mavjud bo'lsa xato bermaydi)
    for sql in [
        "ALTER TABLE users ADD COLUMN role TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN assigned_to INTEGER",
        "ALTER TABLE orders ADD COLUMN assigned_name TEXT",
        "ALTER TABLE orders ADD COLUMN delivery_date TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN delivery_address TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN currency TEXT DEFAULT \"so'm\"",
        "ALTER TABLE orders ADD COLUMN note TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN client_name TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN client_phone TEXT DEFAULT ''",
        "ALTER TABLE orders ADD COLUMN source TEXT DEFAULT 'Ilova'",
        "ALTER TABLE tasks ADD COLUMN metr REAL DEFAULT 0",
        "ALTER TABLE products ADD COLUMN photo_data TEXT",
        "ALTER TABLE orders ADD COLUMN state_json TEXT DEFAULT ''",
    ]:
        try: c.execute(sql)
        except sqlite3.OperationalError: pass
    c.execute("UPDATE orders SET status='buyurtma_olindi' WHERE status='yangi'")
    c.execute("UPDATE orders SET status='tikilmoqda' WHERE status='jarayonda'")
    c.execute("UPDATE users SET role='bosh_admin' WHERE id=?", (BOSH_ADMIN_ID,))
    # eski buyurtmalar: mijoz ismi/telini chek matnidan olish
    for r in c.execute("SELECT id, text FROM orders WHERE (client_name IS NULL OR client_name='') "
                       "AND text LIKE '%Mijoz:%'").fetchall():
        nm, ph = parse_client(r["text"])
        if nm or ph:
            c.execute("UPDATE orders SET client_name=?, client_phone=? WHERE id=?",
                      (nm, ph, r["id"]))
    c.commit(); c.close()


def parse_client(text):
    """Chek matnidan 'Mijoz:' va birinchi 'Tel:' qatorlarini oladi."""
    nm = re.search(r"Mijoz:\s*(.+)", text or "")
    ph = re.search(r"Tel:\s*([+\d][\d\s+,()-]*)", text or "")
    return ((nm.group(1).strip() if nm else ""),
            (ph.group(1).strip() if ph else ""))


def now(): return datetime.now().isoformat(timespec="seconds")


def get_role(uid):
    if uid == BOSH_ADMIN_ID: return "bosh_admin"
    c = db()
    r = c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    c.close()
    role = (r["role"] if r else "") or ""
    if role.startswith("pending"): return "mijoz"
    return role or ""


def set_role(uid, role):
    c = db()
    c.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    c.commit(); c.close()


def is_staff(role): return role in ("admin", "bosh_admin")


def staff_ids():
    c = db()
    rows = c.execute("SELECT id FROM users WHERE role IN ('admin','bosh_admin')").fetchall()
    c.close()
    ids = {r["id"] for r in rows}; ids.add(BOSH_ADMIN_ID)
    return list(ids)


def can_manage(order_row, uid, role):
    if role == "bosh_admin": return True
    if role == "admin":
        return order_row["assigned_to"] == uid or order_row["user_id"] == uid
    return False


def can_view(order_row, uid, role):
    if is_staff(role): return True
    if order_row["user_id"] == uid: return True
    if role == "tikuvchi":
        c = db()
        t = c.execute("SELECT 1 FROM tasks WHERE order_id=? AND assignee_id=? LIMIT 1",
                      (order_row["id"], uid)).fetchone()
        s = c.execute("SELECT 1 FROM seams WHERE order_id=? AND tikuvchi_id=? LIMIT 1",
                      (order_row["id"], uid)).fetchone()
        c.close()
        return bool(t or s)
    return False


def upsert_user(u, chat_id):
    c = db()
    c.execute("INSERT INTO users(id,username,full_name,chat_id,created_at) VALUES(?,?,?,?,?) "
              "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
              "full_name=excluded.full_name, chat_id=excluded.chat_id",
              (u.id, u.username or "", u.full_name, chat_id, now()))
    c.commit(); c.close()


def find_user(token):
    c = db()
    r = None
    t = str(token)
    if t.startswith("@"):
        r = c.execute("SELECT * FROM users WHERE username=?", (t[1:],)).fetchone()
    elif t.lstrip("-").isdigit():
        r = c.execute("SELECT * FROM users WHERE id=?", (int(t),)).fetchone()
    c.close(); return r


def add_history(order_id, status, by_name):
    c = db()
    c.execute("INSERT INTO history(order_id,status,by_name,created_at) VALUES(?,?,?,?)",
              (order_id, status, by_name, now()))
    c.commit(); c.close()


def status_kb(order_id):
    rows, row = [], []
    for s in STATUSLAR:
        row.append(InlineKeyboardButton(STATUS_LABEL[s], callback_data=f"st:{order_id}:{s}"))
        if len(row) == 2: rows.append(row); row = []
    if row: rows.append(row)
    return InlineKeyboardMarkup(rows)


def webapp_url():
    return WEBAPP_URL + ("?api=" + quote(API_URL, safe="") if API_URL else "")


def app_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("🪟 Ilovani ochish", web_app=WebAppInfo(url=webapp_url()))]],
        resize_keyboard=True)


# ======================= RO'YXATDAN O'TISH =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    upsert_user(u, update.effective_chat.id)
    c = db()
    r = c.execute("SELECT role FROM users WHERE id=?", (u.id,)).fetchone()
    c.close()
    raw_role = (r["role"] if r else "") or ""
    if u.id == BOSH_ADMIN_ID: raw_role = "bosh_admin"

    if not raw_role:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛍 Mijozman", callback_data="reg:mijoz"),
            InlineKeyboardButton("👷 Hodimman", callback_data="reg:hodim"),
        ]])
        await update.message.reply_text(
            "Assalomu alaykum! 👋 Rayyon Pardalar botiga xush kelibsiz.\n\n"
            "Kim sifatida foydalanasiz?", reply_markup=kb)
        return
    if raw_role.startswith("pending"):
        await update.message.reply_text(
            "⏳ Hodimlik so'rovingiz bosh admin tomonidan ko'rilmoqda. Kuting…")
        return

    try:
        await context.bot.set_chat_menu_button(
            chat_id=update.effective_chat.id,
            menu_button=MenuButtonWebApp(text="Ilova",
                                         web_app=WebAppInfo(url=webapp_url())))
    except Exception:
        pass

    role = get_role(u.id)
    if role == "bosh_admin":
        await update.message.reply_text(
            f"Assalomu alaykum, BOSH ADMIN! 👑  (ID: {u.id})\n\n"
            "🪟 Ilova (hisob-kitob + CRM) — pastdagi tugma\n"
            "📋 /buyurtmalar  📌 /tasklar  🛒 /mahsulotlar\n"
            "➕ Mahsulot: rasm + izoh:  mahsulot: NOMI | kategoriya | narx | miqdor\n"
            "📌 Task: /task BUYURTMA_ID @user SOAT MATN",
            reply_markup=app_kb())
    elif role == "admin":
        await update.message.reply_text(
            f"Assalomu alaykum, ADMIN! 🛠  (ID: {u.id})\n\n"
            "🪟 Ilova — pastdagi tugma. Sizga biriktirilgan buyurtmalarni boshqarasiz.",
            reply_markup=app_kb())
    elif role == "tikuvchi":
        await update.message.reply_text(
            f"Assalomu alaykum! 🧵  (ID: {u.id})\n\n"
            "Sizga biriktirilgan tasklar shu yerga keladi.\n"
            f"Tikuv haqi: {TIKUV_NARX:,} so'm/metr.\n"
            "Bajargach: /bajarildi TASK_ID izoh",
            reply_markup=app_kb())
    else:
        await update.message.reply_text(
            f"Assalomu alaykum! 🛍  (ID: {u.id})\n\n"
            "🪟 Ilovada hisob-kitob qiling va buyurtmalaringizni kuzating.\n"
            "🛒 /mahsulotlar — katalog",
            reply_markup=app_kb())


async def on_reg_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    data = q.data
    if data == "reg:mijoz":
        set_role(u.id, "mijoz")
        await q.edit_message_text("✅ Mijoz sifatida ro'yxatdan o'tdingiz!")
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Telefon raqamni ulashish", request_contact=True)],
             [KeyboardButton("🪟 Ilovani ochish", web_app=WebAppInfo(url=webapp_url()))]],
            resize_keyboard=True)
        await context.bot.send_message(
            u.id,
            "📱 Telefon raqamingizni ulashing — agar nomingizga buyurtma qilingan "
            "bo'lsa, uni profilingizga bog'laymiz va holatini yuborib turamiz:",
            reply_markup=kb)
    elif data == "reg:hodim":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛠 Admin", callback_data="reg2:admin"),
             InlineKeyboardButton("🧵 Tikuvchi", callback_data="reg2:tikuvchi")],
            [InlineKeyboardButton("👑 Bosh admin", callback_data="reg2:bosh_admin")],
        ])
        await q.edit_message_text("Qaysi lavozimda ishlaysiz?", reply_markup=kb)
    elif data.startswith("reg2:"):
        role = data.split(":")[1]
        if u.id == BOSH_ADMIN_ID:
            set_role(u.id, role)
            await q.edit_message_text(f"✅ {ROL_LABEL[role]} sifatida tasdiqlandingiz.")
            return
        set_role(u.id, "pending_" + role)
        await q.edit_message_text(
            f"⏳ {ROL_LABEL[role]} bo'lish so'rovingiz bosh adminga yuborildi. Kuting…")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"appr:{u.id}:{role}"),
            InlineKeyboardButton("❌ Rad etish", callback_data=f"rejr:{u.id}"),
        ]])
        try:
            await context.bot.send_message(
                BOSH_ADMIN_ID,
                f"👷 HODIMLIK SO'ROVI\n{u.full_name}"
                + (f" (@{u.username})" if u.username else "")
                + f"\nLavozim: {ROL_LABEL[role]}\nID: {u.id}",
                reply_markup=kb)
        except Exception:
            pass


async def on_approve_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id != BOSH_ADMIN_ID:
        await q.answer("Faqat bosh admin!", show_alert=True); return
    await q.answer()
    parts = q.data.split(":")
    uid = int(parts[1])
    if parts[0] == "appr":
        role = parts[2]
        set_role(uid, role)
        await q.edit_message_text(q.message.text + f"\n\n✅ TASDIQLANDI ({ROL_LABEL[role]})")
        try:
            await context.bot.send_message(
                uid, f"✅ Siz {ROL_LABEL[role]} sifatida tasdiqlandingiz! /start bosing.")
        except Exception:
            pass
    else:
        set_role(uid, "mijoz")
        await q.edit_message_text(q.message.text + "\n\n❌ RAD ETILDI (mijoz qilindi)")
        try:
            await context.bot.send_message(
                uid, "So'rovingiz rad etildi — mijoz sifatida foydalanishingiz mumkin. /start")
        except Exception:
            pass


def norm_phone(p):
    d = "".join(ch for ch in str(p) if ch.isdigit())
    return d[-9:] if len(d) >= 9 else d


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz raqam ulashsa — client_phone mos buyurtmalarni unga bog'laymiz."""
    u = update.effective_user
    contact = update.message.contact
    if not contact:
        return
    upsert_user(u, update.effective_chat.id)
    c0 = db()
    c0.execute("UPDATE users SET phone=? WHERE id=?", (contact.phone_number or "", u.id))
    c0.commit(); c0.close()
    ph = norm_phone(contact.phone_number)
    linked = []
    c = db()
    rows = c.execute("SELECT * FROM orders WHERE client_phone!='' AND client_phone IS NOT NULL").fetchall()
    for r in rows:
        nums = [norm_phone(x) for x in (r["client_phone"] or "").split(",") if x.strip()]
        if ph and ph in nums:
            if r["user_id"] != u.id:
                c.execute("UPDATE orders SET user_id=? WHERE id=?", (u.id, r["id"]))
            linked.append(r)
    c.commit(); c.close()
    if linked:
        s = "🔗 Raqamingizga bog'langan buyurtmalar topildi:\n\n"
        for r in linked:
            s += f"№{r['id']} — {STATUS_LABEL.get(r['status'], r['status'])}\n"
        s += "\nIlovadagi «Buyurtmalar» bo'limida kuzatib boring. Holat o'zgarsa xabar yuboramiz."
        await update.message.reply_text(s, reply_markup=app_kb())
    else:
        await update.message.reply_text(
            "Rahmat! Hozircha raqamingizga bog'langan buyurtma topilmadi. "
            "Buyurtma qilganingizda avtomatik bog'lanadi.", reply_markup=app_kb())


# ======================= BUYURTMALAR =======================
async def notify_staff(bot, text, kb=None, exclude=None):
    for aid in staff_ids():
        if exclude and aid == exclude: continue
        try:
            await bot.send_message(aid, text, reply_markup=kb)
        except Exception:
            pass


async def create_order(bot, user_id, username, full_name, text, photos=None,
                       client_name="", client_phone="", state_json=""):
    if not client_name or not client_phone:
        nm, ph = parse_client(text)
        client_name = client_name or nm
        client_phone = client_phone or ph
    c = db()
    cur = c.execute("INSERT INTO orders(user_id,username,full_name,text,client_name,client_phone,"
                    "state_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (user_id, username or "", full_name, text,
                     client_name, client_phone, state_json, now(), now()))
    oid = cur.lastrowid
    if photos:
        for p in photos[:10]:
            c.execute("INSERT INTO photos(order_id,data,by_name,created_at) VALUES(?,?,?,?)",
                      (oid, p, full_name, now()))
    c.commit(); c.close()
    add_history(oid, "buyurtma_olindi", full_name)
    msg = (f"🆕 YANGI BUYURTMA №{oid}\n"
           f"👤 {full_name}" + (f" (@{username})" if username else "") + "\n"
           f"🕒 {datetime.now():%d.%m.%Y %H:%M}"
           + (f"\n📷 {len(photos)} ta rasm biriktirilgan" if photos else "")
           + f"\n━━━━━━━━━━━━━━\n{text[:3400]}")
    await notify_staff(bot, msg, kb=status_kb(oid))
    return oid


async def on_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.effective_message.web_app_data.data
    u = update.effective_user
    upsert_user(u, update.effective_chat.id)
    c = db()
    c.execute("INSERT INTO calculations(user_id,username,full_name,report,created_at) VALUES(?,?,?,?,?)",
              (u.id, u.username or "", u.full_name, data, now()))
    c.commit(); c.close()
    oid = await create_order(context.bot, u.id, u.username, u.full_name, data)
    await update.message.reply_text(
        f"✅ Hisob-kitob BUYURTMA №{oid} sifatida saqlandi.\n"
        f"Ilovadagi «Buyurtmalar» bo'limida kuzatishingiz mumkin.")


async def buyurtma_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    upsert_user(u, update.effective_chat.id)
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text(
            "Buyurtma matnini yozing:\n/buyurtma Mehmonxonaga parda kerak, eni 4m ...")
        return
    oid = await create_order(context.bot, u.id, u.username, u.full_name, text)
    await update.message.reply_text(f"✅ Buyurtmangiz qabul qilindi! №{oid}")


async def buyurtmalar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_staff(get_role(update.effective_user.id)): return
    c = db()
    rows = c.execute("SELECT * FROM orders WHERE status NOT IN ('yakunlandi','bekor') "
                     "ORDER BY id DESC LIMIT 20").fetchall()
    c.close()
    if not rows:
        await update.message.reply_text("Ochiq buyurtma yo'q. 🎉"); return
    for r in rows:
        extra = f"\n👨‍🔧 Mas'ul: {r['assigned_name']}" if r["assigned_name"] else ""
        await update.message.reply_text(
            f"📋 BUYURTMA №{r['id']} — {STATUS_LABEL.get(r['status'], r['status'])}{extra}\n"
            f"👤 {r['full_name']}\n🕒 {r['created_at'][:16]}\n"
            f"━━━━━━━━━━━━━━\n{r['text'][:700]}",
            reply_markup=status_kb(r["id"]))


async def set_status(bot, oid, status, by_name):
    c = db()
    r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not r:
        c.close(); return None
    c.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (status, now(), oid))
    c.commit(); c.close()
    add_history(oid, status, by_name)
    try:
        await bot.send_message(
            r["user_id"], f"📦 Buyurtmangiz №{oid}: {STATUS_LABEL.get(status, status)}")
    except Exception:
        pass
    return r


async def on_status_btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    role = get_role(q.from_user.id)
    c = db()
    order = c.execute("SELECT * FROM orders WHERE id=?", (int(q.data.split(":")[1]),)).fetchone()
    c.close()
    if not order or not can_manage(order, q.from_user.id, role):
        await q.answer("Bu buyurtma sizga biriktirilmagan!", show_alert=True); return
    await q.answer()
    _, oid, status = q.data.split(":")
    await set_status(context.bot, int(oid), status, q.from_user.full_name)
    try:
        first_line = (q.message.text or f"BUYURTMA №{oid}").split("\n")[0]
        await q.edit_message_text(
            f"{first_line}\n➡️ {STATUS_LABEL.get(status,status)}\n"
            f"🕒 {datetime.now():%d.%m %H:%M} — {q.from_user.full_name}",
            reply_markup=status_kb(int(oid)))
    except Exception:
        pass


# --------- Mahsulotlar (chat orqali) ---------
def parse_product(text):
    t = (text or "").strip()
    if not t.lower().startswith("mahsulot:"): return None
    parts = [p.strip() for p in t.split(":", 1)[1].split("|")]
    if len(parts) < 3: return None
    try:
        price = float(parts[2].replace(" ", "").replace(",", "."))
        qty = float(parts[3].replace(" ", "").replace(",", ".")) if len(parts) > 3 else 0
    except ValueError:
        return None
    return {"name": parts[0], "category": parts[1].lower(), "price": price, "qty": qty}


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if get_role(u.id) != "bosh_admin": return
    p = parse_product(update.message.caption)
    if not p: return
    fid = update.message.photo[-1].file_id
    c = db()
    c.execute("INSERT INTO products(name,category,price,qty,photo_file_id,added_by,created_at) "
              "VALUES(?,?,?,?,?,?,?)",
              (p["name"], p["category"], p["price"], p["qty"], fid, u.id, now()))
    c.commit(); c.close()
    await update.message.reply_text(
        f"✅ Mahsulot qo'shildi: {p['name']} ({p['category']}) — {p['price']:,.0f} so'm")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    role = get_role(u.id)
    if role == "bosh_admin":
        p = parse_product(update.message.text)
        if not p: return
        c = db()
        c.execute("INSERT INTO products(name,category,price,qty,photo_file_id,added_by,created_at) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (p["name"], p["category"], p["price"], p["qty"], None, u.id, now()))
        c.commit(); c.close()
        await update.message.reply_text(f"✅ Mahsulot qo'shildi: {p['name']} — {p['price']:,.0f} so'm")
        return
    if is_staff(role) or role == "tikuvchi":
        return
    # MIJOZ yozdi — xabari faol buyurtmasi ichiga izoh bo'lib tushadi
    text = (update.message.text or "").strip()
    if not text: return
    upsert_user(u, update.effective_chat.id)
    c = db()
    r = c.execute("SELECT * FROM orders WHERE user_id=? AND status NOT IN ('yakunlandi','bekor') "
                  "ORDER BY id DESC LIMIT 1", (u.id,)).fetchone()
    if not r:
        r = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1",
                      (u.id,)).fetchone()
    if not r:
        c.close()
        await update.message.reply_text(
            "Xabaringiz uchun rahmat! Sizda hali buyurtma yo'q — ilovadan buyurtma "
            "bering yoki: /buyurtma matn")
        return
    c.execute("INSERT INTO comments(order_id,user_id,name,text,created_at) VALUES(?,?,?,?,?)",
              (r["id"], u.id, u.full_name, text, now()))
    c.commit(); c.close()
    msg = (f"💬 MIJOZ XABARI — Buyurtma №{r['id']}\n"
           f"👤 {u.full_name}\n📝 {text[:800]}\n\n"
           f"Javob berish: ilovada buyurtmani ochib «↩ Javob berish»")
    if r["assigned_to"]:
        try:
            await context.bot.send_message(r["assigned_to"], msg)
        except Exception:
            pass
    else:
        try:
            await context.bot.send_message(
                BOSH_ADMIN_ID,
                msg + "\n\n⚠️ Mas'ul shaxs biriktirilmagan — ilovada buyurtmani "
                      "ochib mas'ul biriktiring.")
        except Exception:
            pass
    await update.message.reply_text(
        f"✅ Xabaringiz buyurtma №{r['id']} ga biriktirildi — mas'ul hodim tez orada javob beradi.")


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if get_role(u.id) != "bosh_admin": return
    doc = update.message.document
    if not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("Faqat CSV (ustunlar: nom,kategoriya,narx,miqdor).")
        return
    f = await doc.get_file()
    raw = await f.download_as_bytearray()
    try:
        textdata = bytes(raw).decode("utf-8-sig")
    except UnicodeDecodeError:
        textdata = bytes(raw).decode("cp1251", errors="replace")
    added = 0
    c = db()
    for row in csv.DictReader(io.StringIO(textdata)):
        try:
            c.execute("INSERT INTO products(name,category,price,qty,photo_file_id,added_by,created_at) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (row["nom"].strip(), row["kategoriya"].strip().lower(),
                       float(str(row["narx"]).replace(" ", "").replace(",", ".")),
                       float(str(row.get("miqdor") or 0).replace(" ", "").replace(",", ".")),
                       None, u.id, now()))
            added += 1
        except Exception:
            continue
    c.commit(); c.close()
    await update.message.reply_text(f"✅ CSV'dan {added} ta mahsulot qo'shildi.")


async def mahsulotlar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = db()
    rows = c.execute("SELECT * FROM products ORDER BY category, id DESC LIMIT 60").fetchall()
    c.close()
    if not rows:
        await update.message.reply_text("Katalog hozircha bo'sh."); return
    by_cat = {}
    for r in rows: by_cat.setdefault(r["category"] or "boshqa", []).append(r)
    sent = 0
    for cat, items in by_cat.items():
        s = f"🗂 {cat.upper()}:\n"
        for r in items:
            s += f"  • {r['name']} — {r['price']:,.0f} so'm"
            if r["qty"]: s += f" ({r['qty']:g})"
            s += "\n"
        await update.message.reply_text(s)
        for r in items:
            if r["photo_file_id"] and sent < 8:
                try:
                    await update.message.reply_photo(r["photo_file_id"],
                        caption=f"{r['name']} — {r['price']:,.0f} so'm ({cat})")
                    sent += 1
                except Exception:
                    pass


# --------- Tasklar ---------
async def make_task(bot, job_queue, oid, assignee, deadline, text, by_name, metr=0):
    c = db()
    cur = c.execute("INSERT INTO tasks(order_id,assignee_id,assignee_name,text,deadline,metr,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (oid, assignee["id"], assignee["full_name"], text,
                     deadline.isoformat(timespec="seconds"), metr, now()))
    tid = cur.lastrowid
    c.commit(); c.close()
    mtxt = f"\n📐 Tikuv: {metr:g} m × {TIKUV_NARX:,} = {metr*TIKUV_NARX:,.0f} so'm" if metr else ""
    try:
        await bot.send_message(
            assignee["id"],
            f"📌 YANGI TASK №{tid} (Buyurtma №{oid})\n{text}{mtxt}\n"
            f"⏰ Muddat: {deadline:%d.%m.%Y %H:%M}\nQo'ygan: {by_name}\n\n"
            f"Bajargach: /bajarildi {tid} izoh\nKechiksa: /sabab {tid} sabab")
    except Exception:
        pass
    if job_queue:
        job_queue.run_once(task_deadline_check, when=deadline, data=tid, name=f"task:{tid}")
    return tid


async def task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if not is_staff(get_role(u.id)): return
    args = context.args
    if len(args) < 4:
        await update.message.reply_text(
            "Format: /task BUYURTMA_ID FOYDALANUVCHI SOAT MATN\n"
            "Masalan: /task 3 @usta_ali 24 Pardalarni tikish")
        return
    try:
        oid = int(args[0]); hours = float(args[2])
    except ValueError:
        await update.message.reply_text("BUYURTMA_ID va SOAT raqam bo'lishi kerak."); return
    assignee = find_user(args[1])
    if not assignee:
        await update.message.reply_text(f"{args[1]} topilmadi (u botga /start yozgan bo'lishi kerak).")
        return
    deadline = datetime.now() + timedelta(hours=hours)
    tid = await make_task(context.bot, context.job_queue, oid, assignee,
                          deadline, " ".join(args[3:]), u.full_name)
    await update.message.reply_text(
        f"✅ Task №{tid} → {assignee['full_name']}, muddat {deadline:%d.%m %H:%M}")


async def task_deadline_check(context: ContextTypes.DEFAULT_TYPE):
    tid = context.job.data
    c = db()
    t = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t or t["status"] != "ochiq":
        c.close(); return
    c.execute("UPDATE tasks SET status='kechikdi' WHERE id=?", (tid,))
    c.commit(); c.close()
    try:
        await context.bot.send_message(
            BOSH_ADMIN_ID,
            f"⚠️ TASK KECHIKDI! №{tid} (Buyurtma №{t['order_id']})\n"
            f"👤 {t['assignee_name']}\n📝 {t['text']}\n⏰ {t['deadline'][:16]}")
    except Exception:
        pass
    try:
        await context.bot.send_message(
            t["assignee_id"],
            f"⚠️ Task №{tid} muddati o'tdi! Sabab: /sabab {tid} sabab matni")
    except Exception:
        pass


async def bajarildi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Format: /bajarildi TASK_ID izoh"); return
    tid = int(args[0]); izoh = " ".join(args[1:]) or "—"
    c = db()
    t = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
    if not t:
        c.close(); await update.message.reply_text("Bunday task yo'q."); return
    c.execute("UPDATE tasks SET status='bajarildi', result=? WHERE id=?", (izoh, tid))
    c.commit(); c.close()
    mtxt = f"\n📐 Tikuv haqi: {t['metr']:g} m × {TIKUV_NARX:,} = {t['metr']*TIKUV_NARX:,.0f} so'm" if t["metr"] else ""
    try:
        await context.bot.send_message(
            BOSH_ADMIN_ID,
            f"✅ TASK BAJARILDI №{tid} (Buyurtma №{t['order_id']})\n"
            f"👤 {update.effective_user.full_name}\n💬 {izoh}{mtxt}")
    except Exception:
        pass
    await update.message.reply_text(f"✅ Task №{tid} yakunlandi.")


async def sabab_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2 or not args[0].isdigit():
        await update.message.reply_text("Format: /sabab TASK_ID sabab"); return
    try:
        await context.bot.send_message(
            BOSH_ADMIN_ID,
            f"💬 KECHIKISH SABABI — Task №{args[0]}\n"
            f"👤 {update.effective_user.full_name}\n📝 {' '.join(args[1:])}")
    except Exception:
        pass
    await update.message.reply_text("Sabab bosh adminga yetkazildi.")


async def tasklar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_staff(get_role(update.effective_user.id)): return
    c = db()
    rows = c.execute("SELECT * FROM tasks WHERE status IN ('ochiq','kechikdi') "
                     "ORDER BY deadline LIMIT 20").fetchall()
    c.close()
    if not rows:
        await update.message.reply_text("Ochiq task yo'q. 🎉"); return
    s = "📌 OCHIQ TASKLAR:\n\n"
    for t in rows:
        em = "⚠️" if t["status"] == "kechikdi" else "🕒"
        s += (f"{em} №{t['id']} (buyurtma №{t['order_id']}) → {t['assignee_name']}\n"
              f"    {t['text'][:60]}\n    muddat: {t['deadline'][:16]}\n")
    await update.message.reply_text(s)


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bazani fayl qilib yuboradi (faqat bosh admin)."""
    if update.effective_user.id != BOSH_ADMIN_ID: return
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(
                f, filename=f"hisobot_{datetime.now():%Y%m%d_%H%M}.db",
                caption="💾 Baza zaxira nusxasi")
    except Exception as e:
        await update.message.reply_text(f"Xatolik: {e}")


async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = db()
    rows = c.execute("SELECT created_at, report FROM calculations WHERE user_id=? "
                     "ORDER BY id DESC LIMIT 5", (update.effective_user.id,)).fetchall()
    c.close()
    if not rows:
        await update.message.reply_text("Hozircha saqlangan hisob-kitob yo'q."); return
    for r in rows:
        await update.message.reply_text(f"🕒 {r['created_at']}\n\n{r['report'][:3800]}")


# ======================= API SERVER =======================
def validate_init_data(init_data):
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        h = data.pop("hash", None)
        if not h: return None
        dcs = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
        if calc != h: return None
        return json.loads(data.get("user", "{}"))
    except Exception:
        return None


def api_user(request):
    init_data = request.headers.get("X-Init-Data", "")
    u = validate_init_data(init_data) if init_data else None
    if u and u.get("id"):
        name = " ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or "Foydalanuvchi"
        role = get_role(u["id"]) or "mijoz"
        return {"id": u["id"], "name": name, "role": role}
    # DEV rejim FAQAT lokal kompyuterdan (localhost) — telefon/tunnel so'rovlariga EMAS!
    host = request.headers.get("Host", "")
    if DEV_MODE and (host.startswith("localhost") or host.startswith("127.")):
        return {"id": BOSH_ADMIN_ID, "name": "Dev Admin", "role": "bosh_admin"}
    return None


def jsonresp(data, status=200):
    return web.json_response(data, status=status,
                             dumps=lambda d: json.dumps(d, ensure_ascii=False))


@web.middleware
async def cors_mw(request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        try:
            resp = await handler(request)
        except web.HTTPException as e:
            resp = e
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Init-Data"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def make_api(tg_app: Application):
    bot = tg_app.bot

    def load_order(oid):
        c = db()
        r = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        c.close(); return r

    async def me(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        c = db()
        row = c.execute("SELECT phone FROM users WHERE id=?", (u["id"],)).fetchone()
        c.close()
        return jsonresp({**u, "admin": is_staff(u["role"]),
                         "phone": (row["phone"] if row else "") or "",
                         "rol_label": ROL_LABEL.get(u["role"], u["role"])})

    async def orders_list(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        role = u["role"]
        c = db()
        if is_staff(role):
            rows = c.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
        elif role == "tikuvchi":
            rows = c.execute("""SELECT DISTINCT o.* FROM orders o
                LEFT JOIN tasks t ON t.order_id=o.id
                WHERE o.user_id=? OR t.assignee_id=? ORDER BY o.id DESC LIMIT 100""",
                (u["id"], u["id"])).fetchall()
        else:
            rows = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 50",
                             (u["id"],)).fetchall()
        overdue = {x["order_id"]: x["n"] for x in c.execute(
            "SELECT order_id, COUNT(*) n FROM tasks WHERE status='kechikdi' GROUP BY order_id")}
        c.close()
        return jsonresp({"orders": [
            {"id": r["id"], "full_name": r["full_name"], "status": r["status"],
             "client_name": r["client_name"] or "",
             "overdue": overdue.get(r["id"], 0),
             "created_at": r["created_at"], "assigned_name": r["assigned_name"],
             "can_manage": can_manage(r, u["id"], role),
             "snippet": (r["text"] or "")[:120]} for r in rows],
            "role": role, "admin": is_staff(role)})

    async def order_create(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text: return jsonresp({"error": "empty"}, 400)
        photos = [p for p in (body.get("photos") or []) if isinstance(p, str) and p.startswith("data:image")]
        c = db()
        usr = c.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone()
        c.close()
        username = usr["username"] if usr else ""
        state = body.get("state")
        oid = await create_order(bot, u["id"], username, u["name"], text, photos,
                                 client_name=(body.get("client_name") or "").strip(),
                                 client_phone=(body.get("client_phone") or "").strip(),
                                 state_json=json.dumps(state, ensure_ascii=False) if state else "")
        try:
            await bot.send_message(u["id"], f"✅ Buyurtmangiz №{oid} qabul qilindi!")
        except Exception:
            pass
        return jsonresp({"ok": True, "order_id": oid})

    async def order_detail(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r or not can_view(r, u["id"], u["role"]):
            return jsonresp({"error": "not_found"}, 404)
        c = db()
        comments = c.execute("SELECT * FROM comments WHERE order_id=? ORDER BY id", (oid,)).fetchall()
        tasks = c.execute("SELECT * FROM tasks WHERE order_id=? ORDER BY id DESC", (oid,)).fetchall()
        hist = c.execute("SELECT * FROM history WHERE order_id=? ORDER BY id", (oid,)).fetchall()
        fotos = c.execute("SELECT * FROM photos WHERE order_id=? ORDER BY id", (oid,)).fetchall()
        items = c.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (oid,)).fetchall()
        seams = c.execute("SELECT * FROM seams WHERE order_id=? ORDER BY id", (oid,)).fetchall()
        expenses = c.execute("SELECT * FROM expenses WHERE order_id=? ORDER BY id", (oid,)).fetchall()
        cf_defs = c.execute("SELECT * FROM cf_defs ORDER BY pos, id").fetchall()
        cf_vals = {x["def_id"]: x["value"] for x in
                   c.execute("SELECT * FROM cf_vals WHERE order_id=?", (oid,))}
        calls_in = c.execute("SELECT COUNT(*) n FROM calls WHERE order_id=? AND type='in'",
                             (oid,)).fetchone()["n"]
        calls_out = c.execute("SELECT COUNT(*) n FROM calls WHERE order_id=? AND type='out'",
                              (oid,)).fetchone()["n"]
        c.close()
        try:
            days = (datetime.now() - datetime.fromisoformat(r["created_at"])).days
        except Exception:
            days = 0
        tasks_done = sum(1 for x in tasks if x["status"] == "bajarildi")
        tasks_over = sum(1 for x in tasks if x["status"] == "kechikdi")
        try:
            state = json.loads(r["state_json"]) if r["state_json"] else None
        except Exception:
            state = None
        return jsonresp({
            "state": state,
            "order": {"id": r["id"], "full_name": r["full_name"], "username": r["username"],
                      "status": r["status"], "text": r["text"],
                      "assigned_to": r["assigned_to"], "assigned_name": r["assigned_name"],
                      "delivery_date": r["delivery_date"] or "",
                      "delivery_address": r["delivery_address"] or "",
                      "currency": r["currency"] or "so'm", "note": r["note"] or "",
                      "client_name": r["client_name"] or "", "client_phone": r["client_phone"] or "",
                      "source": r["source"] or "Ilova",
                      "created_at": r["created_at"], "updated_at": r["updated_at"]},
            "comments": [{"id": x["id"], "name": x["name"], "text": x["text"],
                          "at": x["created_at"], "mine": x["user_id"] == u["id"]} for x in comments],
            "tasks": [{"id": x["id"], "assignee": x["assignee_name"], "text": x["text"],
                       "deadline": x["deadline"], "status": x["status"], "metr": x["metr"],
                       "result": x["result"]} for x in tasks],
            "history": [{"status": x["status"], "by": x["by_name"], "at": x["created_at"]}
                        for x in hist],
            "photos": [{"id": x["id"], "data": x["data"], "by": x["by_name"]} for x in fotos],
            "items": [{"id": x["id"], "name": x["name"], "unit": x["unit"],
                       "warehouse": x["warehouse"], "qty": x["qty"], "price": x["price"],
                       "discount": x["discount"]} for x in items],
            "seams": [{"id": x["id"], "tikuvchi_id": x["tikuvchi_id"],
                       "tikuvchi": x["tikuvchi_name"], "work": x["work"], "fee": x["fee"],
                       "status": x["status"], "note": x["note"]} for x in seams],
            "expenses": [{"id": x["id"], "name": x["name"], "amount": x["amount"],
                          "by": x["by_name"]} for x in expenses],
            "cf": {"defs": [{"id": x["id"], "name": x["name"]} for x in cf_defs],
                   "vals": cf_vals},
            "stats": {"source": r["source"] or "Ilova", "created_at": r["created_at"],
                      "days": days, "calls_in": calls_in, "calls_out": calls_out,
                      "tasks_done": tasks_done, "tasks_over": tasks_over,
                      "notes": len(comments)},
            "can_manage": can_manage(r, u["id"], u["role"]),
            "role": u["role"], "admin": is_staff(u["role"]),
            "bosh": u["role"] == "bosh_admin"})

    async def order_status(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r: return jsonresp({"error": "not_found"}, 404)
        if not can_manage(r, u["id"], u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        status = body.get("status")
        if status not in STATUSLAR: return jsonresp({"error": "bad_status"}, 400)
        await set_status(bot, oid, status, u["name"])
        await notify_staff(bot, f"🔄 Buyurtma №{oid}: {STATUS_LABEL[status]} — {u['name']}",
                           exclude=u["id"])
        return jsonresp({"ok": True})

    async def order_assign(request):
        u = api_user(request)
        if not u or u["role"] != "bosh_admin": return jsonresp({"error": "forbidden"}, 403)
        oid = int(request.match_info["id"])
        body = await request.json()
        target = find_user(str(body.get("user_id", "")))
        if not target: return jsonresp({"error": "user_not_found"}, 400)
        c = db()
        c.execute("UPDATE orders SET assigned_to=?, assigned_name=?, updated_at=? WHERE id=?",
                  (target["id"], target["full_name"], now(), oid))
        c.commit(); c.close()
        try:
            await bot.send_message(target["id"],
                f"📋 Sizga BUYURTMA №{oid} biriktirildi! Ilovada ko'ring.")
        except Exception:
            pass
        return jsonresp({"ok": True})

    async def order_comment(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r or not can_view(r, u["id"], u["role"]): return jsonresp({"error": "not_found"}, 404)
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text: return jsonresp({"error": "empty"}, 400)
        c = db()
        c.execute("INSERT INTO comments(order_id,user_id,name,text,created_at) VALUES(?,?,?,?,?)",
                  (oid, u["id"], u["name"], text, now()))
        c.commit(); c.close()
        if is_staff(u["role"]):
            # mijozga TOZA matn (sarlavhasiz)
            clean = re.sub(r"^@[^:]{1,40}:\s*", "", text)
            try: await bot.send_message(r["user_id"], clean)
            except Exception: pass
            await notify_staff(bot, f"💬 Buyurtma №{oid} — {u['name']}:\n{text}",
                               exclude=u["id"])
        else:
            msg = f"💬 Buyurtma №{oid} — {u['name']}:\n{text}"
            if r["assigned_to"]:
                try: await bot.send_message(r["assigned_to"], msg)
                except Exception: pass
            else:
                try:
                    await bot.send_message(
                        BOSH_ADMIN_ID,
                        msg + "\n\n⚠️ Mas'ul shaxs biriktirilmagan — ilovada buyurtmani "
                              "ochib mas'ul biriktiring.")
                except Exception:
                    pass
        return jsonresp({"ok": True})

    async def order_photo(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        allowed = r and (can_manage(r, u["id"], u["role"]) or r["user_id"] == u["id"])
        if not allowed: return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        p = body.get("photo") or ""
        if not p.startswith("data:image"): return jsonresp({"error": "bad_photo"}, 400)
        c = db()
        c.execute("INSERT INTO photos(order_id,data,by_name,created_at) VALUES(?,?,?,?)",
                  (oid, p, u["name"], now()))
        c.commit(); c.close()
        await notify_staff(bot, f"📷 Buyurtma №{oid}ga yangi rasm qo'shildi — {u['name']}",
                           exclude=u["id"])
        return jsonresp({"ok": True})

    async def order_task(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r: return jsonresp({"error": "not_found"}, 404)
        if not can_manage(r, u["id"], u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        assignee = find_user(str(body.get("assignee_id", "")))
        if not assignee: return jsonresp({"error": "user_not_found"}, 400)
        text = (body.get("text") or "").strip()
        if not text: return jsonresp({"error": "empty"}, 400)
        dl_raw = body.get("deadline") or ""
        try:
            deadline = datetime.fromisoformat(dl_raw)
        except ValueError:
            deadline = datetime.now() + timedelta(hours=24)
        try:
            metr = float(body.get("metr") or 0)
        except (TypeError, ValueError):
            metr = 0
        tid = await make_task(bot, tg_app.job_queue, oid, assignee, deadline, text,
                              u["name"], metr)
        return jsonresp({"ok": True, "task_id": tid})

    async def users_list(request):
        u = api_user(request)
        if not u or not is_staff(u["role"]): return jsonresp({"error": "auth"}, 401)
        c = db()
        rows = c.execute("SELECT id, username, full_name, role FROM users "
                         "WHERE role NOT IN ('', 'mijoz') AND role NOT LIKE 'pending%' "
                         "ORDER BY full_name").fetchall()
        c.close()
        return jsonresp({"users": [{"id": r["id"], "username": r["username"],
                                    "full_name": r["full_name"],
                                    "role": r["role"],
                                    "rol_label": ROL_LABEL.get(r["role"], r["role"])}
                                   for r in rows]})

    async def products_list(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        staff = is_staff(u["role"])
        c = db()
        rows = c.execute("SELECT * FROM products ORDER BY category, id DESC LIMIT 200").fetchall()
        # izohlar: admin hammasini, boshqalar faqat o'zinikini ko'radi
        if staff:
            crows = c.execute("SELECT * FROM product_comments ORDER BY id").fetchall()
        else:
            crows = c.execute("SELECT * FROM product_comments WHERE user_id=? ORDER BY id",
                              (u["id"],)).fetchall()
        c.close()
        cmap = {}
        for x in crows:
            cmap.setdefault(x["product_id"], []).append(
                {"name": x["name"], "text": x["text"], "at": x["created_at"],
                 "mine": x["user_id"] == u["id"]})
        items = []
        for r in rows:
            img = None
            if r["photo_data"]: img = r["photo_data"]
            elif r["photo_file_id"]: img = "/api/photo/" + r["photo_file_id"]
            items.append({"id": r["id"], "name": r["name"], "category": r["category"] or "boshqa",
                          "price": r["price"], "qty": r["qty"], "img": img,
                          "comments": cmap.get(r["id"], [])})
        return jsonresp({"products": items, "admin": staff, "role": u["role"],
                         "bosh": u["role"] == "bosh_admin"})

    async def product_comment(request):
        """Mahsulot tagiga izoh (admin va o'ziga ko'rinadi)."""
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        pid = int(request.match_info["pid"])
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text: return jsonresp({"error": "empty"}, 400)
        c = db()
        p = c.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not p:
            c.close(); return jsonresp({"error": "not_found"}, 404)
        c.execute("INSERT INTO product_comments(product_id,user_id,name,text,created_at) "
                  "VALUES(?,?,?,?,?)", (pid, u["id"], u["name"], text, now()))
        c.commit(); c.close()
        if not is_staff(u["role"]):
            await notify_staff(bot,
                f"💬 MAHSULOT IZOHI — {p['name']}\n👤 {u['name']}: {text[:500]}")
        return jsonresp({"ok": True})

    async def products_add(request):
        u = api_user(request)
        if not u or u["role"] != "bosh_admin": return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        name = (body.get("name") or "").strip()
        cat = (body.get("category") or "boshqa").strip().lower()
        if not name: return jsonresp({"error": "empty"}, 400)
        try:
            price = float(body.get("price") or 0)
            qty = float(body.get("qty") or 0)
        except (TypeError, ValueError):
            return jsonresp({"error": "bad_number"}, 400)
        photo = body.get("photo") or None
        if photo and not photo.startswith("data:image"): photo = None
        c = db()
        c.execute("INSERT INTO products(name,category,price,qty,photo_data,added_by,created_at) "
                  "VALUES(?,?,?,?,?,?,?)", (name, cat, price, qty, photo, u["id"], now()))
        c.commit(); c.close()
        return jsonresp({"ok": True})

    async def product_update(request):
        """Mahsulotni tahrirlash yoki o'chirish (faqat bosh admin)."""
        u = api_user(request)
        if not u or u["role"] != "bosh_admin": return jsonresp({"error": "forbidden"}, 403)
        pid = int(request.match_info["pid"])
        body = await request.json()
        c = db()
        if body.get("delete"):
            c.execute("DELETE FROM products WHERE id=?", (pid,))
            c.execute("DELETE FROM product_comments WHERE product_id=?", (pid,))
        else:
            for k in ("name", "category", "price", "qty"):
                if k in body:
                    c.execute(f"UPDATE products SET {k}=? WHERE id=?", (body[k], pid))
            photo = body.get("photo")
            if photo and photo.startswith("data:image"):
                c.execute("UPDATE products SET photo_data=?, photo_file_id=NULL WHERE id=?",
                          (photo, pid))
        c.commit(); c.close()
        return jsonresp({"ok": True})

    async def order_delete(request):
        """Buyurtmani butunlay o'chirish (faqat bosh admin)."""
        u = api_user(request)
        if not u or u["role"] != "bosh_admin": return jsonresp({"error": "forbidden"}, 403)
        oid = int(request.match_info["id"])
        c = db()
        for tbl in ("comments", "history", "photos", "order_items", "seams",
                    "calls", "cf_vals", "expenses", "tasks"):
            c.execute(f"DELETE FROM {tbl} WHERE order_id=?", (oid,))
        c.execute("DELETE FROM orders WHERE id=?", (oid,))
        c.commit(); c.close()
        return jsonresp({"ok": True})

    async def photo_proxy(request):
        fid = request.match_info["fid"]
        if fid in PHOTO_CACHE:
            return web.Response(body=PHOTO_CACHE[fid], content_type="image/jpeg")
        try:
            f = await bot.get_file(fid)
            buf = bytes(await f.download_as_bytearray())
            if len(PHOTO_CACHE) < 200: PHOTO_CACHE[fid] = buf
            return web.Response(body=buf, content_type="image/jpeg")
        except Exception:
            return web.Response(status=404)

    async def order_fields(request):
        """Umumiy tab maydonlarini saqlash."""
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r: return jsonresp({"error": "not_found"}, 404)
        if not can_manage(r, u["id"], u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        if "state" in body and body["state"]:
            body["state_json"] = json.dumps(body["state"], ensure_ascii=False)
        allowed = ["delivery_date", "delivery_address", "currency", "note",
                   "client_name", "client_phone", "source", "text", "state_json"]
        sets, vals = [], []
        for k in allowed:
            if k in body:
                sets.append(f"{k}=?"); vals.append(str(body[k] or ""))
        if not sets: return jsonresp({"ok": True})
        vals += [now(), oid]
        c = db()
        c.execute(f"UPDATE orders SET {', '.join(sets)}, updated_at=? WHERE id=?", vals)
        c.commit(); c.close()
        if "text" in body:
            # chek qayta hisoblandi — hammaga xabar
            add_history(oid, r["status"], u["name"] + " (chek yangilandi)")
            try:
                await bot.send_message(
                    r["user_id"],
                    f"♻️ Buyurtmangiz №{oid} hisob-kitobi yangilandi.\n"
                    f"Yangi chekni ilovadagi «Buyurtmalar» bo'limida ko'ring.")
            except Exception:
                pass
            await notify_staff(bot, f"♻️ Buyurtma №{oid} cheki qayta hisoblandi — {u['name']}",
                               exclude=u["id"])
        return jsonresp({"ok": True})

    async def order_item(request):
        """Mahsulot qatori: qo'shish / o'zgartirish / o'chirish."""
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r: return jsonresp({"error": "not_found"}, 404)
        if not can_manage(r, u["id"], u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        c = db()
        iid = body.get("item_id")
        if iid and body.get("delete"):
            c.execute("DELETE FROM order_items WHERE id=? AND order_id=?", (iid, oid))
        elif iid:
            for k in ["name", "unit", "warehouse", "qty", "price", "discount"]:
                if k in body:
                    c.execute(f"UPDATE order_items SET {k}=? WHERE id=? AND order_id=?",
                              (body[k], iid, oid))
        else:
            c.execute("INSERT INTO order_items(order_id,name,unit,warehouse,qty,price,discount,created_at) "
                      "VALUES(?,?,?,?,?,?,?,?)",
                      (oid, body.get("name", ""), body.get("unit", "m"),
                       body.get("warehouse", "Asosiy"), body.get("qty", 0),
                       body.get("price", 0), body.get("discount", 0), now()))
            iid = c.execute("SELECT last_insert_rowid() i").fetchone()["i"]
        c.commit(); c.close()
        return jsonresp({"ok": True, "item_id": iid})

    async def order_seam(request):
        """Tikuvchi biriktirish: qo'shish / holat / o'chirish."""
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r: return jsonresp({"error": "not_found"}, 404)
        if not can_manage(r, u["id"], u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        c = db()
        sid = body.get("seam_id")
        if sid and body.get("delete"):
            c.execute("DELETE FROM seams WHERE id=? AND order_id=?", (sid, oid))
            c.commit(); c.close()
            return jsonresp({"ok": True})
        if sid:
            for k in ["work", "fee", "status", "note"]:
                if k in body:
                    c.execute(f"UPDATE seams SET {k}=? WHERE id=? AND order_id=?",
                              (body[k], sid, oid))
            c.commit()
            s = c.execute("SELECT * FROM seams WHERE id=?", (sid,)).fetchone()
            c.close()
            if body.get("status") == "topshirildi" and s:
                try:
                    await bot.send_message(
                        BOSH_ADMIN_ID,
                        f"🧵 TOPSHIRILDI — Buyurtma №{oid}\n"
                        f"👤 {s['tikuvchi_name']} · {s['work']}\n"
                        f"💰 Haqi: {s['fee']:,.0f} so'm")
                except Exception:
                    pass
            return jsonresp({"ok": True})
        tik = find_user(str(body.get("tikuvchi_id", "")))
        if not tik: return jsonresp({"error": "user_not_found"}, 400)
        try:
            fee = float(body.get("fee") or 0)
        except (TypeError, ValueError):
            fee = 0
        c.execute("INSERT INTO seams(order_id,tikuvchi_id,tikuvchi_name,work,fee,status,note,created_at) "
                  "VALUES(?,?,?,?,?,?,?,?)",
                  (oid, tik["id"], tik["full_name"], body.get("work", ""),
                   fee, "biriktirildi", body.get("note", ""), now()))
        c.commit(); c.close()
        try:
            await bot.send_message(
                tik["id"],
                f"🧵 Sizga BUYURTMA №{oid} biriktirildi!\n"
                f"Ish: {body.get('work','')}\n💰 Haqi: {fee:,.0f} so'm\n"
                f"Ilovada ko'ring.")
        except Exception:
            pass
        return jsonresp({"ok": True})

    async def order_expense(request):
        """Qo'shimcha xarajat: qo'shish / o'chirish."""
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r: return jsonresp({"error": "not_found"}, 404)
        if not can_manage(r, u["id"], u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        c = db()
        eid = body.get("expense_id")
        if eid and body.get("delete"):
            c.execute("DELETE FROM expenses WHERE id=? AND order_id=?", (eid, oid))
        else:
            try:
                amount = float(body.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0
            c.execute("INSERT INTO expenses(order_id,name,amount,by_name,created_at) VALUES(?,?,?,?,?)",
                      (oid, body.get("name", ""), amount, u["name"], now()))
        c.commit(); c.close()
        return jsonresp({"ok": True})

    async def task_result(request):
        """Topshiriq natijasini kiritish (yakunlash). Bosh adminga xabar;
        'telefon ko'tarmadi' bo'lsa mijozga matn yuboriladi."""
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        tid = int(request.match_info["tid"])
        c = db()
        t = c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        if not t:
            c.close(); return jsonresp({"error": "not_found"}, 404)
        order = c.execute("SELECT * FROM orders WHERE id=?", (t["order_id"],)).fetchone()
        allowed = is_staff(u["role"]) or t["assignee_id"] == u["id"]
        if not allowed:
            c.close(); return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        result = (body.get("result") or "").strip()
        no_answer = bool(body.get("no_answer"))
        if no_answer and not result:
            result = "📵 Telefon ko'tarmadi"
        c.execute("UPDATE tasks SET status='bajarildi', result=? WHERE id=?",
                  (result or "—", tid))
        c.commit(); c.close()
        try:
            await bot.send_message(
                BOSH_ADMIN_ID,
                f"✅ TOPSHIRIQ YAKUNLANDI №{tid} (Buyurtma №{t['order_id']})\n"
                f"📝 Topshiriq: {t['text']}\n"
                f"👤 Bajardi: {u['name']}\n💬 Natija: {result or '—'}")
        except Exception:
            pass
        if no_answer and order:
            try:
                await bot.send_message(
                    order["user_id"],
                    f"Assalomu alaykum! 🙋‍♂️\n"
                    f"Buyurtmangiz №{order['id']} yuzasidan sizga qo'ng'iroq qildik, "
                    f"lekin javob bo'lmadi.\n\n"
                    f"Iltimos, bo'sh vaqt topib aloqaga chiqing yoki shu yerga yozib qoldiring:\n"
                    f"📞 +998 90 940 11 41")
            except Exception:
                pass
        return jsonresp({"ok": True})

    async def order_call(request):
        u = api_user(request)
        if not u or not is_staff(u["role"]): return jsonresp({"error": "forbidden"}, 403)
        oid = int(request.match_info["id"])
        body = await request.json()
        t = body.get("type")
        if t not in ("in", "out"): return jsonresp({"error": "bad_type"}, 400)
        c = db()
        c.execute("INSERT INTO calls(order_id,type,by_name,created_at) VALUES(?,?,?,?)",
                  (oid, t, u["name"], now()))
        c.commit(); c.close()
        return jsonresp({"ok": True})

    async def cf_defs_api(request):
        """Maxsus maydonlar: ro'yxat / qo'shish / tahrirlash / o'chirish / tartiblash."""
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        if request.method == "GET":
            c = db()
            rows = c.execute("SELECT * FROM cf_defs ORDER BY pos, id").fetchall()
            c.close()
            return jsonresp({"defs": [{"id": r["id"], "name": r["name"]} for r in rows]})
        if not is_staff(u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        c = db()
        if body.get("order"):
            for i, did in enumerate(body["order"]):
                c.execute("UPDATE cf_defs SET pos=? WHERE id=?", (i, did))
        elif body.get("delete") and body.get("id"):
            c.execute("DELETE FROM cf_defs WHERE id=?", (body["id"],))
            c.execute("DELETE FROM cf_vals WHERE def_id=?", (body["id"],))
        elif body.get("id"):
            c.execute("UPDATE cf_defs SET name=? WHERE id=?", (body.get("name", ""), body["id"]))
        elif body.get("name"):
            pos = c.execute("SELECT COALESCE(MAX(pos),0)+1 p FROM cf_defs").fetchone()["p"]
            c.execute("INSERT INTO cf_defs(name,pos) VALUES(?,?)", (body["name"], pos))
        c.commit(); c.close()
        return jsonresp({"ok": True})

    async def cf_val_api(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        oid = int(request.match_info["id"])
        r = load_order(oid)
        if not r: return jsonresp({"error": "not_found"}, 404)
        if not can_manage(r, u["id"], u["role"]): return jsonresp({"error": "forbidden"}, 403)
        body = await request.json()
        c = db()
        c.execute("INSERT INTO cf_vals(order_id,def_id,value) VALUES(?,?,?) "
                  "ON CONFLICT(order_id,def_id) DO UPDATE SET value=excluded.value",
                  (oid, body.get("def_id"), str(body.get("value", ""))))
        c.commit(); c.close()
        return jsonresp({"ok": True})

    def tikuvchi_hisob(tid):
        """Tikuvchining yozilgan haqi, to'langani va balansi."""
        c = db()
        s = c.execute("""SELECT COALESCE(SUM(CASE WHEN status IN ('tikildi','topshirildi')
                THEN fee ELSE 0 END),0) yozilgan FROM seams WHERE tikuvchi_id=?""",
                (tid,)).fetchone()["yozilgan"]
        t = c.execute("""SELECT COALESCE(SUM(CASE WHEN status='bajarildi' THEN metr ELSE 0 END),0) m
                FROM tasks WHERE assignee_id=?""", (tid,)).fetchone()["m"]
        p = c.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE tikuvchi_id=?",
                      (tid,)).fetchone()["s"]
        c.close()
        yozilgan = round(s + t * TIKUV_NARX)
        return yozilgan, round(p), yozilgan - round(p)

    async def tikuvchi_detail(request):
        u = api_user(request)
        if not u or u["role"] != "bosh_admin": return jsonresp({"error": "forbidden"}, 403)
        tid = int(request.match_info["uid"])
        c = db()
        usr = c.execute("SELECT * FROM users WHERE id=?", (tid,)).fetchone()
        seams = c.execute("SELECT * FROM seams WHERE tikuvchi_id=? ORDER BY id DESC", (tid,)).fetchall()
        pays = c.execute("SELECT * FROM payments WHERE tikuvchi_id=? ORDER BY id DESC", (tid,)).fetchall()
        c.close()
        yozilgan, tolangan, balans = tikuvchi_hisob(tid)
        return jsonresp({
            "name": usr["full_name"] if usr else "?",
            "seams": [{"order_id": x["order_id"], "work": x["work"], "fee": x["fee"],
                       "status": x["status"], "at": (x["created_at"] or "")[:10]} for x in seams],
            "payments": [{"amount": x["amount"], "note": x["note"], "by": x["by_name"],
                          "at": (x["created_at"] or "")[:16].replace("T", " ")} for x in pays],
            "yozilgan": yozilgan, "tolangan": tolangan, "balans": balans})

    async def tikuvchi_pay(request):
        u = api_user(request)
        if not u or u["role"] != "bosh_admin": return jsonresp({"error": "forbidden"}, 403)
        tid = int(request.match_info["uid"])
        body = await request.json()
        try:
            amount = float(body.get("amount") or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0: return jsonresp({"error": "bad_amount"}, 400)
        tik = find_user(str(tid))
        c = db()
        c.execute("INSERT INTO payments(tikuvchi_id,tikuvchi_name,amount,note,by_name,created_at) "
                  "VALUES(?,?,?,?,?,?)",
                  (tid, tik["full_name"] if tik else "?", amount,
                   body.get("note", ""), u["name"], now()))
        c.commit(); c.close()
        yozilgan, tolangan, balans = tikuvchi_hisob(tid)
        try:
            await bot.send_message(
                tid, f"💵 Sizga {amount:,.0f} so'm to'landi"
                     + (f" ({body.get('note')})" if body.get("note") else "")
                     + f"\nQolgan balans: {balans:,.0f} so'm")
        except Exception:
            pass
        return jsonresp({"ok": True, "balans": balans})

    async def reports(request):
        u = api_user(request)
        if not u: return jsonresp({"error": "auth"}, 401)
        role, uid = u["role"], u["id"]
        c = db()

        if role in ("", "mijoz"):
            st = {s: 0 for s in STATUSLAR}
            for r in c.execute("SELECT status, COUNT(*) n FROM orders WHERE user_id=? GROUP BY status", (uid,)):
                if r["status"] in st: st[r["status"]] = r["n"]
            cm = c.execute("SELECT COUNT(*) n FROM comments WHERE user_id=?", (uid,)).fetchone()["n"]
            pc = c.execute("SELECT COUNT(*) n FROM product_comments WHERE user_id=?", (uid,)).fetchone()["n"]
            c.close()
            return jsonresp({"role": "mijoz",
                             "my": {"by_status": st, "total": sum(st.values()),
                                    "comments": cm, "product_comments": pc}})

        if role == "tikuvchi":
            s = c.execute("""SELECT COUNT(*) jami,
                    COALESCE(SUM(status IN ('biriktirildi','jarayonda')),0) faol,
                    COALESCE(SUM(status IN ('tikildi','topshirildi')),0) tayyor,
                    COALESCE(SUM(CASE WHEN status IN ('tikildi','topshirildi') THEN fee ELSE 0 END),0) tolanadigan,
                    COALESCE(SUM(fee),0) jami_haq
                    FROM seams WHERE tikuvchi_id=?""", (uid,)).fetchone()
            t = c.execute("""SELECT COALESCE(SUM(CASE WHEN status='bajarildi' THEN metr ELSE 0 END),0) metr,
                    COUNT(*) jami, COALESCE(SUM(status='bajarildi'),0) bajarildi,
                    COALESCE(SUM(status='kechikdi'),0) kechikdi
                    FROM tasks WHERE assignee_id=?""", (uid,)).fetchone()
            c.close()
            task_haq = round((t["metr"] or 0) * TIKUV_NARX)
            return jsonresp({"role": "tikuvchi", "tikuv_narx": TIKUV_NARX,
                             "salary": {"ishlar": s["jami"], "faol": s["faol"], "tayyor": s["tayyor"],
                                        "tolanadigan": round(s["tolanadigan"]),
                                        "jami_haq": round(s["jami_haq"]),
                                        "task_metr": t["metr"] or 0, "task_haq": task_haq,
                                        "task_bajarildi": t["bajarildi"], "task_kechikdi": t["kechikdi"],
                                        "umumiy_tolanadigan": round(s["tolanadigan"]) + task_haq}})

        if role == "admin":
            st = {s: 0 for s in STATUSLAR}
            for r in c.execute("SELECT status, COUNT(*) n FROM orders "
                               "WHERE assigned_to=? OR user_id=? GROUP BY status", (uid, uid)):
                if r["status"] in st: st[r["status"]] = r["n"]
            workers = []
            for r in c.execute("""SELECT t.assignee_name nm, COUNT(*) jami,
                    COALESCE(SUM(t.status='bajarildi'),0) b, COALESCE(SUM(t.status='kechikdi'),0) k,
                    COALESCE(SUM(t.status='ochiq'),0) o
                    FROM tasks t JOIN orders od ON od.id=t.order_id
                    WHERE od.assigned_to=? OR od.user_id=? GROUP BY t.assignee_id""", (uid, uid)):
                workers.append({"name": r["nm"], "role": "", "jami": r["jami"],
                                "bajarildi": r["b"], "kechikdi": r["k"], "ochiq": r["o"],
                                "metr": 0, "haq": 0})
            c.close()
            return jsonresp({"role": "admin",
                             "orders": {"by_status": st, "total": sum(st.values())},
                             "workers": workers})

        # bosh_admin — global hisobot
        st = {s: 0 for s in STATUSLAR}
        for r in c.execute("SELECT status, COUNT(*) n FROM orders GROUP BY status"):
            if r["status"] in st: st[r["status"]] = r["n"]
        total = c.execute("SELECT COUNT(*) n FROM orders").fetchone()["n"]
        workers = []
        for r in c.execute("""SELECT t.assignee_id, t.assignee_name,
                COALESCE(u.role,'') role,
                SUM(t.status='ochiq') ochiq, SUM(t.status='bajarildi') bajarildi,
                SUM(t.status='kechikdi') kechikdi, COUNT(*) jami,
                SUM(CASE WHEN t.status='bajarildi' THEN t.metr ELSE 0 END) metr
                FROM tasks t LEFT JOIN users u ON u.id=t.assignee_id
                GROUP BY t.assignee_id ORDER BY jami DESC"""):
            w = {"name": r["assignee_name"], "role": ROL_LABEL.get(r["role"], r["role"] or "—"),
                 "ochiq": r["ochiq"] or 0, "bajarildi": r["bajarildi"] or 0,
                 "kechikdi": r["kechikdi"] or 0, "jami": r["jami"],
                 "metr": r["metr"] or 0}
            w["haq"] = round((r["metr"] or 0) * TIKUV_NARX) if r["role"] == "tikuvchi" else 0
            workers.append(w)
        tikuvchilar = []
        for r in c.execute("""SELECT tikuvchi_id, tikuvchi_name, COUNT(*) jami,
                SUM(status IN ('biriktirildi','jarayonda')) faol,
                SUM(status IN ('tikildi','topshirildi')) tayyor,
                SUM(CASE WHEN status IN ('tikildi','topshirildi') THEN fee ELSE 0 END) tolanadigan,
                SUM(fee) jami_haq
                FROM seams GROUP BY tikuvchi_id ORDER BY jami DESC"""):
            yz, tl, bal = tikuvchi_hisob(r["tikuvchi_id"])
            tikuvchilar.append({"id": r["tikuvchi_id"], "name": r["tikuvchi_name"],
                                "jami": r["jami"],
                                "faol": r["faol"] or 0, "tayyor": r["tayyor"] or 0,
                                "tolanadigan": round(r["tolanadigan"] or 0),
                                "jami_haq": round(r["jami_haq"] or 0),
                                "yozilgan": yz, "tolangan": tl, "balans": bal})
        members_total = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        by_role = {}
        for r in c.execute("SELECT COALESCE(NULLIF(role,''),'mijoz') rr, COUNT(*) n FROM users "
                           "WHERE role NOT LIKE 'pending%' GROUP BY rr"):
            by_role[ROL_LABEL.get(r["rr"], r["rr"])] = r["n"]
        latest = [{"name": r["full_name"], "username": r["username"],
                   "role": ROL_LABEL.get((r["role"] or "mijoz"), r["role"] or "mijoz"),
                   "at": (r["created_at"] or "")[:10]}
                  for r in c.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT 10")]
        c.close()
        return jsonresp({"role": "bosh_admin",
                         "orders": {"by_status": st, "total": total},
                         "workers": workers, "tikuvchilar": tikuvchilar,
                         "tikuv_narx": TIKUV_NARX,
                         "members": {"total": members_total, "by_role": by_role,
                                     "latest": latest}})

    api = web.Application(middlewares=[cors_mw], client_max_size=20*1024*1024)
    api.router.add_get("/api/me", me)
    api.router.add_get("/api/orders", orders_list)
    api.router.add_post("/api/orders", order_create)
    api.router.add_get("/api/orders/{id}", order_detail)
    api.router.add_post("/api/orders/{id}/status", order_status)
    api.router.add_post("/api/orders/{id}/assign", order_assign)
    api.router.add_post("/api/orders/{id}/comment", order_comment)
    api.router.add_post("/api/orders/{id}/photo", order_photo)
    api.router.add_post("/api/orders/{id}/task", order_task)
    api.router.add_post("/api/orders/{id}/fields", order_fields)
    api.router.add_post("/api/orders/{id}/item", order_item)
    api.router.add_post("/api/orders/{id}/seam", order_seam)
    api.router.add_post("/api/orders/{id}/call", order_call)
    api.router.add_post("/api/orders/{id}/expense", order_expense)
    api.router.add_post("/api/tasks/{tid}/result", task_result)
    api.router.add_post("/api/orders/{id}/cf", cf_val_api)
    api.router.add_get("/api/cf", cf_defs_api)
    api.router.add_post("/api/cf", cf_defs_api)
    api.router.add_get("/api/users", users_list)
    api.router.add_get("/api/tikuvchi/{uid}", tikuvchi_detail)
    api.router.add_post("/api/tikuvchi/{uid}/pay", tikuvchi_pay)
    api.router.add_get("/api/products", products_list)
    api.router.add_post("/api/products", products_add)
    api.router.add_post("/api/products/{pid}/comment", product_comment)
    api.router.add_post("/api/products/{pid}/update", product_update)
    api.router.add_post("/api/orders/{id}/delete", order_delete)
    api.router.add_get("/api/photo/{fid}", photo_proxy)
    api.router.add_get("/api/reports", reports)
    async def health(request):
        return web.Response(text="Rayyon Pardalar API — ishlayapti ✅")
    api.router.add_get("/", health)
    api.router.add_route("OPTIONS", "/{tail:.*}", lambda r: web.Response())
    return api


def start_tunnel():
    global API_URL
    if IS_CLOUD:   # bulutda (Railway) domen bor — tunnel kerak emas
        return
    if not os.path.exists(CLOUDFLARED):
        log.warning("cloudflared.exe topilmadi — tunnel ochilmadi.")
        return
    def run():
        global API_URL
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            p = subprocess.Popen(
                [CLOUDFLARED, "tunnel", "--url", f"http://localhost:{API_PORT}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                creationflags=flags)
            for line in p.stdout:
                if "trycloudflare.com" in line and "https://" in line:
                    for tok in line.split():
                        if tok.startswith("https://") and "trycloudflare.com" in tok:
                            API_URL = tok.strip().rstrip("/")
                            log.info("🌐 Tunnel ochildi: %s", API_URL)
                            break
                if API_URL: break
            for _ in p.stdout:
                pass
        except Exception as e:
            log.warning("Tunnel xatosi: %s", e)
    threading.Thread(target=run, daemon=True).start()


async def post_init(app: Application):
    api = make_api(app)
    runner = web.AppRunner(api)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", API_PORT)
    await site.start()
    log.info("API server: 0.0.0.0:%s", API_PORT)
    if IS_CLOUD:
        log.info("☁️ Bulut rejimi (Railway) — API_URL: %s", API_URL)
    start_tunnel()

    async def set_default_menu(ctx):
        """Menyu tugmasini BARCHA foydalanuvchilar uchun API manziliga sozlaydi."""
        if not API_URL: return
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Ilova",
                                             web_app=WebAppInfo(url=webapp_url())))
            log.info("✅ Menyu tugmasi yangilandi (global) — /start shart emas")
        except Exception as e:
            log.warning("Menyu tugmasi xatosi: %s", e)

    async def daily_backup(ctx):
        """Har kuni bazani bosh adminga fayl qilib yuboradi."""
        try:
            with open(DB_PATH, "rb") as f:
                await app.bot.send_document(
                    BOSH_ADMIN_ID, f,
                    filename=f"hisobot_{datetime.now():%Y%m%d}.db",
                    caption="💾 Kunlik avto-zaxira")
        except Exception as e:
            log.warning("Avto-zaxira xatosi: %s", e)

    if app.job_queue:
        app.job_queue.run_once(set_default_menu, 12)
        app.job_queue.run_once(set_default_menu, 45)
        local_tz = datetime.now().astimezone().tzinfo
        app.job_queue.run_daily(daily_backup, time=dtime(21, 0, tzinfo=local_tz))
        log.info("Kunlik avto-zaxira rejalashtirildi: har kuni 21:00")
    if app.job_queue:
        c = db()
        rows = c.execute("SELECT * FROM tasks WHERE status='ochiq'").fetchall()
        c.close()
        for t in rows:
            try:
                dl = datetime.fromisoformat(t["deadline"])
            except ValueError:
                continue
            when = dl if dl > datetime.now() else datetime.now() + timedelta(seconds=10)
            app.job_queue.run_once(task_deadline_check, when=when, data=t["id"],
                                   name=f"task:{t['id']}")


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        log.error("⚠️ KONFLIKT: bot BOSHQA JOYDA ham ishlab turibdi! "
                  "start-bot.bat orqali qayta ishga tushiring.")
    elif isinstance(err, (NetworkError, TimedOut)):
        log.warning("Internet uzilishi: %s (bot o'zi qayta ulanadi)", err)
    else:
        log.exception("Kutilmagan xato:", exc_info=err)


def main():
    if not BOT_TOKEN:
        raise SystemExit("❗ BOT_TOKEN ni to'ldiring.")
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("buyurtma", buyurtma_cmd))
    app.add_handler(CommandHandler("buyurtmalar", buyurtmalar_cmd))
    app.add_handler(CommandHandler("mahsulotlar", mahsulotlar_cmd))
    app.add_handler(CommandHandler("task", task_cmd))
    app.add_handler(CommandHandler("tasklar", tasklar_cmd))
    app.add_handler(CommandHandler("bajarildi", bajarildi_cmd))
    app.add_handler(CommandHandler("sabab", sabab_cmd))
    app.add_handler(CallbackQueryHandler(on_reg_btn, pattern=r"^reg2?:"))
    app.add_handler(CallbackQueryHandler(on_approve_btn, pattern=r"^(appr|rejr):"))
    app.add_handler(CallbackQueryHandler(on_status_btn, pattern=r"^st:"))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, on_webapp_data))
    app.add_handler(MessageHandler(filters.CONTACT, on_contact))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    print("Bot + API + rollar ishga tushdi. To'xtatish uchun Ctrl+C.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
