import logging
from aiogram import F, Router, types, Bot
from aiogram.fsm.context import FSMContext

from services.repository import Repository
from states.admin import AdminSettingsStates
from keyboards.admin_kb import (
    get_admin_panel_kb, get_admin_settings_kb, get_settings_texts_kb, get_settings_support_kb,
    get_settings_channel_kb, MaintenanceCallback
)

router = Router()

@router.callback_query(MaintenanceCallback.filter(F.action == "toggle"))
async def toggle_maintenance_mode(call: types.CallbackQuery, repo: Repository):
    is_maintenance_old = await repo.get_setting('maintenance_mode') == '1'
    new_status = not is_maintenance_old
    
    await repo.update_setting('maintenance_mode', '1' if new_status else '0')
    
    status_text = "ВКЛЮЧЕН" if new_status else "ВЫКЛЮЧЕН"
    await call.answer(f"Режим технического перерыва {status_text}", show_alert=True)
    
    is_maintenance_new = await repo.get_setting('maintenance_mode') == '1'
    await call.message.edit_reply_markup(reply_markup=get_admin_panel_kb(is_maintenance_new))

@router.callback_query(F.data == "admin_settings")
async def admin_settings_menu(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("<b>⚙️ Настройки бота</b>", reply_markup=get_admin_settings_kb())

@router.callback_query(F.data == "settings_texts_menu")
async def settings_texts_menu(call: types.CallbackQuery):
    await call.message.edit_text("<b>📝 Управление текстами</b>", reply_markup=get_settings_texts_kb())

@router.callback_query(F.data.startswith("settings_edit_text_"))
async def settings_edit_text_start(call: types.CallbackQuery, state: FSMContext):
    text_key = call.data.replace("settings_edit_text_", "")
    state_map = {
        "start_text": AdminSettingsStates.waiting_for_start_text,
        "purchase_success_text": AdminSettingsStates.waiting_for_purchase_text
    }
    
    placeholders_info = (
        "\n\n<b>Доступные переменные:</b>\n"
        "<code>{ID}</code> - ID пользователя\n"
        "<code>{@username}</code> - @username пользователя\n"
        "<code>{full_name}</code> - Полное имя пользователя"
    )
    
    await state.update_data(text_key=text_key)
    await state.set_state(state_map[text_key])
    await call.message.edit_text(
        f"Отправьте новый текст для '{text_key}'.\nПоддерживается HTML-разметка.{placeholders_info}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_texts_menu")]])
    )

@router.message(AdminSettingsStates.waiting_for_start_text)
@router.message(AdminSettingsStates.waiting_for_purchase_text)
async def settings_process_new_text(message: types.Message, state: FSMContext, repo: Repository):
    data = await state.get_data()
    text_key = data.get("text_key")
    await repo.update_setting(text_key, message.html_text)
    await state.clear()
    await message.answer(f"✅ Текст для '{text_key}' успешно обновлен.")
    await message.answer("<b>📝 Управление текстами</b>", reply_markup=get_settings_texts_kb())
    
@router.callback_query(F.data == "settings_support_menu")
async def settings_support_menu(call: types.CallbackQuery, repo: Repository):
    contact = await repo.get_setting('support_contact') or "Не задан"
    await call.message.edit_text(f"<b>🆘 Управление поддержкой</b>\n\nТекущий контакт: @{contact}", reply_markup=get_settings_support_kb())

@router.callback_query(F.data == "settings_edit_support")
async def settings_edit_support_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsStates.waiting_for_support_contact)
    await call.message.edit_text(
        "Отправьте юзернейм для поддержки (например, @username или просто username).",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_support_menu")]])
    )
    
@router.message(AdminSettingsStates.waiting_for_support_contact)
async def settings_process_new_support(message: types.Message, state: FSMContext, repo: Repository):
    contact = message.text.strip().lstrip('@')
    await repo.update_setting('support_contact', contact)
    await state.clear()
    await message.answer(f"✅ Контакт поддержки обновлен на @{contact}.")
    await message.answer(f"<b>🆘 Управление поддержкой</b>\n\nТекущий контакт: @{contact}", reply_markup=get_settings_support_kb())

