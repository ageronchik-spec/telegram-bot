import asyncio
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    MessageReactionUpdated,
    ReactionTypeEmoji,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.exceptions import TelegramAPIError

BOT_TOKEN = os.getenv("BOT_TOKEN")
ANON_CHAT_ID = -1003896678128
ADMIN_ID = 7710764694  # ⚠️ УКАЖИ ЗДЕСЬ СВОЙ TELEGRAM USER ID

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# -----------------------
# Состояния FSM (для авторизации)
# -----------------------

class AuthState(StatesGroup):
    waiting_for_credentials = State()

# -----------------------
# Инициализация базы данных
# -----------------------

async def init_db():
    async with aiosqlite.connect("anonymous.db") as db:
        # Таблица сообщений
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
        
        # Таблица авторизованных пользователей (0 - на проверке, 1 - одобрен)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_approved INTEGER DEFAULT 0
        )
        """)

        try:
            await db.execute("ALTER TABLE anonymous_messages ADD COLUMN user_message_id INTEGER")
        except Exception:
            pass
        await db.commit()

# -----------------------
# Вспомогательные функции авторизации
# -----------------------

async def is_user_approved(user_id: int) -> bool:
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute("SELECT is_approved FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return bool(row and row[0] == 1)

async def set_user_approval(user_id: int, approved: bool):
    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute(
            """
            INSERT INTO users (user_id, is_approved) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET is_approved = excluded.is_approved
            """,
            (user_id, 1 if approved else 0)
        )
        await db.commit()

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
async def start_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id
    
    # Если пользователь уже авторизован — показываем стандартную инструкцию
    if await is_user_approved(user_id):
        welcome_text = (
            "📌 Инструкция по проверке номеров и запросам:\n"
            "Проверка номеров:\n"
            "Отправляйте фото анкеты, номер телефона и юзернейм Telegram (@username).\n"
            "На ваше сообщение будет поставлена реакция:\n"
            "🏆 Кубок — плюсовой номер\n"
            "💩 Говно — минусовой номер\n\n"
            "Запросы на сайты:\n"
            "В начале сообщения обязательно отмечайте ответственного: @damn2788, затем указывайте ваш стейдж и сам запрос.\n"
            "Пример:\n"
            "@damn2788\n"
            "Buivol\n"
            "2 вкз на телефон"
        )
        await message.answer(welcome_text)
        return

    # Если не авторизован — просим ввести данные
    await state.set_state(AuthState.waiting_for_credentials)
    await message.answer(
        "🔒 <b>Авторизация необходима!</b>\n\n"
        "Пожалуйста, введите ваши данные для авторизации (например: ФИО, логин или стейдж), "
        "чтобы администратор мог подтвердить ваш доступ.",
        parse_mode="HTML"
    )

# -----------------------
# Ввод данных авторизации пользователем
# -----------------------

@dp.message(AuthState.waiting_for_credentials)
async def process_credentials(message: Message, state: FSMContext):
    user = message.from_user
    credentials_text = message.text

    await state.clear()

    # Сохраняем в БД со статусом 0 (ожидает)
    await set_user_approval(user.id, False)

    await message.answer("⌛ Ваши данные отправлены на проверку администратору. Ожидайте подтверждения.")

    # Кнопки для администратора
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{user.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user.id}")
        ]
    ])

    # Уведомляем администратора
    username_str = f"@{user.username}" if user.username else "нет username"
    admin_msg = (
        "📥 <b>Новая заявка на авторизацию!</b>\n\n"
        f"<b>Пользователь:</b> {user.first_name} ({username_str})\n"
        f"<b>ID:</b> <code>{user.id}</code>\n"
        f"<b>Введенные данные:</b>\n{credentials_text}"
    )

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=admin_kb, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка отправки админу: {e}")

# -----------------------
# Обработка нажатий кнопок админа (Одобрить / Отклонить)
# -----------------------

@dp.callback_query(F.data.startswith("approve_"))
async def approve_user_callback(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    await set_user_approval(target_user_id, True)

    await callback.message.edit_text(
        f"{callback.message.text}\n\n✅ <b>ДОСТУП ПОДТВЕРЖДЕН</b>",
        parse_mode="HTML"
    )

    # Уведомляем пользователя
    welcome_text = (
        "🎉 <b>Ваш доступ подтвержден!</b>\n\n"
        "📌 Инструкция по проверке номеров и запросам:\n"
        "Проверка номеров:\n"
        "Отправляйте фото анкеты, номер телефона и юзернейм Telegram (@username).\n"
        "На ваше сообщение будет поставлена реакция:\n"
        "🏆 Кубок — плюсовой номер\n"
        "💩 Говно — минусовой номер\n\n"
        "Запросы на сайты:\n"
        "В начале сообщения обязательно отмечайте ответственного: @damn2788, затем указывайте ваш стейдж и сам запрос.\n"
        "Пример:\n"
        "@damn2788\n"
        "Buivol\n"
        "2 вкз на телефон"
    )
    try:
        await bot.send_message(chat_id=target_user_id, text=welcome_text, parse_mode="HTML")
    except TelegramAPIError:
        pass

@dp.callback_query(F.data.startswith("reject_"))
async def reject_user_callback(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])

    await callback.message.edit_text(
        f"{callback.message.text}\n\n❌ <b>ЗАЯВКА ОТКЛОНЕНА</b>",
        parse_mode="HTML"
    )

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text="❌ Ваша заявка на доступ была отклонена администратором."
        )
    except TelegramAPIError:
        pass

# -----------------------
# Ответ пользователя на свое сообщение в ЛС
# -----------------------

@dp.message(F.chat.type == "private", F.reply_to_message)
async def user_reply_in_pm(message: Message):
    if not await is_user_approved(message.from_user.id):
        await message.answer("🔒 Вы не авторизованы. Нажмите /start для авторизации.")
        return

    if message.text and message.text.startswith("/"):
        return

    replied_user_msg_id = message.reply_to_message.message_id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            "SELECT id, telegram_message_id FROM anonymous_messages WHERE user_message_id = ?",
            (replied_user_msg_id,)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
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
# Анонимные текстовые сообщения
# -----------------------

@dp.message(F.chat.type == "private", F.text)
async def anonymous_message(message: Message):
    if not await is_user_approved(message.from_user.id):
        await message.answer("🔒 Вы не авторизованы. Нажмите /start для авторизации.")
        return

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
            "UPDATE anonymous_messages SET telegram_message_id = ? WHERE id = ?",
            (sent_message.message_id, anonymous_id)
        )
        await db.commit()

# -----------------------
# Анонимные фотографии
# -----------------------

@dp.message(F.chat.type == "private", F.photo)
async def anonymous_photo(message: Message):
    if not await is_user_approved(message.from_user.id):
        await message.answer("🔒 Вы не авторизованы. Нажмите /start для авторизации.")
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

        caption_text = f"🥷 Аноним #{anonymous_id}"
        if message.caption:
            caption_text += f"\n\n{message.caption}"

        sent_message = await bot.send_photo(
            chat_id=ANON_CHAT_ID,
            photo=message.photo[-1].file_id,
            caption=caption_text
        )

        await db.execute(
            "UPDATE anonymous_messages SET telegram_message_id = ? WHERE id = ?",
            (sent_message.message_id, anonymous_id)
        )
        await db.commit()

# -----------------------
# Обработка реакций из группы
# -----------------------

@dp.message_reaction(F.chat.id == ANON_CHAT_ID)
async def reaction_handler(reaction: MessageReactionUpdated):
    msg_id = reaction.message_id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            "SELECT user_id, user_message_id FROM anonymous_messages WHERE telegram_message_id = ?",
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
            new_reactions = [
                ReactionTypeEmoji(emoji=r.emoji)
                for r in reaction.new_reaction
                if hasattr(r, "emoji")
            ]
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
# Обработка ответов в группе
# -----------------------

@dp.message(F.chat.id == ANON_CHAT_ID, F.reply_to_message)
async def group_reply_handler(message: Message):
    if message.text and message.text.startswith("/"):
        return

    replied_msg_id = message.reply_to_message.message_id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            "SELECT id, user_id, user_message_id FROM anonymous_messages WHERE telegram_message_id = ?",
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
            "SELECT user_id, username, first_name FROM anonymous_messages WHERE id = ?",
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
    await dp.start_polling(bot, allowed_updates=["message", "message_reaction", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
