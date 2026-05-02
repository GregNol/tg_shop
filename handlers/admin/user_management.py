from aiogram import F, Router, types, Bot
from aiogram.fsm.context import FSMContext
from datetime import datetime
import logging

from services.repository import Repository
from states.admin import AdminUserManagementStates
from keyboards.admin_kb import get_user_info_kb, get_user_payments_kb, UserPaymentsCallback, AdminUserNavCallback

router = Router()
PAGE_SIZE = 5

async def show_user_info_menu(message: types.Message, state: FSMContext, repo: Repository):
    data = await state.get_data()
    user_id = data['target_user_id']
    
    user = await repo.get_user(user_id)
    if not user:
        await message.answer("❌ Пользователь не найден.")
        await state.clear()
        return

    total_top_up = await repo.get_total_top_up(user_id)
    total_stars_bought = await repo.get_total_stars_bought(user_id)
    reg_date = user['created_at'].strftime('%d.%m.%Y')
    status = "🟢 Активен" if not user['is_blocked'] else "🔴 Заблокирован"
    
    text = (
        f"<b>👤 Профиль пользователя</b>\n\n"
        f"<b>🆔 ID:</b> <code>{user['telegram_id']}</code>\n"
        f"<b>🔗 Username:</b> @{user['username'] or '-'}\n\n"
        f"<b>💰 Баланс:</b> {user['balance']:.2f} ₽\n"
        f"<b>📈 Всего пополнил:</b> {total_top_up:.2f} ₽\n"
        f"<b>⭐️ Куплено звезд:</b> {total_stars_bought:,}\n\n"
        f"<b>🚦 Статус:</b> {status}\n"
        f"<b>📆 Дата регистрации:</b> {reg_date}"
    )
    
    await message.edit_text(text, reply_markup=get_user_info_kb(user['is_blocked']))
    await state.set_state(AdminUserManagementStates.user_menu)

@router.callback_query(F.data == "admin_users")
async def admin_users_start(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        text="<b>👤 Управление пользователями</b>\n\nВведите username (с @) или ID пользователя:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]])
    )
    await state.set_state(AdminUserManagementStates.waiting_for_user)

@router.message(AdminUserManagementStates.waiting_for_user)
async def admin_get_user(message: types.Message, state: FSMContext, repo: Repository):
    user_input = message.text.strip().lstrip('@')
    user = await repo.get_user_by_id_or_username(user_input)

    if not user:
        await message.answer("❌ Пользователь не найден.", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]]))
        return
    
    await state.update_data(target_user_id=user['telegram_id'])
    
    dummy_message = await message.answer("...")
    await show_user_info_menu(dummy_message, state, repo)
    await message.delete()

@router.callback_query(AdminUserManagementStates.user_menu, F.data == 'admin_toggle_block')
async def admin_toggle_block_user(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    data = await state.get_data()
    user_id = data['target_user_id']
    user = await repo.get_user(user_id)
    
    await repo.update_user_block_status(user_id, not user['is_blocked'])
    await call.answer(f"Статус пользователя изменен ✅")
    await show_user_info_menu(call.message, state, repo)

@router.callback_query(AdminUserManagementStates.user_menu, F.data == 'admin_give_balance')
async def admin_give_balance_start(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target_user_id = data['target_user_id']
    await state.set_state(AdminUserManagementStates.giving_balance_amount)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=target_user_id).pack())]
    ])
    await call.message.edit_text("💰 Введите сумму для выдачи:", reply_markup=kb)

@router.callback_query(AdminUserManagementStates.user_menu, F.data == 'admin_take_balance')
async def admin_take_balance_start(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    data = await state.get_data()
    user_id = data['target_user_id']
    user = await repo.get_user(user_id)

    if user['balance'] <= 0:
        await call.answer("У этого пользователя нечего списывать.", show_alert=True)
        return

    await state.set_state(AdminUserManagementStates.taking_balance_amount)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=user_id).pack())]
    ])
    await call.message.edit_text("💸 Введите сумму для списания:", reply_markup=kb)

@router.callback_query(AdminUserNavCallback.filter(F.action == "back_to_menu"))
async def back_to_user_menu(call: types.CallbackQuery, callback_data: AdminUserNavCallback, state: FSMContext, repo: Repository):
    await state.update_data(target_user_id=callback_data.target_user_id)
    await show_user_info_menu(call.message, state, repo)

