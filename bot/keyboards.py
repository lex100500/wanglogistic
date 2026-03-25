from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---- Клиент ----

def main_menu(rules_url: str = "https://telegra.ph/Pravila-ispolzovaniya-servisa-WangLogistic-03-23") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Правила пользования", url=rules_url)],
        [InlineKeyboardButton(text="🎁 Акции", callback_data="promotions")],
        [InlineKeyboardButton(text="💱 Обменять", callback_data="exchange")],
        [InlineKeyboardButton(text="📦 Мои заявки", callback_data="my_orders")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="🆘 Помощь", callback_data="help")],
    ])


def direction_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 RUB → CNY 🇨🇳", callback_data="dir:RUB:CNY")],
        [InlineKeyboardButton(text="🇨🇳 CNY → RUB 🇷🇺", callback_data="dir:CNY:RUB")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])


def confirm_order_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_order"),
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_order"),
        ]
    ])


def order_detail_kb(order_id: str, closed: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if not closed:
        rows.append([InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="my_orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bank_select_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💚 СБЕР", callback_data="bank:СБЕР"),
         InlineKeyboardButton(text="🟡 Т-Банк", callback_data="bank:Т-Банк")],
        [InlineKeyboardButton(text="🔴 АЛЬФА", callback_data="bank:АЛЬФА"),
         InlineKeyboardButton(text="🔵 ВТБ", callback_data="bank:ВТБ")],
        [InlineKeyboardButton(text="🟠 ОЗОН", callback_data="bank:ОЗОН")],
        [InlineKeyboardButton(text="🏦 Другой", callback_data="bank:other")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])


def pay_method_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 WeChat Pay", callback_data="pay:wechat")],
        [InlineKeyboardButton(text="💙 Alipay", callback_data="pay:alipay")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])


def profile_menu_kb(has_wechat=False, has_alipay=False, has_card=False, only=None) -> InlineKeyboardMarkup:
    """only='qr' — только QR-коды, only='card' — только карта, None — всё."""
    rows = []
    if only in (None, "qr"):
        r1 = [InlineKeyboardButton(
            text="💬 WeChat QR (изменить)" if has_wechat else "💬 WeChat QR",
            callback_data="profile:wechat_qr"
        )]
        if has_wechat:
            r1.append(InlineKeyboardButton(text="🗑 Удалить", callback_data="profile_del:wechat_qr"))
        rows.append(r1)
        r2 = [InlineKeyboardButton(
            text="💙 Alipay QR (изменить)" if has_alipay else "💙 Alipay QR",
            callback_data="profile:alipay_qr"
        )]
        if has_alipay:
            r2.append(InlineKeyboardButton(text="🗑 Удалить", callback_data="profile_del:alipay_qr"))
        rows.append(r2)
    if only in (None, "card"):
        r3 = [InlineKeyboardButton(
            text="💳 Реквизиты карты (изменить)" if has_card else "💳 Реквизиты карты",
            callback_data="profile:card"
        )]
        if has_card:
            r3.append(InlineKeyboardButton(text="🗑 Удалить", callback_data="profile_del:card"))
        rows.append(r3)
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---- Менеджер ----

def manager_take_order_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤝 Взять заказ", callback_data=f"take:{order_id}")],
    ])


def manager_status_kb(order_id: str, show_qr: bool = False, show_req: bool = True, send_qr_mode: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if show_qr:
        buttons.append([InlineKeyboardButton(text="📷 QR-код клиента", callback_data=f"get_qr:{order_id}")])
    if show_req:
        if send_qr_mode:
            buttons.append([InlineKeyboardButton(text="📷 Скинуть QR код клиенту", callback_data=f"send_req:{order_id}")])
        else:
            buttons.append([InlineKeyboardButton(text="💳 Скинуть реквизиты для оплаты", callback_data=f"send_req:{order_id}")])
    buttons.append([InlineKeyboardButton(text="✉️ Написать клиенту", callback_data=f"mgr_msg:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def manager_bank_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💚 СБЕР", callback_data="mgr_bank:СБЕР"),
         InlineKeyboardButton(text="🟡 Т-Банк", callback_data="mgr_bank:Т-Банк")],
        [InlineKeyboardButton(text="🔴 АЛЬФА", callback_data="mgr_bank:АЛЬФА"),
         InlineKeyboardButton(text="🔵 ВТБ", callback_data="mgr_bank:ВТБ")],
        [InlineKeyboardButton(text="🟠 ОЗОН", callback_data="mgr_bank:ОЗОН")],
        [InlineKeyboardButton(text="🏦 Другой", callback_data="mgr_bank:other")],
    ])


def help_kb(guide_url: str = "https://telegra.ph/test-cheki-03-23") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Гайд по отправке чеков", url=guide_url)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")],
    ])


def receipt_confirm_kb(guide_url: str = "https://telegra.ph/test-cheki-03-23") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, умею", callback_data="receipt_yes")],
        [InlineKeyboardButton(text="📖 Гайд по чекам", url=guide_url)],
    ])


def confirm_payment_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"paid:{order_id}")],
        [InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
    ])


def confirm_payment_with_receipt_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📎 Прикрепить чек", callback_data=f"attach_receipt:{order_id}")],
        [InlineKeyboardButton(text="✅ Подтвердить оплату", callback_data=f"paid:{order_id}")],
        [InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
    ])


def manager_yuan_sent_kb(order_id: str, show_qr: bool = False, is_rub: bool = False) -> InlineKeyboardMarkup:
    label = "✅ Рубли отправлены клиенту" if is_rub else "✅ Юани отправлены клиенту"
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"yuan_sent:{order_id}")],
        [InlineKeyboardButton(text="✉️ Написать клиенту", callback_data=f"mgr_msg:{order_id}")],
    ]
    if show_qr:
        buttons.insert(0, [InlineKeyboardButton(text="📷 QR-код клиента", callback_data=f"get_qr:{order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def client_yuan_delivery_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"yuan_received:{order_id}")],
        [InlineKeyboardButton(text="❌ Не пришли средства", callback_data=f"yuan_missing:{order_id}")],
        [InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
    ])


def manager_payment_received_kb(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Платёж поступил — уведомить клиента", callback_data=f"pay_confirm:{order_id}")],
        [InlineKeyboardButton(text="✉️ Написать клиенту", callback_data=f"mgr_msg:{order_id}")],
    ])


def skip_kb(callback_data: str = "mgr_skip") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data=callback_data)],
    ])
