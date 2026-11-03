import asyncio
import html
import os
import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, CommandObject
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
SUPER_ADMIN_ID = 7710764694  # ⚠️ Твой личный Telegram ID

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

WELCOME_TEXT = (
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

# -----------------------
# Состояния FSM
# -----------------------

class AuthState(StatesGroup):
    choosing_thread = State()      # Выбор ветки кнопкой
    waiting_for_message = State()  # Отправка текста/фото

class AdminState(StatesGroup):
    waiting_for_group_id = State()

class ThreadConfigState(StatesGroup):
    waiting_for_thread_name = State()
    waiting_for_thread_id = State()

# -----------------------
# Инициализация базы данных
# -----------------------

async def init_db():
    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS anonymous_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            thread_id INTEGER,
            user_id INTEGER NOT NULL,
            username TEXT,
            first_name TEXT,
            user_message_id INTEGER,
            telegram_message_id INTEGER
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            group_id INTEGER,
            is_approved INTEGER DEFAULT 0
        )
        """)

        await db.execute("""
        CREATE TABLE IF NOT EXISTS group_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            thread_name TEXT,
            thread_id INTEGER
        )
        """)

        try:
            await db.execute("ALTER TABLE anonymous_messages ADD COLUMN thread_id INTEGER")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE users ADD COLUMN group_id INTEGER")
        except Exception:
            pass

        await db.commit()

# -----------------------
# Вспомогательные функции
# -----------------------

def get_user_mention(user) -> str:
    if user.username:
        return f"@{html.escape(user.username)}"
    safe_name = html.escape(user.first_name or "Пользователь")
    return f'<a href="tg://user?id={user.id}">{safe_name}</a>'

async def get_user_group_id(user_id: int) -> int | None:
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute("SELECT group_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

async def bind_user_to_group(user_id: int, group_id: int, username: str = None, first_name: str = None, approved: bool = False):
    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, first_name, group_id, is_approved)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
                group_id = excluded.group_id,
                username = excluded.username,
                first_name = excluded.first_name,
                is_approved = excluded.is_approved
            """,
            (user_id, username, first_name, group_id, 1 if approved else 0)
        )
        await db.commit()

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

async def is_group_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

async def get_threads_keyboard(group_id: int) -> InlineKeyboardMarkup | None:
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute("SELECT thread_name, thread_id FROM group_threads WHERE group_id = ?", (group_id,)) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        return None

    keyboard = []
    for name, t_id in rows:
        keyboard.append([InlineKeyboardButton(text=name, callback_data=f"sel_thread_{t_id}")])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# -----------------------
# Команда /start
# -----------------------

