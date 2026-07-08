# 🚂 Railway'ga joylash — bosqichma-bosqich

Kod tayyor (bot.py, Procfile, requirements.txt GitHub'da, tokensiz).
Railway o'zi HTTPS domen beradi — cloudflared tunnel kerak emas.

## Narxi
Railway'da doimiy trial yo'q — ~$5/oy (Hobby). Kichik bot + volume shunga sig'adi.

## Qadamlar

### 1. Akkaunt
[railway.app](https://railway.app) → **Login with GitHub** (parda-hisob-bot repo shu yerda).

### 2. Loyiha yaratish
- **New Project** → **Deploy from GitHub repo** → `AsadulloAbdullayev/parda-hisob-bot`
- Railway avtomatik quradi (Procfile'ni ko'radi: `web: python bot.py`)

### 3. Muhit o'zgaruvchilari (Variables)
Loyiha → **Variables** → quyidagilarni qo'shing:
| Nomi | Qiymati |
|------|---------|
| `BOT_TOKEN` | (kompyuterdagi `secrets_local.py` faylidagi token) |
| `DB_PATH` | `/data/hisobot.db` |

### 4. Volume (baza saqlanishi uchun — MUHIM!)
- Servisga o'ng tugma → **Add Volume**
- **Mount path:** `/data`
- (Bu bo'lmasa har deploy'da buyurtmalar o'chib ketadi!)

### 5. Domen olish
- Servis → **Settings** → **Networking** → **Generate Domain**
- Manzil chiqadi: `parda-production-xxxx.up.railway.app`
- Railway o'zi qayta deploy qiladi → bot bu manzilni oladi va Telegram menyusini yangilaydi

### 6. Tekshirish
- `https://SIZNING-DOMEN.up.railway.app/` ochilса **"...ishlayapti ✅"** chiqadi
- Telegram'da botga /start → «Ilova» tugmasi endi Railway manzilini ochadi

### 7. ⚠️ Kompyuterdagi botni O'CHIRING
Ikkala nusxa bir vaqtda ishlasa **Conflict** xatosi bo'ladi!
- `stop-bot.bat` ni bosing
- `C:\Users\777\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\parda-bot-autostart.bat` faylini **o'chiring**

Endi bot 24/7 Railway'da ishlaydi, kompyuterga bog'liq emas. Baza Volume'da saqlanadi.
Kunlik zaxira (21:00, Telegramga) Railway'da ham ishlaydi.
