import random
import string
import re
from datetime import datetime, timedelta
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from services.repository import Repository
from states.admin import PromoStates
from keyboards.admin_kb import get_promos_menu_kb

router = Router()

async def generate_unique_promo_code(repo: Repository) -> str:
    for _ in range(20):
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not await repo.get_promo_by_code(code):
            return code
    raise Exception("Не удалось сгенерировать уникальный промокод")

@router.callback_query(F.data == "admin_promos")
async def admin_promos_menu(call: types.CallbackQuery):
    await call.message.edit_text(text="<b>🎟️ Управление промокодами</b>", reply_markup=get_promos_menu_kb())

@router.callback_query(F.data == "promo_create")
async def promo_create_choose_type(call: types.CallbackQuery, state: FSMContext):
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="💰 Пополнение баланса (₽)", callback_data="promo_type_balance")],
        [types.InlineKeyboardButton(text="📉 Скидка (%)", callback_data="promo_type_discount")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_promos")],
    ])
    await call.message.edit_text(text="<b>➕ Создание промокода</b>\n\nВыберите тип:", reply_markup=kb)
    await state.set_state(PromoStates.create_choose_type)

@router.callback_query(PromoStates.create_choose_type, F.data.startswith("promo_type_"))
async def promo_create_choose_name(call: types.CallbackQuery, state: FSMContext):
    promo_type = call.data.replace("promo_type_", "")
    await state.update_data(promo_type=promo_type)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎲 Сгенерировать название", callback_data="promo_gen_name")],
        [types.InlineKeyboardButton(text="✍️ Ввести своё", callback_data="promo_input_name")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="promo_create")],
    ])
    await call.message.edit_text(text="Выберите способ задания названия:", reply_markup=kb)
    await state.set_state(PromoStates.create_choose_name)

@router.callback_query(PromoStates.create_choose_name, F.data == "promo_gen_name")
async def promo_create_gen_name(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    code = await generate_unique_promo_code(repo)
    await state.update_data(promo_name=code)
    data = await state.get_data()
    promo_type = data.get("promo_type")
    text = f"Введите процент скидки для <code>{code}</code>:" if promo_type == "discount" else f"Введите сумму пополнения для <code>{code}</code>:"
    await call.message.edit_text(text=text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="promo_create")]]))
    await state.set_state(PromoStates.create_input_sum)

@router.callback_query(PromoStates.create_choose_name, F.data == "promo_input_name")
async def promo_create_input_name(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text("Введите название промокода (латиница и цифры):", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="promo_create")]]))
    await state.set_state(PromoStates.create_input_name)

@router.message(PromoStates.create_input_name)
async def promo_create_process_name(message: types.Message, state: FSMContext, repo: Repository):
    code = message.text.strip().upper()
    if not re.match(r'^[A-Z0-9]+$', code):
        await message.answer("❗ Название может содержать только латинские буквы и цифры.")
        return
    if await repo.get_promo_by_code(code):
        await message.answer("❗ Промокод уже существует. Введите другой.")
        return
    
    await state.update_data(promo_name=code)
    data = await state.get_data()
    promo_type = data.get("promo_type")
    text = f"Введите процент скидки для <code>{code}</code>:" if promo_type == "discount" else f"Введите сумму пополнения для <code>{code}</code>:"
    await message.answer(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="promo_create")]]))
    await state.set_state(PromoStates.create_input_sum)

@router.message(PromoStates.create_input_sum)
async def promo_create_input_sum_msg(message: types.Message, state: FSMContext):
    try:
        value = float(message.text.strip())
        if value <= 0: raise ValueError
    except ValueError:
        await message.answer("Введите корректное положительное число.")
        return
    
    await state.update_data(promo_sum=value)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🔢 Кол-во использований", callback_data="promo_limit_uses")],
        [types.InlineKeyboardButton(text="⏰ Время действия", callback_data="promo_limit_time")],
        [types.InlineKeyboardButton(text="♾️ Безлимитный", callback_data="promo_limit_none")],
    ])
    await message.answer("Выберите ограничение для промокода:", reply_markup=kb)
    await state.set_state(PromoStates.create_choose_limit)

