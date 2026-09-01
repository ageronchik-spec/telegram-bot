import asyncio
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, MessageReactionUpdated, ReactionTypeEmoji
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
            user_message_id INTEGER,
            telegram_message_id INTEGER
        )
        """)
        # Миграция: добавляем колонку user_message_id, если её ещё нет
        try:
            await db.execute("ALTER TABLE anonymous_messages ADD COLUMN user_message_id INTEGER")
        except Exception:
            pass
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
        "📌 Инструкция по проверке номеров и запросам:\n\n"
        "Проверка номеров:\n\n"
        "Отправляйте фото анкеты, номер телефона и  юзернейм Telegram (Собачка клиента).\n\n"
        "На ваше сообщение будет поставлена реакция:
        🏆 Кубок — плюсовой номер
        💩 Говно — минусовой номер\n\n"
        " Запросы на сайты:
        В начале сообщения обязательно отмечайте ответственного: 
        @damn2788, затем указывайте ваш стейдж и сам запрос.\n\n"
    )

# -----------------------
# Ответ пользователя на свое сообщение в ЛС (ЛС Reply -> Группа Reply)
# -----------------------

@dp.message(F.chat.type == "private", F.reply_to_message)
async def user_reply_in_pm(message: Message):
    if message.text and message.text.startswith("/"):
        return

    replied_user_msg_id = message.reply_to_message.message_id

    # Ищем пост в базе по ID сообщения пользователя в ЛС
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            """
            SELECT id, telegram_message_id FROM anonymous_messages
            WHERE user_message_id = ?
            """,
            (replied_user_msg_id,)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        # Если ответили просто на обычное сообщение не из базы, обрабатываем как новое анонимное сообщение
        await anonymous_message(message)
        return

    anonymous_id, group_msg_id = result

    try:
        if message.photo:
            caption_text = f"🥷 Аноним #{anonymous_id}"
            if message.caption:
                caption_text += f"\n\n{message.caption}"
            await bot.send_photo(
                chat_id=ANON_CHAT_ID,
                photo=message.photo[-1].file_id,
                caption=caption_text,
                reply_to_message_id=group_msg_id
            )
        elif message.text:
            await bot.send_message(
                chat_id=ANON_CHAT_ID,
                text=f"🥷 Аноним #{anonymous_id}\n\n{message.text}",
                reply_to_message_id=group_msg_id
            )
        else:
            await message.copy_message(
                chat_id=ANON_CHAT_ID,
                reply_to_message_id=group_msg_id
            )
    except TelegramAPIError:
        pass

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
            INSERT INTO anonymous_messages (user_id, username, first_name, user_message_id)
            VALUES (?, ?, ?, ?)
            """,
            (user.id, user.username, user.first_name, message.message_id)
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

# -----------------------
# Анонимные фотографии (ЛС -> Группа)
# -----------------------

@dp.message(F.chat.type == "private", F.photo)
async def anonymous_photo(message: Message):
    user = message.from_user

    async with aiosqlite.connect("anonymous.db") as db:
        cursor = await db.execute(
            """
            INSERT INTO anonymous_messages (user_id, username, first_name, user_message_id)
            VALUES (?, ?, ?, ?)
            """,
            (user.id, user.username, user.first_name, message.message_id)
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

# -----------------------
# Обработка реакций из группы (Группа -> Реакция в ЛС)
# -----------------------

@dp.message_reaction(F.chat.id == ANON_CHAT_ID)
async def reaction_handler(reaction: MessageReactionUpdated):
    msg_id = reaction.message_id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            """
            SELECT user_id, user_message_id FROM anonymous_messages
            WHERE telegram_message_id = ?
            """,
            (msg_id,)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        return

    target_user_id, user_msg_id = result

    if not user_msg_id:
        return

    try:
        if reaction.new_reaction:
            new_reactions = []
            for r in reaction.new_reaction:
                if hasattr(r, "emoji"):
                    new_reactions.append(ReactionTypeEmoji(emoji=r.emoji))
            
            if new_reactions:
                await bot.set_message_reaction(
                    chat_id=target_user_id,
                    message_id=user_msg_id,
                    reaction=new_reactions
                )
        else:
            await bot.set_message_reaction(
                chat_id=target_user_id,
                message_id=user_msg_id,
                reaction=[]
            )
    except TelegramAPIError:
        pass

# -----------------------
# Обработка ответов в группе (Группа -> Reply в ЛС)
# -----------------------

@dp.message(F.chat.id == ANON_CHAT_ID, F.reply_to_message)
async def group_reply_handler(message: Message):
    if message.text and message.text.startswith("/"):
        return

    replied_msg_id = message.reply_to_message.message_id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            """
            SELECT id, user_id, user_message_id FROM anonymous_messages
            WHERE telegram_message_id = ?
            """,
            (replied_msg_id,)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        return

    anonymous_id, target_user_id, user_msg_id = result

    try:
        if message.photo:
            caption_text = message.caption if message.caption else None
            await bot.send_photo(
                chat_id=target_user_id,
                photo=message.photo[-1].file_id,
                caption=caption_text,
                reply_to_message_id=user_msg_id
            )
        elif message.text:
            await bot.send_message(
                chat_id=target_user_id,
                text=message.text,
                reply_to_message_id=user_msg_id
            )
        else:
            await message.copy_message(
                chat_id=target_user_id,
                reply_to_message_id=user_msg_id
            )

    except TelegramAPIError:
        await message.reply("❌ Не удалось доставить ответ (пользователь заблокировал бота).")

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
    await dp.start_polling(bot, allowed_updates=["message", "message_reaction"])

if __name__ == "__main__":
    asyncio.run(main())
