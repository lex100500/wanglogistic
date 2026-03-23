from aiogram import Router, F, types, Bot
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


class ManagerFSM(StatesGroup):
    waiting_message = State()  # relay — менеджер пишет клиенту


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

    await callback.message.edit_text(
        f"Заявка #{order_id} взята менеджером {callback.from_user.first_name}\n"
        f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}",
        reply_markup=kb.manager_status_kb(order_id),
    )
    await callback.answer("Вы взяли заказ")

    # Уведомление клиенту
    try:
        await bot.send_message(
            order["user_id"],
            f"Ваша заявка #{order_id} принята менеджером. Ожидайте.",
        )
    except Exception:
        pass


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
    if new_status in ("completed", "cancelled"):
        await callback.message.edit_text(
            f"Заявка #{order_id} — {st}\n"
            f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}"
        )
    else:
        await callback.message.edit_text(
            f"Заявка #{order_id} — {st}\n"
            f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}",
            reply_markup=kb.manager_status_kb(order_id),
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
