import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Callable, Dict, Any, Awaitable

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, CallbackQuery, TelegramObject,
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from sqlalchemy import BigInteger, String, Text, Integer, DateTime, Boolean, select, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# ==================== 1. CONFIGURATION ====================
load_dotenv()

@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("8220814941:AAHVtTIJOHReLcmhtMG9iH3WqxWgpmjHEbU", "")
    ADMIN_IDS: List[int] = None
    CHANNEL_ID: int = int(os.getenv("-1003852596133", "0"))
    CHANNEL_LINK: str = os.getenv("@kagedub1", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./kino_bot.db")

    def __post_init__(self):
        admin_ids = os.getenv("8318241400", "")
        self.ADMIN_IDS = [int(x.strip()) for x in admin_ids.split(",") if x.strip()]

config = Config()

# ==================== 2. DATABASE MODELS ====================
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=True)
    username: Mapped[str] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    genre: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, nullable=True)
    file_id: Mapped[str] = mapped_column(String(255))  # Telegram video file_id
    quality: Mapped[str] = mapped_column(String(50), default="HD")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# ==================== 3. DATABASE SETUP ====================
engine = create_async_engine(config.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ==================== 4. FSM STATES ====================
class AddMovie(StatesGroup):
    code = State()
    title = State()
    genre = State()
    description = State()
    quality = State()
    video = State()

class DeleteMovie(StatesGroup):
    code = State()

class EditMovie(StatesGroup):
    code = State()
    field = State()
    value = State()

class Broadcast(StatesGroup):
    message = State()

# ==================== 5. KEYBOARDS ====================
def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬 Kod orqali qidirish")],
            [KeyboardButton(text="🔍 Nom bo'yicha qidirish")],
            [KeyboardButton(text="📂 Janrlar")],
        ],
        resize_keyboard=True
    )

def subscription_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Kanalga o'tish", url=config.CHANNEL_LINK)],
            [InlineKeyboardButton(text="✅ Obunani tekshirish", callback_data="check_sub")]
        ]
    )

def genres_keyboard() -> InlineKeyboardMarkup:
    genres = [
        ("🔥 Jangari", "genre_Jangari"),
        ("😂 Komediya", "genre_Komediya"),
        ("🎭 Drama", "genre_Drama"),
        ("👻 Qo'rqinchli", "genre_Qo'rqinchli"),
        ("🚀 Fantastika", "genre_Fantastika"),
        ("❤️ Melodrama", "genre_Melodrama"),
        ("🎌 Anime", "genre_Anime"),
        ("🔙 Orqaga", "back_main")
    ]
    buttons = [
        [InlineKeyboardButton(text=text, callback_data=data)]
        for text, data in genres
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True
    )

def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Kino qo'shish")],
            [KeyboardButton(text="🗑 Kino o'chirish"), KeyboardButton(text="✏️ Kino tahrirlash")],
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="📢 Xabar tarqatish")],
            [KeyboardButton(text="🔙 Asosiy menyu")],
        ],
        resize_keyboard=True
    )

def cancel_admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True
    )

def confirm_broadcast_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Yuborish", callback_data="confirm_broadcast"),
                InlineKeyboardButton(text="❌ Bekor", callback_data="cancel_broadcast")
            ]
        ]
    )

# ==================== 6. MIDDLEWARE ====================
class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = None
        if isinstance(event, Message):
            user = event.from_user
        elif isinstance(event, CallbackQuery):
            user = event.from_user

        if not user:
            return await handler(event, data)

        # Adminlar uchun tekshiruv o'tkazilmaydi
        if user.id in config.ADMIN_IDS:
            return await handler(event, data)

        bot = data["bot"]
        try:
            member = await bot.get_chat_member(config.CHANNEL_ID, user.id)
            if member.status in [
                ChatMemberStatus.MEMBER,
                ChatMemberStatus.ADMINISTRATOR,
                ChatMemberStatus.CREATOR
            ]:
                return await handler(event, data)
        except Exception:
            pass

        # Obuna bo'lmagan foydalanuvchiga obuna talabini ko'rsatish
        if isinstance(event, Message):
            await event.answer(
                "⚠️ Botdan foydalanish uchun quyidagi kanalga obuna bo'ling!",
                reply_markup=subscription_keyboard()
            )
        elif isinstance(event, CallbackQuery):
            await event.message.edit_text(
                "⚠️ Botdan foydalanish uchun quyidagi kanalga obuna bo'ling!",
                reply_markup=subscription_keyboard()
            )
            await event.answer()
        return

