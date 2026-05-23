from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from config import Config
from services.repository import Repository
from services.star_pricing import star_pricing_service
from states.admin import PriceStates
from keyboards.admin_kb import get_prices_menu_kb, get_premium_prices_kb
from keyboards.user_kb import PREMIUM_PLANS

router = Router()

async def get_premium_prices(repo: Repository):
    keys = [f'premium_price_{i}' for i in range(len(PREMIUM_PLANS))]
    prices_db = await repo.get_multiple_settings(keys)
    return [float(prices_db.get(f'premium_price_{i}', plan['price'])) for i, plan in enumerate(PREMIUM_PLANS)]

@router.callback_query(F.data == "admin_prices")
async def admin_prices_menu(call: types.CallbackQuery, repo: Repository, config: Config):
    star_price_mode = (await repo.get_setting('star_price_mode') or 'static').strip().lower()
    star_cost_ton_mode = (await repo.get_setting('star_cost_ton_mode') or 'static').strip().lower()
    dynamic_enabled = star_price_mode == 'dynamic' and star_cost_ton_mode == 'dynamic'
    mode_text = 'Вкл' if dynamic_enabled else 'Выкл'

    settings = await repo.get_multiple_settings([
        'star_price',
        'star_cost_ton',
        'star_target_profit_per_100',
        'star_markup_percent',
        'rollypay_fee',
        'star_min_price',
        'star_max_price',
        'star_cost_ton_quote_username',
        'star_cost_ton_quote_qty',
        'star_cost_ton_cache_seconds',
    ])

    static_star_price = float(settings.get('star_price') or 1.8)
    static_star_cost_ton = float(settings.get('star_cost_ton') or 0.01)
    target_profit_per_100 = float(settings.get('star_target_profit_per_100') or 15)
    markup_percent = float(settings.get('star_markup_percent') or 20)
    rollypay_fee = float(settings.get('rollypay_fee') or 12)
    min_price = float(settings.get('star_min_price') or 0)
    max_price = float(settings.get('star_max_price') or 0)
    quote_username = (settings.get('star_cost_ton_quote_username') or '').strip().lstrip('@') or 'не задан'
    quote_qty = int(settings.get('star_cost_ton_quote_qty') or 50)
    cache_seconds = int(settings.get('star_cost_ton_cache_seconds') or 120)

    current_star_price = await star_pricing_service.get_star_price(repo, config)
    current_star_cost_ton = await star_pricing_service.get_star_cost_ton(repo, config)
    current_ton_rate = await star_pricing_service._get_ton_rub_rate_cached()
    current_cost_rub = current_star_cost_ton * current_ton_rate
    target_profit_per_star = target_profit_per_100 / 100

    pricing_info = (
        "\n\n<b>📐 Расчёт цены</b>\n"
        f"• Режим цены: <code>{star_price_mode}</code>\n"
        f"• Режим себестоимости TON: <code>{star_cost_ton_mode}</code>\n"
        f"• Статическая цена: <code>{static_star_price:.2f} ₽</code>\n"
        f"• Статическая себестоимость: <code>{static_star_cost_ton:.4f} TON</code> за звезду\n"
        f"• Текущая себестоимость: <code>{current_star_cost_ton:.6f} TON</code> за звезду\n"
        f"• Курс TON/RUB: <code>{current_ton_rate:.2f} ₽</code>\n"
        f"• Себестоимость в рублях: <code>{current_cost_rub:.2f} ₽</code> за звезду\n"
        f"• Целевая прибыль: <code>{target_profit_per_100:.2f} ₽</code> на 100 звёзд\n"
        f"• Прибыль на 1 звезду: <code>{target_profit_per_star:.2f} ₽</code>\n"
        f"• Наценка вместо target-profit: <code>{markup_percent:.2f}%</code>\n"
        f"• Комиссия RollyPay в цене: <code>{rollypay_fee:.2f}%</code>\n"
        f"• Минимальная цена: <code>{min_price:.2f} ₽</code>\n"
        f"• Максимальная цена: <code>{max_price:.2f} ₽</code>\n"
        f"• Fragment username: <code>@{quote_username}</code>\n"
        f"• Fragment qty: <code>{quote_qty}</code>\n"
        f"• Cache TTL: <code>{cache_seconds}</code> сек\n"
        f"• Итоговая цена сейчас: <code>{current_star_price:.2f} ₽</code> за 1 звезду"
    )

    kb = get_prices_menu_kb(
        dynamic_button_text=f"🔄 Динамическая цена: {mode_text}",
        dynamic_button_callback="toggle_star_dynamic_price"
    )
    await call.message.edit_text(
        text=(
            "<b>📈 Управление ценами</b>\n\n"
            f"⭐ Динамический режим: <b>{'включен' if dynamic_enabled else 'выключен'}</b>\n"
            f"• Цена за звезду: <code>{star_price_mode}</code>\n"
            f"• Себестоимость TON: <code>{star_cost_ton_mode}</code>"
            f"{pricing_info}"
        ),
        reply_markup=kb
    )


