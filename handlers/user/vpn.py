import uuid
from datetime import datetime, timedelta
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from services.repository import Repository
from keyboards import user_kb
from config import Config
from services.xui import XUIServer
from utils.safe_message import safe_delete_and_send_photo, safe_edit_message

router = Router()

@router.callback_query(F.data == "vpn_menu")
async def vpn_menu_callback(call: types.CallbackQuery, repo: Repository, config: Config):
    user_id = call.from_user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)
    has_subs = len(subs) > 0
    vpn_price = float(await repo.get_setting('vpn_standard_price') or 100)

    if has_subs:
        xui = XUIServer(
            host=config.xui.host,
            port=config.xui.port,
            username=config.xui.username,
            password=config.xui.password,
            https=config.xui.https,
            web_base_path=config.xui.web_base_path
        )
        
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
            user_kb.get_vpn_menu_kb(has_subs, vpn_price)
        )
    else:
        await safe_delete_and_send_photo(
            call, config, config.visuals.img_url_main,
            "<b>🔐 ВПН Сервис</b>\n\nУ вас еще нет подписки. Купите её для безопасного доступа к сети:",
            user_kb.get_vpn_menu_kb(has_subs, vpn_price)
        )

@router.callback_query(F.data == "buy_vpn_plan_standard_1")
async def buy_vpn_plan_callback(call: types.CallbackQuery, repo: Repository, config: Config):
    vpn_price = float(await repo.get_setting('vpn_standard_price') or 100)
    user_db = await repo.get_user(call.from_user.id)
    
    if float(user_db["balance"]) < vpn_price:
        await safe_edit_message(call, text=f"Недостаточно средств! Не хватает: <b>{vpn_price - float(user_db['balance'])}₽</b>", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="profile_topup_menu")]]))
        return

    # Deduct balance
    await repo.update_user_balance(call.from_user.id, vpn_price, operation='sub')
    
    # Process XUI
    xui = XUIServer(
        host=config.xui.host,
        port=config.xui.port,
        username=config.xui.username,
        password=config.xui.password,
        https=config.xui.https,
        web_base_path=config.xui.web_base_path
    )
    
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
        current_expiry = active_sub['expires_at']
        if current_expiry and current_expiry > datetime.now():
            new_expiry = current_expiry + timedelta(days=days_to_add)
        else:
            new_expiry = datetime.now() + timedelta(days=days_to_add)
            
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
        else:
            # refund
            await repo.update_user_balance(call.from_user.id, vpn_price, operation='add')
            await safe_edit_message(call, text="❌ Ошибка при продлении в XUI. Средства возвращены.")
            
    else:
        # Create new
        client_email = f"{call.from_user.id}_{call.from_user.username or 'user'}"
        new_expiry = datetime.now() + timedelta(days=days_to_add)
        new_expiry_ms = int(new_expiry.timestamp() * 1000)
        inbound_id = config.xui.inbound_id
        
        client_id = await xui.add_client(
            inbound_id=inbound_id,
            email=client_email,
            expire_time=new_expiry_ms
        )
        
        if client_id:
            await repo.create_vpn_subscription(
                user_id=call.from_user.id,
                client_uuid=client_id,
                email=client_email,
                inbound_id=inbound_id,
                target_tariff_name='Стандартный',
                total_gb=0,
                expires_at=new_expiry
            )
            sub_url = f"{xui.host_url}/sub/{client_id}"
            await safe_edit_message(call, text=f"✅ Подписка ВПН успешно оформлена до {new_expiry.strftime('%Y-%m-%d %H:%M')}\nВаша ссылка для подключения:\n<code>{sub_url}</code>", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]]))
        else:
            await repo.update_user_balance(call.from_user.id, vpn_price, operation='add')
            await safe_edit_message(call, text="❌ Ошибка при создании в XUI. Средства возвращены.")
            
    await xui.close()

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
    
    xui = XUIServer(
        host=config.xui.host,
        port=config.xui.port,
        username=config.xui.username,
        password=config.xui.password,
        https=config.xui.https,
        web_base_path=config.xui.web_base_path
    )
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
        f"2️⃣ После установки нажмите кнопку <b>«⚡ Подключить»</b> — профиль добавится автоматически!"
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
    xui = XUIServer(
        host=config.xui.host,
        port=config.xui.port,
        username=config.xui.username,
        password=config.xui.password,
        https=config.xui.https,
        web_base_path=config.xui.web_base_path
    )
    sub_url = f"{xui.host_url}/sub/{sub['client_uuid']}"
    happ_deeplink = f"happ://add/{sub_url}"

    text = (
        "<b>Подключение через Happ</b>\n\n"
        "1. Скопируйте ссылку ниже:\n"
        f"<code>{happ_deeplink}</code>\n\n"
        "2. Откройте приложение Happ\n\n"
        "3. Сверху справа нажмите на иконку «+» и выберите «Вставить из буфера обмена»\n\n"
        "4. Профиль добавится автоматически, нажмите на него для подключения!"
    )
    await safe_edit_message(call, text=text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ К выбору устройства", callback_data="vpn_connect_device")],
        [types.InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
    ]))