# ==================== 7. HANDLERS ====================
start_router = Router()
user_router = Router()
admin_router = Router()

# --- START HANDLER ---
@start_router.message(CommandStart())
async def cmd_start(message: Message):
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.telegram_id == message.from_user.id)
        )
        user = result.scalar_one_or_none()

        if not user:
            new_user = User(
                telegram_id=message.from_user.id,
                full_name=message.from_user.full_name,
                username=message.from_user.username
            )
            session.add(new_user)
            await session.commit()

    if message.from_user.id in config.ADMIN_IDS:
        await message.answer(
            "Salom, Admin!\n\nAsosiy menyu:",
            reply_markup=main_menu_keyboard()
        )
        return

    await message.answer(
        f"Assalomu alaykum, {message.from_user.full_name}!\n\n"
        "Kino botiga xush kelibsiz 🎬",
        reply_markup=main_menu_keyboard()
    )

@start_router.callback_query(F.data == "check_sub")
async def check_subscription(callback: CallbackQuery):
    bot = callback.bot
    try:
        member = await bot.get_chat_member(config.CHANNEL_ID, callback.from_user.id)
        if member.status in ["member", "administrator", "creator"]:
            await callback.message.edit_text(
                "✅ Obuna tasdiqlandi!\n\nEndi botdan foydalanishingiz mumkin."
            )
            await callback.message.answer(
                "Asosiy menyu:",
                reply_markup=main_menu_keyboard()
            )
        else:
            await callback.answer("❌ Hali obuna bo'lmadingiz!", show_alert=True)
    except Exception:
        await callback.answer("Xatolik yuz berdi. Qayta urinib ko'ring.", show_alert=True)

# --- USER HANDLERS ---
@user_router.message(F.text == "🎬 Kod orqali qidirish")
async def search_by_code(message: Message, state: FSMContext):
    await state.set_state("waiting_code")
    await message.answer(
        "🔢 Kinoning kodini yuboring (masalan: 101):",
        reply_markup=cancel_keyboard()
    )

@user_router.message(F.text == "🔍 Nom bo'yicha qidirish")
async def search_by_name(message: Message, state: FSMContext):
    await state.set_state("waiting_name")
    await message.answer(
        "✍️ Kino nomining bir qismini yozing:",
        reply_markup=cancel_keyboard()
    )

@user_router.message(F.text == "📂 Janrlar")
async def show_genres(message: Message):
    await message.answer("Janrni tanlang:", reply_markup=genres_keyboard())

@user_router.message(F.text == "❌ Bekor qilish")
async def cancel_search(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_menu_keyboard())

@user_router.message(F.state == "waiting_code")
async def process_code(message: Message, state: FSMContext):
    code = message.text.strip()
    async with async_session() as session:
        result = await session.execute(
            select(Movie).where(Movie.code == code)
        )
        movie = result.scalar_one_or_none()

    if not movie:
        await message.answer("❌ Bunday kodli kino topilmadi.")
        return

    await state.clear()
    caption = (
        f"🎬 <b>{movie.title}</b>\n"
        f"🆔 Kod: <code>{movie.code}</code>\n"
        f"📂 Janr: {movie.genre}\n"
        f"🖥 Sifat: {movie.quality}\n\n"
        f"📝 {movie.description or 'Tavsif yo‘q'}"
    )
    await message.answer_video(
        video=movie.file_id,
        caption=caption,
        parse_mode="HTML",
        reply_markup=main_menu_keyboard()
    )

