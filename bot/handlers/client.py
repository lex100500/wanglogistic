import json
import re

import os

from aiogram import Router, F, types, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import FSInputFile, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot import database as db
from bot import keyboards as kb

router = Router()

DEFAULT_RULES_URL = "https://telegra.ph/Pravila-ispolzovaniya-servisa-WangLogistic-03-23"


def _main_menu():
    return kb.main_menu(db.get_setting("rules_url", DEFAULT_RULES_URL))


STATUS_TEXT = {
    "new": "Новая",
    "taken": "Принята",
    "in_progress": "В работе",
    "completed": "Завершена",
    "cancelled": "Отменена",
}

PAIR = "RUB/CNY"
BANNER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "bannerbot.png")


async def safe_edit(callback: types.CallbackQuery, text: str, **kwargs):
    """edit_text для текстовых сообщений, delete+answer для фото."""
    if callback.message.photo:
        await callback.message.delete()
        await callback.message.answer(text, **kwargs)
    else:
        await callback.message.edit_text(text, **kwargs)


class OrderFSM(StatesGroup):
    waiting_receipt_confirm = State()
    waiting_pay_method = State()
    waiting_bank = State()
    waiting_bank_custom = State()
    waiting_amount = State()
    waiting_confirm = State()
    waiting_message = State()
    waiting_order_search = State()


class ProfileFSM(StatesGroup):
    waiting_wechat_qr = State()
    waiting_alipay_qr = State()
    waiting_card_number = State()
    waiting_card_bank = State()
    waiting_card_holder = State()
    waiting_card_phone = State()


# ---- /start ----

_REPLY_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🏠 Главное меню")]],
    resize_keyboard=True,
    persistent=True,
)


async def _send_start(message: types.Message, state: FSMContext):
    await state.clear()
    db.upsert_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer_photo(
        FSInputFile(BANNER_PATH),
        caption=f"Добро пожаловать, {message.from_user.first_name}!\n"
                "Обмен рублей и юаней. Выберите действие:",
        reply_markup=_main_menu(),
    )


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer(".", reply_markup=_REPLY_KB)
    await message.delete()
    await _send_start(message, state)


@router.message(F.text == "🏠 Главное меню")
async def reply_home(message: types.Message, state: FSMContext):
    await _send_start(message, state)


# ---- Акции ----

def _resolve_promotions_text(text: str) -> str:
    rate_row = db.get_rate("RUB/CNY")
    base_rate = rate_row["buy_rate"] if rate_row else 0.0

    try:
        tiers = json.loads(db.get_setting("volume_discounts", "[]"))
        tiers_map = {str(int(t["min_cny"])): t["discount"] for t in tiers}
    except Exception:
        tiers_map = {}

    try:
        bank_list = json.loads(db.get_setting("bank_discounts", "[]"))
        banks_map = {d["bank"]: d["discount"] for d in bank_list}
    except Exception:
        banks_map = {}

    def replace_tier(m):
        key = str(int(float(m.group(1))))
        discount = tiers_map.get(key)
        if discount is None:
            return m.group(0)
        return str(round(base_rate - discount, 2))

    def replace_bank(m):
        bank_name = m.group(1)
        discount = banks_map.get(bank_name)
        if discount is None:
            return m.group(0)
        return str(round(discount, 2))

    text = re.sub(r'\{тир:(\d+(?:\.\d+)?)\}', replace_tier, text)
    text = re.sub(r'\{банк:([^}]+)\}', replace_bank, text)
    text = text.replace('{курс}', str(round(base_rate, 2)))
    return text


@router.callback_query(F.data == "promotions")
async def show_promotions(callback: types.CallbackQuery):
    raw = db.get_setting("promotions_text", "Акции временно недоступны.")
    text = _resolve_promotions_text(raw)
    await safe_edit(callback,
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")]
        ]),
    )
    await callback.answer()


# ---- /myid ----

@router.message(Command("myid"))
async def cmd_myid(message: types.Message):
    await message.answer(f"Ваш Telegram ID: `{message.from_user.id}`", parse_mode="Markdown")


# ---- Меню ----

