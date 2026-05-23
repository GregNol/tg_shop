import uuid
from datetime import datetime, timedelta
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
import logging

from services.repository import Repository
from keyboards import user_kb
from config import Config
from services.xui import xui_from_config
from utils.safe_message import safe_delete_and_send_photo, safe_edit_message
from services.fragment_sender import FragmentSender
from services.profit_calculator import ProfitCalculator
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot

router = Router()

@router.callback_query(F.data == "vpn_menu")
async def vpn_menu_callback(call: types.CallbackQuery, repo: Repository, config: Config):
    user_id = call.from_user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)
    has_subs = len(subs) > 0
    vpn_price = float(await repo.get_setting('vpn_standard_price') or 100)
    premium_price = int(await repo.get_setting('vpn_premium_price') or 400)

    if has_subs:
        xui = xui_from_config(config)
        
        text = "<b>🔐 Твоя подписка ВПН:</b>\n\n"
        for idx, sub in enumerate(subs, 1):
            status = "✅ Активна" if sub['is_active'] else "❌ Выключена"
            expiry = sub['expires_at'].strftime('%Y-%m-%d %H:%M') if sub['expires_at'] else "Бессрочно"
            sub_url = f"{xui.host_url}/sub/{sub['client_uuid']}"
            text += f"Тариф: <b>«{sub['tariff_name']}»</b>\n"
            text += f"Статус: {status}\n"
            text += f"Истекает: {expiry}\n"
            text += f"Ссылка: <code>{sub_url}</code>\n\n"
            break # Пока предполагаем 1 активную подписку
            
        await safe_delete_and_send_photo(
            call, config, config.visuals.img_url_main,
            text,
            user_kb.get_vpn_menu_kb(has_subs, vpn_price, premium_price)
        )
    else:
        await safe_delete_and_send_photo(
            call, config, config.visuals.img_url_main,
            "<b>🔐 ВПН Сервис</b>\n\nУ вас еще нет подписки. Купите её для безопасного доступа к сети:",
            user_kb.get_vpn_menu_kb(has_subs, vpn_price, premium_price)
        )

