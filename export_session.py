"""
Скрипт для експорту поточної сесії poster_session.session у формат TELEGRAM_STRING_SESSION.
Використовуйте отриманий рядок у змінних оточення на Railway / Render / VPS.
Запуск: python export_session.py
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import config, BASE_DIR


async def main():
    print("=" * 60)
    print("🔑 ЕКСПОРТ TELEGRAM СЕСІЇ ДЛЯ ХМАРНИХ СЕРВІСІВ (RAILWAY)")
    print("=" * 60)
    
    session_file = BASE_DIR / config.TELEGRAM_SESSION_NAME
    if not session_file.with_suffix(".session").exists():
        print(f"❌ Файл сесії {session_file}.session не знайдено!")
        print("Спочатку авторизуйтесь на ПК через команду: python auth.py")
        return

    try:
        client = TelegramClient(str(session_file), config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            print("❌ Сесія не авторизована! Будь ласка, спочатку виконайте: python auth.py")
            await client.disconnect()
            return

        me = await client.get_me()
        string_token = StringSession.save(client.session)
        await client.disconnect()

        print(f"\n✅ Успішно! Авторизовано як: {me.first_name} (@{me.username or 'без юзернейму'})")
        print("\nСкопіюйте наведений нижче рядок і додайте його у Railway у вкладці Variables")
        print("під ім'ям: TELEGRAM_STRING_SESSION\n")
        print("-" * 70)
        print(string_token)
        print("-" * 70)
        print("\n⚠️ Увага: цей рядок надає повний доступ до сесії. Зберігайте його в таємниці!")
    except Exception as e:
        print(f"❌ Помилка під час експорту: {e}")
        print("Переконайтеся, що на ПК зараз не запущено інший процес python main.py, який блокує файл сесії.")


if __name__ == "__main__":
    asyncio.run(main())
