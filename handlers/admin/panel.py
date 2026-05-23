import logging
from datetime import datetime
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from services.repository import Repository
from services.ton_api import get_ton_balance
from services.profit_calculator import ProfitCalculator
from keyboards.admin_kb import get_admin_panel_kb, get_fee_settings_keyboard, get_back_to_admin_keyboard
from keyboards.admin_kb import PurchaseHistoryCallback
from utils.safe_message import safe_answer, safe_answer_document, safe_delete_message
from config import Config
import io
import csv
from aiogram import types

router = Router()

class AdminFeeStates(StatesGroup):
    waiting_for_fee = State()

@router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(call: types.CallbackQuery, state: FSMContext, repo: Repository, config: Config):
    await state.clear()
    is_maintenance = await repo.get_setting('maintenance_mode') == '1'
    
    balance, error = await get_ton_balance(config.ton.ton_wallet_address)
    balance_text = f"💎 Баланс TON: `{balance:.4f} TON`" if not error else f"💎 Баланс TON: `Ошибка: {error}`"

    await safe_delete_message(call)
    await safe_answer(call, text=f"<b>⚙️ Админ панель</b>\n\n{balance_text}\n\nВыберите действие:", reply_markup=get_admin_panel_kb(is_maintenance))

@router.callback_query(F.data.startswith('purchase_history_export|'))
async def export_purchase_history(call: types.CallbackQuery, repo: Repository):
    try:
        _, ptype = call.data.split('|', 1)
    except Exception:
        ptype = 'all'

    if ptype == 'all':
        rows = await repo.db.fetch("SELECT * FROM purchase_history ORDER BY created_at DESC")
    else:
        rows = await repo.db.fetch("SELECT * FROM purchase_history WHERE purchase_type = $1 ORDER BY created_at DESC", ptype)

    # prepare CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id','user_id','purchase_type','item_description','amount','cost','profit','created_at'])
    for r in rows:
        created = r['created_at'].strftime('%Y-%m-%d %H:%M:%S') if r['created_at'] else ''
        writer.writerow([r['id'], r['user_id'], r['purchase_type'], r['item_description'], r['amount'], f"{r['cost']:.2f}", f"{r.get('profit',0):.2f}", created])

    csv_bytes = output.getvalue().encode('utf-8')
    bio = io.BytesIO(csv_bytes)
    bio.seek(0)
    filename = f"purchase_history_{ptype}_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"
    document = types.InputFile(bio, filename=filename)
    await safe_answer_document(call, document=document, caption=f"Экспорт: {ptype}")

@router.callback_query(F.data == "admin_stats")
async def show_statistics(call: types.CallbackQuery, repo: Repository):
    stats = await repo.get_bot_statistics()
    profit_stats = await repo.get_profit_statistics()
    
    stats_text = (
        f"<b>📊 Статистика бота</b>\n\n"
        f"<b>Пользователи:</b>\n"
        f"› Всего: <code>{stats['total_users']}</code>\n"
        f"› За месяц: <code>{stats['month_users']}</code>\n\n"
        f"<b>Куплено звёзд ⭐:</b>\n"
        f"› За сегодня: <code>{stats['day_stars']:,}</code>\n"
        f"› За месяц: <code>{stats['month_stars']:,}</code>\n"
        f"› За всё время: <code>{stats['total_stars']:,}</code>\n\n"
        f"<b>💰 Финансы:</b>\n"
        f"› Выручка сегодня: <code>{profit_stats['day_revenue']:.2f}₽</code>\n"
        f"› Прибыль сегодня: <code>{profit_stats['day_profit']:.2f}₽</code>\n"
        f"› Выручка за месяц: <code>{profit_stats['month_revenue']:.2f}₽</code>\n"
        f"› Прибыль за месяц: <code>{profit_stats['month_profit']:.2f}₽</code>\n"
        f"› Общая выручка: <code>{profit_stats['total_revenue']:.2f}₽</code>\n"
        f"› Общая прибыль: <code>{profit_stats['total_profit']:.2f}₽</code>"
    )
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="📈 Детальная статистика", callback_data="admin_detailed_stats")],
        [types.InlineKeyboardButton(text="💾 Выгрузить базу данных", callback_data="admin_export_db")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])
    await call.message.edit_text(stats_text, reply_markup=kb)