@router.callback_query(F.data == "buy_vpn_plan_standard_1")
async def buy_vpn_plan_callback(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender):
    vpn_price = float(await repo.get_setting('vpn_standard_price') or 100)
    user_db = await repo.get_user(call.from_user.id)
    
    if float(user_db["balance"]) < vpn_price:
        await safe_edit_message(call, text=f"Недостаточно средств! Не хватает: <b>{vpn_price - float(user_db['balance'])}₽</b>", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="profile_topup_menu")]]))
        return

    # Deduct balance
    await repo.update_user_balance(call.from_user.id, vpn_price, operation='sub')
    
    # Process XUI
    xui = xui_from_config(config)
    
    await xui.login()
    
    # Check if user already has a subscription to extend or create a new one
    subs = await repo.get_user_vpn_subscriptions(call.from_user.id)
    active_sub = None
    for sub in subs:
        if sub['tariff_name'] == 'Стандартный':
            active_sub = sub
            break
    
    days_to_add = 30
    duration_ms = days_to_add * 24 * 60 * 60 * 1000
    
    if active_sub:
        # Extend
        current_expiry = active_sub['expires_at'] if active_sub['expires_at'] is not None else None
        if current_expiry and current_expiry > datetime.utcnow():
            new_expiry = current_expiry + timedelta(days=days_to_add)
        else:
            new_expiry = datetime.utcnow() + timedelta(days=days_to_add)

        # Add to XUI
        email = active_sub['email']
        client_uuid = active_sub['client_uuid']
        inbound_id = active_sub['inbound_id']
        
        # updateClient doesn't magically add time, it sets absolute expiry time
        new_expiry_ms = int(new_expiry.timestamp() * 1000)
        
        success = await xui.update_client(
            inbound_id=inbound_id,
            client_uuid=client_uuid,
            email=email,
            enable=True,
            expire_time=new_expiry_ms
        )
        
        if success:
            await repo.extend_vpn_subscription(client_uuid, new_expires_at=new_expiry)
            await safe_edit_message(call, text=f"✅ Ваша подписка успешно продлена до {new_expiry.strftime('%Y-%m-%d %H:%M')}", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]]))
            # notify admins
            profit_calc = ProfitCalculator()
            profit_text = (
                f"🔐 <b>Новая покупка VPN</b>\n\n"
                f"👤 Покупатель: @{call.from_user.username or call.from_user.id}\n"
                f"📅 Тариф: Стандартный (продление)\n"
                f"💵 Выручка: {vpn_price:.2f}₽\n"
                f"📆 До: {new_expiry.strftime('%Y-%m-%d %H:%M')}"
            )
            await fragment_sender._notify_admins(profit_text)
            try:
                await repo.add_purchase_to_history(call.from_user.id, 'vpn_standard', f'Standard extend to {new_expiry.strftime("%Y-%m-%d %H:%M")}', days_to_add, vpn_price, 0.0)
            except Exception:
                logging.exception('Failed to write purchase history for standard extend')
        else:
            # refund
            await repo.update_user_balance(call.from_user.id, vpn_price, operation='add')
            await safe_edit_message(call, text="❌ Ошибка при продлении в XUI. Средства возвращены.")
            try:
                await fragment_sender._notify_admins(f"❌ Ошибка продления VPN для @{call.from_user.username or call.from_user.id}: ошибка при обновлении клиента в XUI.")
            except Exception:
                logging.exception("Failed to notify admins about VPN extend failure")
            
    else:
        # Create new
        client_email = f"{call.from_user.id}_{call.from_user.username or 'user'}"
        new_expiry = datetime.utcnow() + timedelta(days=days_to_add)
        new_expiry_ms = int(new_expiry.timestamp() * 1000)
        inbound_id = config.xui.inbound_id
        
        client_id = await xui.add_client(
            inbound_id=inbound_id,
            email=client_email,
            expire_time=new_expiry_ms
        )

        if client_id:
            try:
                await repo.create_vpn_subscription(
                    user_id=call.from_user.id,
                    client_uuid=client_id,
                    email=client_email,
                    inbound_id=inbound_id,
                    target_tariff_name='Стандартный',
                    total_gb=0,
                    expires_at=new_expiry
                )
            except Exception:
                logging.exception(
                    "User VPN standard create failed: user_id=%s client_uuid=%s email=%s inbound_id=%s expires_at=%s",
                    call.from_user.id,
                    client_id,
                    client_email,
                    inbound_id,
                    new_expiry,
                )
                await repo.update_user_balance(call.from_user.id, vpn_price, operation='add')
                await safe_edit_message(call, text="❌ Ошибка записи подписки в БД. Средства возвращены. Подробности в логах.")
                await xui.close()
                return
            sub_url = f"{xui.host_url}/sub/{client_id}"
            kb = user_kb.get_vpn_menu_kb(True, vpn_price, int(await repo.get_setting('vpn_premium_price') or 400))
            await safe_edit_message(call, text=f"✅ Подписка ВПН успешно оформлена до {new_expiry.strftime('%Y-%m-%d %H:%M')}\nВаша ссылка для подключения:\n<code>{sub_url}</code>", reply_markup=kb)
            # notify admins
            profit_text = (
                f"🔐 <b>Новая покупка VPN</b>\n\n"
                f"👤 Покупатель: @{call.from_user.username or call.from_user.id}\n"
                f"📅 Тариф: Стандартный (новая)\n"
                f"💵 Выручка: {vpn_price:.2f}₽\n"
                f"🔗 Ссылка: {sub_url}"
            )
            await fragment_sender._notify_admins(profit_text)
            try:
                await repo.add_purchase_to_history(call.from_user.id, 'vpn_standard', f'Standard new sub {sub_url}', days_to_add, vpn_price, 0.0)
            except Exception:
                logging.exception('Failed to write purchase history for standard new')
        else:
            await repo.update_user_balance(call.from_user.id, vpn_price, operation='add')
            await safe_edit_message(call, text="❌ Ошибка при создании в XUI. Средства возвращены.")
            try:
                await fragment_sender._notify_admins(f"❌ Ошибка создания VPN для @{call.from_user.username or call.from_user.id}: не удалось создать клиента в XUI при оформлении новой подписки.")
            except Exception:
                logging.exception("Failed to notify admins about VPN create failure")
            
    await xui.close()


