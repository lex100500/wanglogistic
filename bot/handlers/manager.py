import html

from aiogram import Router, F, types, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot import database as db
from bot import keyboards as kb

router = Router()

PAIR = "RUB/CNY"

STATUS_TEXT = {
    "new": "Новая",
    "taken": "Принята",
    "in_progress": "В работе",
    "completed": "Завершена",
    "cancelled": "Отменена",
}


class ManagerFSM(StatesGroup):
    waiting_message = State()  # relay — менеджер пишет клиенту
    waiting_buy_rate = State()
    waiting_sell_rate = State()
    waiting_req_number = State()
    waiting_req_bank = State()
    waiting_req_bank_custom = State()
    waiting_req_holder = State()
    waiting_req_email = State()
    waiting_req_offer_rate = State()
    waiting_req_confirm = State()
    waiting_htx_rate = State()
    waiting_qr_usdt_rate = State()  # CNY→RUB: курс USDT
    waiting_qr_photo = State()      # CNY→RUB: менеджер шлёт QR
    waiting_qr_confirm = State()    # CNY→RUB: подтверждение QR


# ---- /rate — менеджер меняет курс ----

@router.message(Command("change"))
async def cmd_rate(message: types.Message, state: FSMContext):
    if not db.is_manager(message.from_user.id):
        return
    rate = db.get_rate(PAIR)
    buy = rate["buy_rate"] if rate else "—"
    sell = rate["sell_rate"] if rate else "—"
    await state.set_state(ManagerFSM.waiting_buy_rate)
    await message.answer(
        f"Текущий курс {PAIR}:\n"
        f"Покупка (RUB→CNY): {buy}\n"
        f"Продажа (CNY→RUB): {sell}\n\n"
        "Введите новый курс покупки (RUB→CNY):"
    )


@router.message(ManagerFSM.waiting_buy_rate)
async def enter_buy_rate(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith("/"):
        await state.clear()
        await message.answer("Отменено.")
        return
    try:
        buy = float(message.text.replace(",", ".").strip())
        if buy <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите число больше 0:")
        return
    await state.update_data(new_buy=buy)
    await state.set_state(ManagerFSM.waiting_sell_rate)
    await message.answer("Введите новый курс продажи (CNY→RUB):")


@router.message(ManagerFSM.waiting_sell_rate)
async def enter_sell_rate(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith("/"):
        await state.clear()
        await message.answer("Отменено.")
        return
    try:
        sell = float(message.text.replace(",", ".").strip())
        if sell <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите число больше 0:")
        return
    data = await state.get_data()
    buy = data["new_buy"]
    db.update_rate(PAIR, buy, sell, changed_by=message.from_user.id, source="bot")
    await state.clear()
    await message.answer(
        f"Курс {PAIR} обновлён:\n"
        f"Покупка: {buy}\n"
        f"Продажа: {sell}"
    )


# ---- Взять заказ ----

@router.callback_query(F.data.startswith("take:"))
async def take_order(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if order["status"] != "new":
        await callback.answer("Заявка уже взята", show_alert=True)
        return

    active = db.get_manager_active_order(callback.from_user.id)
    if active:
        await callback.answer(
            f"У вас уже есть активная заявка #{active['id']}. Завершите её перед взятием новой.",
            show_alert=True
        )
        return

    # Автоматически добавляем менеджера
    db.add_manager(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    db.update_order_status(order_id, "taken", manager_id=callback.from_user.id)

    mgr_msg = await callback.message.edit_text(
        f"Заявка #{order_id} взята менеджером {callback.from_user.first_name}\n"
        f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}\n\n"
        f"⏳ Ожидаем подтверждения условий от клиента...",
        reply_markup=kb.manager_status_kb(order_id, show_qr=False, show_req=False),
    )
    db.save_manager_message(order_id, mgr_msg.message_id, mgr_msg.chat.id)
    await callback.answer("Вы взяли заказ")

    # Уведомление клиенту
    try:
        await bot.send_message(
            order["user_id"],
            "⚠️ ВАЖНО: Ответственность за корректность платежа полностью лежит на вас. "
            "Перед переводом внимательно проверяйте реквизиты, банк и сумму. "
            "Возврат денежных средств НЕВОЗМОЖЕН!!!\n\n"
            "Для подтверждения согласия с условиями напишите «+»!",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"client_cancel:{order_id}")],
            ]),
        )
    except Exception:
        pass


