import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import TelegramObject

from bot.config import BOT_TOKEN, OWNER_ID
from bot.database import init_db
from bot.handlers.client import router as client_router
from bot.handlers.manager import router as manager_router


class OwnerOnlyMiddleware(BaseMiddleware):
    """Пропускает только сообщения от владельца (OWNER_ID). 0 = отключено."""
    async def __call__(self, handler, event: TelegramObject, data: dict):
        if OWNER_ID:
            user = data.get("event_from_user")
            if user and user.id != OWNER_ID:
                return  # молча игнорируем
        return await handler(event, data)


async def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.outer_middleware(OwnerOnlyMiddleware())

    dp.include_router(manager_router)
    dp.include_router(client_router)

    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
