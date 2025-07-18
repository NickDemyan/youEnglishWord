import os
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, func
from sqlalchemy.orm import sessionmaker, declarative_base
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import random

# Загрузка токена
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("❌ Токен бота не найден! Создайте файл .env с BOT_TOKEN=ваш_токен")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
logging.getLogger("aiogram").setLevel(logging.WARNING)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# База данных (SQLite)
Base = declarative_base()
engine = create_engine('sqlite:///words.db', echo=True)
Session = sessionmaker(bind=engine)
session = Session()

# Модель слова
class Word(Base):
    __tablename__ = 'words'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    english = Column(String)
    russian = Column(String)
    is_learned = Column(Boolean, default=False)
    next_review = Column(DateTime, default=datetime.now())
    repetition_step = Column(Integer, default=0)

# Состояния FSM
class Form(StatesGroup):
    add_english = State()
    add_russian = State()
    study_word = State()
    repeat_all = State()

# ========== КЛАВИАТУРЫ ==========

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Добавить слово ➕")],
            [KeyboardButton(text="Учить слова 📖"), KeyboardButton(text="Статистика 📊")],
            [KeyboardButton(text="Повторить слова 🔄"), KeyboardButton(text="Помощь ❓")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )

def get_study_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Показать ответ", callback_data="show_answer")],
            [InlineKeyboardButton(text="Следующее слово", callback_data="next_word")]
        ]
    )

# ========== ОБРАБОТЧИКИ КОМАНД ==========

@dp.message(Command('start'))
async def start(message: Message):
    user_id = message.from_user.id
    
    if not session.query(Word).filter_by(user_id=user_id).first():
        new_word = Word(
            user_id=user_id,
            english="welcome",
            russian="добро пожаловать"
        )
        session.add(new_word)
        session.commit()
        logger.info(f"Добавлен новый пользователь: {user_id}")
    
    await message.answer(
        "📚 Английский словарь\n\n"
        "Я помогу вам учить новые слова!",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command('help'))
@dp.message(F.text == "Помощь ❓")
async def help_command(message: Message):
    await message.answer(
        "ℹ️ Справка по боту:\n\n"
        "• Добавить слово - введите слово и перевод\n"
        "• Учить слова - повторение слов по алгоритму\n"
        "• Повторить слова - повторение всех слов в случайном порядке\n"
        "• Статистика - ваш прогресс изучения\n\n"
        "Используйте кнопки меню для навигации",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command('add_word'))
