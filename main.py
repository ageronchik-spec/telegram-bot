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
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery
)
from aiogram.exceptions import TelegramAPIError

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = 7710764694  # ⚠️ Укажите свой Telegram ID

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
            selected_thread_id INTEGER,
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
        try:
            await db.execute("ALTER TABLE users ADD COLUMN selected_thread_id INTEGER")
        except Exception:
            pass

        await db.commit()

# -----------------------
# Вспомогательные функции
# -----------------------

def get_user_mention(user) -> str:
    name = f"@{user.username}" if user.username else (user.first_name or "Пользователь")
    safe_name = html.escape(name)
    return f'<a href="tg://user?id={user.id}">{safe_name}</a>'

async def get_user_data(user_id: int):
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute("SELECT group_id, selected_thread_id, is_approved FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1], bool(row[2] == 1)
            return None, None, False

async def bind_user_to_group(user_id: int, group_id: int, username: str = None, first_name: str = None, approved: bool = False):
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            """
            INSERT INTO users (user_id, username, first_name, group_id, is_approved)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET 
                group_id = excluded.group_id,
                username = excluded.username,
                first_name = excluded.first_name,
                is_approved = CASE WHEN excluded.is_approved = 1 THEN 1 ELSE users.is_approved END
            """,
            (user_id, username, first_name, group_id, 1 if approved else 0)
        )
        await db.commit()

async def update_user_selected_thread(user_id: int, thread_id: int):
    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute("UPDATE users SET selected_thread_id = ? WHERE user_id = ?", (thread_id, user_id))
        await db.commit()

async def set_user_approval(user_id: int, approved: bool):
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
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

