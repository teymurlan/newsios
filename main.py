import os
import sys
import asyncio
import logging
import random
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

import google.generativeai as genai

# ==========================================
# 1. ИНИЦИАЛИЗАЦИЯ И ПРОВЕРКА ОКРУЖЕНИЯ
# ==========================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
AUTO_POST_INTERVAL_MINUTES = int(os.getenv("AUTO_POST_INTERVAL_MINUTES", 180))

if not all([BOT_TOKEN, GEMINI_API_KEY, CHANNEL_ID]):
    logging.critical("ОШИБКА: Не заданы обязательные переменные окружения (BOT_TOKEN, GEMINI_API_KEY, CHANNEL_ID).")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

genai.configure(api_key=GEMINI_API_KEY)
# Используем актуальную и быструю модель
model = genai.GenerativeModel('gemini-1.5-flash')

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Хранилище последних сгенерированных постов (в памяти)
# Формат: {user_id: "Текст поста"}
user_last_post = {}

# ==========================================
# 2. UI / КЛАВИАТУРА
# ==========================================
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📰 Сгенерировать пост"), KeyboardButton(text="⚡ Авто-новость")],
            [KeyboardButton(text="📱 Android"), KeyboardButton(text="🍏 iPhone"), KeyboardButton(text="⚔️ Android vs iPhone")],
            [KeyboardButton(text="💡 Фишка дня"), KeyboardButton(text="📲 Полезные приложения")],
            [KeyboardButton(text="🎯 Идея для поста"), KeyboardButton(text="❓ Помощь")],
            [KeyboardButton(text="🚀 Опубликовать в канал")]
        ],
        resize_keyboard=True
    )

# ==========================================
# 3. ЛОГИКА ГЕНЕРАЦИИ (GEMINI)
# ==========================================
async def generate_tech_content(topic: str, is_news: bool = False, is_idea: bool = False) -> str:
    """Генерирует контент через Gemini API в фирменном стиле."""
    
    if is_idea:
        prompt = (
            "Ты главный редактор крупного Telegram-канала о технологиях (Android, iOS, гаджеты). "
            "Придумай 5 сильных, кликабельных и полезных тем для будущих постов. "
            "Темы должны быть актуальными. Напиши только список из 5 пунктов с кратким пояснением (1 предложение) к каждому. "
            "Форматируй текст в HTML (используй <b> для жирного)."
        )
    else:
        length_req = "Коротко, 300-600 символов." if is_news else "Развернуто, но без воды, 700-1200 символов."
        news_req = "Это срочная новость. Пиши только факты, без выдумок. Если точных данных нет, пиши нейтрально." if is_news else ""
        
        prompt = f"""
Ты — профессиональный IT-журналист и автор популярного Telegram-канала о технологиях.
Твоя задача написать пост на тему: {topic}.

ПРАВИЛА СТИЛЯ:
— Современный tech media стиль: дорого, чисто, минималистично.
— Уверенно, без воды, без сухого канцелярита, без кринжа и дешевого кликбейта.
— Понятно широкой аудитории, но технически грамотно.
— {length_req}
— {news_req}
— Не выдумывай конкретные цифры, даты и факты, если не уверен.
— Используй немного уместных эмодзи (2-4 на весь текст).

СТРУКТУРА ПОСТА:
1. Короткий цепляющий заголовок (жирным шрифтом).
2. Пустая строка.
3. 1-2 коротких абзаца с сутью.
4. Пустая строка.
5. Блок "<b>Почему это важно:</b>" (или аналогичный по смыслу).
6. Пустая строка.
7. Блок "<b>Итог:</b>" (или "Что это дает пользователю").
8. Пустая строка.
9. Короткий фирменный финал (например, вопрос к аудитории или емкий вывод).
10. 2-4 тематических хэштега.

ФОРМАТИРОВАНИЕ:
Форматируй текст СТРОГО в HTML, поддерживаемом Telegram (<b>жирный</b>, <i>курсив</i>, <code>код</code>).
НЕ ИСПОЛЬЗУЙ Markdown (**жирный** или # заголовок). НЕ используй теги <br>, <p>, <h1>. 
Используй обычные переносы строк (Enter).
"""

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip()
        # Базовая очистка от возможных markdown-артефактов, если модель ошиблась
        text = text.replace("**", "<b>").replace("**", "</b>") # На случай если модель все же выдаст markdown
        return text
    except Exception as e:
        logging.error(f"Ошибка генерации Gemini: {e}")
        return "❌ Произошла ошибка при генерации контента. Попробуйте позже."

# ==========================================
# 4. ХЭНДЛЕРЫ БОТА
# ==========================================
@router.message(CommandStart())
async def cmd_start(message: Message):
    welcome_text = (
        "👋 <b>Привет! Я твой AI-контент-менеджер.</b>\n\n"
        "Я помогаю вести tech-канал стильно, профессионально и без лишних усилий.\n"
        "Выбирай нужную рубрику в меню ниже, я сгенерирую пост, а ты сможешь опубликовать его в канал в один клик."
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard())