@router.callback_query(F.data == "back_menu")
async def back_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer_photo(
        FSInputFile(BANNER_PATH),
        caption=f"Добро пожаловать, {callback.from_user.first_name}!\n"
                "Обмен рублей и юаней. Выберите действие:",
        reply_markup=_main_menu(),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def show_help(callback: types.CallbackQuery):
    guide_url = db.get_setting("receipt_guide_url", "https://telegra.ph/test-cheki-03-23")
    manager = db.get_setting("main_manager", "bulievich")
    await safe_edit(callback,
        f"Напишите главному менеджеру — @{manager}",
        reply_markup=kb.help_kb(guide_url),
    )
    await callback.answer()


# ---- Обмен: выбор направления ----

@router.callback_query(F.data == "exchange")
async def select_direction(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    active = db.get_user_active_order(callback.from_user.id)
    if active:
        st = STATUS_TEXT.get(active["status"], active["status"])
        await safe_edit(callback,
            f"У вас уже есть активная заявка ({st}).\n"
            "Дождитесь её завершения или отмены.",
            reply_markup=_main_menu(),
        )
        await callback.answer()
        return
    rate_row = db.get_rate(PAIR)
    buy = rate_row["buy_rate"] if rate_row else "—"
    sell = rate_row["sell_rate"] if rate_row else "—"
    await safe_edit(callback,
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

        await state.update_data(
            cur_from=cur_from, cur_to=cur_to, rate=rate,
            has_wechat=bool(has_wechat), has_alipay=bool(has_alipay),
        )

        await _continue_buy_flow(callback.message, state, bool(has_wechat), bool(has_alipay))
        await callback.answer()
        return

    # CNY→RUB: нужна карта для получения рублей + выбор откуда отправляют
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
        await state.set_state(OrderFSM.waiting_pay_method)
        await state.update_data(
            cur_from=cur_from, cur_to=cur_to, rate=rate,
            flow_msg_id=callback.message.message_id,
            flow_chat_id=callback.message.chat.id,
        )
        await callback.message.edit_text(
            "Откуда вы будете отправлять юани?",
            reply_markup=kb.pay_method_kb(),
        )
        await callback.answer()
        return

    await state.set_state(OrderFSM.waiting_amount)
    await state.update_data(
        cur_from=cur_from, cur_to=cur_to, rate=rate, pay_method=None,
        flow_msg_id=callback.message.message_id,
        flow_chat_id=callback.message.chat.id,
    )
    await callback.message.edit_text(f"Введите сумму в {cur_from}:")
    await callback.answer()


async def _continue_buy_flow(message, state: FSMContext, has_wechat: bool, has_alipay: bool):
    if has_wechat and has_alipay:
        await state.set_state(OrderFSM.waiting_pay_method)
        await message.edit_text(
            "Куда хотите получить юани?",
            reply_markup=kb.pay_method_kb(),
        )
    else:
        method = "wechat" if has_wechat else "alipay"
        await state.update_data(pay_method=method)
        await state.set_state(OrderFSM.waiting_bank)
        await message.edit_text(
            "Выберите банк с которого вы будете производить перевод:",
            reply_markup=kb.bank_select_kb(),
        )


@router.callback_query(F.data == "receipt_yes", OrderFSM.waiting_receipt_confirm)
async def receipt_confirmed(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    discount_note = f"\n🎁 {data.get('bank')}: скидка −{data.get('bank_discount')} на курс" if data.get("bank_discount") else ""
    await state.set_state(OrderFSM.waiting_amount)
    await callback.message.edit_text(f"Введите сумму в {data['cur_from']}:{discount_note}")
    await callback.answer()


@router.callback_query(F.data.startswith("pay:"), OrderFSM.waiting_pay_method)
async def pay_method_selected(callback: types.CallbackQuery, state: FSMContext):
    method = callback.data.split(":", 1)[1]
    await state.update_data(pay_method=method)
    data = await state.get_data()

    # CNY→RUB: после выбора метода сразу ввод суммы
    if data.get("cur_from") == "CNY":
        await state.set_state(OrderFSM.waiting_amount)
        await callback.message.edit_text(f"Введите сумму в CNY:")
        await callback.answer()
        return

    await state.set_state(OrderFSM.waiting_bank)
    await callback.message.edit_text(
        "Выберите банк с которого вы будете производить перевод:",
        reply_markup=kb.bank_select_kb(),
    )
    await callback.answer()


def _get_bank_discount(bank: str) -> float:
    """Возвращает скидку к курсу для данного банка."""
    raw = db.get_setting("bank_discounts", "[]")
    try:
        discounts = json.loads(raw)
        for d in discounts:
            if d["bank"] == bank:
                return float(d["discount"])
    except Exception:
        pass
    return 0.0


def _get_volume_discount(cny_amount: float) -> tuple:
    """Возвращает (скидка, метка) для объёма в CNY."""
    raw = db.get_setting("volume_discounts", "[]")
    try:
        tiers = sorted(json.loads(raw), key=lambda t: t["min_cny"])
    except Exception:
        return 0.0, ""
    best_discount, best_label = 0.0, ""
    for tier in tiers:
        if cny_amount >= tier["min_cny"]:
            best_discount = tier["discount"]
            best_label = f"от {int(tier['min_cny'])} CNY"
    return best_discount, best_label


@router.callback_query(F.data.startswith("bank:"), OrderFSM.waiting_bank)
async def bank_selected(callback: types.CallbackQuery, state: FSMContext):
    bank = callback.data.split(":", 1)[1]
    if bank == "other":
        await state.set_state(OrderFSM.waiting_bank_custom)
        await callback.message.edit_text("Введите название вашего банка:")
        await callback.answer()
        return

    data = await state.get_data()
    rate = data["rate"]
    bank_discount = 0.0
    discount_note = ""
    if data.get("cur_from") == "RUB":
        bank_discount = _get_bank_discount(bank)
        if bank_discount > 0:
            rate = round(rate - bank_discount, 2)
            discount_note = f"\n🎁 {bank}: скидка −{bank_discount} на курс"

    await state.update_data(
        bank=bank,
        rate=rate,
        orig_rate=data["rate"],
        bank_discount=bank_discount,
        bank_discount_applied=bank_discount > 0,
        flow_msg_id=callback.message.message_id,
        flow_chat_id=callback.message.chat.id,
    )

    # Показываем предупреждение о чеках только для Т-Банка и только если нет опыта
    if bank == "Т-Банк" and db.get_user_completed_bank_count(callback.from_user.id, "Т-Банк") == 0:
        await state.set_state(OrderFSM.waiting_receipt_confirm)
        guide_url = db.get_setting("receipt_guide_url", "https://telegra.ph/test-cheki-03-23")
        await callback.message.edit_text(
            "⚠️ Прежде чем продолжить — важный вопрос!\n\n"
            "Вы умеете отправлять чеки об оплате от имени банка?\n\n"
            "Это необходимо для подтверждения перевода.\n"
            "Если не знаете как — ознакомьтесь с гайдом по ссылке ниже, "
            "после чего нажмите «Да, умею».",
            reply_markup=kb.receipt_confirm_kb(guide_url),
        )
        await callback.answer()
        return

    await state.set_state(OrderFSM.waiting_amount)
    await callback.message.edit_text(f"Введите сумму в {data['cur_from']}:{discount_note}")
    await callback.answer()


@router.message(OrderFSM.waiting_bank_custom)
async def bank_custom(message: types.Message, state: FSMContext):
    bank = message.text.strip()
    if not bank:
        await message.answer("Введите название банка:")
        return
    data = await state.get_data()
    sent = await message.answer(f"Введите сумму в {data['cur_from']}:")
    await state.update_data(
        bank=bank,
        bank_custom_msg_id=message.message_id,
        flow_msg_id=sent.message_id,
        flow_chat_id=sent.chat.id,
    )
    await state.set_state(OrderFSM.waiting_amount)


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

    # Проверка минимальной суммы для RUB→CNY
    if data.get("cur_from") == "RUB":
        try:
            min_amount = float(db.get_setting("min_buy_amount", "0"))
        except Exception:
            min_amount = 0
        if min_amount > 0 and amount < min_amount:
            await message.answer(f"Минимальная сумма для покупки юаней: {int(min_amount)} RUB")
            return

    base_rate = data["rate"]
    bank_discount = data.get("bank_discount", 0.0)

    discounts_info = []
    if data.get("bank_discount_applied") and bank_discount > 0:
        bank = data.get("bank", "")
        discounts_info.append(f"💳 {bank}: −{bank_discount}")

    rate = base_rate

    # Объёмная скидка только для RUB→CNY
    if data["cur_from"] == "RUB":
        est_cny = amount / rate
        vol_discount, vol_label = _get_volume_discount(est_cny)
        if vol_discount > 0:
            rate = round(rate - vol_discount, 2)
            discounts_info.append(f"📦 Объём ({vol_label}): −{vol_discount}")

    # Расчёт итога
    if data["cur_from"] == "RUB":
        result = round(amount / rate, 2)
    else:
        result = round(amount * rate, 2)

    await state.update_data(amount=amount, amount_result=result, rate=rate, amount_msg_id=message.message_id)
    await state.set_state(OrderFSM.waiting_confirm)

    bank = data.get("bank")
    pay_method = data.get("pay_method")

    discount_block = ""
    if discounts_info:
        orig_rate = data.get("orig_rate", base_rate)
        discount_block = (
            f"\n📊 Скидки применены:\n"
            + "\n".join(f"  {d}" for d in discounts_info)
            + f"\n  Итоговый курс: {rate} (базовый: {orig_rate})\n"
        )

    extra = ""
    if bank:
        extra += f"Банк: {bank}\n"
    if pay_method:
        method_name = "WeChat Pay" if pay_method == "wechat" else "Alipay"
        label = "Вы отправляете с" if data.get("cur_from") == "CNY" else "Получение"
        extra += f"{label}: {method_name}\n"

    await message.answer(
        f"Вы отдаёте: {amount} {data['cur_from']}\n"
        f"Вы получите: {result} {data['cur_to']}\n"
        f"{extra}"
        f"{discount_block}\n"
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
        bank=data.get("bank"),
    )
    chat_id = callback.message.chat.id

    # Удаляем промежуточные сообщения
    for msg_id in filter(None, [
        data.get("flow_msg_id"),
        data.get("amount_msg_id"),
        data.get("bank_custom_msg_id"),
        callback.message.message_id,
    ]):
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception:
            pass

    await state.clear()
    await callback.answer()

    await bot.send_message(
        chat_id,
        f"✅ Заявка создана!\n"
        f"{data['amount']} {data['cur_from']} → {data['amount_result']} {data['cur_to']}\n"
        "Ожидайте — менеджер скоро возьмёт заказ.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"client_cancel:{order_id}")],
        ]),
    )

    # Уведомляем всех менеджеров лично
    managers = db.get_all_active_managers()
    for mgr in managers:
        try:
            bank_line = f"\nБанк: {data['bank']}" if data.get("bank") else ""
            method_line = ""
            pm = data.get("pay_method")
            if pm:
                method_line = f"\nПолучение: {'WeChat' if pm == 'wechat' else 'Alipay'}"
            sent = await bot.send_message(
                mgr["tg_id"],
                f"Новая заявка #{order_id}\n"
                f"Клиент: {callback.from_user.first_name}\n"
                f"{data['amount']} {data['cur_from']} → {data['amount_result']} {data['cur_to']}\n"
                f"Курс: {data['rate']}{bank_line}{method_line}",
                reply_markup=kb.manager_take_order_kb(order_id),
            )
            db.save_order_notification(order_id, mgr["tg_id"], sent.message_id, sent.chat.id)
        except Exception:
            pass


@router.callback_query(F.data == "cancel_order", OrderFSM.waiting_confirm)
async def cancel_order_creation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Заявка отменена.", reply_markup=_main_menu())
    await callback.answer()


# ---- Отмена клиентом после взятия заявки ----

@router.callback_query(F.data.startswith("client_cancel:"))
async def client_cancel_order(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order or str(order["user_id"]) != str(callback.from_user.id):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if order["status"] in ("completed", "cancelled"):
        await callback.answer("Заявка уже закрыта", show_alert=True)
        return

    db.update_order_status(order_id, "cancelled")
    await callback.message.delete()
    await callback.message.answer_photo(
        FSInputFile(BANNER_PATH),
        caption=f"❌ Заявка отменена.\n\n"
                f"Добро пожаловать, {callback.from_user.first_name}!\n"
                "Обмен рублей и юаней. Выберите действие:",
        reply_markup=_main_menu(),
    )
    await callback.answer()

    # Редактируем уведомления всем менеджерам — убираем кнопку, добавляем пометку
    notifications = db.get_order_notifications(order_id)
    for notif in notifications:
        try:
            await bot.edit_message_reply_markup(
                chat_id=notif["chat_id"],
                message_id=notif["message_id"],
                reply_markup=None,
            )
        except Exception:
            pass
        try:
            await bot.edit_message_text(
                chat_id=notif["chat_id"],
                message_id=notif["message_id"],
                text=f"❌ Отменил клиент\n\n"
                     f"Заявка #{order_id}\n"
                     f"Клиент: {callback.from_user.first_name}\n"
                     f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}",
            )
        except Exception:
            pass


# ---- Мои заявки ----

@router.callback_query(F.data == "my_orders")
async def my_orders(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    orders = db.get_user_orders(callback.from_user.id)
    if not orders:
        await safe_edit(callback, "У вас пока нет заявок.", reply_markup=_main_menu())
        await callback.answer()
        return

    lines = []
    for o in orders:
        st = STATUS_TEXT.get(o["status"], o["status"])
        lines.append(f"#{o['id']} | {o['amount']} {o['currency_from']}→{o['currency_to']} | {st}")

    buttons = [
        [types.InlineKeyboardButton(text=f"#{o['id']}", callback_data=f"order:{o['id']}")]
        for o in orders[:3]
    ]
    buttons.append([types.InlineKeyboardButton(text="🔍 Найти другую заявку", callback_data="order_search")])
    buttons.append([types.InlineKeyboardButton(text="◀️ Назад", callback_data="back_menu")])

    await safe_edit(callback,
        "Ваши заявки:\n\n" + "\n".join(lines),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "order_search")
async def order_search_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(OrderFSM.waiting_order_search)
    await callback.message.edit_text("Введите номер заявки:")
    await callback.answer()


@router.message(OrderFSM.waiting_order_search)
async def order_search_input(message: types.Message, state: FSMContext):
    await state.clear()
    order_id = message.text.strip().lstrip("#")
    order = db.get_order(order_id)
    if not order or str(order["user_id"]) != str(message.from_user.id):
        await message.answer("Заявка не найдена.", reply_markup=_main_menu())
        return
    st = STATUS_TEXT.get(order["status"], order["status"])
    text = (
        f"Заявка #{order['id']}\n"
        f"{order['amount']} {order['currency_from']} → {order['amount_result']} {order['currency_to']}\n"
        f"Статус: {st}"
    )
    from bot import keyboards as kb
    await message.answer(text, reply_markup=kb.order_detail_kb(str(order["id"])))


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
        reply_markup=kb.order_detail_kb(order_id, closed=order["status"] in ("completed", "cancelled")),
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
    await safe_edit(callback, "\n".join(lines), reply_markup=_profile_kb(p))
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


# ---- Подтверждение оплаты клиентом ----

@router.callback_query(F.data.startswith("paid:"))
async def client_confirm_payment(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.split(":", 1)[1]
    if callback.message.photo:
        await callback.message.edit_caption(
            caption=(callback.message.caption or "") + "\n\n✅ Вы подтвердили оплату.",
            reply_markup=None,
        )
    else:
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ Вы подтвердили оплату.",
            reply_markup=None,
        )
    await callback.answer()
    await callback.message.answer(
        "⏳ Сейчас проверим ваш перевод.\nПроверка занимает около 10 минут.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
        ]),
    )

    # Уведомляем менеджера с кнопкой подтверждения
    order = db.get_order(order_id)
    if order and order["manager_id"]:
        try:
            await bot.send_message(
                order["manager_id"],
                f"💰 Клиент подтвердил оплату по заявке #{order_id}.\n"
                f"Сумма: {order['amount']} {order['currency_from']}",
                reply_markup=kb.manager_payment_received_kb(order_id),
            )
        except Exception:
            pass


# ---- Подтверждение получения юаней ----

@router.callback_query(F.data.startswith("yuan_received:"))
async def yuan_received(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.split(":", 1)[1]
    order = db.get_order(order_id)
    if not order or order["status"] == "completed":
        await callback.answer("Получение уже подтверждено.", show_alert=True)
        return
    db.update_order_status(order_id, "completed")
    await callback.message.edit_text(
        f"✅ Отлично! Получение юаней подтверждено.\n"
        f"Спасибо, что воспользовались нашим сервисом!",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_menu")],
        ]),
    )
    await callback.answer()

    # Уведомляем менеджера
    order = db.get_order(order_id)
    if order and order["manager_id"]:
        try:
            await bot.send_message(
                order["manager_id"],
                f"✅ Клиент подтвердил получение юаней по заявке #{order_id}.\nЗаявка завершена.",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("yuan_missing:"))
