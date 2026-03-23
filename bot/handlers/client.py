from aiogram import Router, F, types, Bot
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot import database as db
from bot import keyboards as kb

router = Router()

STATUS_TEXT = {
    "new": "Новая",
    "taken": "Принята",
    "in_progress": "В работе",
    "completed": "Завершена",
    "cancelled": "Отменена",
}

PAIR = "RUB/CNY"


class OrderFSM(StatesGroup):
    waiting_pay_method = State()
    waiting_amount = State()
    waiting_confirm = State()
    waiting_message = State()


class ProfileFSM(StatesGroup):
    waiting_wechat_qr = State()
    waiting_alipay_qr = State()
    waiting_card_number = State()
    waiting_card_bank = State()
    waiting_card_holder = State()
    waiting_card_phone = State()


# ---- /start ----

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(
        f"Добро пожаловать, {message.from_user.first_name}!\n"
        "Обмен рублей и юаней. Выберите действие:",
        reply_markup=kb.main_menu(),
    )


# ---- Меню ----

@router.callback_query(F.data == "back_menu")
async def back_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Выберите действие:", reply_markup=kb.main_menu())
    await callback.answer()


@router.callback_query(F.data == "help")
async def show_help(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "Напишите менеджеру — @bulievich",
        reply_markup=kb.main_menu(),
    )
    await callback.answer()


# ---- Обмен: выбор направления ----