# ---- QR-код клиента ----

@router.callback_query(F.data.startswith("get_qr:"))
async def get_client_qr(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    pay_method = (order["pay_method"] if "pay_method" in order.keys() else None)
    if not pay_method:
        await callback.answer("У этой заявки нет QR-кода", show_alert=True)
        return

    profile = db.get_profile(order["user_id"])
    if not profile:
        await callback.answer("Профиль клиента не найден", show_alert=True)
        return

    qr_file_id = profile["wechat_qr"] if pay_method == "wechat" else profile["alipay_qr"]
    method_name = "WeChat Pay" if pay_method == "wechat" else "Alipay"

    if not qr_file_id:
        await callback.answer(f"QR-код {method_name} не загружен клиентом", show_alert=True)
        return

    await bot.send_photo(
        callback.from_user.id,
        photo=qr_file_id,
        caption=f"QR-код клиента ({method_name}) для заявки #{order_id}",
    )
    await callback.answer()


# ---- Смена статуса ----

@router.callback_query(F.data.startswith("status:"))
async def change_status(callback: types.CallbackQuery, bot: Bot):
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Ошибка", show_alert=True)
        return
    new_status = parts[1]
    order_id = parts[2]

    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Проверяем, что менеджер — владелец заказа
    if order["manager_id"] and order["manager_id"] != callback.from_user.id:
        await callback.answer("Это не ваш заказ", show_alert=True)
        return

    db.update_order_status(order_id, new_status)

    st = STATUS_TEXT.get(new_status, new_status)
    has_qr = order["currency_from"] == "RUB" and (order["pay_method"] if "pay_method" in order.keys() else None)
    if new_status in ("completed", "cancelled"):
        await callback.message.edit_text(
            f"Заявка #{order_id} — {st}\n"
            f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}"
        )
    else:
        await callback.message.edit_text(
            f"Заявка #{order_id} — {st}\n"
            f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}",
            reply_markup=kb.manager_status_kb(order_id, show_qr=bool(has_qr)),
        )
    await callback.answer(f"Статус: {st}")

    # Уведомление клиенту
    try:
        await bot.send_message(
            order["user_id"],
            f"Статус вашей заявки изменён: {st}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
            ]),
        )
    except Exception:
        pass


# ---- Relay: менеджер → клиент ----

@router.callback_query(F.data.startswith("mgr_msg:"))
async def start_manager_message(callback: types.CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await state.set_state(ManagerFSM.waiting_message)
    await state.update_data(relay_order_id=order_id, relay_user_id=order["user_id"])
    await callback.message.answer(
        f"Напишите сообщение клиенту по заявке #{order_id}:\n"
        "(Отправьте текст или /cancel для отмены)"
    )
    await callback.answer()


@router.message(ManagerFSM.waiting_message)
async def relay_manager_to_client(message: types.Message, state: FSMContext, bot: Bot):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.")
        return

    data = await state.get_data()
    order_id = data.get("relay_order_id")
    user_id = data.get("relay_user_id")
    if not order_id or not user_id:
        await state.clear()
        return

    photo_file_id = message.photo[-1].file_id if message.photo else None
    db.save_message(order_id, message.from_user.id, message.text or message.caption or "[фото]", file_id=photo_file_id)
    await state.clear()
    await message.answer("Сообщение отправлено клиенту.")

    reply_kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
    ])
    try:
        if message.photo:
            await bot.send_photo(
                user_id,
                photo=message.photo[-1].file_id,
                caption="Сообщение от менеджера:\n\n" + (message.caption or ""),
                reply_markup=reply_kb,
            )
        else:
            await bot.send_message(
                user_id,
                f"Сообщение от менеджера:\n\n{message.text}",
                reply_markup=reply_kb,
            )
    except Exception:
        pass


# ---- Отправка реквизитов клиенту ----