@router.callback_query(F.data == "toggle_star_dynamic_price")
async def toggle_star_dynamic_price(call: types.CallbackQuery, repo: Repository):
    star_price_mode = (await repo.get_setting('star_price_mode') or 'static').strip().lower()
    star_cost_ton_mode = (await repo.get_setting('star_cost_ton_mode') or 'static').strip().lower()
    enable_dynamic = not (star_price_mode == 'dynamic' and star_cost_ton_mode == 'dynamic')
    new_value = 'dynamic' if enable_dynamic else 'static'

    await repo.update_setting('star_price_mode', new_value)
    await repo.update_setting('star_cost_ton_mode', new_value)

    await call.answer(f"Динамическая цена {'включена' if enable_dynamic else 'выключена'}", show_alert=True)
    await admin_prices_menu(call, repo)

@router.callback_query(F.data == "price_stars")
async def price_stars_show(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    star_price = await repo.get_setting('star_price')
    await call.message.edit_text(
        text=f"<b>⭐ Текущая цена за 1 звезду:</b> <code>{star_price}</code> ₽\n\nВведите новую цену:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_prices")]])
    )
    await state.set_state(PriceStates.stars_input)


@router.callback_query(F.data == "price_stars_min")
async def price_stars_min_show(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    min_price = float(await repo.get_setting('star_min_price') or 0)
    await call.message.edit_text(
        text=f"<b>📉 Минимальная цена звезды:</b> <code>{min_price:.2f}</code> ₽\n\nВведите новое значение (0 — отключить ограничение):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_prices")]])
    )
    await state.set_state(PriceStates.stars_min_input)


@router.message(PriceStates.stars_min_input)
async def price_stars_min_input_msg(message: types.Message, state: FSMContext, repo: Repository):
    try:
        price = float(message.text.replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное число 0 или больше.")
        return

    await repo.update_setting('star_min_price', price)
    await message.answer(
        f"✅ Минимальная цена звезды изменена на <b>{price:.2f}₽</b>.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]])
    )
    await state.clear()


@router.callback_query(F.data == "price_stars_max")
async def price_stars_max_show(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    max_price = float(await repo.get_setting('star_max_price') or 0)
    await call.message.edit_text(
        text=f"<b>📈 Максимальная цена звезды:</b> <code>{max_price:.2f}</code> ₽\n\nВведите новое значение (0 — отключить ограничение):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_prices")]])
    )
    await state.set_state(PriceStates.stars_max_input)


@router.message(PriceStates.stars_max_input)
async def price_stars_max_input_msg(message: types.Message, state: FSMContext, repo: Repository):
    try:
        price = float(message.text.replace(",", "."))
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное число 0 или больше.")
        return

    await repo.update_setting('star_max_price', price)
    await message.answer(
        f"✅ Максимальная цена звезды изменена на <b>{price:.2f}₽</b>.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]])
    )
    await state.clear()

@router.message(PriceStates.stars_input)
async def price_stars_input_msg(message: types.Message, state: FSMContext, repo: Repository):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0: raise ValueError
    except ValueError:
        await message.answer("Введите корректное положительное число.")
        return
    await repo.update_setting('star_price', price)
    await message.answer(f"✅ Цена за 1 звезду изменена на <b>{price}₽</b>.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]]))
    await state.clear()

@router.callback_query(F.data == "price_premium")
async def price_premium_choose(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    premium_prices = await get_premium_prices(repo)
    await call.message.edit_text(
        text="<b>💎 Выберите тариф для изменения цены:</b>",
        reply_markup=get_premium_prices_kb(premium_prices)
    )
    await state.set_state(PriceStates.premium_choose)
    
@router.callback_query(PriceStates.premium_choose, F.data.startswith("price_premium_"))
async def price_premium_input_start(call: types.CallbackQuery, state: FSMContext):
    plan_index = int(call.data.split("_")[-1])
    await state.update_data(plan_index=plan_index)
    await call.message.edit_text(
        f"<b>💎 Тариф «{PREMIUM_PLANS[plan_index]['name']}»</b>\n\nВведите новую цену в рублях:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_prices")]])
    )
    await state.set_state(PriceStates.premium_input)

@router.message(PriceStates.premium_input)
async def price_premium_input_msg(message: types.Message, state: FSMContext, repo: Repository):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0: raise ValueError
    except ValueError:
        await message.answer("Введите корректное положительное число.")
        return
        
    data = await state.get_data()
    plan_index = data.get("plan_index")
    await repo.update_setting(f'premium_price_{plan_index}', price)
    await message.answer(f"✅ Цена тарифа «{PREMIUM_PLANS[plan_index]['name']}» изменена на <b>{price}₽</b>.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]]))
    await state.clear()

@router.callback_query(F.data == "price_vpn")
async def price_vpn_show(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    vpn_price = float(await repo.get_setting('vpn_standard_price') or 100)
    await call.message.edit_text(
        text=f"<b>🔐 Текущая цена ВПН тарифа Стандартный:</b> <code>{vpn_price}</code> ₽\n\nВведите новую цену:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_prices")]])
    )
    await state.set_state(PriceStates.vpn_input)

@router.message(PriceStates.vpn_input)
async def price_vpn_input_msg(message: types.Message, state: FSMContext, repo: Repository):
    try:
        price = float(message.text.replace(",", "."))
        if price <= 0: raise ValueError
    except ValueError:
        await message.answer("Введите корректное положительное число.")
        return
    await repo.update_setting('vpn_standard_price', price)
    await message.answer(f"✅ Цена ВПН тарифа Стандартный изменена на <b>{price}₽</b>.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]]))
    await state.clear()