@dp.message(CommandStart())
async def start_handler(message: Message, command: CommandObject, state: FSMContext):
    if message.chat.type != "private":
        return

    user = message.from_user
    args = command.args

    if args and args.startswith("group_"):
        try:
            target_group_id = int(args.replace("group_", ""))
            await bind_user_to_group(user.id, target_group_id, user.username, user.first_name, approved=True)
            await state.clear()
            
            kb = await get_threads_keyboard(target_group_id)
            if kb:
                await state.set_state(AuthState.choosing_thread)
                await message.answer("✅ <b>Доступ получен!</b>\n\n📂 Выберите ветку для отправки сообщений:", reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer("✅ <b>Доступ получен!</b>\n\n" + WELCOME_TEXT, parse_mode="HTML")
            return
        except ValueError:
            pass

    group_id = await get_user_group_id(user.id)
    if not group_id:
        await message.answer(
            "⚠️ <b>Вы не привязаны ни к одной группе!</b>\n\n"
            "Перейдите по пригласительной ссылке от вашего администратора.",
            parse_mode="HTML"
        )
        return

    if not await is_user_approved(user.id):
        await message.answer("⏳ <b>Ваш запрос находится на рассмотрении.</b>", parse_mode="HTML")
        return

    kb = await get_threads_keyboard(group_id)
    if kb:
        await state.set_state(AuthState.choosing_thread)
        await message.answer("📂 Выберите ветку для отправки сообщений:", reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer(WELCOME_TEXT)

# -----------------------
# Управление ветками (/addthread)
# -----------------------

@dp.message(Command("addthread"), F.chat.type == "private")
async def add_thread_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    if not group_id:
        await message.answer("⚠️ Сначала привяжите группу через /admin.")
        return

    await state.set_state(ThreadConfigState.waiting_for_thread_name)
    await message.answer("➕ <b>Создание новой кнопки ветки</b>\n\nВведите название кнопки (например: <i>📌 Проверка номеров</i>):", parse_mode="HTML")

@dp.message(ThreadConfigState.waiting_for_thread_name)
async def process_thread_name(message: Message, state: FSMContext):
    await state.update_data(thread_name=message.text.strip())
    await state.set_state(ThreadConfigState.waiting_for_thread_id)
    await message.answer(
        "🆔 Теперь отправьте <b>ID ветки (топика)</b> для этой кнопки.\n\n"
        "💡 <i>ПКМ по ветке в Telegram Desktop -> «Копировать ссылку на тему». Число на конце (например, 12345) — это ID.</i>",
        parse_mode="HTML"
    )

@dp.message(ThreadConfigState.waiting_for_thread_id)
async def process_thread_id(message: Message, state: FSMContext):
    try:
        thread_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом. Попробуйте еще раз:")
        return

    data = await state.get_data()
    thread_name = data.get("thread_name")
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute(
            "INSERT INTO group_threads (group_id, thread_name, thread_id) VALUES (?, ?, ?)",
            (group_id, thread_name, thread_id)
        )
        await db.commit()

    await state.clear()
    await message.answer(f"✅ Кнопка «{thread_name}» создана и привязана к ветке `{thread_id}`!", parse_mode="HTML")

# -----------------------
# Выбор ветки через инлайн-кнопку
# -----------------------

@dp.callback_query(F.data.startswith("sel_thread_"))
async def select_thread_callback(callback: CallbackQuery, state: FSMContext):
    thread_id = int(callback.data.replace("sel_thread_", ""))
    
    await state.update_data(selected_thread_id=thread_id)
    await state.set_state(AuthState.waiting_for_message)
    
    await callback.message.edit_text(
        "✅ <b>Ветка выбрана!</b>\n\n"
        "💬 Теперь отправьте ваше сообщение или фото, и оно автоматически улетит в выбранную тему.",
        parse_mode="HTML"
    )
    await callback.answer()

# -----------------------
# Команда /admin
# -----------------------

@dp.message(Command("admin"), F.chat.type == "private")
async def admin_command_handler(message: Message, state: FSMContext):
    await state.set_state(AdminState.waiting_for_group_id)
    await message.answer(
        "⚙️ <b>Настройка группы для бота</b>\n\n"
        "1. Добавьте бота в группу и сделайте администратором.\n"
        "2. Введите здесь <b>ID группы</b> (например: <code>-1003896678128</code>).",
        parse_mode="HTML"
    )

@dp.message(AdminState.waiting_for_group_id)
async def process_group_id(message: Message, state: FSMContext):
    try:
        group_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Некорректный формат ID.")
        return

    try:
        chat = await bot.get_chat(group_id)
    except Exception:
        await message.answer("❌ Бот не найден в этой группе.")
        return

    if not await is_group_admin(group_id, message.from_user.id):
        await message.answer("❌ Вы не являетесь администратором этой группы!")
        return

    user = message.from_user
    await bind_user_to_group(user.id, group_id, user.username, user.first_name, approved=False)
    await state.clear()

    bot_info = await bot.get_me()
    invite_link = f"https://t.me/{bot_info.username}?start=group_{group_id}"

    await message.answer("⏳ Запрос отправлен главному администратору.", parse_mode="HTML")

    admin_notify_msg = (
        "🔔 <b>Запрос на привязку группы через /admin!</b>\n\n"
        f"👤 <b>Админ:</b> {html.escape(user.first_name or '')} (@{html.escape(user.username or 'нет')})\n"
        f"👥 <b>Группа:</b> {html.escape(chat.title)}\n"
        f"🆔 <b>Group ID:</b> <code>{group_id}</code>\n"
        f"🔗 <b>Инвайт:</b>\n{invite_link}"
    )

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_group_{user.id}_{group_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_group_{user.id}")
        ]
    ])

    try:
        await bot.send_message(chat_id=SUPER_ADMIN_ID, text=admin_notify_msg, reply_markup=confirm_kb, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка отправки админу: {e}")

@dp.callback_query(F.data.startswith("approve_group_"))
async def approve_group_callback(callback: CallbackQuery):
    parts = callback.data.split("_")
    target_user_id = int(parts[2])
    group_id = int(parts[3])

    await set_user_approval(target_user_id, True)
    await callback.message.edit_text(f"{callback.message.text}\n\n✅ <b>ОДОБРЕНО</b>", parse_mode="HTML")

    bot_info = await bot.get_me()
    invite_link = f"https://t.me/{bot_info.username}?start=group_{group_id}"

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=(
                "🎉 <b>Заявка одобрена!</b>\n\n"
                f"🔗 <b>Инвайт-ссылка для сотрудников:</b>\n<code>{invite_link}</code>\n\n"
                "⚙️ Чтобы добавить кнопки для веток, введите команду /addthread"
            ),
            parse_mode="HTML"
        )
    except TelegramAPIError:
        pass

