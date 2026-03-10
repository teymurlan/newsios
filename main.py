import os
import sys
import html
import re
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
from google import genai

# ==========================================
# 1. ИНИЦИАЛИЗАЦИЯ И ПРОВЕРКА ОКРУЖЕНИЯ
# ==========================================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Интервал автопостинга в минутах
try:
    AUTO_POST_INTERVAL_MINUTES = int(os.getenv("AUTO_POST_INTERVAL_MINUTES", "180"))
except ValueError:
    AUTO_POST_INTERVAL_MINUTES = 180

if not all([BOT_TOKEN, GEMINI_API_KEY, CHANNEL_ID]):
    logging.critical(
        "ОШИБКА: Не заданы обязательные переменные окружения "
        "(BOT_TOKEN, GEMINI_API_KEY, CHANNEL_ID)."
    )
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logging.info("BOT_TOKEN найден: %s", bool(BOT_TOKEN))
logging.info("GEMINI_API_KEY найден: %s", bool(GEMINI_API_KEY))
logging.info("CHANNEL_ID найден: %s", bool(CHANNEL_ID))

# ==========================================
# 2. GEMINI CLIENT
# ==========================================
client = genai.Client(api_key=GEMINI_API_KEY)

# ==========================================
# 3. TELEGRAM BOT
# ==========================================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# Последний сгенерированный пост для каждого пользователя
user_last_post: dict[int, str] = {}

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
    """
    Безопасная очистка текста и конвертация Markdown в HTML.
    """
    if not text:
        return ""

    # 1. Превращаем блоки кода ``` в <code>
    text = re.sub(r"```(?:html)?\n?(.*?)\n?```", r"<code>\1</code>", text, flags=re.DOTALL)
    
    # 2. Конвертируем маркдаун **жирный** в <b>жирный</b> (Gemini часто его использует)
    text = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", text)
    
    # 3. Убираем маркдаун заголовки (###)
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)

    # Разрешаем только безопасные телеграм-теги
    allowed_tags = ["b", "strong", "i", "em", "code", "u", "s", "pre"]

    # Экранируем всё, потом возвращаем разрешённые теги
    escaped = html.escape(text.strip())

    for tag in allowed_tags:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        escaped = escaped.replace(f"&lt;/{tag}&gt;", f"</{tag}>")

    return escaped

async def generate_with_gemini(prompt: str) -> str:
    """
    Генерация текста через новый Google Gen AI SDK (нативный Async).
    """
    try:
        # Используем client.aio для асинхронных запросов
        response = await client.aio.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )

        text = response.text
        if not text:
            logging.error("Gemini вернул пустой ответ.")
            return "❌ Gemini вернул пустой ответ. Попробуйте позже."

        safe_text = sanitize_html_for_telegram(text)
        return safe_text

    except Exception as e:
        logging.error("Ошибка генерации Gemini: %s", e)
        return "❌ Произошла ошибка при генерации контента. Попробуйте позже."

async def generate_tech_content(topic: str, is_news: bool = False, is_idea: bool = False) -> str:
    """
    Генерирует контент в фирменном стиле.
    """
    if is_idea:
        prompt = (
            "Ты главный редактор крупного Telegram-канала о технологиях, Android, iPhone, iOS, приложениях и гаджетах.\n"
            "Придумай 5 сильных тем для будущих постов.\n"
            "Нужно:\n"
            "- только на русском\n"
            "- современно и интересно\n"
            "- полезно для широкой аудитории\n"
            "- без воды\n"
            "- формат строго для Telegram\n\n"
            "Сделай результат в HTML Telegram-формате.\n"
            "Используй <b> для заголовков.\n"
            "Каждый пункт — коротко и по делу."
        )
        return await generate_with_gemini(prompt)

    length_req = "300-600 символов" if is_news else "700-1200 символов"
    news_req = (
        "Это новостной пост. Не выдумывай факты. Если не уверен в точных данных, пиши нейтрально."
        if is_news else
        "Это не срочная новость, а качественный авторский tech-пост."
    )

    prompt = f"""
Ты — сильный автор Telegram-канала о технологиях, Android, iPhone, iOS, приложениях и гаджетах.

Напиши пост на тему:
{topic}

Требования:
- язык: русский
- стиль: современный tech media
- тон: уверенный, умный, чистый, без воды
- текст должен легко читаться с телефона
- объем: {length_req}
- {news_req}
- без кринжа
- без мусорного кликбейта
- без выдуманных цифр и дат
- можно использовать 2-4 уместных эмодзи на весь текст

Структура:
1. Короткий цепляющий заголовок в теге <b>
2. Пустая строка
3. 1-2 коротких абзаца с сутью
4. Пустая строка
5. Блок <b>Почему это важно:</b>
6. Пустая строка
7. Блок <b>Итог:</b>
8. Пустая строка
9. Короткий финальный вывод или вопрос
10. В конце 2-4 хэштега

Формат:
- ТОЛЬКО HTML, совместимый с Telegram
- Разрешены теги: <b>, <i>, <code>
- КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать Markdown (никаких **, __, #)
"""
    return await generate_with_gemini(prompt)