@router.callback_query(F.data.startswith("send_req:"))
async def send_req_start(callback: types.CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if order["manager_id"] and order["manager_id"] != callback.from_user.id:
        await callback.answer("Это не ваш заказ", show_alert=True)
        return

    # CNY→RUB: сначала курс USDT, потом QR
    if order["currency_from"] == "CNY":
        await state.set_state(ManagerFSM.waiting_qr_usdt_rate)
        await state.update_data(req_order_id=order_id, req_user_id=order["user_id"])
        await callback.message.answer("Введите курс покупки USDT (для отчётности):")
        await callback.answer()
        return

    await state.set_state(ManagerFSM.waiting_req_number)
    await state.update_data(req_order_id=order_id, req_user_id=order["user_id"])
    await callback.message.answer(
        f"Заявка #{order_id}. Введите номер карты или телефон для перевода:"
    )
    await callback.answer()


# ---- QR-флоу для CNY→RUB ----

@router.message(ManagerFSM.waiting_qr_usdt_rate)
async def qr_usdt_rate(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.")
        return
    try:
        rate = float(message.text.replace(",", ".").strip())
        if rate <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите число больше 0:")
        return
    data = await state.get_data()
    await state.update_data(qr_offer_rate=rate)
    await state.set_state(ManagerFSM.waiting_qr_photo)
    await message.answer(
        f"📷 Пришлите QR-код для оплаты по заявке #{data['req_order_id']}:\n(отправьте фото)"
    )


@router.message(ManagerFSM.waiting_qr_photo)
async def receive_qr_photo(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.")
        return
    if not message.photo:
        await message.answer("📷 Пришлите фото QR-кода:")
        return

    file_id = message.photo[-1].file_id
    await state.update_data(qr_file_id=file_id)
    await state.set_state(ManagerFSM.waiting_qr_confirm)

    data = await state.get_data()
    order_id = data["req_order_id"]
    await message.answer_photo(
        photo=file_id,
        caption=f"📋 Вы указали этот QR-код для заявки #{order_id}.\n\nПодтвердить и отправить клиенту?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Подтвердить и отправить", callback_data="qr_confirm_send")],
            [types.InlineKeyboardButton(text="✏️ Изменить", callback_data="qr_confirm_edit")],
        ]),
    )


@router.callback_query(F.data == "qr_confirm_send", ManagerFSM.waiting_qr_confirm)
async def qr_confirm_send(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data["req_order_id"]
    user_id = data["req_user_id"]
    file_id = data["qr_file_id"]

    offer_rate = data.get("qr_offer_rate")
    order = db.get_order(order_id)
    db.update_order_status(order_id, "in_progress")
    if offer_rate:
        db.update_order_offer_rate(order_id, offer_rate)
    await state.clear()

    amount_line = f"{order['amount']} {order['currency_from']}" if order else "—"
    caption = (
        f"📷 QR-код для оплаты по вашей заявке\n\n"
        f"Сумма к переводу: <b>{html.escape(str(amount_line))}</b>\n\n"
        f"⏰ Оплатите в течение 10 минут\n"
        f"‼️ Убедитесь, что сканируете правильный QR-код!"
    )

    db.save_message(order_id, callback.from_user.id, "[QR-код для оплаты]", file_id=file_id)

    try:
        await bot.send_photo(
            user_id,
            photo=file_id,
            caption=caption,
            parse_mode="HTML",
            reply_markup=kb.confirm_payment_kb(order_id),
        )
    except Exception:
        pass

    await callback.message.edit_caption(
        caption=callback.message.caption + "\n\n✅ QR-код отправлен клиенту.",
        reply_markup=None,
    )
    await callback.answer("Отправлено")
    await callback.message.answer(f"✅ QR-код отправлен клиенту по заявке #{order_id}.")


@router.callback_query(F.data == "qr_confirm_edit", ManagerFSM.waiting_qr_confirm)
async def qr_confirm_edit(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ManagerFSM.waiting_qr_photo)
    await callback.message.edit_caption(caption="✏️ Пришлите новый QR-код фото:", reply_markup=None)
    await callback.answer()


@router.message(ManagerFSM.waiting_req_number)
async def req_number(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.")
        return
    await state.update_data(req_number=message.text.strip())
    await state.set_state(ManagerFSM.waiting_req_bank)
    await message.answer("Выберите банк:", reply_markup=kb.manager_bank_kb())


@router.callback_query(F.data.startswith("mgr_bank:"), ManagerFSM.waiting_req_bank)
async def req_bank(callback: types.CallbackQuery, state: FSMContext):
    bank = callback.data.split(":", 1)[1]
    if bank == "other":
        await state.set_state(ManagerFSM.waiting_req_bank_custom)
        await callback.message.edit_text("Введите название банка:")
        await callback.answer()
        return
    await state.update_data(req_bank=bank)
    await state.set_state(ManagerFSM.waiting_req_holder)
    await callback.message.edit_text(
        "Введите ФИО получателя (для отчётности, или нажмите «Пропустить»):",
        reply_markup=kb.skip_kb("mgr_skip_holder"),
    )
    await callback.answer()


@router.message(ManagerFSM.waiting_req_bank_custom)
async def req_bank_custom(message: types.Message, state: FSMContext):
    await state.update_data(req_bank=message.text.strip())
    await state.set_state(ManagerFSM.waiting_req_holder)
    await message.answer(
        "Введите ФИО получателя (для отчётности, или нажмите «Пропустить»):",
        reply_markup=kb.skip_kb("mgr_skip_holder"),
    )


async def _ask_email(msg):
    await msg.answer(
        "Введите почту для отправки чека об оплате:",
        reply_markup=kb.skip_kb("mgr_skip_email"),
    )


async def _show_req_preview(state: FSMContext, holder, email, offer_rate, msg):
    """Показывает превью реквизитов менеджеру для подтверждения."""
    await state.update_data(req_holder=holder, req_email=email, req_offer_rate=offer_rate)
    await state.set_state(ManagerFSM.waiting_req_confirm)

    data = await state.get_data()
    req_number = data["req_number"]
    req_bank = data["req_bank"]

    lines = [
        "📋 Проверьте данные перед отправкой клиенту:\n",
        f"Номер карты / телефон: <code>{html.escape(str(req_number))}</code>",
        f"Банк: <code>{html.escape(str(req_bank))}</code>",
    ]
    if holder:
        lines.append(f"ФИО: <code>{html.escape(str(holder))}</code>")
    if email:
        lines.append(f"Почта: <code>{html.escape(str(email))}</code>")
    if offer_rate:
        lines.append(f"Курс USDT: <code>{html.escape(str(offer_rate))}</code>")

    await msg.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Подтвердить и отправить", callback_data="req_confirm_send")],
            [types.InlineKeyboardButton(text="✏️ Изменить", callback_data="req_confirm_edit")],
        ]),
    )