@dp.message(F.text == "Добавить слово ➕")
async def add_word_start(message: Message, state: FSMContext):
    await state.set_state(Form.add_english)
    await message.answer(
        "Введите слово на английском:",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(Form.add_english)
async def add_english_word(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление слова отменено", reply_markup=get_main_keyboard())
        return
    
    existing_word = session.query(Word).filter_by(user_id=message.from_user.id, english=message.text).first()
    if existing_word:
        await message.answer(
            f"❌ Слово '{message.text}' уже добавлено!",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
        
    await state.update_data(english=message.text)
    await state.set_state(Form.add_russian)
    await message.answer(
        "Теперь введите перевод на русском:",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(Form.add_russian)
async def add_russian_translation(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Добавление слова отменено", reply_markup=get_main_keyboard())
        return
        
    data = await state.get_data()
    new_word = Word(
        user_id=message.from_user.id,
        english=data['english'],
        russian=message.text
    )
    session.add(new_word)
    session.commit()
    await state.clear()
    await message.answer(
        f"✅ Слово '{data['english']}' добавлено!",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command('study'))
@dp.message(F.text == "Учить слова 📖")
async def study_words(message: Message, state: FSMContext):
    user_id = message.from_user.id
    words = session.query(Word).filter(
        Word.user_id == user_id,
        Word.next_review <= datetime.now()
    ).all()
    
    if not words:
        await message.answer(
            "🎉 Пока нет слов для повторения!",
            reply_markup=get_main_keyboard()
        )
        return
    
    await state.set_state(Form.study_word)
    await state.update_data(words=words, current_word_index=0)
    await send_study_word(message, state)

@dp.message(F.text == "Повторить слова 🔄")
async def repeat_all_words(message: Message, state: FSMContext):
    user_id = message.from_user.id
    words = session.query(Word).filter_by(user_id=user_id).all()
    
    if not words:
        await message.answer(
            "📝 У вас пока нет добавленных слов.",
            reply_markup=get_main_keyboard()
        )
        return
    
    random.shuffle(words)
    await state.set_state(Form.repeat_all)
    await state.update_data(words=words, current_word_index=0)
    await send_study_word(message, state)

async def send_study_word(message: Message, state: FSMContext):
    data = await state.get_data()
    words = data['words']
    current_word_index = data['current_word_index']
    
    if current_word_index >= len(words):
        await message.answer(
            "🏁 Все слова повторены!",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
    
    word = words[current_word_index]
    await state.update_data(current_word=word)
    await message.answer(
        f"📝 Слово: <b>{word.english}</b>\n"
        "Напишите перевод или нажмите кнопку ниже",
        parse_mode="HTML",
        reply_markup=get_study_keyboard()
    )

@dp.callback_query(Form.study_word, F.data == "show_answer")
@dp.callback_query(Form.repeat_all, F.data == "show_answer")
async def show_answer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    word = data['current_word']
    
    await callback.message.answer(
        f"✅ Перевод: <b>{word.russian}</b>",
        parse_mode="HTML",
    )
    
    data = await state.get_data()
    words = data['words']
    current_word_index = data['current_word_index'] + 1
    await state.update_data(current_word_index=current_word_index)
    await send_study_word(callback.message, state)

@dp.callback_query(Form.study_word, F.data == "next_word")
@dp.callback_query(Form.repeat_all, F.data == "next_word")
async def next_word(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    words = data['words']
    current_word_index = data['current_word_index'] + 1
    
    if await state.get_state() == Form.study_word:
        word = data['current_word']
        word.repetition_step += 1
        word.next_review = datetime.now() + timedelta(days=2**word.repetition_step)
        session.commit()
    
    await state.update_data(current_word_index=current_word_index)
    await send_study_word(callback.message, state)

@dp.message(Form.study_word)
@dp.message(Form.repeat_all)
async def check_translation(message: Message, state: FSMContext):
    data = await state.get_data()
    word = data['current_word']
    
    if message.text.lower() == word.russian.lower():
        await message.answer("✅ Правильно! Молодец!")
        if await state.get_state() == Form.study_word:
            word.repetition_step += 1
            word.next_review = datetime.now() + timedelta(days=2**word.repetition_step)
            session.commit()
        
        data = await state.get_data()
        words = data['words']
        current_word_index = data['current_word_index'] + 1
        await state.update_data(current_word_index=current_word_index)
        await send_study_word(message, state)
    else:
        await message.answer("❌ Неправильно. Попробуйте ещё раз.")

@dp.message(Command('stats'))
@dp.message(F.text == "Статистика 📊")
async def show_stats(message: Message):
    user_id = message.from_user.id
    total = session.query(Word).filter_by(user_id=user_id).count()
    learned = session.query(Word).filter_by(user_id=user_id, is_learned=True).count()
    
    progress = (learned/total*100) if total > 0 else 0
    
    await message.answer(
        f"📊 Ваша статистика:\n\n"
        f"• Всего слов: {total}\n"
        f"• Выучено: {learned}\n"
        f"• Прогресс: {progress:.0f}%\n\n"
        f"Продолжайте в том же духе!",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command('cancel'))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено",
        reply_markup=get_main_keyboard()
    )

@dp.message()
async def handle_unknown(message: Message):
    await message.answer(
        "Я не понимаю эту команду. Пожалуйста, используйте кнопки меню",
        reply_markup=get_main_keyboard()
    )

# ========== ПЛАНИРОВЩИК ==========

scheduler = AsyncIOScheduler()

async def send_daily_words():
    try:
        users = session.query(Word.user_id).distinct().all()
        
        for (user_id,) in users:
            try:
                words = session.query(Word).filter(
                    Word.user_id == user_id,
                    Word.next_review <= datetime.now()
                ).limit(3).all()
                
                if words:
                    await bot.send_message(
                        user_id,
                        "📖 Пора повторить слова!\nНажмите 'Учить слова 📖'",
                        reply_markup=get_main_keyboard()
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка в send_daily_words: {e}")

# ========== ЗАПУСК БОТА ==========

async def on_startup():
    Base.metadata.create_all(engine)
    scheduler.add_job(send_daily_words, 'interval', hours=24)
    scheduler.start()
    logger.info("Бот запущен")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())