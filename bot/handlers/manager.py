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
    waiting_htx_rate = State()


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

    # Автоматически добавляем менеджера
    db.add_manager(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
    db.update_order_status(order_id, "taken", manager_id=callback.from_user.id)

    has_qr = order["currency_from"] == "RUB" and (order["pay_method"] if "pay_method" in order.keys() else None)
    await callback.message.edit_text(
        f"Заявка #{order_id} взята менеджером {callback.from_user.first_name}\n"
        f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}",
        reply_markup=kb.manager_status_kb(order_id, show_qr=bool(has_qr)),
    )
    await callback.answer("Вы взяли заказ")

    # Уведомление клиенту
    try:
        await bot.send_message(
            order["user_id"],
            f"✅ Ваша заявка #{order_id} принята менеджером.\n"
            f"Оставайтесь на связи — реквизиты для оплаты придут в течение 10 минут.",
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
            f"Статус заявки #{order_id} изменён: {st}",
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

    db.save_message(order_id, message.from_user.id, message.text or "")
    await state.clear()
    await message.answer("Сообщение отправлено клиенту.")

    try:
        await bot.send_message(
            user_id,
            f"Сообщение от менеджера по заявке #{order_id}:\n\n{message.text}",
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

    await state.set_state(ManagerFSM.waiting_req_number)
    await state.update_data(req_order_id=order_id, req_user_id=order["user_id"])
    await callback.message.answer(
        f"Заявка #{order_id}. Введите номер карты или телефон для перевода:"
    )
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
        "Введите ФИО получателя (или нажмите «Пропустить»):",
        reply_markup=kb.skip_kb("mgr_skip_holder"),
    )
    await callback.answer()


@router.message(ManagerFSM.waiting_req_bank_custom)
async def req_bank_custom(message: types.Message, state: FSMContext):
    await state.update_data(req_bank=message.text.strip())
    await state.set_state(ManagerFSM.waiting_req_holder)
    await message.answer(
        "Введите ФИО получателя (или нажмите «Пропустить»):",
        reply_markup=kb.skip_kb("mgr_skip_holder"),
    )


async def _ask_email(msg):
    await msg.answer("Введите почту для отправки чека об оплате:")


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
        f"💳 Реквизиты для оплаты — заявка #{order_id}\n\n"
        f"Сумма к переводу: {amount_line}\n"
        f"Номер карты / телефон: {req_number}\n"
        f"Банк: {req_bank}\n"
    )
    if holder:
        text += f"Получатель: {holder}\n"
    if email:
        text += f"\n📧 Адрес почты: {email}\nОтправьте на неё чек от имени банка.\n"
    text += (
        f"\n⏰ Оплатите в течение 10 минут\n\n"
        f"‼️ УБЕДИТЕСЬ, ЧТО ПЕРЕВОДИТЕ НА ПРАВИЛЬНЫЕ РЕКВИЗИТЫ И ВЕРНЫЙ БАНК!!!"
    )

    db.save_message(order_id, msg.from_user.id, text)

    try:
        await bot.send_message(user_id, text, reply_markup=kb.confirm_payment_kb(order_id))
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
        await message.answer("Введите почту для отправки чека об оплате:")
        return
    await state.update_data(req_email=message.text.strip())
    await state.set_state(ManagerFSM.waiting_req_offer_rate)
    await message.answer("Введите курс оффера с биржи (для отчётности, клиенту не отправляется):")


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
        await message.answer("Введите число больше 0:")
        return
    data = await state.get_data()
    await _send_requisites(bot, state, data.get("req_holder"), data.get("req_email"), offer_rate, message)


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
            f"✅ Платёж по заявке #{order_id} успешно поступил!\n"
            f"Ожидайте поступление средств по вашим реквизитам.\n"
            f"Ожидание около 10 минут.",
        )
    except Exception:
        pass

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Клиент уведомлён о получении платежа.",
        reply_markup=None,
    )
    await callback.answer("Клиент уведомлён")

    await state.set_state(ManagerFSM.waiting_htx_rate)
    await state.update_data(htx_order_id=order_id)
    await callback.message.answer(
        f"Введите курс покупки CNY на HTX (для отчётности):"
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

    margin_text = ""
    if order and order["currency_from"] == "RUB" and order["offer_rate"]:
        usdt_amount = order["amount"] / order["offer_rate"]
        cny_bought  = usdt_amount * htx_rate
        margin_cny  = round(cny_bought - order["amount_result"], 4)
        margin_rub  = round(margin_cny * order["rate"], 2)
        db.update_order_margin(order_id, round(usdt_amount, 6), round(cny_bought, 4), margin_cny, margin_rub)
        margin_text = (
            f"\n\n📊 Расчёт маржи:\n"
            f"  {order['amount']} RUB ÷ {order['offer_rate']} = {round(usdt_amount, 2)} USDT\n"
            f"  {round(usdt_amount, 2)} USDT × {htx_rate} = {round(cny_bought, 2)} CNY куплено\n"
            f"  Клиент получил: {order['amount_result']} CNY\n"
            f"  💰 Маржа: {margin_rub} RUB"
        )

    await message.answer(
        f"Курс HTX сохранён: {htx_rate}{margin_text}\n\nКогда отправите юани клиенту — нажмите кнопку ниже.",
        reply_markup=kb.manager_yuan_sent_kb(order_id, show_qr=show_qr),
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
            f"💸 Юани по заявке #{order_id} отправлены!\n"
            f"Сумма: {order['amount_result']} {order['currency_to']}\n\n"
            f"Проверьте поступление средств и подтвердите получение.",
            reply_markup=kb.client_yuan_delivery_kb(order_id),
        )
    except Exception:
        pass

    await callback.message.edit_text(
        callback.message.text + "\n\n✅ Клиент уведомлён об отправке юаней.",
        reply_markup=None,
    )
    await callback.answer("Клиент уведомлён")