async def _send_requisites(bot: Bot, state: FSMContext, holder, email, offer_rate, msg):
    data = await state.get_data()
    order_id = data["req_order_id"]
    user_id = data["req_user_id"]
    req_number = data["req_number"]
    req_bank = data["req_bank"]

    order = db.get_order(order_id)
    amount_line = f"{order['amount']} {order['currency_from']}" if order else "—"

    db.update_order_status(order_id, "in_progress")
    if offer_rate:
        db.update_order_offer_rate(order_id, offer_rate)
    await state.clear()

    text = (
        f"⚠️ ВАЖНО:\n"
        f"Ответственность за корректность платежа полностью лежит на вас. "
        f"Перед переводом внимательно проверяйте реквизиты, банк и сумму. "
        f"Возврат денежных средств НЕВОЗМОЖЕН.\n\n"
        f"💳 Реквизиты для оплаты\n\n"
        f"Сумма к переводу: <code>{html.escape(str(amount_line))}</code>\n"
        f"Номер карты / телефон: <code>{html.escape(str(req_number))}</code>\n"
        f"Банк: <code>{html.escape(str(req_bank))}</code>\n"
    )
    if holder:
        text += f"Получатель: <code>{html.escape(str(holder))}</code>\n"
    if email:
        text += f"\n📧 Адрес почты: <code>{html.escape(str(email))}</code>\nОтправьте на неё чек от имени банка.\n"
    text += (
        f"\n⏰ Оплатите в течение 10 минут\n\n"
        f"‼️ УБЕДИТЕСЬ, ЧТО ПЕРЕВОДИТЕ НА ПРАВИЛЬНЫЕ РЕКВИЗИТЫ И ВЕРНЫЙ БАНК!!!"
    )

    db.save_message(order_id, msg.from_user.id, text)

    try:
        await bot.send_message(user_id, text, parse_mode="HTML", reply_markup=kb.confirm_payment_kb(order_id))
    except Exception:
        pass

    await msg.answer(f"✅ Реквизиты отправлены клиенту по заявке #{order_id}.")


