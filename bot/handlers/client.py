from aiogram import Router, F, types, Bot
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot import database as db
from bot import keyboards as kb
from bot.config import MANAGER_GROUP_CHAT_ID

router = Router()

STATUS_TEXT = {
    "new": "Новая",
    "taken": "Принята",
    "in_progress": "В работе",
    "completed": "Завершена",
    "cancelled": "Отменена",
}


class OrderFSM(StatesGroup):
    waiting_amount = State()
    waiting_confirm = State()
    waiting_message = State()  # relay — клиент пишет менеджеру


# ---- /start ----

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(
        f"Добро пожаловать, {message.from_user.first_name}!\n"
        "Я помогу вам обменять валюту. Выберите действие:",
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
        "Как пользоваться:\n"
        "1. Нажмите «Обменять»\n"
        "2. Выберите валютную пару\n"
        "3. Введите сумму\n"
        "4. Подтвердите заявку\n"
        "5. Менеджер свяжется с вами через бота\n\n"
        "По вопросам: напишите в поддержку.",
        reply_markup=kb.main_menu(),
    )
    await callback.answer()


# ---- Обмен: выбор пары ----

@router.callback_query(F.data == "exchange")
async def select_pair(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Выберите валютную пару:", reply_markup=kb.currency_pairs_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("pair:"))
async def pair_selected(callback: types.CallbackQuery, state: FSMContext):
    pair = callback.data.split(":", 1)[1]
    rate_row = db.get_rate(pair)
    if not rate_row:
        await callback.answer("Курс не найден", show_alert=True)
        return
    cur_from, cur_to = pair.split("/")
    await state.set_state(OrderFSM.waiting_amount)
    await state.update_data(pair=pair, cur_from=cur_from, cur_to=cur_to, rate=rate_row["buy_rate"])
    await callback.message.edit_text(
        f"Пара: {pair}\n"
        f"Курс: {rate_row['buy_rate']}\n\n"
        f"Введите сумму в {cur_from}:"
    )
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
    )
    await state.clear()

    await callback.message.edit_text(
        f"Заявка #{order_id} создана!\n"
        f"{data['amount']} {data['cur_from']} → {data['amount_result']} {data['cur_to']}\n"
        "Ожидайте — менеджер скоро возьмёт заказ.",
        reply_markup=kb.main_menu(),
    )
    await callback.answer()

    # Уведомление менеджерам
    if MANAGER_GROUP_CHAT_ID:
        try:
            await bot.send_message(
                MANAGER_GROUP_CHAT_ID,
                f"Новая заявка #{order_id}\n"
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

    # Пересылка менеджеру
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
    elif MANAGER_GROUP_CHAT_ID:
        try:
            await bot.send_message(
                MANAGER_GROUP_CHAT_ID,
                f"Сообщение от клиента по заявке #{order_id}:\n\n{message.text}",
            )
        except Exception:
            pass
