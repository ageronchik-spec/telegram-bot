import asyncio
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANON_CHAT_ID = -1003896678128

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# -----------------------
# Инициализация базы данных
# -----------------------

async def init_db():
    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS anonymous_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            telegram_message_id INTEGER
        )
        """)
        await db.commit()

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
# Команда /start
# -----------------------

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👤 Анонимный чат\n\n"
        "Отправь мне сообщение или фото в личку, "
        "и я опубликую его в группе анонимно.\n\n"
        "Если на твой пост ответят в группе, бот пришлёт этот ответ тебе сюда!"
    )

# -----------------------
# Анонимные текстовые сообщения (ЛС -> Группа)
# -----------------------

@dp.message(F.chat.type == "private", F.text)
async def anonymous_message(message: Message):
    if message.text.startswith("/"):
        return

    user = message.from_user

    async with aiosqlite.connect("anonymous.db") as db:
        cursor = await db.execute(
            """
            INSERT INTO anonymous_messages (user_id, username, first_name)
            VALUES (?, ?, ?)
            """,
            (user.id, user.username, user.first_name)
        )
        await db.commit()
        anonymous_id = cursor.lastrowid

        sent_message = await bot.send_message(
            chat_id=ANON_CHAT_ID,
            text=f"🥷 Аноним #{anonymous_id}\n\n{message.text}"
        )

        await db.execute(
            """
            UPDATE anonymous_messages
            SET telegram_message_id = ?
            WHERE id = ?
            """,
            (sent_message.message_id, anonymous_id)
        )
        await db.commit()

    await message.answer(
        f"✅ Сообщение отправлено анонимно.\n"
        f"Номер: #{anonymous_id}"
    )

# -----------------------
# Анонимные фотографии (ЛС -> Группа)
# -----------------------

@dp.message(F.chat.type == "private", F.photo)
async def anonymous_photo(message: Message):
    user = message.from_user

    async with aiosqlite.connect("anonymous.db") as db:
        cursor = await db.execute(
            """
            INSERT INTO anonymous_messages (user_id, username, first_name)
            VALUES (?, ?, ?)
            """,
            (user.id, user.username, user.first_name)
        )
        await db.commit()
        anonymous_id = cursor.lastrowid

        caption_text = f"🥷 Аноним #{anonymous_id}"
        if message.caption:
            caption_text += f"\n\n{message.caption}"

        sent_message = await bot.send_photo(
            chat_id=ANON_CHAT_ID,
            photo=message.photo[-1].file_id,
            caption=caption_text
        )

        await db.execute(
            """
            UPDATE anonymous_messages
            SET telegram_message_id = ?
            WHERE id = ?
            """,
            (sent_message.message_id, anonymous_id)
        )
        await db.commit()

    await message.answer(
        f"✅ Фотография отправлена анонимно.\n"
        f"Номер: #{anonymous_id}"
    )

# -----------------------
# Обработка ответов в группе (Группа -> ЛС автору)
# -----------------------

@dp.message(F.chat.id == ANON_CHAT_ID, F.reply_to_message)
async def group_reply_handler(message: Message):
    # Игнорируем ответы на сообщения бота, содержащие команды вроде /who
    if message.text and message.text.startswith("/"):
        return

    replied_msg_id = message.reply_to_message.message_id

    # Ищем в БД автора сообщения, на которое ответили
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            """
            SELECT id, user_id FROM anonymous_messages
            WHERE telegram_message_id = ?
            """,
            (replied_msg_id,)
        ) as cursor:
            result = await cursor.fetchone()

    # Если исходное сообщение не найдено в БД (например, ответили не на анонимку) — ничего не делаем
    if not result:
        return

    anonymous_id, target_user_id = result

    try:
        # Уведомляем автора и дублируем ответ
        await bot.send_message(
            chat_id=target_user_id,
            text=f"💬 **Вам пришел ответ на анонимный пост #{anonymous_id}:**",
            parse_mode="Markdown"
        )
        await message.copy_message(chat_id=target_user_id)
    except TelegramAPIError:
        # Ошибка возникает, если пользователь заблокировал бота
        await message.reply("❌ Не удалось доставить ответ (возможно, автор заблокировал бота).")

# -----------------------
# Команда /who
# -----------------------

@dp.message(Command("who"))
async def who_handler(message: Message):
    if message.chat.id != ANON_CHAT_ID:
        return

    if not await is_admin(message.from_user.id):
        await message.reply("❌ Эта команда доступна только администраторам.")
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.reply("Использование:\n/who 15")
        return

    try:
        anonymous_id = int(parts[1])
    except ValueError:
        await message.reply("❌ Неверный номер.")
        return

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            """
            SELECT user_id, username, first_name
            FROM anonymous_messages
            WHERE id = ?
            """,
            (anonymous_id,)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        await message.reply("❌ Сообщение с таким номером не найдено.")
        return

    user_id, username, first_name = result
    username_text = f"@{username}" if username else "нет username"

    await message.reply(
        "🔐 Информация об авторе\n\n"
        f"Аноним: #{anonymous_id}\n"
        f"Имя: {first_name}\n"
        f"Username: {username_text}\n"
        f"User ID: `{user_id}`",
        parse_mode="Markdown"
    )

# -----------------------
# Точка входа
# -----------------------

async def main():
    await init_db()
    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