async def safe_send(message: Message, text: str, reply_markup=None):
    """Отправляет сообщение, защищая от падения бота из-за кривого HTML от нейросети"""
    try:
        await message.answer(text, reply_markup=reply_markup)
    except TelegramAPIError as e:
        if "parse" in str(e).lower() or "entities" in str(e).lower():
            logging.warning("Gemini выдал кривой HTML. Отправляю как обычный текст.")
            await message.answer(text, reply_markup=reply_markup, parse_mode=None)
        else:
            logging.error(f"Ошибка отправки сообщения: {e}")
            await message.answer("❌ Ошибка при отправке сообщения.")

# ==========================================
# 6. ХЭНДЛЕРЫ БОТА
# ==========================================
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    welcome_text = (
        "👋 <b>Привет! Я AI-контент-менеджер для tech-канала.</b>\n\n"
        "Я умею:\n"
        "• генерировать посты\n"
        "• делать авто-новости\n"
        "• предлагать идеи контента\n"
        "• публиковать посты в канал\n\n"
        "Выбирай нужную кнопку ниже."
    )
    await safe_send(message, welcome_text, get_main_keyboard())

@router.message(Command("help"))
@router.message(F.text == "❓ Помощь")
async def cmd_help(message: Message) -> None:
    help_text = (
        "🤖 <b>Как пользоваться ботом:</b>\n\n"
        "1. Нажми кнопку рубрики\n"
        "2. Получи готовый пост\n"
        "3. Нажми <b>🚀 Опубликовать в канал</b>\n\n"
        "Команды:\n"
        "<code>/post</code> — обычный пост\n"
        "<code>/autonews</code> — авто-новость\n"
        "<code>/idea</code> — идеи для постов\n"
        "<code>/publish</code> — опубликовать последний пост"
    )
    await safe_send(message, help_text, get_main_keyboard())

@router.message(F.text == "🎯 Идея для поста")
@router.message(Command("idea"))
async def cmd_idea(message: Message) -> None:
    await message.answer("⏳ <i>Генерирую идеи...</i>")
    ideas = await generate_tech_content("Идеи для постов", is_idea=True)
    await safe_send(message, ideas, get_main_keyboard())

@router.message(F.text == "🚀 Опубликовать в канал")
@router.message(Command("publish"))
async def cmd_publish(message: Message) -> None:
    post_text = user_last_post.get(message.from_user.id)
    if not post_text:
        await message.answer("⚠️ Сначала сгенерируйте пост.")
        return

    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=post_text)
        await message.answer("✅ <b>Пост опубликован в канал.</b>", reply_markup=get_main_keyboard())
        user_last_post.pop(message.from_user.id, None)
    except TelegramAPIError as e:
        if "parse" in str(e).lower() or "entities" in str(e).lower():
            # Если в канал не лезет из-за HTML, шлем без форматирования
            await bot.send_message(chat_id=CHANNEL_ID, text=post_text, parse_mode=None)
            await message.answer("✅ <b>Пост опубликован (без форматирования, т.к. были ошибки в тегах).</b>", reply_markup=get_main_keyboard())
            user_last_post.pop(message.from_user.id, None)
        else:
            logging.error("Ошибка публикации в канал: %s", e)
            await message.answer(
                "❌ <b>Ошибка публикации.</b>\n"
                "Проверьте, что бот добавлен в канал как администратор и CHANNEL_ID верный."
            )

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

    await message.answer("⏳ <i>Пишу пост...</i>")
    generated_text = await generate_tech_content(topic_prompt, is_news=is_news)

    if not generated_text.startswith("❌"):
        user_last_post[message.from_user.id] = generated_text

    await safe_send(message, generated_text, get_main_keyboard())

@router.message(Command("post"))
async def cmd_post(message: Message) -> None:
    await message.answer("⏳ <i>Пишу пост...</i>")
    text = await generate_tech_content("Интересный технологический пост", is_news=False)
    if not text.startswith("❌"):
        user_last_post[message.from_user.id] = text
    await safe_send(message, text, get_main_keyboard())

@router.message(Command("autonews"))
async def cmd_autonews(message: Message) -> None:
    await message.answer("⏳ <i>Пишу новость...</i>")
    text = await generate_tech_content("Свежая новость из мира IT, Android, iPhone или AI", is_news=True)
    if not text.startswith("❌"):
        user_last_post[message.from_user.id] = text
    await safe_send(message, text, get_main_keyboard())

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

            post_text = await generate_tech_content(topic, is_news=is_news)
            if post_text.startswith("❌"):
                logging.error("Автопостинг: генерация не удалась.")
                continue

            try:
                await bot.send_message(chat_id=CHANNEL_ID, text=post_text)
            except TelegramAPIError as e:
                if "parse" in str(e).lower() or "entities" in str(e).lower():
                    await bot.send_message(chat_id=CHANNEL_ID, text=post_text, parse_mode=None)
                else:
                    raise e

            logging.info(
                "Автопостинг: пост отправлен в канал %s в %s",
                CHANNEL_ID,
                datetime.now().strftime("%H:%M:%S")
            )

        except Exception as e:
            logging.error("Автопостинг ошибка: %s", e)

# ==========================================
# 8. ЗАПУСК
# ==========================================
async def main() -> None:
    logging.info("Запуск бота...")

    # убираем вебхук, если был
    await bot.delete_webhook(drop_pending_updates=True)

    # фон
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
