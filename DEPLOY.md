# 🌐 Serverga joylash yo'riqnomasi

Bot hozir kompyuteringizda ishlaydi (avto-start + kunlik zaxira bilan).
Kompyuterga bog'liq bo'lmaslik uchun — VPS server kerak.

## Nega aynan VPS? (halol taqqoslash)

| Variant | Narxi | Baholash |
|---|---|---|
| **VPS server** ✅ | ~40-55 ming so'm/oy | 24/7, ma'lumotlar saqlanadi, hozirgi kod O'ZGARISHSIZ ko'chadi |
| Render/Railway (bepul) | 0 | ❌ Uxlab qoladi + **baza o'chib ketadi** (restartda) — CRM uchun yaroqsiz |
| PythonAnywhere | $5/oy | ❌ Bizning API arxitekturasiga mos emas (qayta yozish kerak) |
| Hozirgi kompyuter | 0 | Kompyuter o'chiq bo'lsa bot ishlamaydi |

Bizning arxitektura (bot + cloudflared tunnel + avto-menyu yangilash) **istalgan
Linux VPSga 10 daqiqada ko'chadi** — domen ham, SSL sozlash ham kerak emas.

## Tavsiya qilingan serverlar

1. **ahost.uz** (O'zbekiston) — VPS "Start": ~40-50 ming so'm/oy.
   Payme/Click bilan to'lanadi, o'zbekcha qo'llab-quvvatlash. **Eng qulayi.**
2. **Hetzner.com** (Germaniya) — CX22: €3.79/oy. Eng arzon xalqaro, karta kerak.
3. **Timeweb.cloud** — ~200 rub/oy, ruscha panel.

**Server talabi:** Ubuntu 22.04, 1 GB RAM, 10 GB disk — eng kichigi yetadi.

## Qadamlar (siz uchun — 10 daqiqa)

1. Yuqoridagi saytlardan birida akkaunt oching
2. VPS yarating: **Ubuntu 22.04**, eng kichik tarif
3. Sizga **IP manzil** va **root parol** beriladi (email'ga keladi)
4. Shu ikkisini Claude'ga yozing: `IP: x.x.x.x, parol: ...`

## Qolgani — Claude qiladi (SSH orqali avtomatik)

Claude serveringizga ulanib o'zi bajaradi:
```bash
apt update && apt install -y python3-pip python3-venv
# kodni ko'chirish, .venv, kutubxonalar
# cloudflared o'rnatish (linux versiyasi)
# systemd xizmati: server yonganda bot avto-start, yiqilsa avto-restart
# bazani kompyuterdan serverga ko'chirish
# tekshirish va ishga tushirish
```

Shundan so'ng kompyuteringizdagi botni o'chirib qo'yamiz (Startup'dagi
`parda-bot-autostart.bat` o'chiriladi) — hammasi serverda 24/7 ishlaydi.

## Xavfsizlik

- Parolni ishlatib bo'lgach Claude sizga uni **almashtirishni** aytadi
- Token serverda muhit o'zgaruvchisida saqlanadi
- Kunlik zaxira (21:00, Telegramga) serverda ham ishlayveradi