async def yuan_missing(callback: types.CallbackQuery):
    order_id = callback.data.split(":", 1)[1]
    manager = db.get_setting("main_manager", "bulievich")
    # Сообщение с кнопками остаётся — отправляем новое сообщение ниже
    await callback.message.answer(
        f"❌ Средства не поступили?\n\n"
        f"Свяжитесь с менеджером через бота.\n"
        f"Если менеджер не отвечает — напишите главному менеджеру: @{manager}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Подтвердить получение", callback_data=f"yuan_received:{order_id}")],
            [types.InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
        ]),
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
    await callback.message.answer(
        f"Напишите сообщение менеджеру:\n"
        "(Отправьте текст или /cancel для отмены)"
    )
    await callback.answer()


@router.message(OrderFSM.waiting_message)
async def relay_client_to_manager(message: types.Message, state: FSMContext, bot: Bot):
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=_main_menu())
        return

    data = await state.get_data()
    order_id = data.get("relay_order_id")
    if not order_id:
        await state.clear()
        return

    order = db.get_order(order_id)
    if not order:
        await state.clear()
        await message.answer("Заявка не найдена.", reply_markup=_main_menu())
        return

    photo_file_id = message.photo[-1].file_id if message.photo else None
    db.save_message(order_id, message.from_user.id, message.text or message.caption or "[фото]", file_id=photo_file_id)
    await state.set_state(OrderFSM.waiting_message)
    await message.answer(
        "Напишите сообщение менеджеру:\n"
        "(Отправьте текст или фото, /cancel для отмены)\n\n"
        "✅ Сообщение отправлено менеджеру."
    )

    reply_kb = kb.manager_status_kb(order_id, show_req=False)
    caption_prefix = f"Сообщение от клиента:\n\n"

    async def _send_to(tg_id):
        try:
            if message.photo:
                await bot.send_photo(
                    tg_id,
                    photo=message.photo[-1].file_id,
                    caption=caption_prefix + (message.caption or ""),
                    reply_markup=reply_kb,
                )
            else:
                await bot.send_message(
                    tg_id,
                    caption_prefix + (message.text or ""),
                    reply_markup=reply_kb,
                )
        except Exception:
            pass

    manager_id = order["manager_id"]
    if manager_id:
        await _send_to(manager_id)
    else:
        for mgr in db.get_all_active_managers():
            await _send_to(mgr["tg_id"])


