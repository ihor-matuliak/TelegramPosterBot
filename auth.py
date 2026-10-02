"""
Interactive Telegram Userbot Authorization Script.
Run this script to log into your Telegram profile and generate the session file.
Command: python auth.py
"""

import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import asyncio
import logging
from config import config
from core.client import (
    client,
    init_telegram_client,
    normalize_phone,
    describe_sent_code_type,
    login_with_bot_token,
    logout_client,
    reset_client_session
)
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    PasswordHashInvalidError
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main():
    print("=" * 65)
    print("🔑 АВТОРИЗАЦІЯ TELEGRAM КЛІЄНТА (ПРОФІЛЬ ДЛЯ РОЗСИЛКИ)")
    print("=" * 65)
    
    await init_telegram_client()
    
    if await client.is_user_authorized():
        me = await client.get_me()
        is_bot = getattr(me, "bot", False)
        if is_bot:
            print(f"\n⚠️  УВАГА! Ви зараз авторизовані як БОТ: {me.first_name} (@{me.username}) [ID: {me.id}]")
            print("❌ Telegram-боти НЕ МАЮТЬ змоги розсилати оголошення у публічні групи!")
            print("👉 Вам необхідно скинути цю сесію та увійти під РЕАЛЬНИМ ПРОФІЛЕМ користувача.")
            reauth = input("\nСкинути сесію бота та увійти під профілем? (Y/n): ").strip().lower()
            if reauth not in ["", "y", "yes", "так"]:
                return
        else:
            print(f"\n✅ Ви вже авторизовані під профілем: {me.first_name} (@{me.username or 'без юзернейму'}) [ID: {me.id}]")
            print("Сесія активна і готова до розсилки оголошень у main.py!")
            reauth = input("\nБажаєте змінити акаунт або переавторизуватися? (y/N): ").strip().lower()
            if reauth not in ["y", "yes", "так"]:
                return

        print("🧹 Очищення та скидання попередньої сесії...")
        await logout_client()

    print("\nОберіть спосіб авторизації під ПРОФІЛЕМ Telegram:")
    print("  [1] 📷 Авторизація за QR-кодом (НАЙПРОСТІШЕ: Telegram на телефоні -> Пристрої -> Сканувати QR)")
    print("  [2] 📱 Авторизація за номером телефону (код надходить у чат «Telegram» або SMS)")
    print("  [3] ⚠️  Авторизація через Bot Token (НЕ підходить для розсилки оголошень)")
    
    choice = input("\nВаш вибір (1, 2 або 3, Enter = 1): ").strip() or "1"

    # ==================== OPTION 1: QR CODE LOGIN ====================
    if choice == "1":
        print("\n⏳ Генерація QR-коду для входу під вашим профілем...")
        try:
            if not client.is_connected():
                await client.connect()
            qr = await client.qr_login()
        except Exception as e:
            print(f"❌ Не вдалося створити QR-код: {e}")
            return

        print("\n" + "=" * 65)
        print("📱 ВІДКРИЙТЕ TELEGRAM НА ВАШОМУ СМАРТФОНІ:")
        print("   1. Відкрийте Налаштування (Settings) -> Пристрої (Devices)")
        print("   2. Натисніть «Підключити пристрій» (Link Desktop Device)")
        print("   3. Наведіть камеру телефону на цей QR-код:")
        print("=" * 65 + "\n")

        try:
            import qrcode
            qr_obj = qrcode.QRCode(border=1)
            qr_obj.add_data(qr.url)
            qr_obj.print_ascii(invert=True)
        except Exception:
            print(f"Посилання для входу: {qr.url}\n(Встановіть пакет qrcode для відображення графічного коду у терміналі)")

        print("\n⏳ Очікування сканування QR-коду (діє до 2 хвилин)...")
        try:
            await qr.wait(timeout=120)
        except SessionPasswordNeededError:
            print("\n🔐 На вашому акаунті активовано двоетапну перевірку (2FA)!")
            pwd = input("Введіть ваш хмарний пароль 2FA: ").strip()
            try:
                await client.sign_in(password=pwd)
            except Exception as e:
                print(f"❌ Невірний пароль 2FA: {e}")
                return
        except asyncio.TimeoutError:
            print("❌ Час очікування сканування вичерпано. Будь ласка, запустіть скрипт ще раз.")
            return
        except Exception as e:
            print(f"❌ Помилка під час входу за QR-кодом: {e}")
            return

        me = await client.get_me()
        is_bot = getattr(me, "bot", False)
        print("\n" + "=" * 65)
        if is_bot:
            print(f"⚠️  Увага: Відбувся вхід як БОТ: {me.first_name} (@{me.username}) [ID: {me.id}]")
        else:
            print(f"🎉 УСПІХ! Ви успішно авторизовані під профілем: {me.first_name} (@{me.username or 'без юзернейму'}) [ID: {me.id}]")
            print("✅ Файл сесії збережено (poster_session.session). Тепер система може розсилати оголошення!")
        print("=" * 65)
        return

    # ==================== OPTION 3: BOT TOKEN (WITH WARNING) ====================
    if choice == "3":
        print("\n⚠️  ПОПЕРЕДЖЕННЯ: Telegram боти НЕ МОЖУТЬ писати в групи, де вони не є адміністраторами.")
        confirm = input("Ви впевнені, що хочете увійти як бот? (y/N): ").strip().lower()
        if confirm not in ["y", "yes", "так"]:
            return
        default_token = config.BOT_TOKEN or ""
        token_input = input(f"Введіть Bot Token [натисніть Enter для {default_token[:10]}...]: ").strip()
        token = token_input or default_token
        print("⏳ Авторизація токена бота в Telegram...")
        res = await login_with_bot_token(token)
        if res.get("status") == "ok":
            me = await client.get_me()
            print(f"\n🎉 Авторизовано як бот: {me.first_name} (@{me.username}) [ID: {me.id}]")
            print("⚠️ Пам'ятайте: бот не може розсилати у звичайні публічні групи!")
            return
        else:
            print(f"❌ {res.get('message')}")
            return

    # ==================== OPTION 2: PHONE NUMBER LOGIN ====================
    raw_phone = input("\nВведіть ваш номер телефону (наприклад +380991234567 або 0991234567): ").strip()
    phone = normalize_phone(raw_phone)
    if not phone or len(phone) < 8:
        print(f"❌ Некоректний формат номера: '{raw_phone}'. Потрібно вказати повний номер.")
        return

    print(f"⏳ Надсилаємо запит коду для {phone}...")
    try:
        sent_code = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        print(f"❌ Помилка: Номер {phone} не зареєстрований або вказаний невірно.")
        return
    except FloodWaitError as e:
        print(f"⏳ Telegram вимагає зачекати {e.seconds} секунд через часті запити.")
        return
    except Exception as e:
        print(f"❌ Помилка запиту коду: {e}")
        return

    target_desc = describe_sent_code_type(sent_code.type)
    print("\n" + "—" * 65)
    print("📩 КОД НАДІСЛАНО!")
    print(f"📍 Куди Telegram надіслав код: {target_desc}")
    print("⚠️  УВАГА: Код надходить НЕ в SMS на телефон, а в офіційний додаток Telegram!")
    print("    Відкрийте додаток Telegram на телефоні або комп'ютері.")
    print("    Знайдіть чат «Telegram» (з синьою галочкою, від сервісу 777000).")
    print("—" * 65)
    
    raw_code = input("\nВведіть отриманий код підтвердження: ").strip()
    code = raw_code.replace(" ", "").replace("-", "")
    
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent_code.phone_code_hash)
    except SessionPasswordNeededError:
        print("\n🔐 На акаунті активовано двоетапну перевірку (2FA).")
        pwd = input("Введіть ваш хмарний пароль 2FA: ").strip()
        try:
            await client.sign_in(password=pwd)
        except Exception as e:
            print(f"❌ Невірний пароль 2FA: {e}")
            return
    except PhoneCodeInvalidError:
        print("❌ Невірний код підтвердження! Перевірте цифри у чаті «Telegram».")
        return
    except PhoneCodeExpiredError:
        print("⚠️ Термін дії коду вичерпано. Запустіть скрипт ще раз.")
        return
    except Exception as e:
        print(f"❌ Помилка авторизації: {e}")
        return

    me = await client.get_me()
    print("\n" + "=" * 65)
    print(f"🎉 Вітаємо! Успішна авторизація під іменем: {me.first_name} (@{me.username or 'без юзернейму'}) [ID: {me.id}]")
    print("✅ Файл сесії збережено. Тепер розсилка готова до запуску!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