# --- VPN tariffs & limits ---

# (key, label, unit, type, default)
VPN_SETTINGS = [
    ('vpn_standard_price', 'Цена «Стандартный»', '₽', 'float', '100'),
    ('vpn_premium_price', 'Цена «Premium+»', '₽', 'float', '400'),
    ('vpn_device_price', 'Устройство (в мес.)', '₽', 'float', '30'),
    ('vpn_device_limit_default', 'Устройств по умолчанию', ' шт', 'int', '3'),
    ('vpn_trial_days', 'Триал: дней', ' дн', 'int', '3'),
    ('vpn_trial_gb', 'Триал: трафик', ' ГБ', 'int', '5'),
]


def _vpn_settings_kb(values: dict, trial_enabled: bool) -> types.InlineKeyboardMarkup:
    rows = []
    for key, label, unit, _type, default in VPN_SETTINGS:
        val = values.get(key) or default
        rows.append([types.InlineKeyboardButton(text=f"{label}: {val}{unit} ✏️", callback_data=f"settings_vpn_edit_{key}")])
    rows.append([types.InlineKeyboardButton(text=f"🎁 Триал: {'Вкл 🟢' if trial_enabled else 'Выкл 🔴'}", callback_data="settings_vpn_toggle_trial")])
    rows.append([types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings")])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def _vpn_settings_view(repo: Repository):
    keys = [k for k, *_ in VPN_SETTINGS] + ['vpn_trial_enabled']
    values = await repo.get_multiple_settings(keys)
    trial_enabled = (values.get('vpn_trial_enabled') or '1') == '1'
    text = "<b>🛜 VPN-тарифы и лимиты</b>\n\nНажмите параметр, чтобы изменить значение."
    return text, _vpn_settings_kb(values, trial_enabled)


@router.callback_query(F.data == "settings_vpn_menu")
async def settings_vpn_menu(call: types.CallbackQuery, repo: Repository, state: FSMContext):
    await state.clear()
    text, kb = await _vpn_settings_view(repo)
    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "settings_vpn_toggle_trial")
async def settings_vpn_toggle_trial(call: types.CallbackQuery, repo: Repository):
    cur = (await repo.get_setting('vpn_trial_enabled') or '1') == '1'
    await repo.update_setting('vpn_trial_enabled', '0' if cur else '1')
    await call.answer(f"Триал {'выключен' if cur else 'включён'}")
    _, kb = await _vpn_settings_view(repo)
    await call.message.edit_reply_markup(reply_markup=kb)


@router.callback_query(F.data.startswith("settings_vpn_edit_"))
async def settings_vpn_edit_start(call: types.CallbackQuery, state: FSMContext):
    key = call.data.replace("settings_vpn_edit_", "")
    spec = next((s for s in VPN_SETTINGS if s[0] == key), None)
    if not spec:
        await call.answer("Неизвестный параметр", show_alert=True)
        return
    _, label, _unit, vtype, _default = spec
    await state.update_data(vpn_key=key, vpn_type=vtype)
    await state.set_state(AdminSettingsStates.waiting_for_vpn_value)
    hint = "целое число ≥ 0" if vtype == 'int' else "число ≥ 0 (можно с копейками)"
    await call.message.edit_text(
        f"Введите новое значение для «{label}» ({hint}):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_vpn_menu")]]),
    )