@router.message(AdminUserManagementStates.giving_balance_amount)
async def admin_give_balance_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❗ Введите корректное положительное число.")
        return
    
    data = await state.get_data()
    target_user_id = data['target_user_id']
    await state.update_data(amount_change=amount)
    
    await message.answer(
        f"Вы уверены, что хотите выдать <b>{amount:.2f} ₽</b>?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Да", callback_data="confirm_give_balance"), 
             types.InlineKeyboardButton(text="❌ Нет", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=target_user_id).pack())]
        ])
    )
    await state.set_state(AdminUserManagementStates.giving_balance_confirm)

@router.message(AdminUserManagementStates.taking_balance_amount)
async def admin_take_balance_amount(message: types.Message, state: FSMContext, repo: Repository):
    try:
        amount = float(message.text.strip())
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❗ Введите корректное положительное число.")
        return
    
    data = await state.get_data()
    user_id = data['target_user_id']
    user = await repo.get_user(user_id)

    if amount > user['balance']:
        await message.answer(f"❗ Нельзя списать больше, чем есть на балансе.\nТекущий баланс: {user['balance']:.2f} ₽")
        return

    await state.update_data(amount_change=amount)
    await message.answer(
        f"Вы уверены, что хотите отнять <b>{amount:.2f} ₽</b>?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="✅ Да", callback_data="confirm_take_balance"), 
             types.InlineKeyboardButton(text="❌ Нет", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=user_id).pack())]
        ])
    )
    await state.set_state(AdminUserManagementStates.taking_balance_confirm)

@router.callback_query(AdminUserManagementStates.giving_balance_confirm, F.data == 'confirm_give_balance')
async def admin_give_balance_confirm(call: types.CallbackQuery, state: FSMContext, repo: Repository, bot: Bot):
    data = await state.get_data()
    user_id, amount = data['target_user_id'], data['amount_change']

    await repo.update_user_balance(user_id, amount, 'add')
    
    try:
        await bot.send_message(user_id, f"💰 Администратор пополнил ваш баланс на <b>{amount:.2f} ₽</b>.")
    except Exception as e:
        logging.error(f"Failed to notify user about balance change: {e}")
    
    await call.answer("✅ Баланс успешно выдан.")
    await show_user_info_menu(call.message, state, repo)
    
@router.callback_query(AdminUserManagementStates.taking_balance_confirm, F.data == 'confirm_take_balance')
async def admin_take_balance_confirm(call: types.CallbackQuery, state: FSMContext, repo: Repository):
    data = await state.get_data()
    user_id, amount = data['target_user_id'], data['amount_change']

    await repo.update_user_balance(user_id, amount, 'sub')
    
    await call.answer("✅ Баланс успешно списан.")
    await show_user_info_menu(call.message, state, repo)

@router.callback_query(UserPaymentsCallback.filter())
async def view_user_payments(call: types.CallbackQuery, callback_data: UserPaymentsCallback, state: FSMContext, repo: Repository):
    data = await state.get_data()
    user_id = data.get("target_user_id")
    page = callback_data.page
    
    total_payments = await repo.count_user_payments(user_id)
    text = f"🧾 История пополнений пользователя <code>{user_id}</code>\n\n"

    if total_payments == 0:
        text += "У этого пользователя нет истории пополнений."
        kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад к профилю", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=user_id).pack())]])
    else:
        max_page = (total_payments + PAGE_SIZE - 1) // PAGE_SIZE
        payments = await repo.get_user_payments_page(user_id, page, PAGE_SIZE)
        
        status_map = {
            'paid': '✅ Оплачен',
            'pending': '⏳ Ожидает',
            'cancelled': '❌ Отменен',
            'expired': '⌛️ Истек'
        }

        text_lines = []
        for p in payments:
            status_text = status_map.get(p['status'], p['status'])
            payment_system = p['payment_method'].capitalize() if p.get('payment_method') else 'N/A'
            date_formatted = p['created_at'].strftime('%d.%m.%Y %H:%M')
            text_lines.append(
                f"▫️ <b>{p['amount']:.2f} ₽</b> ({payment_system}) - {status_text}\n"
                f"   <code>{p['invoice_id']}</code> | {date_formatted}"
            )
        
        text += "\n\n".join(text_lines)
        kb = get_user_payments_kb(page, max_page, user_id)
        
    await call.message.edit_text(text, reply_markup=kb)