@router.callback_query(F.data == "admin_detailed_stats")
async def show_detailed_statistics(call: types.CallbackQuery, repo: Repository):
    profit_stats = await repo.get_profit_statistics()
    stats = await repo.get_bot_statistics()
    profit_calc = ProfitCalculator()
    
    day_margin = profit_calc.get_profit_margin(profit_stats['day_revenue'] - profit_stats['day_profit'], profit_stats['day_revenue'])
    month_margin = profit_calc.get_profit_margin(profit_stats['month_revenue'] - profit_stats['month_profit'], profit_stats['month_revenue'])
    total_margin = profit_calc.get_profit_margin(profit_stats['total_revenue'] - profit_stats['total_profit'], profit_stats['total_revenue'])
    ton_rate = await profit_calc.get_ton_rub_rate()

    detailed_text = (
        f"<b>📈 Детальная статистика</b>\n\n"
        f"<b>💹 Маржинальность (от выручки):</b>\n"
        f"› Сегодня: <code>{day_margin:.1f}%</code>\n"
        f"› За месяц: <code>{month_margin:.1f}%</code>\n"
        f"› Общая: <code>{total_margin:.1f}%</code>\n\n"
        f"<b>💱 Курсы:</b>\n"
        f"› TON/RUB: <code>{ton_rate:.2f}₽</code>\n\n"
        f"<b>🎯 Эффективность:</b>\n"
        f"› Прибыль на пользователя: <code>{profit_stats['total_profit'] / max(1, stats.get('total_users', 1)):.2f}₽</code>"
    )
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ К статистике", callback_data="admin_stats")]])
    await call.message.edit_text(detailed_text, reply_markup=kb)

@router.callback_query(F.data == "admin_export_db")
async def export_database(call: types.CallbackQuery, config: Config):
    document = types.FSInputFile(config.database_path)
    await call.message.answer_document(document, caption=f"📊 Экспорт базы данных от {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}")
    await call.answer("База данных выгружена", show_alert=False)

@router.callback_query(F.data == "admin_payment_stats")
async def show_payment_stats(call: types.CallbackQuery, repo: Repository):
    stats = await repo.get_payments_stats()
    methods_text = ""
    method_names = {"lolz": "🔥 Lolz", "cryptobot": "🤖 CryptoBot", "xrocet": "🚀 xRocet", "crystalpay": "💎 CrystalPay"}
    
    for method, data in stats['methods'].items():
        name = method_names.get(method, method)
        methods_text += (f"<b>{name}:</b>\n"
                         f"  - Успешных платежей: {data['paid_payments']} на {data['paid_revenue']:.2f} ₽\n"
                         f"  - Всего создано: {data['total_payments']} на {data['total_revenue']:.2f} ₽\n")
    stats_text = (f"<b>📊 Статистика пополнений</b>\n\n"
                  f"<b>Всего успешно:</b> {stats['paid_payments']} платежей на <b>{stats['paid_revenue']:.2f} ₽</b>\n"
                  f"<b>Всего создано:</b> {stats['total_payments']} счетов на <b>{stats['total_revenue']:.2f} ₽</b>\n\n"
                  f"<b>По системам:</b>\n{methods_text if methods_text else 'Платежей не было.'}")
    await call.message.edit_text(stats_text, reply_markup=get_back_to_admin_keyboard())

@router.callback_query(F.data == "admin_fees")
async def show_fee_settings(call: types.CallbackQuery, repo: Repository):
    fees = await repo.get_multiple_settings(['lolz_fee', 'cryptobot_fee', 'xrocet_fee', 'crystalpay_fee'])
    text = (f"💸 <b>Настройка комиссий</b>\n\n"
            f"🔥 Lolz: <code>{fees.get('lolz_fee', 'N/A')}%</code>\n"
            f"🤖 CryptoBot: <code>{fees.get('cryptobot_fee', 'N/A')}%</code>\n"
            f"🚀 xRocet: <code>{fees.get('xrocet_fee', 'N/A')}%</code>\n"
            f"💎 CrystalPay: <code>{fees.get('crystalpay_fee', 'N/A')}%</code>\n\n"
            "Выберите систему для изменения:")
    await call.message.edit_text(text, reply_markup=get_fee_settings_keyboard())