# ---- Подтверждение условий клиентом ----

@router.message(F.text == "+")
async def client_confirm_terms(message: types.Message, bot: Bot):
    order = db.get_pending_terms_order(message.from_user.id)
    if not order:
        return
    order_id = str(order["id"])
    db.confirm_order_terms(order_id)
    await message.answer(
        "✅ Условия приняты. Ожидайте реквизиты для оплаты.\nБудьте на связи — менеджер скоро напишет!",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✉️ Написать менеджеру", callback_data=f"msg:{order_id}")],
        ]),
    )

    # Обновляем сообщение менеджера — добавляем кнопку реквизитов
    if order["manager_id"] and order["manager_msg_id"] and order["manager_chat_id"]:
        try:
            is_cny_rub = order["currency_from"] == "CNY"
            await bot.edit_message_reply_markup(
                chat_id=order["manager_chat_id"],
                message_id=order["manager_msg_id"],
                reply_markup=kb.manager_status_kb(order_id, show_qr=False, show_req=True, send_qr_mode=is_cny_rub),
            )
        except Exception:
            pass
        try:
            await bot.send_message(
                order["manager_id"],
                f"✅ Клиент подтвердил условия по заявке #{order_id}. Можно скидывать реквизиты.",
            )
        except Exception:
            pass
