"""
Interactive Telegram Userbot Authorization Script.
Run this script once to log into your Telegram account and generate the session file.
Command: python auth.py
"""

import asyncio
import logging
from config import config
from core.client import client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


async def main():
    print("=" * 60)
    print("🔑 АВТОРИЗАЦІЯ TELEGRAM ЮЗЕРБОТА")
    print("=" * 60)
    
    await client.connect()
    
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"\n✅ Ви вже авторизовані як: {me.first_name} (@{me.username or 'без юзернейму'}) [ID: {me.id}]")
        print("Сесія активна і готова до використання у main.py!")
        return

    phone = input("\nВведіть ваш номер телефону (наприклад +380991234567): ").strip()
    await client.send_code_request(phone)
    
    code = input("Введіть код підтвердження, який прийшов у Telegram: ").strip()
    try:
        await client.sign_in(phone, code)
    except Exception as e:
        # Check if 2FA password is required
        if "password" in str(e).lower() or "two-steps" in str(e).lower():
            pwd = input("Введіть пароль двофакторної автентифікації (2FA): ").strip()
            await client.sign_in(password=pwd)
        else:
            raise e

    me = await client.get_me()
    print(f"\n🎉 Вітаємо! Успішна авторизація під іменем: {me.first_name} (@{me.username or 'без юзернейму'})")
    print("Файл сесії збережено. Тепер ви можете запускати main.py!")


if __name__ == "__main__":
    asyncio.run(main())