@dp.callback_query(F.data.startswith("reject_group_"))
async def reject_group_callback(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[2])
    await callback.message.edit_text(f"{callback.message.text}\n\n❌ <b>ОТКЛОНЕНО</b>", parse_mode="HTML")
    try:
        await bot.send_message(chat_id=target_user_id, text="❌ Ваша заявка отклонена.")
    except TelegramAPIError:
        pass

# ----------------------------------------------------
# Отправка ТЕКСТА в выбранную ветку
# ----------------------------------------------------

@dp.message(AuthState.waiting_for_message, F.chat.type == "private", F.text)
async def anonymous_message(message: Message, state: FSMContext):
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    if not group_id or not await is_user_approved(user_id):
        await message.answer("⚠️ Нет доступа или группа не привязана.")
        return

    if message.text.startswith("/"):
        return

    data = await state.get_data()
    thread_id = data.get("selected_thread_id")

    if not thread_id:
        kb = await get_threads_keyboard(group_id)
        if kb:
            await state.set_state(AuthState.choosing_thread)
            await message.answer("📂 Сначала выберите ветку с помощью кнопок:", reply_markup=kb)
        else:
            await message.answer("⚠️ В этой группе еще не настроены ветки для отправки.")
        return

    user = message.from_user
    user_mention = get_user_mention(user)

    async with aiosqlite.connect("anonymous.db") as db:
        cursor = await db.execute(
            """
            INSERT INTO anonymous_messages (group_id, thread_id, user_id, username, first_name, user_message_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (group_id, thread_id, user.id, user.username, user.first_name, message.message_id)
        )
        await db.commit()
        anonymous_id = cursor.lastrowid

        text_to_send = f"🥷 Аноним #{anonymous_id}\n\n{html.escape(message.text)}\n\n👤 {user_mention}"

        sent_message = await bot.send_message(
            chat_id=group_id,
            message_thread_id=thread_id,
            text=text_to_send,
            parse_mode="HTML"
        )

        await db.execute(
            "UPDATE anonymous_messages SET telegram_message_id = ? WHERE id = ?",
            (sent_message.message_id, anonymous_id)
        )
        await db.commit()

    await message.answer("✅ Сообщение отправлено в выбранную ветку!")

# ----------------------------------------------------
# Отправка ФОТОГРАФИЙ в выбранную ветку
# ----------------------------------------------------

@dp.message(AuthState.waiting_for_message, F.chat.type == "private", F.photo)
async def anonymous_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    if not group_id or not await is_user_approved(user_id):
        await message.answer("⚠️ Нет доступа или группа не привязана.")
        return

    data = await state.get_data()
    thread_id = data.get("selected_thread_id")

    if not thread_id:
        kb = await get_threads_keyboard(group_id)
        if kb:
            await state.set_state(AuthState.choosing_thread)
            await message.answer("📂 Сначала выберите ветку с помощью кнопок:", reply_markup=kb)
        else:
            await message.answer("⚠️ В этой группе еще не настроены ветки для отправки.")
        return

    user = message.from_user
    user_mention = get_user_mention(user)

    async with aiosqlite.connect("anonymous.db") as db:
        cursor = await db.execute(
            """
            INSERT INTO anonymous_messages (group_id, thread_id, user_id, username, first_name, user_message_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (group_id, thread_id, user.id, user.username, user.first_name, message.message_id)
        )
        await db.commit()
        anonymous_id = cursor.lastrowid

        raw_caption = message.caption or ""
        caption_text = f"🥷 Аноним #{anonymous_id}"
        if raw_caption:
            caption_text += f"\n\n{html.escape(raw_caption)}"
        caption_text += f"\n\n👤 {user_mention}"

        sent_message = await bot.send_photo(
            chat_id=group_id,
            message_thread_id=thread_id,
            photo=message.photo[-1].file_id,
            caption=caption_text,
            parse_mode="HTML"
        )

        await db.execute(
            "UPDATE anonymous_messages SET telegram_message_id = ? WHERE id = ?",
            (sent_message.message_id, anonymous_id)
        )
        await db.commit()

    await message.answer("✅ Фотография отправлена в выбранную ветку!")

# ----------------------------------------------------
# Ответ менеджера на сообщение администратора в ЛС
# ----------------------------------------------------

@dp.message(F.chat.type == "private", F.reply_to_message)
async def user_reply_in_pm(message: Message, state: FSMContext):
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    if not group_id or not await is_user_approved(user_id):
        return

    if message.text and message.text.startswith("/"):
        return

    replied_user_msg_id = message.reply_to_message.message_id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            "SELECT id, thread_id, telegram_message_id FROM anonymous_messages WHERE user_message_id = ?",
            (replied_user_msg_id,)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        return

    anonymous_id, thread_id, group_msg_id = result
    user_mention = get_user_mention(message.from_user)

    try:
        if message.photo:
            raw_caption = message.caption or ""
            caption_text = f"🥷 Аноним #{anonymous_id}"
            if raw_caption:
                caption_text += f"\n\n{html.escape(raw_caption)}"
            caption_text += f"\n\n👤 {user_mention}"

            await bot.send_photo(
                chat_id=group_id,
                message_thread_id=thread_id,
                photo=message.photo[-1].file_id,
                caption=caption_text,
                reply_to_message_id=group_msg_id,
                parse_mode="HTML"
            )
        elif message.text:
            text_to_send = f"🥷 Аноним #{anonymous_id}\n\n{html.escape(message.text)}\n\n👤 {user_mention}"
            await bot.send_message(
                chat_id=group_id,
                message_thread_id=thread_id,
                text=text_to_send,
                reply_to_message_id=group_msg_id,
                parse_mode="HTML"
            )
    except TelegramAPIError as e:
        await message.answer(f"❌ Ошибка отправки в группу: {e}")

# ----------------------------------------------------
# Ответ администратора из группы в ЛС менеджеру
# ----------------------------------------------------

@dp.message(F.chat.type.in_({"group", "supergroup"}), F.reply_to_message)
async def group_reply_handler(message: Message):
    if message.text and message.text.startswith("/"):
        return

    replied_msg_id = message.reply_to_message.message_id
    chat_id = message.chat.id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            "SELECT id, user_id, user_message_id FROM anonymous_messages WHERE telegram_message_id = ? AND group_id = ?",
            (replied_msg_id, chat_id)
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

# ----------------------------------------------------
# Команда /who
# ----------------------------------------------------

@dp.message(Command("who"), F.chat.type.in_({"group", "supergroup"}))
async def who_handler(message: Message):
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
            "SELECT user_id, username, first_name FROM anonymous_messages WHERE id = ? AND group_id = ?",
            (anonymous_id, message.chat.id)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        await message.reply("❌ Сообщение с таким номером не найдено в этой группе.")
        return

    user_id, username, first_name = result
    username_text = f"@{html.escape(username)}" if username else "нет username"

    await message.reply(
        "🔐 <b>Информация об авторе</b>\n\n"
        f"Аноним: #{anonymous_id}\n"
        f"Имя: {html.escape(first_name or '')}\n"
        f"Username: {username_text}\n"
        f"User ID: <code>{user_id}</code>",
        parse_mode="HTML"
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