async def get_reply_threads_keyboard(group_id: int) -> ReplyKeyboardMarkup | None:
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute("SELECT thread_name FROM group_threads WHERE group_id = ?", (group_id,)) as cursor:
            rows = await cursor.fetchall()
            
    if not rows:
        return None

    keyboard = []
    for row in rows:
        keyboard.append([KeyboardButton(text=row[0])])
    
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

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
            
            kb = await get_reply_threads_keyboard(target_group_id)
            welcome_msg = "✅ <b>Доступ получен!</b>\n\n" + WELCOME_TEXT
            if kb:
                welcome_msg += "\n\n👇 Выберите ветку для отправки на клавиатуре ниже:"
                await message.answer(welcome_msg, reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer(welcome_msg, parse_mode="HTML")
            return
        except ValueError:
            pass

    group_id, _, is_approved = await get_user_data(user.id)
    if not group_id:
        await message.answer(
            "⚠️ <b>Вы не привязаны ни к одной группе!</b>\n\n"
            "Перейдите по пригласительной ссылке от вашего администратора.",
            parse_mode="HTML"
        )
        return

    if not is_approved:
        await message.answer("⏳ <b>Ваш запрос находится на рассмотрении.</b>", parse_mode="HTML")
        return

    kb = await get_reply_threads_keyboard(group_id)
    if kb:
        await message.answer("📂 Переключайтесь между ветками с помощью постоянных кнопок ниже:", reply_markup=kb)
    else:
        await message.answer(WELCOME_TEXT)

# -----------------------
# Управление ветками (/addthread и /delthread)
# -----------------------

@dp.message(Command("addthread"), F.chat.type == "private")
async def add_thread_command(message: Message, state: FSMContext):
    user_id = message.from_user.id
    group_id, _, _ = await get_user_data(user_id)

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
    group_id, _, _ = await get_user_data(user_id)

    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute(
            "INSERT INTO group_threads (group_id, thread_name, thread_id) VALUES (?, ?, ?)",
            (group_id, thread_name, thread_id)
        )
        await db.commit()

    await state.clear()
    kb = await get_reply_threads_keyboard(group_id)
    await message.answer(
        f"✅ Кнопка «<b>{thread_name}</b>» создана и привязана к ветке `{thread_id}`!",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.message(Command("delthread"), F.chat.type == "private")
async def delete_thread_command(message: Message):
    """Показывает список кнопок для удаления"""
    user_id = message.from_user.id
    group_id, _, _ = await get_user_data(user_id)

    if not group_id:
        await message.answer("⚠️ Сначала привяжите группу через /admin.")
        return

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute("SELECT id, thread_name FROM group_threads WHERE group_id = ?", (group_id,)) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await message.answer("⚠️ В вашей группе пока нет созданных кнопок.")
        return

    keyboard = []
    for row_id, name in rows:
        keyboard.append([InlineKeyboardButton(text=f"❌ Удалить: {name}", callback_data=f"del_th_{row_id}")])

    await message.answer(
        "🗑 <b>Управление кнопками</b>\n\nВыберите кнопку, которую хотите удалить:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("del_th_"))
async def process_delete_thread(callback: CallbackQuery):
    row_id = int(callback.data.replace("del_th_", ""))
    user_id = callback.from_user.id
    group_id, _, _ = await get_user_data(user_id)

    async with aiosqlite.connect("anonymous.db") as db:
        # Узнаем имя для отчета
        async with db.execute("SELECT thread_name FROM group_threads WHERE id = ?", (row_id,)) as cursor:
            row = await cursor.fetchone()
            thread_name = row[0] if row else "Кнопка"

        # Удаляем из базы
        await db.execute("DELETE FROM group_threads WHERE id = ?", (row_id,))
        await db.commit()

    # Обновляем инлайн-сообщение
    await callback.message.edit_text(f"✅ Кнопка «<b>{thread_name}</b>» успешно удалена!", parse_mode="HTML")

    # Обновляем менеджеру постоянную клавиатуру внизу
    kb = await get_reply_threads_keyboard(group_id)
    if kb:
        await bot.send_message(user_id, "🔄 Клавиатура обновлена:", reply_markup=kb)
    else:
        # Если кнопок не осталось вообще, убираем reply-клавиатуру
        from aiogram.types import ReplyKeyboardRemove
        await bot.send_message(user_id, "🔄 Все кнопки удалены.", reply_markup=ReplyKeyboardRemove())

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
                "⚙️ Добавить кнопки: /addthread\n"
                "🗑 Удалить кнопки: /delthread"
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
# Перехват клика по постоянным кнопкам веток
# ----------------------------------------------------

@dp.message(F.chat.type == "private", F.text)
async def check_thread_switch_or_send(message: Message):
    user_id = message.from_user.id
    group_id, selected_thread_id, is_approved = await get_user_data(user_id)

    if not group_id or not is_approved:
        return

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            "SELECT thread_id, thread_name FROM group_threads WHERE group_id = ? AND thread_name = ?",
            (group_id, message.text)
        ) as cursor:
            thread_row = await cursor.fetchone()

    if thread_row:
        new_thread_id, thread_name = thread_row
        await update_user_selected_thread(user_id, new_thread_id)
        await message.answer(f"📌 <b>Вы переключились на ветку:</b> {html.escape(thread_name)}\nТеперь все новые сообщения улетают туда.", parse_mode="HTML")
        return

    await anonymous_message(message)

# ----------------------------------------------------
# Отправка первично создаваемого ТЕКСТА в ветку
# ----------------------------------------------------

async def anonymous_message(message: Message):
    user_id = message.from_user.id
    group_id, thread_id, is_approved = await get_user_data(user_id)

    if not group_id or not is_approved:
        await message.answer("⚠️ Нет доступа или группа не привязана.")
        return

    if message.text and message.text.startswith("/"):
        return

    if not thread_id:
        kb = await get_reply_threads_keyboard(group_id)
        if kb:
            await message.answer("📂 Пожалуйста, выберите ветку на клавиатуре ниже:", reply_markup=kb)
        else:
            await message.answer("⚠️ В вашей группе ещё не настроены ветки для отправки.")
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

# ----------------------------------------------------
# Отправка первично создаваемой ФОТОГРАФИИ в ветку
# ----------------------------------------------------

@dp.message(F.chat.type == "private", F.photo)
async def anonymous_photo(message: Message):
    user_id = message.from_user.id
    group_id, thread_id, is_approved = await get_user_data(user_id)

    if not group_id or not is_approved:
        await message.answer("⚠️ Нет доступа или группа не привязана.")
        return

    if not thread_id:
        kb = await get_reply_threads_keyboard(group_id)
        if kb:
            await message.answer("📂 Пожалуйста, выберите ветку на клавиатуре ниже:", reply_markup=kb)
        else:
            await message.answer("⚠️ В вашей группе ещё не настроены ветки для отправки.")
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

# ----------------------------------------------------
# Пересылка ответа администратора из группы менеджеру в ЛС
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