@router.callback_query(F.data == "buy_vpn_plan_premium_1")
async def buy_vpn_premium_callback(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender):
    """Купить премиум-тариф, создающий клиентов в обеих панелях.
    Второй клиент получает ограничение трафика в 100 ГБ.
    """
    premium_price = float(await repo.get_setting('vpn_premium_price') or 400)
    user_db = await repo.get_user(call.from_user.id)
    if float(user_db["balance"]) < premium_price:
        await safe_edit_message(call, text=f"Недостаточно средств! Не хватает: <b>{premium_price - float(user_db['balance'])}₽</b>", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="profile_topup_menu")]]))
        return

    # Deduct balance
    await repo.update_user_balance(call.from_user.id, premium_price, operation='sub')

    logging.info(f"User {call.from_user.id} attempts to buy Premium+ for {premium_price} RUB")
    # Ensure secondary panel configured
    if not getattr(config, 'xui2', None):
        await repo.update_user_balance(call.from_user.id, premium_price, operation='add')
        await safe_edit_message(call, text="❌ Вторая панель не настроена. Администратор должен задать XUI2_* переменные.")
        try:
            await fragment_sender._notify_admins(f"❌ Пользователь @{call.from_user.username or call.from_user.id} попытался купить Premium+, но в конфиге не настроена вторая панель (XUI2_*).")
        except Exception:
            logging.exception("Failed to notify admins about missing xui2 config")
        return

    xui_primary = xui_from_config(config)
    xui_secondary = xui_from_config(config, secondary=True)

    await xui_primary.login()
    await xui_secondary.login()

    days_to_add = 30
    new_expiry = datetime.utcnow() + timedelta(days=days_to_add)
    new_expiry_ms = int(new_expiry.timestamp() * 1000)

    user = call.from_user
    client_email_p1 = f"{user.id}_{user.username or 'user'}_p1"
    client_email_p2 = f"{user.id}_{user.username or 'user'}_p2"

    # create primary client
    inbound_id_p1 = config.xui.inbound_id
    client_id_p1 = await xui_primary.add_client(
        inbound_id=inbound_id_p1,
        email=client_email_p1,
        expire_time=new_expiry_ms
    )

    # create secondary client with 100GB limit
    inbound_id_p2 = config.xui2.inbound_id
    client_id_p2 = await xui_secondary.add_client(
        inbound_id=inbound_id_p2,
        email=client_email_p2,
        expire_time=new_expiry_ms,
        total_gb=100
    )

    if not client_id_p1 or not client_id_p2:
        # refund
        await repo.update_user_balance(call.from_user.id, premium_price, operation='add')
        logging.error(f"Failed to create premium clients for user {call.from_user.id}: p1={client_id_p1}, p2={client_id_p2}")
        await safe_edit_message(call, text="❌ Ошибка при создании клиентов в панелях. Средства возвращены.")
        try:
            await fragment_sender._notify_admins(f"❌ Ошибка создания Premium+ для @{call.from_user.username or call.from_user.id}: p1={client_id_p1}, p2={client_id_p2}")
        except Exception:
            logging.exception("Failed to notify admins about premium client creation failure")
        await xui_primary.close()
        await xui_secondary.close()
        return

    # persist to DB: create subscription (primary client) and then add secondary client linked to it
    try:
        sub = await repo.create_vpn_subscription(
            user_id=call.from_user.id,
            client_uuid=client_id_p1,
            email=client_email_p1,
            inbound_id=inbound_id_p1,
            target_tariff_name='Premium+',
            total_gb=0,
            expires_at=new_expiry
        )

        # create secondary client record linked to same subscription
        sec_client = await repo.create_vpn_subscription_client(
            subscription_id=sub['subscription_id'],
            client_uuid=client_id_p2,
            email=client_email_p2,
            inbound_id=inbound_id_p2,
            panel='secondary',
            total_gb=100
        )
    except Exception:
        logging.exception(
            "User VPN premium DB create failed: user_id=%s p1_uuid=%s p2_uuid=%s p1_inbound=%s p2_inbound=%s expires_at=%s",
            call.from_user.id,
            client_id_p1,
            client_id_p2,
            inbound_id_p1,
            inbound_id_p2,
            new_expiry,
        )
        await repo.update_user_balance(call.from_user.id, premium_price, operation='add')
        await safe_edit_message(call, text="❌ Ошибка записи Premium+ подписки в БД. Средства возвращены. Подробности в логах.")
        await xui_primary.close()
        await xui_secondary.close()
        return
    logging.info(f"Premium+ subscription created for user {call.from_user.id}: primary={client_id_p1}, secondary={client_id_p2}, sub_id={sub['subscription_id']}")

    sub_url_p1 = f"{xui_primary.host_url}/sub/{client_id_p1}"
    sub_url_p2 = f"{xui_secondary.host_url}/sub/{client_id_p2}"

    kb = user_kb.get_vpn_menu_kb(True, float(await repo.get_setting('vpn_standard_price') or 100), int(await repo.get_setting('vpn_premium_price') or 400))
    await safe_edit_message(call, text=(f"✅ Подписка Premium+ успешно оформлена до {new_expiry.strftime('%Y-%m-%d %H:%M')}\n\n"
                                         f"Ссылка 1: <code>{sub_url_p1}</code>\n"
                                         f"Ссылка 2: <code>{sub_url_p2}</code>"), reply_markup=kb)

    # notify admins
    profit_text = (
        f"🔐 <b>Новая покупка VPN</b>\n\n"
        f"👤 Покупатель: @{call.from_user.username or call.from_user.id}\n"
        f"📅 Тариф: Premium+\n"
        f"💵 Выручка: {premium_price:.2f}₽\n"
        f"🔗 Ссылка1: {sub_url_p1}\n"
        f"🔗 Ссылка2: {sub_url_p2}"
    )
    await fragment_sender._notify_admins(profit_text)
    try:
        await repo.add_purchase_to_history(call.from_user.id, 'vpn_premium', f'Premium+ sub {sub["subscription_id"]}', days_to_add, premium_price, 0.0)
    except Exception:
        logging.exception('Failed to write purchase history for premium purchase')

    await xui_primary.close()
    await xui_secondary.close()