@router.callback_query(PurchaseHistoryCallback.filter())
async def show_purchase_history(call: types.CallbackQuery, callback_data: PurchaseHistoryCallback, repo: Repository):
    page = callback_data.page or 1
    ptype = callback_data.ptype or 'all'
    PAGE_SIZE = 10

    # build count query
    if ptype == 'all':
        total_row = await repo.db.fetchrow("SELECT COUNT(*) as cnt FROM purchase_history")
        rows = await repo.db.fetch("SELECT * FROM purchase_history ORDER BY created_at DESC LIMIT $1 OFFSET $2", PAGE_SIZE, (page-1)*PAGE_SIZE)
    else:
        total_row = await repo.db.fetchrow("SELECT COUNT(*) as cnt FROM purchase_history WHERE purchase_type = $1", ptype)
        rows = await repo.db.fetch("SELECT * FROM purchase_history WHERE purchase_type = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3", ptype, PAGE_SIZE, (page-1)*PAGE_SIZE)

    total = int(total_row['cnt']) if total_row and total_row['cnt'] is not None else 0
    max_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    text_lines = [f"📜 История покупок — фильтр: <b>{ptype}</b> — всего: {total}\n"]
    if not rows:
        text_lines.append("Нет записей.")
    else:
        for r in rows:
            ts = r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else '-'
            text_lines.append(f"• ID:{r['id']} User:{r['user_id']} Type:{r['purchase_type']} Item:{r['item_description']} Amount:{r['amount']} Cost:{r['cost']:.2f} ₽ [{ts}]")

    # keyboard: filters and pagination
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb_rows = []
    # filters
    filter_opts = [('all','Все'), ('vpn','VPN'), ('vpn_autotopup','Автодокупка'), ('vpn_gb','Докупка GB'), ('vpn_premium','Premium'), ('stars','Stars')]
    filter_buttons = [InlineKeyboardButton(text=label, callback_data=PurchaseHistoryCallback(page=1, ptype=code).pack()) for code,label in filter_opts]
    # arrange filter buttons in two rows
    kb_rows.append(filter_buttons[:3])
    kb_rows.append(filter_buttons[3:])

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text='⬅️', callback_data=PurchaseHistoryCallback(page=page-1, ptype=ptype).pack()))
    nav_row.append(InlineKeyboardButton(text=f"{page}/{max_page}", callback_data='ignore'))
    if page < max_page:
        nav_row.append(InlineKeyboardButton(text='➡️', callback_data=PurchaseHistoryCallback(page=page+1, ptype=ptype).pack()))
    kb_rows.append(nav_row)
    kb_rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data='admin_panel')])
    # export button
    kb_rows.append([InlineKeyboardButton(text='⬇️ Выгрузить CSV', callback_data=f'purchase_history_export|{ptype}')])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await call.message.edit_text('\n'.join(text_lines), reply_markup=kb)

@router.callback_query(F.data.startswith("set_fee_"))
async def set_fee_start(call: types.CallbackQuery, state: FSMContext):
    payment_method = call.data.split("_")[2]
    await state.set_state(AdminFeeStates.waiting_for_fee)
    await state.update_data(payment_method=payment_method)
    
    method_names = {"lolz": "Lolz", "cryptobot": "CryptoBot", "xrocet": "xRocet", "crystalpay": "CrystalPay"}
    await call.message.edit_text(f"Введите новую комиссию для <b>{method_names.get(payment_method)}</b> в процентах (например, 7.5):",
                                  reply_markup=get_back_to_admin_keyboard())

@router.message(AdminFeeStates.waiting_for_fee)
async def process_new_fee(message: types.Message, state: FSMContext, repo: Repository):
    try:
        fee = float(message.text.replace(",", "."))
        if not (0 <= fee <= 100): raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректное число от 0 до 100.")
        return

    data = await state.get_data()
    payment_method = data["payment_method"]
    await repo.update_setting(f"{payment_method}_fee", fee)
    
    await message.answer(f"✅ Комиссия для <b>{payment_method.capitalize()}</b> установлена на <b>{fee}%</b>.")
    await state.clear()
    
    fees = await repo.get_multiple_settings(['lolz_fee', 'cryptobot_fee', 'xrocet_fee', 'crystalpay_fee'])
    text = (f"💸 <b>Настройка комиссий</b>\n\n"
            f"🔥 Lolz: <code>{fees.get('lolz_fee', 'N/A')}%</code>\n"
            f"🤖 CryptoBot: <code>{fees.get('cryptobot_fee', 'N/A')}%</code>\n"
            f"🚀 xRocet: <code>{fees.get('xrocet_fee', 'N/A')}%</code>\n"
            f"💎 CrystalPay: <code>{fees.get('crystalpay_fee', 'N/A')}%</code>\n\n"
            "Выберите систему для изменения:")
    await message.answer(text, reply_markup=get_fee_settings_keyboard())