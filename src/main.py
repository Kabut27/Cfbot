"""Entry point."""
import logging, os, sys

for _k in ("BOT_TOKEN", "SECRET_KEY"):
    if not os.environ.get(_k):
        sys.exit(f"Missing env var: {_k} (run install.sh or see .env.example)")

import config
import storage
from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from core import router
import handlers  # noqa: F401  (registers routes)
from handlers import home, jobs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cfbot")


async def post_init(app):
    await app.bot.set_my_commands([BotCommand("start", "Menu kuu"), BotCommand("menu", "Menu kuu"), BotCommand("cancel", "Ghairi")])


async def on_error(update, context):
    log.error("Unhandled error", exc_info=context.error)


def main():
    storage.init()
    app = (Application.builder().token(config.TOKEN).concurrent_updates(True).post_init(post_init).build())
    app.add_handler(CommandHandler(["start", "menu", "cancel"], home.cmd))
    app.add_handler(CallbackQueryHandler(router.on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router.on_text))
    app.add_error_handler(on_error)
    jq = app.job_queue
    if jq:
        jq.run_repeating(jobs.watch_tick, interval=30, first=20)
        jq.run_repeating(jobs.health_tick, interval=900, first=120)
        jq.run_repeating(jobs.usage_tick, interval=3600, first=300)
    else:
        log.warning("JobQueue haipo: install 'python-telegram-bot[job-queue]' ili arifa za nyuma zifanye kazi.")
    log.info("Bot inaanza...")
    app.run_polling(allowed_updates=["message", "callback_query"], drop_pending_updates=True)


if __name__ == "__main__":
    main()