@user_router.message(F.state == "waiting_name")
async def process_name(message: Message, state: FSMContext):
    query = message.text.strip()
    async with async_session() as session:
        result = await session.execute(
            select(Movie).where(Movie.title.ilike(f"%{query}%")).limit(10)
        )
        movies = result.scalars().all()

    if not movies:
        await message.answer("❌ Hech narsa topilmadi.")
        return

    await state.clear()
    text = "🔍 Topilgan kinolar:\n\n"
    for m in movies:
        text += f"• <b>{m.title}</b> — kod: <code>{m.code}</code>\n"

    text += "\nKodni yuborib kinoni olishingiz mumkin."
    await message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())

@user_router.callback_query(F.data.startswith("genre_"))
async def process_genre(callback: CallbackQuery):
    genre = callback.data.replace("genre_", "")
    async with async_session() as session:
        result = await session.execute(
            select(Movie).where(Movie.genre == genre).limit(15)
        )
        movies = result.scalars().all()

    if not movies:
        await callback.answer("Bu janrda kino yo‘q.", show_alert=True)
        return

    text = f"📂 <b>{genre}</b> janridagi kinolar:\n\n"
    for m in movies:
        text += f"• {m.title} — <code>{m.code}</code>\n"

    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()

@user_router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer("Asosiy menyu:", reply_markup=main_menu_keyboard())

# --- ADMIN HANDLERS ---
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

@admin_router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("⛔ Sizda ruxsat yo‘q.")
    await message.answer("🛠 Admin panel:", reply_markup=admin_menu_keyboard())

@admin_router.message(F.text == "🔙 Asosiy menyu")
async def back_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Asosiy menyu:", reply_markup=main_menu_keyboard())

@admin_router.message(F.text == "➕ Kino qo'shish")
async def add_movie_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddMovie.code)
    await message.answer("🔢 Kinoning kodini kiriting:", reply_markup=cancel_admin_keyboard())

@admin_router.message(AddMovie.code)
async def add_movie_code(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=admin_menu_keyboard())

    await state.update_data(code=message.text.strip())
    await state.set_state(AddMovie.title)
    await message.answer("🎬 Kino nomini kiriting:")

@admin_router.message(AddMovie.title)
async def add_movie_title(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=admin_menu_keyboard())

    await state.update_data(title=message.text.strip())
    await state.set_state(AddMovie.genre)
    await message.answer("📂 Janrni kiriting (masalan: Jangari, Komediya, Anime):")

@admin_router.message(AddMovie.genre)
async def add_movie_genre(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=admin_menu_keyboard())

    await state.update_data(genre=message.text.strip())
    await state.set_state(AddMovie.description)
    await message.answer("📝 Tavsifni kiriting (yoki /skip):")

@admin_router.message(AddMovie.description)
async def add_movie_description(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=admin_menu_keyboard())

    desc = None if message.text == "/skip" else message.text.strip()
    await state.update_data(description=desc)
    await state.set_state(AddMovie.quality)
    await message.answer("🖥 Sifatni kiriting (masalan: 720p, 1080p, 4K):")

@admin_router.message(AddMovie.quality)
async def add_movie_quality(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=admin_menu_keyboard())

    await state.update_data(quality=message.text.strip())
    await state.set_state(AddMovie.video)
    await message.answer("🎥 Video faylni yuboring:")