@router.callback_query(F.data == "mgr_skip_holder", ManagerFSM.waiting_req_holder)
async def req_holder_skip(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(req_holder=None)
    await state.set_state(ManagerFSM.waiting_req_email)
    await callback.message.edit_text("ФИО пропущено.")
    await _ask_email(callback.message)
    await callback.answer()



@router.message(ManagerFSM.waiting_req_holder)
async def req_holder(message: types.Message, state: FSMContext):
    holder = message.text.strip() if message.text else None
    await state.update_data(req_holder=holder)
    await state.set_state(ManagerFSM.waiting_req_email)
    await _ask_email(message)


@router.message(ManagerFSM.waiting_req_email)
async def req_email(message: types.Message, state: FSMContext):
    if not message.text or not message.text.strip():
        await message.answer(
            "Введите почту для отправки чека об оплате:",
            reply_markup=kb.skip_kb("mgr_skip_email"),
        )
        return
    await state.update_data(req_email=message.text.strip())
    await state.set_state(ManagerFSM.waiting_req_offer_rate)
    await message.answer(
        "Введите курс покупки USDT (для отчётности):",
    )


@router.callback_query(F.data == "mgr_skip_email", ManagerFSM.waiting_req_email)
async def req_email_skip_to_offer(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(req_email=None)
    await state.set_state(ManagerFSM.waiting_req_offer_rate)
    await callback.message.edit_text("Почта пропущена.")
    await callback.message.answer(
        "Введите курс покупки USDT (для отчётности):",
    )
    await callback.answer()


@router.callback_query(F.data == "mgr_skip_offer_rate", ManagerFSM.waiting_req_offer_rate)
async def req_offer_rate_skip(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await callback.message.edit_text("Курс USDT пропущен.")
    await callback.answer()
    await _show_req_preview(state, data.get("req_holder"), data.get("req_email"), None, callback.message)


@router.message(ManagerFSM.waiting_req_offer_rate)
async def req_offer_rate(message: types.Message, state: FSMContext, bot: Bot):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.")
        return
    try:
        offer_rate = float(message.text.replace(",", ".").strip())
        if offer_rate <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer(
            "Введите число больше 0:",
        )
        return
    data = await state.get_data()
    await _show_req_preview(state, data.get("req_holder"), data.get("req_email"), offer_rate, message)


# ---- Подтверждение реквизитов менеджером ----

@router.callback_query(F.data == "req_confirm_send", ManagerFSM.waiting_req_confirm)
async def req_confirm_send(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer()
    await _send_requisites(
        bot, state,
        data.get("req_holder"), data.get("req_email"), data.get("req_offer_rate"),
        callback.message,
    )


@router.callback_query(F.data == "req_confirm_edit", ManagerFSM.waiting_req_confirm)
async def req_confirm_edit(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("req_order_id")
    user_id = data.get("req_user_id")
    await state.clear()
    await state.set_state(ManagerFSM.waiting_req_number)
    await state.update_data(req_order_id=order_id, req_user_id=user_id)
    await callback.message.edit_text("Введите номер карты или телефон для перевода заново:")
    await callback.answer()


# ---- Подтверждение получения платежа ----

@router.callback_query(F.data.startswith("pay_confirm:"))
async def manager_confirm_payment(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    try:
        await bot.send_message(
            order["user_id"],
            f"✅ Платёж успешно поступил!\n"
            f"Ожидайте поступление средств по вашим реквизитам.\n"
            f"Ожидание около 10 минут.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
            ]),
        )
    except Exception:
        pass

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Клиент уведомлён о получении платежа.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✉️ Написать клиенту", callback_data=f"mgr_msg:{order_id}")],
        ]),
    )
    await callback.answer("Клиент уведомлён")

    await state.set_state(ManagerFSM.waiting_htx_rate)
    await state.update_data(htx_order_id=order_id)
    await callback.message.answer(
        f"Введите курс покупки RUB на HTX (для отчётности):",
    )


def _client_card_text(order) -> str:
    """Возвращает строку с реквизитами клиента для перевода RUB."""
    if not order:
        return ""
    profile = db.get_profile(order["user_id"])
    if not profile:
        return "\n\n⚠️ Профиль клиента не найден — уточните реквизиты вручную."
    lines = []
    if profile["card_number"]:
        lines.append(f"Номер карты/телефон: <code>{html.escape(str(profile['card_number']))}</code>")
    if profile["card_bank"]:
        lines.append(f"Банк: <code>{html.escape(str(profile['card_bank']))}</code>")
    if profile["card_holder"]:
        lines.append(f"ФИО: <code>{html.escape(str(profile['card_holder']))}</code>")
    if profile["card_phone"]:
        lines.append(f"Телефон: <code>{html.escape(str(profile['card_phone']))}</code>")
    if not lines:
        return "\n\n⚠️ Клиент не заполнил реквизиты — уточните вручную."
    return "\n\n💳 Реквизиты клиента для перевода RUB:\n" + "\n".join(lines)


@router.callback_query(F.data == "mgr_skip_htx", ManagerFSM.waiting_htx_rate)
async def skip_htx_rate(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("htx_order_id")
    await state.clear()
    await callback.message.edit_text("Курс HTX пропущен.")
    await callback.answer()
    if order_id:
        order = db.get_order(order_id)
        show_qr = bool(order and order["currency_from"] == "RUB" and
                       ("pay_method" in order.keys()) and order["pay_method"])
        card_text = _client_card_text(order) if order and order["currency_from"] == "CNY" else ""
        await callback.message.answer(
            "Когда отправите рубли клиенту — нажмите кнопку ниже." + card_text if order and order["currency_from"] == "CNY"
            else "Когда отправите юани клиенту — нажмите кнопку ниже.",
            parse_mode="HTML",
            reply_markup=kb.manager_yuan_sent_kb(order_id, show_qr=show_qr, is_rub=bool(order and order["currency_from"] == "CNY")),
        )


@router.message(ManagerFSM.waiting_htx_rate)
async def enter_htx_rate(message: types.Message, state: FSMContext):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.")
        return
    try:
        htx_rate = float(message.text.replace(",", ".").strip())
        if htx_rate <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите число больше 0:")
        return

    data = await state.get_data()
    order_id = data["htx_order_id"]
    db.update_order_htx_rate(order_id, htx_rate)
    await state.clear()

    order = db.get_order(order_id)
    show_qr = bool(order and order["currency_from"] == "RUB" and
                   ("pay_method" in order.keys()) and order["pay_method"])

    if order and order["offer_rate"]:
        if order["currency_from"] == "RUB":
            # RUB→CNY: покупаем USDT за RUB, продаём USDT за CNY на HTX
            usdt_amount = order["amount"] / order["offer_rate"]
            cny_bought  = usdt_amount * htx_rate
            margin_cny  = round(cny_bought - order["amount_result"], 4)
            margin_rub  = round(margin_cny * order["rate"], 2)
            db.update_order_margin(order_id, round(usdt_amount, 6), round(cny_bought, 4), margin_cny, margin_rub)
        elif order["currency_from"] == "CNY":
            # CNY→RUB: продаём CNY за USDT на HTX, продаём USDT за RUB
            usdt_amount  = order["amount"] / order["offer_rate"]
            rub_received = usdt_amount * htx_rate
            margin_rub   = round(rub_received - order["amount_result"], 2)
            margin_cny   = round(margin_rub / order["rate"], 4) if order["rate"] else 0
            db.update_order_margin(order_id, round(usdt_amount, 6), round(rub_received, 2), margin_cny, margin_rub)

    is_cny_to_rub = order and order["currency_from"] == "CNY"
    card_text = _client_card_text(order) if is_cny_to_rub else ""
    action_text = "Когда отправите рубли клиенту — нажмите кнопку ниже." if is_cny_to_rub else "Когда отправите юани клиенту — нажмите кнопку ниже."
    await message.answer(
        f"Курс USDT: {order['offer_rate'] if order and order['offer_rate'] else '—'}\n"
        f"Курс RUB: {htx_rate}\n\n{action_text}{card_text}",
        parse_mode="HTML",
        reply_markup=kb.manager_yuan_sent_kb(order_id, show_qr=show_qr, is_rub=is_cny_to_rub),
    )


@router.callback_query(F.data.startswith("yuan_sent:"))
async def yuan_sent(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    try:
        await bot.send_message(
            order["user_id"],
            f"💸 {'Рубли' if order['currency_to'] == 'RUB' else 'Юани'} отправлены!\n"
            f"Сумма: {order['amount_result']} {order['currency_to']}\n\n"
            f"Проверьте поступление средств и подтвердите получение.",
            reply_markup=kb.client_yuan_delivery_kb(order_id),
        )
    except Exception:
        pass

    await callback.message.edit_text(
        callback.message.text + f"\n\n✅ Клиент уведомлён об отправке {'рублей' if order and order['currency_to'] == 'RUB' else 'юаней'}.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✉️ Написать клиенту", callback_data=f"mgr_msg:{order_id}")],
        ]),
    )
    await callback.answer("Клиент уведомлён")