@router.message(AdminSettingsStates.waiting_for_vpn_value)
async def settings_vpn_process(message: types.Message, state: FSMContext, repo: Repository):
    data = await state.get_data()
    key = data.get('vpn_key')
    vtype = data.get('vpn_type')
    if not key:
        await state.clear()
        return
    raw = (message.text or '').strip().replace(',', '.')
    try:
        num = float(raw)
        if num < 0:
            raise ValueError
        stored = str(int(num)) if vtype == 'int' else str(round(num, 2))
    except ValueError:
        await message.answer("❗ Введите корректное неотрицательное число.")
        return
    await repo.update_setting(key, stored)
    await state.clear()
    text, kb = await _vpn_settings_view(repo)
    await message.answer(f"✅ Сохранено: <b>{stored}</b>")
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == "settings_channel_menu")
async def settings_channel_menu(call: types.CallbackQuery, repo: Repository):
    settings = await repo.get_multiple_settings(['news_channel_link', 'force_subscribe'])
    channel_link = settings.get('news_channel_link')
    channel_display = channel_link or "Не задан"
    is_forced = settings.get('force_subscribe') == '1'
    force_status_text = "Включена" if is_forced else "Выключена"
    
    text = (
        f"<b>📢 Управление новостным каналом</b>\n\n"
        f"Текущий канал: {channel_display}\n"
        f"Обязательная подписка: <b>{force_status_text}</b>"
    )
    
    await call.message.edit_text(text, reply_markup=get_settings_channel_kb(is_forced, bool(channel_link)))

@router.callback_query(F.data == "settings_set_channel")
async def settings_set_channel_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminSettingsStates.waiting_for_channel_forward)
    await call.message.edit_text(
        "Чтобы привязать канал, добавьте бота в администраторы вашего канала, а затем перешлите сюда любой пост из него.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="settings_channel_menu")]])
    )

@router.callback_query(F.data == "settings_unset_channel")
async def settings_unset_channel(call: types.CallbackQuery, repo: Repository):
    await repo.update_setting('news_channel_id', '')
    await repo.update_setting('news_channel_link', '')
    await call.answer("✅ Канал успешно отвязан.", show_alert=True)
    await settings_channel_menu(call, repo)

@router.message(AdminSettingsStates.waiting_for_channel_forward, F.forward_from_chat)
async def settings_process_channel_forward(message: types.Message, state: FSMContext, repo: Repository, bot: Bot):
    if message.forward_from_chat.type != 'channel':
        await message.answer("❗️Пожалуйста, перешлите сообщение именно из канала.")
        return
        
    try:
        invite_link = await bot.create_chat_invite_link(message.forward_from_chat.id)
        await repo.update_setting('news_channel_id', message.forward_from_chat.id)
        await repo.update_setting('news_channel_link', invite_link.invite_link)
        await state.clear()
        
        await message.answer(f"✅ Канал '{message.forward_from_chat.title}' успешно привязан.")
        
        settings = await repo.get_multiple_settings(['news_channel_link', 'force_subscribe'])
        channel_link = settings.get('news_channel_link')
        channel_display = channel_link or "Не задан"
        is_forced = settings.get('force_subscribe') == '1'
        force_status_text = "Включена" if is_forced else "Выключена"
        
        text = (
            f"<b>📢 Управление новостным каналом</b>\n\n"
            f"Текущий канал: {channel_display}\n"
            f"Обязательная подписка: <b>{force_status_text}</b>"
        )
        await message.answer(text, reply_markup=get_settings_channel_kb(is_forced, bool(channel_link)))

    except Exception as e:
        logging.error(f"Failed to set channel: {e}")
        await message.answer("❌ Не удалось привязать канал. Убедитесь, что бот является администратором с правом 'Приглашать пользователей'.")

@router.callback_query(F.data == "settings_toggle_subscribe")
async def settings_toggle_subscribe(call: types.CallbackQuery, repo: Repository):
    is_forced = await repo.get_setting('force_subscribe') == '1'
    new_status = not is_forced
    await repo.update_setting('force_subscribe', '1' if new_status else '0')
    
    await call.answer(f"Обязательная подписка {'ВКЛЮЧЕНА' if new_status else 'ВЫКЛЮЧЕНА'}", show_alert=True)
    await settings_channel_menu(call, repo)