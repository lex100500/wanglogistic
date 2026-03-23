from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---- Клиент ----

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Правила использования", url="https://telegra.ph/Pravila-ispolzovaniya-servisa-WangLogistic-03-23")],
        [InlineKeyboardButton(text="Обменять", callback_data="exchange")],
        [InlineKeyboardButton(text="Мои заявки", callback_data="my_orders")],
        [InlineKeyboardButton(text="Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="Помощь", callback_data="help")],
    ])


def direction_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="RUB → CNY", callback_data="dir:RUB:CNY")],
        [InlineKeyboardButton(text="CNY → RUB", callback_data="dir:CNY:RUB")],
        [InlineKeyboardButton(text="Назад", callback_data="back_menu")],
    ])


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


def pay_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="WeChat Pay", callback_data="pay:wechat")],
        [InlineKeyboardButton(text="Alipay", callback_data="pay:alipay")],
        [InlineKeyboardButton(text="Назад", callback_data="back_menu")],
    ])


def profile_menu_kb(has_wechat=False, has_alipay=False, has_card=False, only=None) -> InlineKeyboardMarkup:
    """only='qr' — только QR-коды, only='card' — только карта, None — всё."""
    rows = []
    if only in (None, "qr"):
        r1 = [InlineKeyboardButton(text="WeChat QR", callback_data="profile:wechat_qr")]
        if has_wechat:
            r1.append(InlineKeyboardButton(text="X", callback_data="profile_del:wechat_qr"))
        rows.append(r1)
        r2 = [InlineKeyboardButton(text="Alipay QR", callback_data="profile:alipay_qr")]
        if has_alipay:
            r2.append(InlineKeyboardButton(text="X", callback_data="profile_del:alipay_qr"))
        rows.append(r2)
    if only in (None, "card"):
        r3 = [InlineKeyboardButton(text="Реквизиты карты", callback_data="profile:card")]
        if has_card:
            r3.append(InlineKeyboardButton(text="X", callback_data="profile_del:card"))
        rows.append(r3)
    rows.append([InlineKeyboardButton(text="Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---- Менеджер ----

def manager_take_order_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Взять заказ", callback_data=f"take:{order_id}")],
    ])


def manager_status_kb(order_id: str, show_qr: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="В работу", callback_data=f"status:in_progress:{order_id}")],
        [InlineKeyboardButton(text="Завершить", callback_data=f"status:completed:{order_id}")],
        [InlineKeyboardButton(text="Отменить", callback_data=f"status:cancelled:{order_id}")],
        [InlineKeyboardButton(text="Написать клиенту", callback_data=f"mgr_msg:{order_id}")],
    ]
    if show_qr:
        buttons.insert(0, [InlineKeyboardButton(text="QR-код клиента", callback_data=f"get_qr:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
