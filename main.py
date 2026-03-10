import os
import sys
import html
import re
import asyncio
import logging
import random
import base64
import urllib.request
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from google import genai
from google.genai import types

# ==========================================
# 1. ИНИЦИАЛИЗАЦИЯ И ПРОВЕРКА ОКРУЖЕНИЯ
# ==========================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")

try:
    AUTO_POST_INTERVAL_MINUTES = int(os.getenv("AUTO_POST_INTERVAL_MINUTES", "180"))
except ValueError:
    AUTO_POST_INTERVAL_MINUTES = 180

if not all([BOT_TOKEN, GEMINI_API_KEY, CHANNEL_ID]):
    logging.critical("ОШИБКА: Не заданы обязательные переменные окружения.")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==========================================
# 2. GEMINI CLIENT
# ==========================================
client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 3. TELEGRAM BOT
# ==========================================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

user_last_post: dict[int, dict] = {}

# База крутых картинок про технологии (на случай блокировки генерации от Google)
TECH_IMAGES = [
    "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1498049794561-7780e7231661?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1551288049-bebda4e38f71?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1519389950473-47ba0277781c?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1550751827-4bd374c3f58b?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1504610926078-a1611febcad3?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1593640408182-31c70c8268f5?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1601972599720-36938d4ecd31?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1526406915894-7bcd65f60845?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1611162617474-5b21e879e113?q=80&w=800&auto=format&fit=crop",
    "https://images.unsplash.com/photo-1531297183-14a232b69226?q=80&w=800&auto=format&fit=crop"
]

# ==========================================
# 4. UI / КЛАВИАТУРА
# ==========================================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📰 Сгенерировать пост"), KeyboardButton(text="⚡ Авто-новость")],
            [KeyboardButton(text="📱 Android"), KeyboardButton(text="🍏 iPhone")],
            [KeyboardButton(text="⚔️ Android vs iPhone"), KeyboardButton(text="💡 Фишка дня")],
            [KeyboardButton(text="📲 Полезные приложения"), KeyboardButton(text="🎯 Идея для поста")],
            [KeyboardButton(text="❓ Помощь"), KeyboardButton(text="🚀 Опубликовать в канал")]
        ],
        resize_keyboard=True
    )

# ==========================================
# 5. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def sanitize_html_for_telegram(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"```(?:html)?\n?(.*?)\n?```", r"<code>\1</code>", text, flags=re.DOTALL)
    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    allowed_tags = ["b", "strong", "i", "em", "code", "u", "s", "pre"]
    escaped = html.escape(text.strip())
    for tag in allowed_tags:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        escaped = escaped.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return escaped

async def generate_image_with_gemini(topic: str) -> bytes | None:
    """Генерация картинки с надежным запасным вариантом."""
    prompt = f"A modern cinematic illustration for a tech blog about: {topic}. Minimalistic, gadgets, digital art style. No text."
    
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model='gemini-2.5-flash-image',
            contents=prompt
        )
        for part in response.candidates[0].content.parts:
            if part.inline_data:
                data = part.inline_data.data
                return base64.b64decode(data) if isinstance(data, str) else data
    except Exception as e:
        logging.warning("Google заблокировал генерацию картинок: %s", e)
            
    # Запасная картинка из нашей крутой базы
    logging.info("Берем красивую tech-картинку из базы...")
    try:
        url = random.choice(TECH_IMAGES)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = await asyncio.to_thread(urllib.request.urlopen, req, timeout=5)
        return response.read()
    except Exception as e3:
        logging.error("Не удалось скачать запасную картинку: %s", e3)
        
    return None

async def generate_with_gemini(prompt: str) -> str:
    try:
        # Используем 2.5-flash с огромными лимитами
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt
        )
        text = response.text
        if not text:
            return "❌ Gemini вернул пустой ответ."
        return sanitize_html_for_telegram(text)
    except Exception as e:
        logging.error("Ошибка генерации текста 2.5-flash: %s", e)
        try:
            # Запасная модель, если первая упала
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.0-flash",
                contents=prompt
            )
            return sanitize_html_for_telegram(response.text)
        except Exception as e2:
            logging.error("Запасная генерация текста тоже упала: %s", e2)
            return "❌ Произошла ошибка при генерации контента. Лимиты Google исчерпаны."

