import asyncio
import logging
import sys
from typing import Any, Awaitable, Callable

from aiogram import Bot, Dispatcher
from aiogram.types import TelegramObject, Update
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from bot.config import BOT_TOKEN
from bot.database import init_db, is_banned, get_banned_users, get_setting, cleanup_old_photos
from bot.fsm_storage import JsonFileStorage
from bot.handlers.client import router as client_router
from bot.handlers.manager import router as manager_router


class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        if event.message and event.message.from_user:
            user_id = event.message.from_user.id
        elif event.callback_query and event.callback_query.from_user:
            user_id = event.callback_query.from_user.id
        if user_id and is_banned(user_id):
            bot: Bot = data.get("bot")
            if bot:
                # Get ban info
                conn_rows = get_banned_users()
                ban = next((r for r in conn_rows if r["tg_id"] == user_id), None)
                reason_line = f"\nПричина: <b>{ban['reason']}</b>" if ban and ban["reason"] else ""
                manager = get_setting("main_manager")
                manager_line = f"\n\nЕсли считаете, что это ошибка — напишите главному менеджеру: @{manager}" if manager else ""
                text = f"🚫 Вы были заблокированы в боте WangLogistic.{reason_line}{manager_line}"
                try:
                    if event.message:
                        await event.message.answer(text, parse_mode="HTML")
                    elif event.callback_query:
                        await event.callback_query.answer("🚫 Вы заблокированы", show_alert=True)
                except Exception:
                    pass
            return
        return await handler(event, data)


async def photo_cleanup_loop():
    while True:
        await asyncio.sleep(24 * 3600)  # раз в сутки
        cleanup_old_photos()
        logging.info("Photo cleanup done")


async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=JsonFileStorage())

    dp.update.middleware(BanMiddleware())

    dp.include_router(manager_router)
    dp.include_router(client_router)

    logging.info("Bot started")
    asyncio.create_task(photo_cleanup_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