@router.callback_query(F.data == "upgrade_vpn_to_premium")
async def upgrade_vpn_to_premium(call: types.CallbackQuery, repo: Repository, config: Config):
    user_id = call.from_user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)
    if not subs:
        await call.answer("У вас нет подписок для апгрейда.", show_alert=True)
        return

    # choose active subscription (prefer Standard)
    active_sub = None
    for sub in subs:
        if sub['tariff_name'] == 'Стандартный':
            active_sub = sub
            break
    if not active_sub:
        active_sub = subs[0]

    expires_at = active_sub['expires_at'] if active_sub['expires_at'] is not None else None
    if not expires_at or expires_at <= datetime.utcnow():
        await call.answer('Ваша подписка уже истекла. Купите Premium+ обычным способом.', show_alert=True)
        return

    remaining_seconds = (expires_at - datetime.utcnow()).total_seconds()
    remaining_days = remaining_seconds / (24 * 3600)
    proportion = max(0.0, min(remaining_days / 30.0, 1.0))

    standard_price = float(await repo.get_setting('vpn_standard_price') or 100)
    premium_price = float(await repo.get_setting('vpn_premium_price') or 400)
    diff = max(0.0, premium_price - standard_price)
    upgrade_cost = round(diff * proportion, 2)

    text = (
        f"⬆️ Апгрейд до Premium+\n\n"
        f"Текущий тариф: {active_sub['tariff_name']}\n"
        f"Осталось дней: {remaining_days:.1f}\n"
        f"Цена апгрейда: <b>{upgrade_cost:.2f}₽</b> (пропорционально оставшимся дням)")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Подтвердить ({upgrade_cost:.2f}₽)", callback_data="confirm_upgrade_vpn")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="vpn_menu")]
    ])

    await safe_edit_message(call, text=text, reply_markup=kb)