async def generate_tech_content(topic: str, is_news: bool = False, is_idea: bool = False) -> str:
    if is_idea:
        prompt = (
            "Ты главный редактор Telegram-канала о технологиях.\n"
            "Придумай 5 сильных тем для будущих постов.\n"
            "Сделай результат в HTML Telegram-формате. Используй <b> для заголовков."
        )
        return await generate_with_gemini(prompt)

    # КОРОТКИЕ ПОСТЫ
    length_req = "300-400 символов" if is_news else "400-600 символов"
    news_req = "Это новостной пост. Пиши строго по делу, без воды." if is_news else "Это качественный авторский tech-пост."

    prompt = f"""
Ты — сильный автор Telegram-канала о технологиях, Android, iPhone и гаджетах.

Напиши пост на тему:
{topic}

Требования:
- язык: русский
- стиль: современный tech media, лаконичный
- тон: уверенный, умный, чистый, без воды
- текст должен легко и быстро читаться с телефона (делай абзацы короткими, по 1-2 предложения)
- объем: {length_req} (ПИШИ КОРОТКО И ПО ДЕЛУ)
- {news_req}
- без кринжа и мусорного кликбейта
- 1-2 уместных эмодзи на весь текст

Структура:
1. Короткий цепляющий заголовок в теге <b>
2. 1-2 коротких абзаца с самой сутью (без долгих вступлений)
3. Короткий вывод
4. Хэштеги: 3-4 штуки в самом конце. 2-3 на русском, 1 на английском (например: #технологии #смартфоны #Apple).

Формат:
- ТОЛЬКО HTML, совместимый с Telegram (<b>, <i>, <code>)
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать Markdown (никаких **, __, # для заголовков)
"""
    return await generate_with_gemini(prompt)

async def safe_send_post(target, text: str, photo_bytes: bytes | None, reply_markup=None, is_channel=False):
    """Умная отправка поста с картинкой и защитой от ошибок HTML."""
    try:
        if photo_bytes:
            photo = BufferedInputFile(photo_bytes, filename="post_image.jpg")
            if len(text) <= 1000:
                if is_channel:
                    await bot.send_photo(chat_id=target, photo=photo, caption=text, reply_markup=reply_markup)
                else:
                    await target.answer_photo(photo=photo, caption=text, reply_markup=reply_markup)
            else:
                if is_channel:
                    await bot.send_photo(chat_id=target, photo=photo)
                    await bot.send_message(chat_id=target, text=text, reply_markup=reply_markup)
                else:
                    await target.answer_photo(photo=photo)
                    await target.answer(text, reply_markup=reply_markup)
        else:
            if is_channel:
                await bot.send_message(chat_id=target, text=text, reply_markup=reply_markup)
            else:
                await target.answer(text, reply_markup=reply_markup)
                
    except TelegramAPIError as e:
        error_msg = str(e).lower()
        if "parse" in error_msg or "entities" in error_msg:
            logging.warning("Gemini выдал кривой HTML. Отправляю без форматирования.")
            if photo_bytes and len(text) <= 1000:
                photo = BufferedInputFile(photo_bytes, filename="post_image.jpg")
                if is_channel:
                    await bot.send_photo(chat_id=target, photo=photo, caption=text, parse_mode=None)
                else:
                    await target.answer_photo(photo=photo, caption=text, parse_mode=None, reply_markup=reply_markup)
            else:
                if is_channel:
                    await bot.send_message(chat_id=target, text=text, parse_mode=None)
                else:
                    await target.answer(text, parse_mode=None, reply_markup=reply_markup)
        else:
            logging.error(f"Ошибка отправки: {e}")
            if not is_channel:
                await target.answer("❌ Ошибка при отправке сообщения.")

# ==========================================
# 6. ХЭНДЛЕРЫ БОТА
# ==========================================
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    welcome_text = (
        "👋 <b>Привет! Я AI-контент-менеджер для tech-канала.</b>\n\n"
        "Я умею:\n"
        "• генерировать короткие посты с крутыми картинками 🖼\n"
        "• делать авто-новости\n"
        "• предлагать идеи контента\n"
        "• публиковать посты в канал\n\n"
        "Выбирай нужную кнопку ниже."
    )
    await safe_send_post(message, welcome_text, None, get_main_keyboard())

@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message) -> None:
    help_text = (
        "🤖 <b>Как пользоваться ботом:</b>\n\n"
        "1. Нажми кнопку рубрики\n"
        "2. Получи готовый пост с картинкой\n"
        "3. Нажми <b>🚀 Опубликовать в канал</b>\n\n"
        "Команды:\n"
        "<code>/post</code> — обычный пост\n"
        "<code>/autonews</code> — авто-новость\n"
        "<code>/idea</code> — идеи для постов\n"
        "<code>/publish</code> — опубликовать последний пост"
    )
    await safe_send_post(message, help_text, None, get_main_keyboard())

@router.message(F.text == "🎯 Идея для поста")
@router.message(Command("idea"))
async def cmd_idea(message: Message) -> None:
    await message.answer("⏳ <i>Генерирую идеи...</i>")
    ideas = await generate_tech_content("Идеи для постов", is_idea=True)
    await safe_send_post(message, ideas, None, get_main_keyboard())