@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message):
    help_text = (
        "🤖 <b>Как со мной работать:</b>\n\n"
        "1️⃣ Нажми на любую кнопку рубрики (например, <i>📱 Android</i> или <i>⚡ Авто-новость</i>).\n"
        "2️⃣ Я сгенерирую пост в фирменном стиле.\n"
        "3️⃣ Прочитай его. Если нравится — жми <b>🚀 Опубликовать в канал</b>.\n\n"
        "Также я сам публикую посты в канал каждые несколько часов (автопостинг).\n"
        "Команды:\n"
        "/post - Обычный пост\n"
        "/autonews - Новость\n"
        "/idea - Идеи для постов\n"
        "/publish - Опубликовать последний пост"
    )
    await message.answer(help_text, reply_markup=get_main_keyboard())

@router.message(F.text == "🚀 Опубликовать в канал")
@router.message(Command("publish"))
async def cmd_publish(message: Message):
    post_text = user_last_post.get(message.from_user.id)
    if not post_text:
        await message.answer("⚠️ Нет сохраненного поста для публикации. Сначала сгенерируйте его.")
        return

    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=post_text)
        await message.answer("✅ <b>Пост успешно опубликован в канал!</b>")
        # Очищаем память после публикации
        user_last_post.pop(message.from_user.id, None)
    except TelegramAPIError as e:
        await message.answer(f"❌ <b>Ошибка публикации:</b>\n<code>{e}</code>\n\nПроверьте, добавлен ли бот в канал как администратор.")

@router.message(F.text == "🎯 Идея для поста")
@router.message(Command("idea"))
async def cmd_idea(message: Message):
    await message.answer("⏳ <i>Генерирую идеи...</i>")
    ideas = await generate_tech_content("Идеи", is_idea=True)
    await message.answer(ideas, reply_markup=get_main_keyboard())

# Обработка рубрик
@router.message(F.text.in_({"📰 Сгенерировать пост", "⚡ Авто-новость", "📱 Android", "🍏 iPhone", "⚔️ Android vs iPhone", "💡 Фишка дня", "📲 Полезные приложения"}))
async def handle_topic_buttons(message: Message):
    topic_map = {
        "📰 Сгенерировать пост": ("Интересный технологический пост (гаджеты, тренды, софт)", False),
        "⚡ Авто-новость": ("Свежая актуальная новость из мира IT, смартфонов или нейросетей", True),
        "📱 Android": ("Интересная функция, обновление или смартфон на Android", False),
        "🍏 iPhone": ("Скрытая фишка iOS, обновление Apple или совет для iPhone", False),
        "⚔️ Android vs iPhone": ("Честное сравнение одной функции или подхода между iOS и Android", False),
        "💡 Фишка дня": ("Короткий wow-совет по использованию смартфона или ПК", True),
        "📲 Полезные приложения": ("Разбор одного крутого и полезного приложения для смартфона", False)
    }
    
    topic_prompt, is_news = topic_map.get(message.text, ("Технологии", False))
    
    await message.answer("⏳ <i>Пишу пост... Это займет пару секунд.</i>")
    
    generated_text = await generate_tech_content(topic_prompt, is_news=is_news)
    
    if not generated_text.startswith("❌"):
        user_last_post[message.from_user.id] = generated_text
        
    await message.answer(generated_text, reply_markup=get_main_keyboard())

# Обработка команд /post и /autonews
@router.message(Command("post"))
async def cmd_post(message: Message):
    await message.answer("⏳ <i>Пишу пост...</i>")
    text = await generate_tech_content("Интересный технологический пост", is_news=False)
    user_last_post[message.from_user.id] = text
    await message.answer(text)

@router.message(Command("autonews"))
async def cmd_autonews(message: Message):
    await message.answer("⏳ <i>Пишу новость...</i>")
    text = await generate_tech_content("Свежая актуальная новость из мира IT", is_news=True)
    user_last_post[message.from_user.id] = text
    await message.answer(text)

# ==========================================
# 5. ФОНОВАЯ ЗАДАЧА (АВТОПОСТИНГ)
# ==========================================
async def auto_post_worker(bot: Bot):
    """Фоновая задача для автоматической публикации постов в канал."""
    topics = [
        ("Свежая новость из мира IT", True),
        ("Интересная функция Android", False),
        ("Скрытая фишка iOS", False),
        ("Сравнение подхода Android и iOS", False),
        ("Крутое полезное приложение", False),
        ("Короткий wow-совет по гаджетам", True)
    ]
    
    logging.info(f"Автопостинг запущен. Интервал: {AUTO_POST_INTERVAL_MINUTES} минут.")
    
    while True:
        await asyncio.sleep(AUTO_POST_INTERVAL_MINUTES * 60)
        
        topic, is_news = random.choice(topics)
        logging.info(f"Автопостинг: генерация поста на тему '{topic}'...")
        
        post_text = await generate_tech_content(topic, is_news=is_news)
        
        if post_text.startswith("❌"):
            logging.error("Автопостинг: ошибка генерации, пропуск цикла.")
            continue
            
        try:
            await bot.send_message(chat_id=CHANNEL_ID, text=post_text)
            logging.info(f"Автопостинг: пост успешно отправлен в канал {CHANNEL_ID} в {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            logging.error(f"Автопостинг: ошибка отправки в канал: {e}")

# ==========================================
# 6. ЗАПУСК БОТА
# ==========================================
async def main():
    logging.info("Запуск бота...")
    
    # Запускаем фоновую задачу автопостинга
    asyncio.create_task(auto_post_worker(bot))
    
    # Запускаем polling
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