@router.callback_query(F.data == "confirm_upgrade_vpn")
async def confirm_upgrade_vpn(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender):
    user = call.from_user
    user_id = user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)
    if not subs:
        await call.answer("Нет подписок для апгрейда.", show_alert=True)
        return

    # pick same logic as above
    active_sub = None
    for sub in subs:
        if sub['tariff_name'] == 'Стандартный':
            active_sub = sub
            break
    if not active_sub:
        active_sub = subs[0]

    expires_at = active_sub['expires_at'] if active_sub['expires_at'] is not None else None
    if not expires_at or expires_at <= datetime.utcnow():
        await call.answer('Подписка уже истекла.', show_alert=True)
        return

    remaining_seconds = (expires_at - datetime.utcnow()).total_seconds()
    remaining_days = remaining_seconds / (24 * 3600)
    proportion = max(0.0, min(remaining_days / 30.0, 1.0))

    standard_price = float(await repo.get_setting('vpn_standard_price') or 100)
    premium_price = float(await repo.get_setting('vpn_premium_price') or 400)
    diff = max(0.0, premium_price - standard_price)
    upgrade_cost = round(diff * proportion, 2)

    user_db = await repo.get_user(user_id)
    if float(user_db['balance']) < upgrade_cost:
        await call.answer(f"Недостаточно средств. Нужно {upgrade_cost:.2f}₽", show_alert=True)
        return

    if not getattr(config, 'xui2', None):
        await call.answer('Вторая панель не настроена. Обратитесь к администратору.', show_alert=True)
        return

    # Deduct balance
    await repo.update_user_balance(user_id, upgrade_cost, operation='sub')

    # create secondary client on secondary panel
    xui_secondary = xui_from_config(config, secondary=True)
    await xui_secondary.login()
    client_email_p2 = f"{user.id}_{user.username or 'user'}_p2"
    inbound_id_p2 = config.xui2.inbound_id
    new_expiry_ms = int(expires_at.timestamp() * 1000)

    client_id_p2 = await xui_secondary.add_client(
        inbound_id=inbound_id_p2,
        email=client_email_p2,
        expire_time=new_expiry_ms,
        total_gb=100
    )

    if not client_id_p2:
        # refund
        await repo.update_user_balance(user_id, upgrade_cost, operation='add')
        await xui_secondary.close()
        await call.message.answer('❌ Ошибка при создании клиента на второй панели. Средства возвращены.')
        try:
            await fragment_sender._notify_admins(f"❌ Ошибка апгрейда VPN для @{user.username or user.id}: не удалось создать клиента на второй панели.")
        except Exception:
            logging.exception("Failed to notify admins about upgrade client creation failure")
        return

    # persist client and change tariff
    await repo.create_vpn_subscription_client(
        subscription_id=active_sub['subscription_id'],
        client_uuid=client_id_p2,
        email=client_email_p2,
        inbound_id=inbound_id_p2,
        panel='secondary',
        total_gb=100
    )

    # update subscription tariff name to Premium+
    await repo.change_vpn_subscription_tariff(active_sub['client_uuid'], 'Premium+')

    await xui_secondary.close()

    # notify admins
    sub_url_p2 = f"{xui_secondary.host_url}/sub/{client_id_p2}"
    profit_text = (
        f"🔐 <b>Апгрейд VPN</b>\n\n"
        f"👤 Пользователь: @{user.username or user.id}\n"
        f"📅 Тариф: Premium+\n"
        f"💵 Оплата апгрейда: {upgrade_cost:.2f}₽\n"
        f"🔗 Ссылка2: {sub_url_p2}"
    )
    await fragment_sender._notify_admins(profit_text)
    try:
        await repo.add_purchase_to_history(user_id, 'vpn_upgrade', f'Upgrade to Premium+ sub={active_sub["subscription_id"]}', 1, upgrade_cost, 0.0)
    except Exception:
        logging.exception('Failed to write purchase history for upgrade')

    await call.message.answer(f"✅ Апгрейд выполнен. С вашего баланса списано {upgrade_cost:.2f}₽.")
    kb = user_kb.get_vpn_menu_kb(True, float(await repo.get_setting('vpn_standard_price') or 100), int(await repo.get_setting('vpn_premium_price') or 400))
    await call.message.answer('Ваша новая ссылка для второй панели:', reply_markup=kb)

@router.callback_query(F.data == "vpn_connect_device")
async def vpn_connect_device_cb(call: types.CallbackQuery):
    text = "<b>Какое у вас устройство?</b>\n\nВыберите тип вашего устройства для получения инструкции:"
    await safe_edit_message(call, text=text, reply_markup=user_kb.get_vpn_devices_kb())

@router.callback_query(F.data.startswith("vpn_device_"))
async def vpn_device_selected_cb(call: types.CallbackQuery, repo: Repository, config: Config):
    device = call.data.split("vpn_device_")[1]
    user_id = call.from_user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)
    
    if not subs:
        await call.answer("У вас нет активной подписки!", show_alert=True)
        return
        
    sub = subs[0]
    
    xui = xui_from_config(config)
    sub_url = f"{xui.host_url}/sub/{sub['client_uuid']}"
    
    download_urls = {
        "ios": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973",
        "macos": "https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973",
        "android": "https://play.google.com/store/apps/details?id=com.happproxy",
        "windows": "https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x64.exe"
    }
    dl_url = download_urls.get(device, "https://happ-app.com")
    app_name = "Happ"
    
    text = (
        f"<b>Инструкция по подключению ({device.upper()})</b>\n\n"
        f"1️⃣ Скачайте приложение <b>{app_name}</b> по кнопке ниже.\n"
        f"2️⃣ После установки нажмите кнопку <b>«⚡ Подключить»</b>"
    )
    
    await safe_edit_message(
        call, 
        text=text, 
        reply_markup=user_kb.get_vpn_connect_instruction_kb(dl_url)
    )