@router.callback_query(F.data == "exchange")
async def select_direction(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    active = db.get_user_active_order(callback.from_user.id)
    if active:
        st = STATUS_TEXT.get(active["status"], active["status"])
        await callback.message.edit_text(
            f"У вас уже есть активная заявка #{active['id']} ({st}).\n"
            "Дождитесь её завершения или отмены.",
            reply_markup=kb.main_menu(),
        )
        await callback.answer()
        return
    rate_row = db.get_rate(PAIR)
    buy = rate_row["buy_rate"] if rate_row else "—"
    sell = rate_row["sell_rate"] if rate_row else "—"
    await callback.message.edit_text(
        f"Курсы RUB/CNY:\n"
        f"Покупка (RUB→CNY): {buy}\n"
        f"Продажа (CNY→RUB): {sell}\n\n"
        "Выберите направление обмена:",
        reply_markup=kb.direction_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dir:"))
async def direction_selected(callback: types.CallbackQuery, state: FSMContext):
    _, cur_from, cur_to = callback.data.split(":")
    rate_row = db.get_rate(PAIR)
    if not rate_row:
        await callback.answer("Курс не найден", show_alert=True)
        return

    if cur_from == "RUB":
        rate = rate_row["buy_rate"]
    else:
        rate = rate_row["sell_rate"]

    # RUB→CNY: нужен QR-код для получения юаней
    if cur_from == "RUB":
        profile = db.get_profile(callback.from_user.id)
        has_wechat = profile and profile["wechat_qr"]
        has_alipay = profile and profile["alipay_qr"]

        if not has_wechat and not has_alipay:
            await callback.message.edit_text(
                "Для покупки юаней нужен QR-код WeChat или Alipay.\n"
                "Добавьте его в профиле.",
                reply_markup=kb.profile_menu_kb(only="qr"),
            )
            await callback.answer()
            return

        await state.update_data(cur_from=cur_from, cur_to=cur_to, rate=rate)

        if has_wechat and has_alipay:
            # Оба — спрашиваем
            await state.set_state(OrderFSM.waiting_pay_method)
            await callback.message.edit_text(
                "Куда хотите получить юани?",
                reply_markup=kb.pay_method_kb(),
            )
            await callback.answer()
            return
        else:
            # Один — авто
            method = "wechat" if has_wechat else "alipay"
            await state.update_data(pay_method=method)
            await state.set_state(OrderFSM.waiting_amount)
            await callback.message.edit_text(f"Введите сумму в {cur_from}:")
            await callback.answer()
            return

    # CNY→RUB: нужна карта для получения рублей
    if cur_from == "CNY":
        profile = db.get_profile(callback.from_user.id)
        if not profile or not profile["card_number"]:
            await callback.message.edit_text(
                "Для продажи юаней нужны реквизиты карты.\n"
                "Добавьте их в профиле.",
                reply_markup=kb.profile_menu_kb(only="card"),
            )
            await callback.answer()
            return

    await state.set_state(OrderFSM.waiting_amount)
    await state.update_data(cur_from=cur_from, cur_to=cur_to, rate=rate, pay_method=None)
    await callback.message.edit_text(f"Введите сумму в {cur_from}:")
    await callback.answer()


@router.callback_query(F.data.startswith("pay:"), OrderFSM.waiting_pay_method)
async def pay_method_selected(callback: types.CallbackQuery, state: FSMContext):
    method = callback.data.split(":", 1)[1]  # wechat или alipay
    await state.update_data(pay_method=method)
    await state.set_state(OrderFSM.waiting_amount)
    data = await state.get_data()
    await callback.message.edit_text(f"Введите сумму в {data['cur_from']}:")
    await callback.answer()


# ---- Обмен: ввод суммы ----

@router.message(OrderFSM.waiting_amount)
async def enter_amount(message: types.Message, state: FSMContext):
    text = message.text.replace(",", ".").strip()
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await message.answer("Введите корректную сумму (число больше 0):")
        return

    data = await state.get_data()
    rate = data["rate"]

    # RUB→CNY: юани = рубли / курс
    # CNY→RUB: рубли = юани * курс
    if data["cur_from"] == "RUB":
        result = round(amount / rate, 2)
    else:
        result = round(amount * rate, 2)

    await state.update_data(amount=amount, amount_result=result)
    await state.set_state(OrderFSM.waiting_confirm)

    await message.answer(
        f"Вы отдаёте: {amount} {data['cur_from']}\n"
        f"Курс: {rate}\n"
        f"Вы получите: {result} {data['cur_to']}\n\n"
        "Подтвердить заявку?",
        reply_markup=kb.confirm_order_kb(),
    )


# ---- Обмен: подтверждение ----

@router.callback_query(F.data == "confirm_order", OrderFSM.waiting_confirm)
async def confirm_order(callback: types.CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = db.create_order(
        user_id=callback.from_user.id,
        currency_from=data["cur_from"],
        currency_to=data["cur_to"],
        amount=data["amount"],
        rate=data["rate"],
        amount_result=data["amount_result"],
        pay_method=data.get("pay_method"),
    )
    await state.clear()

    await callback.message.edit_text(
        f"Заявка #{order_id} создана!\n"
        f"{data['amount']} {data['cur_from']} → {data['amount_result']} {data['cur_to']}\n"
        "Ожидайте — менеджер скоро возьмёт заказ.",
        reply_markup=kb.main_menu(),
    )
    await callback.answer()

    # Уведомляем всех менеджеров лично
    managers = db.get_all_active_managers()
    for mgr in managers:
        try:
            await bot.send_message(
                mgr["tg_id"],
                f"🆕 Новая заявка #{order_id}\n"
                f"Клиент: {callback.from_user.first_name} (@{callback.from_user.username or '—'})\n"
                f"{data['amount']} {data['cur_from']} → {data['amount_result']} {data['cur_to']}\n"
                f"Курс: {data['rate']}",
                reply_markup=kb.manager_take_order_kb(order_id),
            )
        except Exception:
            pass


@router.callback_query(F.data == "cancel_order", OrderFSM.waiting_confirm)
async def cancel_order_creation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Заявка отменена.", reply_markup=kb.main_menu())
    await callback.answer()


# ---- Мои заявки ----

@router.callback_query(F.data == "my_orders")
async def my_orders(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    orders = db.get_user_orders(callback.from_user.id)
    if not orders:
        await callback.message.edit_text("У вас пока нет заявок.", reply_markup=kb.main_menu())
        await callback.answer()
        return

    lines = []
    for o in orders:
        st = STATUS_TEXT.get(o["status"], o["status"])
        lines.append(f"#{o['id']} | {o['amount']} {o['currency_from']}→{o['currency_to']} | {st}")

    buttons = [
        [types.InlineKeyboardButton(text=f"#{o['id']}", callback_data=f"order:{o['id']}")]
        for o in orders
    ]
    buttons.append([types.InlineKeyboardButton(text="Назад", callback_data="back_menu")])

    await callback.message.edit_text(
        "Ваши заявки:\n\n" + "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("order:"))
async def order_detail(callback: types.CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    st = STATUS_TEXT.get(order["status"], order["status"])
    await callback.message.edit_text(
        f"Заявка #{order['id']}\n"
        f"Сумма: {order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}\n"
        f"Курс: {order['rate']}\n"
        f"Статус: {st}\n"
        f"Создана: {order['created_at']}",
        reply_markup=kb.order_detail_kb(order_id),
    )
    await callback.answer()


# ---- Профиль ----

def _profile_kb(p):
    """Клавиатура профиля с кнопками удаления если данные есть."""
    return kb.profile_menu_kb(
        has_wechat=bool(p and p["wechat_qr"]),
        has_alipay=bool(p and p["alipay_qr"]),
        has_card=bool(p and p["card_number"]),
    )


@router.callback_query(F.data == "profile")
async def show_profile(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    p = db.get_profile(callback.from_user.id)
    lines = ["Ваш профиль:\n"]
    if p:
        lines.append(f"WeChat QR: {'загружен' if p['wechat_qr'] else 'не задан'}")
        lines.append(f"Alipay QR: {'загружен' if p['alipay_qr'] else 'не задан'}")
        if p["card_number"]:
            lines.append(f"\nРеквизиты карты:")
            lines.append(f"  Номер: {p['card_number']}")
            lines.append(f"  Банк: {p['card_bank'] or '—'}")
            lines.append(f"  ФИО: {p['card_holder'] or '—'}")
            lines.append(f"  Телефон: {p['card_phone'] or '—'}")
        else:
            lines.append("Реквизиты карты: не заданы")
    else:
        lines.append("Данные не заполнены")
    lines.append("\nНажмите кнопку чтобы изменить (X — удалить):")
    await callback.message.edit_text("\n".join(lines), reply_markup=_profile_kb(p))
    await callback.answer()


@router.callback_query(F.data.startswith("profile_del:"))
async def profile_delete(callback: types.CallbackQuery):
    field = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    if field == "wechat_qr":
        db.update_profile(uid, wechat_qr=None)
    elif field == "alipay_qr":
        db.update_profile(uid, alipay_qr=None)
    elif field == "card":
        db.update_profile(uid, card_number=None, card_bank=None, card_holder=None, card_phone=None)
    p = db.get_profile(uid)
    lines = ["Данные удалены.\n\nВаш профиль:\n"]
    if p:
        lines.append(f"WeChat QR: {'загружен' if p['wechat_qr'] else 'не задан'}")
        lines.append(f"Alipay QR: {'загружен' if p['alipay_qr'] else 'не задан'}")
        if p["card_number"]:
            lines.append(f"\nРеквизиты карты:")
            lines.append(f"  Номер: {p['card_number']}")
            lines.append(f"  Банк: {p['card_bank'] or '—'}")
            lines.append(f"  ФИО: {p['card_holder'] or '—'}")
            lines.append(f"  Телефон: {p['card_phone'] or '—'}")
        else:
            lines.append("Реквизиты карты: не заданы")
    lines.append("\nНажмите кнопку чтобы изменить (X — удалить):")
    await callback.message.edit_text("\n".join(lines), reply_markup=_profile_kb(p))
    await callback.answer("Удалено")


@router.callback_query(F.data == "profile:wechat_qr")
async def profile_wechat(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileFSM.waiting_wechat_qr)
    await callback.message.edit_text("Отправьте фото QR-кода WeChat Pay:")
    await callback.answer()


@router.message(ProfileFSM.waiting_wechat_qr, F.photo)
async def save_wechat_qr(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    db.update_profile(message.from_user.id, wechat_qr=file_id)
    await state.clear()
    p = db.get_profile(message.from_user.id)
    await message.answer("WeChat QR сохранён!", reply_markup=_profile_kb(p))


@router.message(ProfileFSM.waiting_wechat_qr)
async def save_wechat_qr_invalid(message: types.Message):
    await message.answer("Отправьте именно фото (не файл):")


@router.callback_query(F.data == "profile:alipay_qr")
async def profile_alipay(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileFSM.waiting_alipay_qr)
    await callback.message.edit_text("Отправьте фото QR-кода Alipay:")
    await callback.answer()


@router.message(ProfileFSM.waiting_alipay_qr, F.photo)
async def save_alipay_qr(message: types.Message, state: FSMContext):
    file_id = message.photo[-1].file_id
    db.update_profile(message.from_user.id, alipay_qr=file_id)
    await state.clear()
    p = db.get_profile(message.from_user.id)
    await message.answer("Alipay QR сохранён!", reply_markup=_profile_kb(p))


@router.message(ProfileFSM.waiting_alipay_qr)
async def save_alipay_qr_invalid(message: types.Message):
    await message.answer("Отправьте именно фото (не файл):")


@router.callback_query(F.data == "profile:card")
async def profile_card(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileFSM.waiting_card_number)
    await callback.message.edit_text("Введите номер карты или телефон (для СБП):")
    await callback.answer()


@router.message(ProfileFSM.waiting_card_number)
async def save_card_number(message: types.Message, state: FSMContext):
    db.update_profile(message.from_user.id, card_number=message.text.strip())
    await state.set_state(ProfileFSM.waiting_card_bank)
    await message.answer("Введите название банка:")


@router.message(ProfileFSM.waiting_card_bank)
async def save_card_bank(message: types.Message, state: FSMContext):
    db.update_profile(message.from_user.id, card_bank=message.text.strip())
    await state.set_state(ProfileFSM.waiting_card_holder)
    await message.answer("Введите ФИО получателя:")


@router.message(ProfileFSM.waiting_card_holder)
async def save_card_holder(message: types.Message, state: FSMContext):
    db.update_profile(message.from_user.id, card_holder=message.text.strip())
    await state.set_state(ProfileFSM.waiting_card_phone)
    await message.answer("Введите номер телефона получателя (или «-» чтобы пропустить):")


@router.message(ProfileFSM.waiting_card_phone)
async def save_card_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if phone == "-":
        phone = None
    db.update_profile(message.from_user.id, card_phone=phone)
    await state.clear()
    p = db.get_profile(message.from_user.id)
    await message.answer(
        f"Реквизиты сохранены!\n\n"
        f"Номер: {p['card_number']}\n"
        f"Банк: {p['card_bank']}\n"
        f"ФИО: {p['card_holder']}\n"
        f"Телефон: {p['card_phone'] or '—'}",
        reply_markup=_profile_kb(p),
    )


# ---- Relay: клиент → менеджер ----

@router.callback_query(F.data.startswith("msg:"))
async def start_client_message(callback: types.CallbackQuery, state: FSMContext):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if order["status"] in ("completed", "cancelled"):
        await callback.answer("Заявка закрыта", show_alert=True)
        return

    await state.set_state(OrderFSM.waiting_message)
    await state.update_data(relay_order_id=order_id)
    await callback.message.edit_text(
        f"Напишите сообщение менеджеру по заявке #{order_id}:\n"
        "(Отправьте текст или /cancel для отмены)"
    )
    await callback.answer()


@router.message(OrderFSM.waiting_message)
async def relay_client_to_manager(message: types.Message, state: FSMContext, bot: Bot):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=kb.main_menu())
        return

    data = await state.get_data()
    order_id = data.get("relay_order_id")
    if not order_id:
        await state.clear()
        return

    order = db.get_order(order_id)
    if not order:
        await state.clear()
        await message.answer("Заявка не найдена.", reply_markup=kb.main_menu())
        return

    db.save_message(order_id, message.from_user.id, message.text or "")
    await state.clear()
    await message.answer("Сообщение отправлено менеджеру.", reply_markup=kb.main_menu())

    manager_id = order["manager_id"]
    if manager_id:
        try:
            await bot.send_message(
                manager_id,
                f"Сообщение от клиента по заявке #{order_id}:\n\n{message.text}",
                reply_markup=kb.manager_status_kb(order_id),
            )
        except Exception:
            pass
    else:
        # Если менеджер не назначен — шлём всем менеджерам
        managers = db.get_all_active_managers()
        for mgr in managers:
            try:
                await bot.send_message(
                    mgr["tg_id"],
                    f"Сообщение от клиента по заявке #{order_id}:\n\n{message.text}",
                )
            except Exception:
                pass
