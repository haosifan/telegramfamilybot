from __future__ import annotations

import logging
from datetime import datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, CallbackContext, CommandHandler, MessageHandler, filters

from .config import settings
from .llm import LLMParser
from .notion import NotionStore
from .reviews import Reviews
from .service import TaskService
from .stt import SpeechToText

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("familybot")

tz = ZoneInfo(settings.timezone)
store = NotionStore(settings.notion_token, settings.notion_data_source_id, settings.notion_version)
parser = LLMParser(settings.openai_api_key, settings.openai_model, settings.timezone)
service = TaskService(store, settings.default_area)
reviews = Reviews(service, settings.timezone)
stt = SpeechToText(settings.openai_api_key, settings.openai_transcription_model, settings.transcription_language)


def authorized(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == settings.telegram_allowed_user_id)


async def reject(update: Update):
    if update.effective_message:
        uid = update.effective_user.id if update.effective_user else "?"
        await update.effective_message.reply_text(f"⛔ Dieser Bot ist privat. Deine Telegram-ID ist {uid}.")


async def start(update: Update, context: CallbackContext):
    if not authorized(update):
        return await reject(update)
    await update.message.reply_text(
        "👋 Persönlicher Aufgabenassistent bereit.\n\n"
        "Schreib oder sprich einfach natürlich, z. B.:\n"
        "• Morgen Schule anrufen.\n"
        "• Paket wegbringen auf hoch und morgen dringend erinnern.\n"
        "• Rechnung ist erledigt.\n"
        "• Was ist heute offen?"
    )


async def handle_text(update: Update, context: CallbackContext):
    if not authorized(update):
        return await reject(update)
    await process_input(update, update.message.text)


async def handle_voice(update: Update, context: CallbackContext):
    if not authorized(update):
        return await reject(update)
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "voice.ogg"
        f = await update.message.voice.get_file()
        await f.download_to_drive(custom_path=path)
        text = await stt.transcribe(path)
    if not text:
        return await update.message.reply_text("⚠️ Ich konnte die Sprachnachricht nicht verstehen.")
    await update.message.reply_text(f"🎙️ _{text}_", parse_mode="Markdown")
    await process_input(update, text)


async def process_input(update: Update, text: str):
    try:
        plan = await parser.parse(text, datetime.now(tz))
        if plan.clarification:
            return await update.message.reply_text(f"❓ {plan.clarification}")
        if not plan.actions:
            return await update.message.reply_text("Ich habe darin keine Aufgabenaktion erkannt.")
        results = []
        for action in plan.actions:
            results.append(await service.execute(action))
        await update.message.reply_text("\n\n".join(results))
    except Exception as e:
        log.exception("processing failed")
        await update.message.reply_text(f"⚠️ Verarbeitung fehlgeschlagen: {type(e).__name__}. Details stehen im Server-Log.")


async def send_morning(context: CallbackContext):
    await context.bot.send_message(settings.telegram_allowed_user_id, await reviews.morning_text())


async def send_evening(context: CallbackContext):
    await context.bot.send_message(settings.telegram_allowed_user_id, await reviews.evening_text())


async def send_urgent(context: CallbackContext):
    text = await reviews.urgent_text()
    if text:
        await context.bot.send_message(settings.telegram_allowed_user_id, text)


def parse_clock(value: str) -> time:
    h, m = map(int, value.split(":"))
    return time(hour=h, minute=m, tzinfo=tz)


async def shutdown(app: Application):
    await store.close()
    await parser.close()
    await stt.close()


def main():
    app = Application.builder().token(settings.telegram_bot_token).post_shutdown(shutdown).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.job_queue.run_daily(send_morning, parse_clock(settings.morning_review_time), name="morning")
    app.job_queue.run_daily(send_evening, parse_clock(settings.evening_review_time), name="evening")
    for i, clock in enumerate(settings.urgent_reminder_times.split(",")):
        app.job_queue.run_daily(send_urgent, parse_clock(clock.strip()), name=f"urgent-{i}")

    log.info("Starting private Telegram bot via long polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