@router.callback_query(F.data == "vpn_connect_now")
async def vpn_connect_now_cb(call: types.CallbackQuery, repo: Repository, config: Config):
    user_id = call.from_user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)

    if not subs:
        await call.answer("У вас нет активной подписки!", show_alert=True)
        return

    sub = subs[0]
    xui = xui_from_config(config)
    sub_url = f"{xui.host_url}/sub/{sub['client_uuid']}"

    text = (
        "<b>Подключение через Happ</b>\n\n"
        "1. Скопируйте ссылку ниже:\n"
        f"<code>{sub_url}</code>\n\n"
        "2. Откройте приложение Happ\n\n"
        "3. Сверху справа нажмите на иконку «+» и выберите «Вставить из буфера обмена»\n\n"
        "4. Профиль добавится автоматически, нажмите на него для подключения!"
    )
    await safe_edit_message(call, text=text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ К выбору устройства", callback_data="vpn_connect_device")],
        [types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ]))


@router.callback_query(F.data.startswith("buy_vpn_gb|"))
async def buy_vpn_gb_callback(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender):
    # callback_data format: buy_vpn_gb|<client_uuid>|<amount>
    parts = call.data.split("|")
    if len(parts) != 3:
        await call.answer("Неверный формат запроса.", show_alert=True)
        return
    _, client_uuid, amount_s = parts
    try:
        amount = int(amount_s)
    except ValueError:
        await call.answer("Неверное количество ГБ.", show_alert=True)
        return

    client = await repo.get_vpn_subscription(client_uuid)
    if not client:
        await call.answer("Клиент не найден.", show_alert=True)
        return

    if client['user_id'] != call.from_user.id:
        await call.answer("Это не ваш клиент.", show_alert=True)
        return

    price_per_gb = 3.0
    total_cost = round(price_per_gb * amount, 2)

    user_db = await repo.get_user(call.from_user.id)
    if float(user_db['balance']) < total_cost:
        await call.answer(f"Недостаточно средств. Необходимо {total_cost:.2f}₽", show_alert=True)
        return

    # Deduct balance
    await repo.update_user_balance(call.from_user.id, total_cost, operation='sub')

    # Update DB
    await repo.extend_vpn_subscription(client_uuid, added_gb=amount)

    # Update in panel
    panel = client['panel']
    xui = xui_from_config(config, secondary=(panel == 'secondary'))
    await xui.login()
    new_total = (client['total_gb'] or 0) + amount
    try:
        await xui.update_client(inbound_id=client['inbound_id'], client_uuid=client_uuid, email=client['email'], enable=True, total_gb=new_total)
    except Exception:
        logging.exception("Failed to update client total_gb on XUI")

    await xui.close()

    # clear low_gb notification for subscription
    try:
        await repo.db.execute("DELETE FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", client['subscription_id'], 'low_gb')
    except Exception:
        logging.exception("Failed to clear low_gb notification record")

    await call.answer(f"✅ Куплено {amount} ГБ за {total_cost:.2f}₽")
    await call.message.edit_text(f"✅ Куплено {amount} ГБ. Новый лимит: {new_total} ГБ", reply_markup=user_kb.get_vpn_menu_kb(True, float(await repo.get_setting('vpn_standard_price') or 100), int(await repo.get_setting('vpn_premium_price') or 400)))

    # notify admins
    try:
        await fragment_sender._notify_admins(f"🔄 Пользователь @{call.from_user.username or call.from_user.id} докупил {amount}ГБ за {total_cost:.2f}₽ (client={client_uuid})")
    except Exception:
        logging.exception("Failed to notify admins about GB top-up")
    try:
        await repo.add_purchase_to_history(call.from_user.id, 'vpn_gb', f'GB topup client={client_uuid}', amount, total_cost, 0.0)
    except Exception:
        logging.exception('Failed to write purchase history for GB topup')