@admin_router.message(AddMovie.video, F.video)
async def add_movie_video(message: Message, state: FSMContext):
    data = await state.get_data()
    file_id = message.video.file_id

    async with async_session() as session:
        exists = await session.execute(
            select(Movie).where(Movie.code == data["code"])
        )
        if exists.scalar_one_or_none():
            await message.answer("❌ Bu kod allaqachon mavjud!")
            await state.clear()
            return await message.answer("Admin panel:", reply_markup=admin_menu_keyboard())

        movie = Movie(
            code=data["code"],
            title=data["title"],
            genre=data["genre"],
            description=data.get("description"),
            quality=data["quality"],
            file_id=file_id
        )
        session.add(movie)
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Kino muvaffaqiyatli qo‘shildi!\n\n"
        f"Kod: <code>{data['code']}</code>\n"
        f"Nomi: {data['title']}",
        parse_mode="HTML",
        reply_markup=admin_menu_keyboard()
    )

@admin_router.message(F.text == "🗑 Kino o'chirish")
async def delete_movie_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(DeleteMovie.code)
    await message.answer("O‘chiriladigan kinoning kodini kiriting:", reply_markup=cancel_admin_keyboard())

@admin_router.message(DeleteMovie.code)
async def delete_movie_process(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=admin_menu_keyboard())

    code = message.text.strip()
    async with async_session() as session:
        result = await session.execute(select(Movie).where(Movie.code == code))
        movie = result.scalar_one_or_none()
        if not movie:
            await message.answer("❌ Bunday kino topilmadi.")
            return

        await session.delete(movie)
        await session.commit()

    await state.clear()
    await message.answer(f"✅ <code>{code}</code> kodi o‘chirildi.", parse_mode="HTML", reply_markup=admin_menu_keyboard())

@admin_router.message(F.text == "📊 Statistika")
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        return

    async with async_session() as session:
        users_count = await session.scalar(select(func.count(User.id)))
        movies_count = await session.scalar(select(func.count(Movie.id)))

    await message.answer(
        f"📊 <b>Bot statistikasi</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users_count}</b>\n"
        f"🎬 Kinolar: <b>{movies_count}</b>",
        parse_mode="HTML"
    )

@admin_router.message(F.text == "📢 Xabar tarqatish")
async def broadcast_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(Broadcast.message)
    await message.answer(
        "Yuboriladigan xabarni yozing (matn yoki media bilan):",
        reply_markup=cancel_admin_keyboard()
    )

@admin_router.message(Broadcast.message)
async def broadcast_preview(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        return await message.answer("Bekor qilindi.", reply_markup=admin_menu_keyboard())

    await state.update_data(
        text=message.text or message.caption,
        photo=message.photo[-1].file_id if message.photo else None,
        video=message.video.file_id if message.video else None
    )
    await message.answer(
        "Xabar yuborilsinmi?",
        reply_markup=confirm_broadcast_keyboard()
    )

@admin_router.callback_query(F.data == "confirm_broadcast")
async def confirm_broadcast(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.clear()

    async with async_session() as session:
        result = await session.execute(select(User.telegram_id).where(User.is_active == True))
        users = result.scalars().all()

    success = 0
    fail = 0
    bot = callback.bot

    for user_id in users:
        try:
            if data.get("photo"):
                await bot.send_photo(user_id, data["photo"], caption=data.get("text"))
            elif data.get("video"):
                await bot.send_video(user_id, data["video"], caption=data.get("text"))
            else:
                await bot.send_message(user_id, data.get("text"))
            success += 1
        except Exception:
            fail += 1

    await callback.message.edit_text(
        f"✅ Yuborildi: {success}\n❌ Xato: {fail}"
    )
    await callback.message.answer("Admin panel:", reply_markup=admin_menu_keyboard())

@admin_router.callback_query(F.data == "cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Bekor qilindi.")
    await callback.message.answer("Admin panel:", reply_markup=admin_menu_keyboard())

# ==================== 8. MAIN ENTRYPOINT ====================
async def main():
    logging.basicConfig(level=logging.INFO)

    # Baza yaratish
    await init_db()

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Middlewares
    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())

    # Routers
    dp.include_router(start_router)
    dp.include_router(user_router)
    dp.include_router(admin_router)

    print("Bot muvaffaqiyatli ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
