# 🪟 Parda Hisob-kitob — Telegram Mini App bot

Xona o'lchamlariga qarab **tul**, **parter (portyera)** va **zashitniy** uchun
necha metr mato ketishini hisoblab beruvchi bot. Interfeys — Telegram Mini App
(saytga o'xshash forma).

## Hisoblash mantig'i

| Mahsulot | Formula |
|----------|---------|
| **Zashitniy** | eni + **0.20 m** |
| **Parter** | bo'yi + **0.20 m** |
| **Tul** | eni × koeffitsient + **0.20 m** |

Tul koeffitsienti uchun uchala variant ham (×2, ×2.5, ×3) ko'rsatiladi.
Karniz turiga ko'ra **tavsiya** belgilanadi:

| Karniz turi | Tavsiya koeffitsient |
|-------------|----------------------|
| Pataloshniy (potolochniy) | ×3 |
| Turba (труба) | ×2 |
| Relsli | ×2.5 |

Har bir xona uchun karniz qatorlari soni va har bir qator turi alohida tanlanadi.

---

## Fayllar

| Fayl | Vazifasi |
|------|----------|
| `index.html` | Mini App — hisoblash interfeysi (brauzerda ham ishlaydi) |
| `bot.py` | Telegram bot — Mini App'ni ochadi, natijani SQLite'ga saqlaydi |
| `requirements.txt` | Python kutubxonalari |
| `hisobot.db` | (avtomatik yaratiladi) saqlangan hisoblar |

---

## 1-qadam. Tez sinash (Telegramsiz)

`index.html` faylni shunchaki brauzerda oching (ikki marta bosing).
Kalkulyator to'liq ishlaydi — "Natijani yuborish" o'rniga matnni nusxa oladi.

---

## 2-qadam. Botni ishga tushirish

### a) Token olish
1. Telegram'da [@BotFather](https://t.me/BotFather) ga yozing.
2. `/newbot` → nom va username bering → **token** oling.

### b) Mini App'ni internetga qo'yish (HTTPS shart)
Telegram Mini App faqat **HTTPS** manzilda ishlaydi. Variantlar:

- **GitHub Pages (bepul, tavsiya):**
  1. GitHub'da repozitoriy oching, `index.html` ni yuklang.
  2. Settings → Pages → Branch: `main` → Save.
  3. Manzil: `https://USERNAME.github.io/REPO/` — shu URL kerak bo'ladi.

- **Test uchun ngrok:**
  ```bash
  # index.html turgan papkada:
  python -m http.server 8080
  # boshqa oynada:
  ngrok http 8080
  ```
  ngrok bergan `https://xxxx.ngrok-free.app` — sizning URL.

### c) Sozlash
`bot.py` ichida to'ldiring (yoki muhit o'zgaruvchisi orqali bering):
```python
BOT_TOKEN  = "123456:ABC..."                       # BotFather tokeni
WEBAPP_URL = "https://USERNAME.github.io/REPO/"     # index.html manzili
```

### d) O'rnatish va ishga tushirish (Windows PowerShell)
```powershell
cd C:\Users\777\parda-hisob-bot
python -m pip install -r requirements.txt
python bot.py
```

Muhit o'zgaruvchisi bilan (tokenni faylga yozmaslik uchun) muqobil:
```powershell
$env:BOT_TOKEN  = "123456:ABC..."
$env:WEBAPP_URL = "https://USERNAME.github.io/REPO/"
python bot.py
```

---

## 3-qadam. Foydalanish
1. Botga `/start` yozing.
2. **🪟 Hisob-kitobni ochish** tugmasini bosing.
3. Xonalarni qo'shing, o'lchamlarni kiriting — natija darhol chiqadi.
4. **✅ Natijani yuborish** — hisob botga keladi va saqlanadi.
5. `/history` — oxirgi 5 ta saqlangan hisobni ko'rsatadi.

---

## Buyruqlar
| Buyruq | Vazifa |
|--------|--------|
| `/start` | Botni ochish + Mini App tugmasi |
| `/history` | Oxirgi hisob-kitoblar |

## Keyingi bosqichda qo'shsa bo'ladi
- Mijozlar ro'yxati va har bir mijozga hisoblarni biriktirish
- Narx (metr × narx) qo'shib umumiy summa chiqarish
- Excel/PDF hisobot eksporti
