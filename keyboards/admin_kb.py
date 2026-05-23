from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards.user_kb import PREMIUM_PLANS

class MaintenanceCallback(CallbackData, prefix="maint"):
    action: str

class UserPaymentsCallback(CallbackData, prefix="user_payments"):
    page: int

class AdminUserNavCallback(CallbackData, prefix="admin_user_nav"):
    action: str
    target_user_id: int

class AdminVPNCallback(CallbackData, prefix="admin_vpn"):
    action: str
    client_uuid: str

class PurchaseHistoryCallback(CallbackData, prefix="purchase_history"):
    page: int
    ptype: str

def get_admin_panel_kb(is_maintenance: bool) -> InlineKeyboardMarkup:
    maint_text = "🟡 Тех. перерыв: Вкл" if is_maintenance else "⚪️ Тех. перерыв: Выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Управление пользователями", callback_data="admin_users"), InlineKeyboardButton(text="🎟️ Промокоды", callback_data="admin_promos")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast"), InlineKeyboardButton(text="📈 Управление ценами", callback_data="admin_prices")],
        [InlineKeyboardButton(text="💸 Настроить комиссии", callback_data="admin_fees"), InlineKeyboardButton(text="📊 Статистика платежек", callback_data="admin_payment_stats")],
        [InlineKeyboardButton(text="💳 История покупок", callback_data=PurchaseHistoryCallback(page=1, ptype='all').pack())],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings"), InlineKeyboardButton(text=maint_text, callback_data=MaintenanceCallback(action="toggle").pack())],
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats"), InlineKeyboardButton(text="🔗 Fragment статус", callback_data="admin_fragment_status")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")],
    ])

def get_fee_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Комиссия Lolz", callback_data="set_fee_lolz")],
        [InlineKeyboardButton(text="🤖 Комиссия CryptoBot", callback_data="set_fee_cryptobot")],
        [InlineKeyboardButton(text="🚀 Комиссия xRocet", callback_data="set_fee_xrocet")],
        [InlineKeyboardButton(text="💎 Комиссия CrystalPay", callback_data="set_fee_crystalpay")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])

def get_back_to_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]])

def get_user_info_kb(is_blocked: bool) -> InlineKeyboardMarkup:
    block_btn_text = "🚫 Заблокировать" if not is_blocked else "✅ Разблокировать"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выдать баланс", callback_data="admin_give_balance"), InlineKeyboardButton(text="💳 Списать баланс", callback_data="admin_take_balance")],
        [InlineKeyboardButton(text="🔐 Выдать ВПН", callback_data="admin_give_vpn")],
        [InlineKeyboardButton(text="🔐 VPN клиенты", callback_data="admin_vpn_clients")],
        [InlineKeyboardButton(text=block_btn_text, callback_data="admin_toggle_block"), InlineKeyboardButton(text="💸 Платежи", callback_data=UserPaymentsCallback(page=1).pack())],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
    ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выдать баланс", callback_data="admin_give_balance"), InlineKeyboardButton(text="💸 Отнять баланс", callback_data="admin_take_balance")],
        [InlineKeyboardButton(text="🧾 Чеки", callback_data=UserPaymentsCallback(page=1).pack()), InlineKeyboardButton(text=block_btn_text, callback_data="admin_toggle_block")],
        [InlineKeyboardButton(text="⬅️ Назад в админку", callback_data="admin_panel")]
    ])

def get_user_payments_kb(page: int, max_page: int, target_user_id: int) -> InlineKeyboardMarkup:
    kb_rows = []
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=UserPaymentsCallback(page=page-1).pack()))
    nav_row.append(InlineKeyboardButton(text=f"{page}/{max_page}", callback_data="ignore"))
    if page < max_page:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=UserPaymentsCallback(page=page+1).pack()))
    if nav_row:
        kb_rows.append(nav_row)
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад к профилю", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=target_user_id).pack())])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)

def get_admin_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Тексты", callback_data="settings_texts_menu")],
        [InlineKeyboardButton(text="📢 Новостной канал", callback_data="settings_channel_menu")],
        [InlineKeyboardButton(text="🆘 Поддержка", callback_data="settings_support_menu")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])

def get_settings_texts_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Текст /start", callback_data="settings_edit_text_start_text")],
        [InlineKeyboardButton(text="Текст после покупки", callback_data="settings_edit_text_purchase_success_text")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings")]
    ])

def get_settings_support_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Изменить контакт", callback_data="settings_edit_support")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings")]
    ])

def get_settings_channel_kb(is_forced: bool, channel_is_set: bool) -> InlineKeyboardMarkup:
    force_text = "🔴 Обязательная подписка: Вкл" if is_forced else "🟢 Обязательная подписка: Выкл"
    channel_button_text = "❌ Отвязать канал" if channel_is_set else "🔗 Привязать канал"
    channel_callback_data = "settings_unset_channel" if channel_is_set else "settings_set_channel"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=channel_button_text, callback_data=channel_callback_data)],
        [InlineKeyboardButton(text=force_text, callback_data="settings_toggle_subscribe")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_settings")]
    ])

def get_promos_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать промокод", callback_data="promo_create")],
        [InlineKeyboardButton(text="📋 Активные промокоды", callback_data="promo_active")],
        [InlineKeyboardButton(text="🗑️ Удалить промокод", callback_data="promo_delete")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")],
    ])

def get_prices_menu_kb(dynamic_button_text: str = "🔄 Динамическая цена", dynamic_button_callback: str = "toggle_star_dynamic_price") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=dynamic_button_text, callback_data=dynamic_button_callback)],
        [InlineKeyboardButton(text="⭐ Цены на звезды", callback_data="price_stars")],
        [InlineKeyboardButton(text="👤 Юзернейм Fragment", callback_data="price_stars_quote_username")],
        [InlineKeyboardButton(text="📉 Мин. цена звезды", callback_data="price_stars_min"), InlineKeyboardButton(text="📈 Макс. цена звезды", callback_data="price_stars_max")],
        [InlineKeyboardButton(text="💎 Цены на премиум", callback_data="price_premium")],
        [InlineKeyboardButton(text="🔐 Цена на ВПН", callback_data="price_vpn")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])

def get_premium_prices_kb(premium_prices: list) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(text=f"{plan['name']} — {premium_prices[i]}₽", callback_data=f"price_premium_{i}")] for i, plan in enumerate(PREMIUM_PLANS)]
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_prices")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_broadcast_constructor_kb(data: dict) -> InlineKeyboardMarkup:
    button_text = data.get("button_text")
    add_edit_button = InlineKeyboardButton(text="🔗 Изменить кнопку" if button_text else "🔗 Добавить кнопку", callback_data="broadcast_add_button")
    kb = [[InlineKeyboardButton(text="📝 Изменить текст", callback_data="broadcast_edit_text"), InlineKeyboardButton(text="🖼️ Изменить/Добавить медиа", callback_data="broadcast_edit_media")]]
    button_row = [add_edit_button]
    if button_text:
        button_row.append(InlineKeyboardButton(text="🗑️ Удалить кнопку", callback_data="broadcast_delete_button"))
    kb.append(button_row)
    kb.extend([
        [InlineKeyboardButton(text="👀 Предпросмотр", callback_data="broadcast_preview")],
        [InlineKeyboardButton(text="✅ Отправить рассылку", callback_data="broadcast_send")],
        [InlineKeyboardButton(text="❌ Отменить рассылку", callback_data="broadcast_cancel")]
    ])
    return InlineKeyboardMarkup(inline_keyboard=kb)