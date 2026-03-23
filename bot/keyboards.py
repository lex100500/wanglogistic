from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---- Клиент ----

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Обменять", callback_data="exchange")],
        [InlineKeyboardButton(text="Мои заявки", callback_data="my_orders")],
        [InlineKeyboardButton(text="Помощь", callback_data="help")],
    ])


CURRENCY_PAIRS = ["USD/RUB", "EUR/RUB", "USDT/RUB", "BTC/RUB"]


def currency_pairs_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=pair, callback_data=f"pair:{pair}")]
        for pair in CURRENCY_PAIRS
    ]
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_order_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Подтвердить", callback_data="confirm_order"),
            InlineKeyboardButton(text="Отменить", callback_data="cancel_order"),
        ]
    ])


def order_detail_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Написать менеджеру", callback_data=f"msg:{order_id}")],
        [InlineKeyboardButton(text="Назад", callback_data="my_orders")],
    ])


# ---- Менеджер ----

def manager_take_order_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Взять заказ", callback_data=f"take:{order_id}")],
    ])


def manager_status_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="В работу", callback_data=f"status:in_progress:{order_id}")],
        [InlineKeyboardButton(text="Завершить", callback_data=f"status:completed:{order_id}")],
        [InlineKeyboardButton(text="Отменить", callback_data=f"status:cancelled:{order_id}")],
        [InlineKeyboardButton(text="Написать клиенту", callback_data=f"mgr_msg:{order_id}")],
    ])