@router.message(F.text == "🚀 Опубликовать в канал")
@router.message(Command("publish"))
async def cmd_publish(message: Message) -> None:
    post_data = user_last_post.get(message.from_user.id)
    if not post_data:
        await message.answer("⚠️ Сначала сгенерируйте пост.")
        return

    text = post_data.get("text")
    photo = post_data.get("photo")

    try:
        await safe_send_post(CHANNEL_ID, text, photo, is_channel=True)
        await message.answer("✅ <b>Пост опубликован в канал!</b>", reply_markup=get_main_keyboard())
        user_last_post.pop(message.from_user.id, None)
    except Exception as e:
        logging.error("Ошибка публикации в канал: %s", e)
        await message.answer("❌ <b>Ошибка публикации.</b> Проверьте права бота в канале.")

@router.message(
    F.text.in_({
        "📰 Сгенерировать пост",
        "⚡ Авто-новость",
        "📱 Android",
        "🍏 iPhone",
        "⚔️ Android vs iPhone",
        "💡 Фишка дня",
        "📲 Полезные приложения"
    })
)
async def handle_topic_buttons(message: Message) -> None:
    topic_map = {
        "📰 Сгенерировать пост": ("Интересный пост о технологиях, гаджетах, приложениях или трендах", False),
        "⚡ Авто-новость": ("Свежая новость из мира Android, iPhone, iOS, гаджетов или AI", True),
        "📱 Android": ("Интересная функция Android, новое обновление, смартфон или полезный совет", False),
        "🍏 iPhone": ("Фишка iPhone, iOS, полезная настройка Apple или совет для пользователей", False),
        "⚔️ Android vs iPhone": ("Честное сравнение Android и iPhone по одной важной функции или сценарию", False),
        "💡 Фишка дня": ("Короткий полезный wow-совет для смартфона или гаджета", True),
        "📲 Полезные приложения": ("Полезное приложение для смартфона и объяснение, зачем оно нужно", False),
    }

    topic_prompt, is_news = topic_map.get(message.text, ("Технологии", False))

    await message.answer("⏳ <i>Пишу короткий текст и готовлю картинку... (около 10-15 сек)</i>")
    
    text_task = generate_tech_content(topic_prompt, is_news=is_news)
    image_task = generate_image_with_gemini(topic_prompt)
    
    generated_text, image_bytes = await asyncio.gather(text_task, image_task)

    if not generated_text.startswith("❌"):
        user_last_post[message.from_user.id] = {
            "text": generated_text,
            "photo": image_bytes
        }

    await safe_send_post(message, generated_text, image_bytes, get_main_keyboard())

@router.message(Command("post"))
async def cmd_post(message: Message) -> None:
    await message.answer("⏳ <i>Пишу текст и готовлю картинку...</i>")
    text_task = generate_tech_content("Интересный технологический пост", is_news=False)
    image_task = generate_image_with_gemini("Интересный технологический пост")
    
    text, photo = await asyncio.gather(text_task, image_task)
    
    if not text.startswith("❌"):
        user_last_post[message.from_user.id] = {"text": text, "photo": photo}
    await safe_send_post(message, text, photo, get_main_keyboard())

@router.message(Command("autonews"))
async def cmd_autonews(message: Message) -> None:
    await message.answer("⏳ <i>Пишу новость и готовлю картинку...</i>")
    text_task = generate_tech_content("Свежая новость из мира IT, Android, iPhone или AI", is_news=True)
    image_task = generate_image_with_gemini("Свежая новость из мира IT, Android, iPhone или AI")
    
    text, photo = await asyncio.gather(text_task, image_task)
    
    if not text.startswith("❌"):
        user_last_post[message.from_user.id] = {"text": text, "photo": photo}
    await safe_send_post(message, text, photo, get_main_keyboard())

# ==========================================
# 7. АВТОПОСТИНГ
# ==========================================
async def auto_post_worker() -> None:
    topics = [
        ("Свежая новость из мира IT", True),
        ("Интересная функция Android", False),
        ("Скрытая фишка iPhone или iOS", False),
        ("Сравнение Android и iPhone", False),
        ("Полезное приложение для смартфона", False),
        ("Короткий wow-совет по гаджетам", True),
    ]

    logging.info("Автопостинг запущен. Интервал: %s минут.", AUTO_POST_INTERVAL_MINUTES)

    while True:
        try:
            await asyncio.sleep(AUTO_POST_INTERVAL_MINUTES * 60)

            topic, is_news = random.choice(topics)
            logging.info("Автопостинг: генерирую пост на тему '%s'", topic)

            text_task = generate_tech_content(topic, is_news=is_news)
            image_task = generate_image_with_gemini(topic)
            
            post_text, image_bytes = await asyncio.gather(text_task, image_task)

            if post_text.startswith("❌"):
                logging.error("Автопостинг: генерация не удалась.")
                continue

            await safe_send_post(CHANNEL_ID, post_text, image_bytes, is_channel=True)
            logging.info("Автопостинг: пост отправлен в канал %s", CHANNEL_ID)

        except Exception as e:
            logging.error("Автопостинг ошибка: %s", e)

# ==========================================
# 8. ЗАПУСК
# ==========================================
async def main() -> None:
    logging.info("Запуск бота...")
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(auto_post_worker())
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")
