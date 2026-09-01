import asyncio
import sqlite3
import os
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message


BOT_TOKEN = os.getenv("BOT_TOKEN")

# ID группы, куда будут уходить анонимные сообщения.
ANON_CHAT_ID = -5403851337


bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# -----------------------
# База данных
# -----------------------

db = sqlite3.connect("anonymous.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS anonymous_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    username TEXT,
    first_name TEXT,
    telegram_message_id INTEGER
)
""")

db.commit()


# -----------------------
# Проверка администратора
# -----------------------

async def is_admin(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(
            chat_id=ANON_CHAT_ID,
            user_id=user_id
        )

        return member.status in ("administrator", "creator")

    except Exception:
        return False


# -----------------------
# /start
# -----------------------

@dp.message(CommandStart())
async def start_handler(message: Message):

    await message.answer(
        "👤 Анонимный чат\n\n"
        "Отправь мне сообщение или фото в личку, "
        "и я опубликую его в группе анонимно.\n\n"
        "Другие участники не увидят твой профиль."
    )


# -----------------------
# Анонимные текстовые сообщения
# -----------------------

@dp.message(F.chat.type == "private", F.text)
async def anonymous_message(message: Message):

    # Не обрабатываем команды как сообщения
    if message.text.startswith("/"):
        return

    user = message.from_user

    # Сначала сохраняем автора
    cursor.execute(
        """
        INSERT INTO anonymous_messages
        (user_id, username, first_name)
        VALUES (?, ?, ?)
        """,
        (
            user.id,
            user.username,
            user.first_name
        )
    )

    db.commit()

    anonymous_id = cursor.lastrowid

    # Публикуем сообщение уже ОТ ИМЕНИ БОТА
    sent_message = await bot.send_message(
        chat_id=ANON_CHAT_ID,
        text=(
            f"🥷 Аноним #{anonymous_id}\n\n"
            f"{message.text}"
        )
    )

    # Запоминаем ID сообщения в Telegram
    cursor.execute(
        """
        UPDATE anonymous_messages
        SET telegram_message_id = ?
        WHERE id = ?
        """,
        (
            sent_message.message_id,
            anonymous_id
        )
    )

    db.commit()

    await message.answer(
        f"✅ Сообщение отправлено анонимно.\n"
        f"Номер: #{anonymous_id}"
    )


# -----------------------
# Анонимные фотографии
# -----------------------

@dp.message(F.chat.type == "private", F.photo)
async def anonymous_photo(message: Message):

    user = message.from_user

    # Сохраняем автора в БД
    cursor.execute(
        """
        INSERT INTO anonymous_messages
        (user_id, username, first_name)
        VALUES (?, ?, ?)
        """,
        (
            user.id,
            user.username,
            user.first_name
        )
    )

    db.commit()

    anonymous_id = cursor.lastrowid

    # Формируем подпись (сохраняем оригинальную подпись к фото, если она была)
    caption_text = f"🥷 Аноним #{anonymous_id}"
    if message.caption:
        caption_text += f"\n\n{message.caption}"

    # Отправляем фото в высшем качестве (photo[-1])
    sent_message = await bot.send_photo(
        chat_id=ANON_CHAT_ID,
        photo=message.photo[-1].file_id,
        caption=caption_text
    )

    # Запоминаем ID сообщения
    cursor.execute(
        """
        UPDATE anonymous_messages
        SET telegram_message_id = ?
        WHERE id = ?
        """,
        (
            sent_message.message_id,
            anonymous_id
        )
    )

    db.commit()

    await message.answer(
        f"✅ Фотография отправлена анонимно.\n"
        f"Номер: #{anonymous_id}"
    )


# -----------------------
# /who
# Только для администраторов
# -----------------------

@dp.message(Command("who"))
async def who_handler(message: Message):

    if message.chat.id != ANON_CHAT_ID:
        return

    if not await is_admin(message.from_user.id):
        await message.reply(
            "❌ Эта команда доступна только администраторам."
        )
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.reply(
            "Использование:\n"
            "/who 15"
        )
        return

    try:
        anonymous_id = int(parts[1])
    except ValueError:
        await message.reply("❌ Неверный номер.")
        return

    cursor.execute(
        """
        SELECT user_id, username, first_name
        FROM anonymous_messages
        WHERE id = ?
        """,
        (anonymous_id,)
    )

    result = cursor.fetchone()

    if not result:
        await message.reply(
            "❌ Сообщение с таким номером не найдено."
        )
        return

    user_id, username, first_name = result

    username_text = (
        f"@{username}"
        if username
        else "нет username"
    )

    await message.reply(
        "🔐 Информация об авторе\n\n"
        f"Аноним: #{anonymous_id}\n"
        f"Имя: {first_name}\n"
        f"Username: {username_text}\n"
        f"User ID: `{user_id}`",
        parse_mode="Markdown"
    )


# -----------------------
# Запуск
# -----------------------

async def main():
    print("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