@router.callback_query(PromoStates.create_choose_limit, F.data == "promo_limit_uses")
async def promo_create_limit_uses(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text("Введите максимальное количество активаций:")
    await state.set_state(PromoStates.create_input_uses)

@router.callback_query(PromoStates.create_choose_limit, F.data == "promo_limit_time")
async def promo_create_limit_time(call: types.CallbackQuery, state: FSMContext):
    await call.message.edit_text("Введите время действия промокода в часах:")
    await state.set_state(PromoStates.create_input_time)

@router.callback_query(PromoStates.create_choose_limit, F.data == "promo_limit_none")
async def promo_create_no_limit(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    data = await state.get_data()
    code, promo_type, value = data['promo_name'], data['promo_type'], data['promo_sum']
    await repo.create_promo_code(code, promo_type, value)
    await call.message.edit_text(f"✅ Безлимитный промокод <code>{code}</code> создан!", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]]))
    await state.clear()

@router.message(PromoStates.create_input_uses)
async def promo_create_process_uses(message: types.Message, state: FSMContext, repo: Repository):
    try:
        uses = int(message.text)
        if uses <= 0: raise ValueError
    except ValueError:
        await message.answer("Введите целое положительное число.")
        return
    data = await state.get_data()
    code, promo_type, value = data['promo_name'], data['promo_type'], data['promo_sum']
    await repo.create_promo_code(code, promo_type, value, max_uses=uses)
    await message.answer(f"✅ Промокод <code>{code}</code> на {uses} использований создан!", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]]))
    await state.clear()
    
@router.message(PromoStates.create_input_time)
async def promo_create_process_time(message: types.Message, state: FSMContext, repo: Repository):
    try:
        hours = int(message.text)
        if hours <= 0: raise ValueError
    except ValueError:
        await message.answer("Введите целое положительное число.")
        return
    data = await state.get_data()
    code, promo_type, value = data['promo_name'], data['promo_type'], data['promo_sum']
    expires_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
    await repo.create_promo_code(code, promo_type, value, expires_at=expires_at)
    await message.answer(f"✅ Промокод <code>{code}</code> со сроком действия {hours} час(ов) создан!", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="В админ-панель", callback_data="admin_panel")]]))
    await state.clear()

@router.callback_query(F.data == "promo_active")
async def promo_active_list(call: types.CallbackQuery, repo: Repository):
    promos = await repo.get_active_promo_codes()
    if not promos:
        await call.answer("Активных промокодов нет.", show_alert=True)
        return
    
    kb = [[types.InlineKeyboardButton(text=p['code'], callback_data=f"promo_stats_{p['code']}")] for p in promos]
    kb.append([types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_promos")])
    await call.message.edit_text("<b>📋 Активные промокоды:</b>\nНажмите для просмотра статистики.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "promo_delete")
async def promo_delete_list(call: types.CallbackQuery, repo: Repository):
    promos = await repo.get_all_promo_codes()
    if not promos:
        await call.answer("Промокодов для удаления нет.", show_alert=True)
        return
        
    kb = [[types.InlineKeyboardButton(text=f"🗑️ {p['code']}", callback_data=f"promo_confirm_delete_{p['code']}")] for p in promos]
    kb.append([types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_promos")])
    await call.message.edit_text("<b>🗑️ Выберите промокод для удаления:</b>", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("promo_confirm_delete_"))
async def promo_delete_confirm(call: types.CallbackQuery, repo: Repository):
    code_to_delete = call.data.replace("promo_confirm_delete_", "")
    await repo.delete_promo_code(code_to_delete)
    await call.answer(f"Промокод {code_to_delete} удалён.", show_alert=True)
    await promo_delete_list(call, repo)

@router.callback_query(F.data.startswith("promo_stats_"))
async def promo_show_stats(call: types.CallbackQuery, repo: Repository):
    code = call.data.replace("promo_stats_", "")
    promo = await repo.get_promo_by_code(code)
    if not promo:
        await call.answer("Промокод не найден.", show_alert=True)
        return

    text = f"<b>📊 Статистика по промокоду:</b> <code>{promo['code']}</code>\n"
    promo_type = "Скидка" if promo['promo_type'] == 'discount' else "Пополнение"
    value_unit = "%" if promo['promo_type'] == 'discount' else "₽"
    text += f"Тип: {promo_type} на {promo['value']} {value_unit}\n"
    text += f"Использовано: {promo['current_uses']} раз\n"
    if promo['max_uses']: text += f"Лимит использований: {promo['max_uses']}\n"
    if promo['expires_at']: text += f"Действует до: {datetime.fromisoformat(promo['expires_at']).strftime('%Y-%m-%d %H:%M')}\n"
    
    await call.message.edit_text(text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="promo_active")]]))