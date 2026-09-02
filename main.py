import asyncio
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
SUPER_ADMIN_ID = 7710764694  # ⚠️ Укажите ваш личный Telegram ID (Главный админ бота)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

# -----------------------
# Состояния FSM
# -----------------------

class AuthState(StatesGroup):
    waiting_for_credentials = State()

class AdminState(StatesGroup):
    waiting_for_group_id = State()

# -----------------------
# Инициализация базы данных
# -----------------------

async def init_db():
    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS anonymous_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
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

        try:
            await db.execute("ALTER TABLE anonymous_messages ADD COLUMN group_id INTEGER")
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

async def get_user_group_id(user_id: int) -> int | None:
    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute("SELECT group_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

async def bind_user_to_group(user_id: int, group_id: int, username: str = None, first_name: str = None):
    async with aiosqlite.connect("anonymous.db") as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, first_name, group_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET group_id = excluded.group_id
            """,
            (user_id, username, first_name, group_id)
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

# -----------------------
# Команда /start (с поддержкой пригласительных ссылок)
# -----------------------

@dp.message(CommandStart())
async def start_handler(message: Message, command: CommandObject, state: FSMContext):
    if message.chat.type != "private":
        return

    user = message.from_user
    args = command.args  # Автоматический перехват параметрической ссылки (deep-linking)

    # 1. Если пользователь пришел по пригласительной ссылке вида t.me/bot?start=group_XXXXX
    if args and args.startswith("group_"):
        try:
            target_group_id = int(args.replace("group_", ""))
            await bind_user_to_group(user.id, target_group_id, user.username, user.first_name)
        except ValueError:
            pass

    # 2. Проверяем, привязан ли пользователь к какой-либо группе
    group_id = await get_user_group_id(user.id)
    if not group_id:
        await message.answer(
            "⚠️ <b>Вы не привязаны ни к одной группе!</b>\n\n"
            "Попросите администратора вашей группы прислать вам специальную пригласительную ссылку для запуска бота.\n\n"
            "<i>Если вы админ группы — введите /admin для настройки.</i>",
            parse_mode="HTML"
        )
        return

    # 3. Если пользователь авторизован — показываем инструкцию
    if await is_user_approved(user.id):
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

    # 4. Если группа есть, но нет авторизации — просим ввести данные
    await state.set_state(AuthState.waiting_for_credentials)
    await message.answer(
        "🔒 <b>Авторизация необходима!</b>\n\n"
        "Пожалуйста, введите ваши данные для авторизации (например: ФИО, логин или стейдж), "
        "чтобы администратор мог подтвердить ваш доступ.",
        parse_mode="HTML"
    )

# -----------------------
# Команда /admin (Привязка группы + генерация инвайт-ссылки)
# -----------------------

@dp.message(Command("admin"), F.chat.type == "private")
async def admin_command_handler(message: Message, state: FSMContext):
    await state.set_state(AdminState.waiting_for_group_id)
    await message.answer(
        "⚙️ <b>Настройка группы для бота</b>\n\n"
        "1. Добавьте бота в нужную группу.\n"
        "2. Выдайте боту права администратора.\n"
        "3. Введите здесь <b>ID группы</b> (начинается с минус-знака, например: <code>-1003896678128</code>).\n\n"
        "💡 <i>Чтобы узнать ID группы, перешлите сюда любое сообщение из вашей группы или используйте @userinfobot.</i>",
        parse_mode="HTML"
    )

@dp.message(AdminState.waiting_for_group_id)
async def process_group_id(message: Message, state: FSMContext):
    try:
        group_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Некорректный формат ID. Отправьте числовой ID группы.")
        return

    try:
        chat = await bot.get_chat(group_id)
    except Exception:
        await message.answer("❌ Бот не найден в этой группе.")
        return

    if not await is_group_admin(group_id, message.from_user.id):
        await message.answer("❌ Вы не являетесь администратором этой группы!")
        return

    # Сохраняем группу за админом
    await bind_user_to_group(message.from_user.id, group_id, message.from_user.username, message.from_user.first_name)
    await state.clear()

    # Формируем пригласительную ссылку для новых участников
    bot_info = await bot.get_me()
    # Заменяем минус на символ, если ID группы отрицательный
    raw_group_id = str(group_id).replace("-", "")
    invite_link = f"https://t.me/{bot_info.username}?start=group_{group_id}"

    await message.answer(
        f"✅ <b>Группа «{chat.title}» успешно привязана!</b>\n\n"
        f"🔗 <b>Пригласительная ссылка для сотрудников:</b>\n"
        f"<code>{invite_link}</code>\n\n"
        f"Перешлите эту ссылку вашим сотрудникам. Перейдя по ней, они автоматически привяжутся к этой группе и смогут сразу пройти авторизацию без настройки ID!",
        parse_mode="HTML"
    )

# -----------------------
# Авторизация пользователя
# -----------------------

@dp.message(AuthState.waiting_for_credentials)
async def process_credentials(message: Message, state: FSMContext):
    user = message.from_user
    credentials_text = message.text

    await state.clear()
    await set_user_approval(user.id, False)

    await message.answer("⌛ Ваши данные отправлены на проверку администратору. Ожидайте подтверждения.")

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve_{user.id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user.id}")
        ]
    ])

    username_str = f"@{user.username}" if user.username else "нет username"
    admin_msg = (
        "📥 <b>Новая заявка на авторизацию!</b>\n\n"
        f"<b>Пользователь:</b> {user.first_name} ({username_str})\n"
        f"<b>ID:</b> <code>{user.id}</code>\n"
        f"<b>Введенные данные:</b>\n{credentials_text}"
    )

    try:
        await bot.send_message(chat_id=SUPER_ADMIN_ID, text=admin_msg, reply_markup=admin_kb, parse_mode="HTML")
    except Exception as e:
        print(f"Ошибка отправки главному админу: {e}")

@dp.callback_query(F.data.startswith("approve_"))
async def approve_user_callback(callback: CallbackQuery):
    target_user_id = int(callback.data.split("_")[1])
    await set_user_approval(target_user_id, True)

    await callback.message.edit_text(
        f"{callback.message.text}\n\n✅ <b>ДОСТУП ПОДТВЕРЖДЕН</b>",
        parse_mode="HTML"
    )

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
        await bot.send_message(chat_id=target_user_id, text="❌ Ваша заявка на доступ была отклонена администратором.")
    except TelegramAPIError:
        pass

# -----------------------
# Ответ пользователя на свое сообщение в ЛС
# -----------------------

@dp.message(F.chat.type == "private", F.reply_to_message)
async def user_reply_in_pm(message: Message):
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    if not group_id:
        await message.answer("⚠️ Вы не привязаны к группе. Перейдите по пригласительной ссылке от вашего админа.")
        return

    if not await is_user_approved(user_id):
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
                chat_id=group_id,
                photo=message.photo[-1].file_id,
                caption=caption_text,
                reply_to_message_id=group_msg_id
            )
        elif message.text:
            await bot.send_message(
                chat_id=group_id,
                text=f"🥷 Аноним #{anonymous_id}\n\n{message.text}",
                reply_to_message_id=group_msg_id
            )
        else:
            await message.copy_message(
                chat_id=group_id,
                reply_to_message_id=group_msg_id
            )
    except TelegramAPIError as e:
        await message.answer(f"❌ Ошибка отправки в группу: {e}")

# -----------------------
# Анонимные текстовые сообщения
# -----------------------

@dp.message(F.chat.type == "private", F.text)
async def anonymous_message(message: Message):
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    if not group_id:
        await message.answer("⚠️ Вы не привязаны к группе. Перейдите по пригласительной ссылке от вашего админа.")
        return

    if not await is_user_approved(user_id):
        await message.answer("🔒 Вы не авторизованы. Нажмите /start для авторизации.")
        return

    if message.text.startswith("/"):
        return

    user = message.from_user

    async with aiosqlite.connect("anonymous.db") as db:
        cursor = await db.execute(
            """
            INSERT INTO anonymous_messages (group_id, user_id, username, first_name, user_message_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (group_id, user.id, user.username, user.first_name, message.message_id)
        )
        await db.commit()
        anonymous_id = cursor.lastrowid

        sent_message = await bot.send_message(
            chat_id=group_id,
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
    user_id = message.from_user.id
    group_id = await get_user_group_id(user_id)

    if not group_id:
        await message.answer("⚠️ Вы не привязаны к группе. Перейдите по пригласительной ссылке от вашего админа.")
        return

    if not await is_user_approved(user_id):
        await message.answer("🔒 Вы не авторизованы. Нажмите /start для авторизации.")
        return

    user = message.from_user

    async with aiosqlite.connect("anonymous.db") as db:
        cursor = await db.execute(
            """
            INSERT INTO anonymous_messages (group_id, user_id, username, first_name, user_message_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (group_id, user.id, user.username, user.first_name, message.message_id)
        )
        await db.commit()
        anonymous_id = cursor.lastrowid

        caption_text = f"🥷 Аноним #{anonymous_id}"
        if message.caption:
            caption_text += f"\n\n{message.caption}"

        sent_message = await bot.send_photo(
            chat_id=group_id,
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

@dp.message_reaction()
async def reaction_handler(reaction: MessageReactionUpdated):
    msg_id = reaction.message_id
    chat_id = reaction.chat.id

    async with aiosqlite.connect("anonymous.db") as db:
        async with db.execute(
            "SELECT user_id, user_message_id FROM anonymous_messages WHERE telegram_message_id = ? AND group_id = ?",
            (msg_id, chat_id)
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

# -----------------------
# Команда /who (работает в любой привязанной группе)
# -----------------------

@dp.message(Command("who"), F.chat.type.in_({"group", "supergroup"}))
async def who_handler(message: Message):
    if not await is_group_admin(message.chat.id, message.from_user.id):
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
            "SELECT user_id, username, first_name FROM anonymous_messages WHERE id = ? AND group_id = ?",
            (anonymous_id, message.chat.id)
        ) as cursor:
            result = await cursor.fetchone()

    if not result:
        await message.reply("❌ Сообщение с таким номером не найдено в этой группе.")
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
