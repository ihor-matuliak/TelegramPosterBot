"""
Interactive Telegram Userbot Authorization Script.
Run this script once to log into your Telegram account and generate the session file.
Command: python auth.py
"""

import asyncio
import logging
from config import config
from core.client import (
    client,
    init_telegram_client,
    normalize_phone,
    describe_sent_code_type,
    login_with_bot_token,
    logout_client
)
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main():
    print("=" * 65)
    print("🔑 АВТОРИЗАЦІЯ TELEGRAM КЛІЄНТА")
    print("=" * 65)
    
    await init_telegram_client()
    
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"\n✅ Ви вже авторизовані як: {me.first_name} (@{me.username or 'без юзернейму'}) [ID: {me.id}]")
        print("Сесія активна і готова до використання у main.py!")
        reauth = input("\nБажаєте змінити сесію або переавторизуватися? (y/N): ").strip().lower()
        if reauth != "y":
            return
        print("🧹 Очищення та скидання попередньої сесії...")
        await logout_client()

    print("\nОберіть спосіб авторизації:")
    print(f"  [1] ⚡ Авторизація через Bot Token (з BotFather) — без SMS і кодів!")
    print(f"  [2] 📱 Авторизація за номером телефону (Userbot)")
    
    choice = input("\nВаш вибір (1 або 2, Enter = 1): ").strip() or "1"

    if choice == "1":
        default_token = config.BOT_TOKEN or ""
        token_input = input(f"Введіть Bot Token [натисніть Enter для {default_token[:10]}...]: ").strip()
        token = token_input or default_token
        print("⏳ Авторизація токена бота в Telegram...")
        res = await login_with_bot_token(token)
        if res.get("status") == "ok":
            me = await client.get_me()
            print(f"\n🎉 УСПІХ! Авторизовано як бот: {me.first_name} (@{me.username}) [ID: {me.id}]")
            print("Файл сесії оновлено. Тепер можна запускати main.py!")
            return
        else:
            print(f"❌ {res.get('message')}")
            return

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
    print(f"\n🎉 Вітаємо! Успішна авторизація під іменем: {me.first_name} (@{me.username or 'без юзернейму'}) [ID: {me.id}]")
    print("Файл сесії збережено. Тепер розсилка готова до запуску!")


if __name__ == "__main__":
    asyncio.run(main())
