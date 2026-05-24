from aiogram import F, Router, types, Bot
from aiogram.fsm.context import FSMContext
from datetime import datetime, timedelta
import logging

from services.repository import Repository
from states.admin import AdminUserManagementStates
from keyboards.admin_kb import get_user_info_kb, get_user_payments_kb, UserPaymentsCallback, AdminUserNavCallback
from keyboards.admin_kb import AdminVPNCallback
from config import Config

router = Router()
PAGE_SIZE = 5


def _is_premium_tariff_name(tariff_name: str | None) -> bool:
    normalized = (tariff_name or '').lower()
    return 'прем' in normalized or 'premium' in normalized


def _get_tariff_choice_text(tariff_key: str) -> str:
    return 'Premium+' if tariff_key == 'premium' else 'Стандартный'


def _find_subscription_by_tariff(subs, tariff_name: str):
    for sub in subs:
        if (sub.get('tariff_name') or '') == tariff_name:
            return sub
    return None


def _get_unique_subscription_rows(subs):
    unique = {}
    order = []
    for sub in subs:
        sub_id = sub.get('subscription_id')
        if sub_id not in unique:
            unique[sub_id] = sub
            order.append(sub_id)
    return [unique[sub_id] for sub_id in order]


def _pick_primary_client(clients):
    return next((client for client in clients if client.get('panel') == 'primary'), clients[0]) if clients else None


def _format_traffic_limit(total_gb) -> str:
    value = int(total_gb or 0)
    return 'Безлимит' if value == 0 else f'{value} ГБ'


def _format_expiry(expires_at) -> str:
    if not expires_at:
        return 'Бессрочно'
    return expires_at.strftime('%Y-%m-%d %H:%M')


def _build_vpn_tariff_choice_kb(target_user_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text='🛒 Стандартный', callback_data='admin_give_vpn_tariff_standard'),
            types.InlineKeyboardButton(text='💎 Premium+', callback_data='admin_give_vpn_tariff_premium'),
        ],
        [
            types.InlineKeyboardButton(
                text='⬅️ Назад',
                callback_data=AdminUserNavCallback(action='back_to_menu', target_user_id=target_user_id).pack(),
            )
        ],
    ])

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


@router.callback_query(AdminUserNavCallback.filter(F.action == "quick_add_gb"))
async def quick_add_vpn_gb(call: types.CallbackQuery, callback_data: AdminUserNavCallback, state: FSMContext, repo: Repository, config: Config):
    await state.update_data(target_user_id=callback_data.target_user_id)
    subs = await repo.get_user_vpn_subscriptions(callback_data.target_user_id)
    if not subs:
        await call.answer('У пользователя нет VPN-подписок', show_alert=True)
        return

    unique_subs = _get_unique_subscription_rows(subs)
    if len(unique_subs) != 1:
        await admin_show_vpn_clients(call, state, repo, config)
        return

    clients = await repo.get_subscription_clients(unique_subs[0]['subscription_id'])
    primary_client = _pick_primary_client(clients)
    if not primary_client:
        await call.answer('Не удалось определить клиента', show_alert=True)
        return

    await state.update_data(target_client_uuid=primary_client['client_uuid'])
    await state.set_state(AdminUserManagementStates.adding_vpn_gb)
    await call.message.edit_text(
        f"Введите количество ГБ для пользователя <code>{callback_data.target_user_id}</code>:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text='⬅️ Отмена', callback_data=AdminUserNavCallback(action='back_to_menu', target_user_id=callback_data.target_user_id).pack())]
        ])
    )


@router.callback_query(AdminUserNavCallback.filter(F.action == "quick_revoke"))
async def quick_revoke_vpn(call: types.CallbackQuery, callback_data: AdminUserNavCallback, state: FSMContext, repo: Repository, config: Config):
    await state.update_data(target_user_id=callback_data.target_user_id)
    subs = await repo.get_user_vpn_subscriptions(callback_data.target_user_id)
    if not subs:
        await call.answer('У пользователя нет VPN-подписок', show_alert=True)
        return

    unique_subs = _get_unique_subscription_rows(subs)
    if len(unique_subs) != 1:
        await admin_show_vpn_clients(call, state, repo, config)
        return

    target_sub = unique_subs[0]
    clients = await repo.get_subscription_clients(target_sub['subscription_id'])
    primary_client = _pick_primary_client(clients)
    if not primary_client:
        await call.answer('Не удалось определить подписку', show_alert=True)
        return

    await state.update_data(target_client_uuid=primary_client['client_uuid'])
    await state.set_state(AdminUserManagementStates.confirming_vpn_revoke)
    await call.message.edit_text(
        f"Удалить VPN-подписку пользователя <code>{callback_data.target_user_id}</code>?",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text='✅ Да, удалить', callback_data=AdminVPNCallback(action='revoke', client_uuid=primary_client['client_uuid']).pack()),
                types.InlineKeyboardButton(text='❌ Отмена', callback_data=AdminUserNavCallback(action='back_to_menu', target_user_id=callback_data.target_user_id).pack()),
            ]
        ])
    )

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

@router.callback_query(AdminUserManagementStates.user_menu, F.data == 'admin_give_vpn')
async def admin_give_vpn_start(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    target_user_id = data['target_user_id']
    await state.set_state(AdminUserManagementStates.choosing_vpn_tariff)
    await call.message.edit_text(
        "🔐 Выберите тариф для выдачи пользователю:",
        reply_markup=_build_vpn_tariff_choice_kb(target_user_id),
    )


@router.callback_query(AdminUserManagementStates.choosing_vpn_tariff, F.data.in_({'admin_give_vpn_tariff_standard', 'admin_give_vpn_tariff_premium'}))
async def admin_choose_vpn_tariff(call: types.CallbackQuery, state: FSMContext):
    tariff_key = 'premium' if call.data.endswith('premium') else 'standard'
    await state.update_data(vpn_tariff=tariff_key)
    await state.set_state(AdminUserManagementStates.giving_vpn_months)
    data = await state.get_data()
    target_user_id = data['target_user_id']
    await call.message.edit_text(
        f"🔐 Выбран тариф: <b>{_get_tariff_choice_text(tariff_key)}</b>\n\nВведите количество дней для выдачи или продления (например, 30):",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=target_user_id).pack())]
        ]),
    )

@router.message(AdminUserManagementStates.giving_vpn_months)
async def admin_give_vpn_process(message: types.Message, state: FSMContext, repo: Repository, config: Config):
    try:
        days = int(message.text.strip())
        if days <= 0: raise ValueError
    except ValueError:
        await message.answer("❗ Введите корректное количество дней (целое положительное число).")
        return
        
    data = await state.get_data()
    target_user_id = data['target_user_id']
    tariff_key = data.get('vpn_tariff', 'standard')
    tariff_name = _get_tariff_choice_text(tariff_key)
    user = await repo.get_user(target_user_id)
    xui = None
    
    try:
        subs = await repo.get_user_vpn_subscriptions(target_user_id)
        existing_sub = _find_subscription_by_tariff(subs, tariff_name)
        if tariff_key == 'premium' and not getattr(config, 'xui2', None):
            await message.answer('❌ Вторая панель не настроена. Нельзя выдать Premium+.')
            return

        if existing_sub:
            current_expiry = existing_sub['expires_at']
            if current_expiry and current_expiry > datetime.utcnow():
                new_expiry = current_expiry + timedelta(days=days)
            else:
                new_expiry = datetime.utcnow() + timedelta(days=days)

            new_expiry_ms = int(new_expiry.timestamp() * 1000)
            clients = await repo.get_subscription_clients(existing_sub['subscription_id'])
            updated_clients = []
            try:
                for client in clients:
                    panel = client.get('panel') or 'primary'
                    xui = xui_from_config(config, secondary=(panel == 'secondary'))
                    try:
                        await xui.login()
                        success = await xui.update_client(
                            inbound_id=client['inbound_id'],
                            client_uuid=client['client_uuid'],
                            email=client['email'],
                            enable=True,
                            expire_time=new_expiry_ms,
                            total_gb=client.get('total_gb', 0),
                        )
                        if not success:
                            raise RuntimeError(f'XUI update failed for {client["client_uuid"]}')
                        updated_clients.append(client)
                    finally:
                        await xui.close()

                await repo.extend_vpn_subscription(existing_sub['client_uuid'], new_expires_at=new_expiry)
                await message.answer(
                    f"✅ Тариф {tariff_name} успешно продлён пользователю {target_user_id} на {days} дней. Новая дата окончания: {new_expiry.strftime('%Y-%m-%d %H:%M')}"
                )
            except Exception as e:
                logging.exception(
                    "Admin VPN extend failed: admin_id=%s target_user_id=%s tariff=%s client_uuid=%s error=%s",
                    message.from_user.id,
                    target_user_id,
                    tariff_name,
                    existing_sub['client_uuid'],
                    e,
                )
                await message.answer('❌ Ошибка при продлении в XUI.')
                for client in updated_clients:
                    try:
                        panel = client.get('panel') or 'primary'
                        xui = xui_from_config(config, secondary=(panel == 'secondary'))
                        await xui.login()
                        await xui.update_client(
                            inbound_id=client['inbound_id'],
                            client_uuid=client['client_uuid'],
                            email=client['email'],
                            enable=True,
                            expire_time=int(current_expiry.timestamp() * 1000) if current_expiry else 0,
                            total_gb=client.get('total_gb', 0),
                        )
                    except Exception:
                        logging.exception('Failed to rollback admin VPN extend for client %s', client['client_uuid'])
                    finally:
                        try:
                            await xui.close()
                        except Exception:
                            pass
                return
        else:
            new_expiry = datetime.utcnow() + timedelta(days=days)
            new_expiry_ms = int(new_expiry.timestamp() * 1000)
            client_email = f"{target_user_id}_{user['username'] or 'user'}"

            if tariff_key == 'premium':
                xui_primary = xui_from_config(config)
                xui_secondary = xui_from_config(config, secondary=True)
                await xui_primary.login()
                await xui_secondary.login()

                client_email_p1 = f"{client_email}_p1"
                client_email_p2 = f"{client_email}_p2"

                client_id_p1 = await xui_primary.add_or_update_client(
                    inbound_id=config.xui.inbound_id,
                    email=client_email_p1,
                    expire_time=new_expiry_ms,
                )
                client_id_p2 = await xui_secondary.add_or_update_client(
                    inbound_id=config.xui2.inbound_id,
                    email=client_email_p2,
                    expire_time=new_expiry_ms,
                    total_gb=100,
                )

                if not client_id_p1 or not client_id_p2:
                    await message.answer('❌ Ошибка при выдаче Premium+ в XUI.')
                    if client_id_p1:
                        await xui_primary.delete_client(config.xui.inbound_id, client_email_p1)
                    if client_id_p2:
                        await xui_secondary.delete_client(config.xui2.inbound_id, client_email_p2)
                    await xui_primary.close()
                    await xui_secondary.close()
                    return

                try:
                    sub = await repo.create_vpn_subscription(
                        user_id=target_user_id,
                        client_uuid=client_id_p1,
                        email=client_email_p1,
                        inbound_id=config.xui.inbound_id,
                        target_tariff_name='Premium+',
                        total_gb=0,
                        expires_at=new_expiry,
                    )
                    await repo.create_vpn_subscription_client(
                        subscription_id=sub['subscription_id'],
                        client_uuid=client_id_p2,
                        email=client_email_p2,
                        inbound_id=config.xui2.inbound_id,
                        panel='secondary',
                        total_gb=100,
                    )
                except Exception:
                    logging.exception(
                        'Admin VPN Premium+ DB create failed: admin_id=%s target_user_id=%s p1=%s p2=%s expires_at=%s',
                        message.from_user.id,
                        target_user_id,
                        client_id_p1,
                        client_id_p2,
                        new_expiry,
                    )
                    await message.answer('❌ Ошибка записи Premium+ подписки в БД. Подробности в логах.')
                    await xui_primary.close()
                    await xui_secondary.close()
                    return

                await message.answer(
                    f"✅ Premium+ успешно выдан пользователю {target_user_id} на {days} дней.\n"
                    f"До: {new_expiry.strftime('%Y-%m-%d %H:%M')}\n"
                    f"Ссылка 1: <code>{xui_primary.host_url}/sub/{client_id_p1}</code>\n"
                    f"Ссылка 2: <code>{xui_secondary.host_url}/sub/{client_id_p2}</code>"
                )
                await xui_primary.close()
                await xui_secondary.close()
            else:
                xui = xui_from_config(config)
                login_ok = await xui.login()
                if not login_ok:
                    logging.error('Admin VPN issue failed at login: admin_id=%s target_user_id=%s', message.from_user.id, target_user_id)
                    await message.answer('❌ Ошибка подключения к XUI панели.')
                    return

                inbound_id = config.xui.inbound_id
                client_id = await xui.add_or_update_client(
                    inbound_id=inbound_id,
                    email=client_email,
                    expire_time=new_expiry_ms,
                )
                if client_id:
                    try:
                        await repo.create_vpn_subscription(
                            user_id=target_user_id,
                            client_uuid=client_id,
                            email=client_email,
                            inbound_id=inbound_id,
                            target_tariff_name='Стандартный',
                            total_gb=0,
                            expires_at=new_expiry,
                        )
                    except Exception:
                        logging.exception(
                            'Admin VPN issue DB create failed: admin_id=%s target_user_id=%s client_uuid=%s email=%s inbound_id=%s expires_at=%s',
                            message.from_user.id,
                            target_user_id,
                            client_id,
                            client_email,
                            inbound_id,
                            new_expiry,
                        )
                        await message.answer('❌ Ошибка записи подписки в БД. Подробности в логах.')
                        return

                    sub_url = f"{xui.host_url}/sub/{client_id}"
                    await message.answer(
                        f"✅ ВПН ({tariff_name}) успешно выдан пользователю {target_user_id} на {days} дней.\nДо: {new_expiry.strftime('%Y-%m-%d %H:%M')}\nСсылка: <code>{sub_url}</code>"
                    )
                else:
                    logging.error(
                        'Admin VPN issue failed in XUI add_client: admin_id=%s target_user_id=%s email=%s inbound_id=%s',
                        message.from_user.id,
                        target_user_id,
                        client_email,
                        inbound_id,
                    )
                    await message.answer('❌ Ошибка при выдаче в XUI.')
    except Exception:
        logging.exception("Unhandled error in admin_give_vpn_process: admin_id=%s target_user_id=%s days=%s", message.from_user.id, target_user_id, days)
        await message.answer("❌ Внутренняя ошибка при выдаче VPN. Подробности в логах.")
    finally:
        try:
            if xui is not None:
                await xui.close()
        except Exception:
            pass

    dummy_message = await message.answer("Возврат...")
    await show_user_info_menu(dummy_message, state, repo)
    await dummy_message.delete()


@router.callback_query(F.data == 'admin_vpn_clients')
async def admin_show_vpn_clients(call: types.CallbackQuery, state: FSMContext, repo: Repository, config: Config):
    data = await state.get_data()
    target_user_id = data.get('target_user_id')
    if not target_user_id:
        await call.answer('Пользователь не выбран', show_alert=True)
        return

    clients = await repo.get_user_vpn_subscriptions(target_user_id)
    if not clients:
        await call.message.edit_text(
            'У пользователя нет VPN-клиентов.',
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text='⬅️ Назад', callback_data=AdminUserNavCallback(action='back_to_menu', target_user_id=target_user_id).pack())]
            ])
        )
        return

    # Build message with clients grouped by subscription
    text_lines = [f"🔐 VPN клиенты пользователя <code>{target_user_id}</code>:\n"]
    kb_rows = []
    for c in clients:
        traffic_text = _format_traffic_limit(c.get('total_gb'))
        expiry_text = _format_expiry(c.get('expires_at'))
        panel_name = 'Основная' if c.get('panel') == 'primary' else 'Вторая' if c.get('panel') == 'secondary' else c.get('panel', '-').capitalize()
        text_lines.append(
            f"• Панель: {panel_name} | UUID: <code>{c['client_uuid']}</code>\n"
            f"  Email: <code>{c['email']}</code> | Inbound: {c['inbound_id']}\n"
            f"  Трафик: {traffic_text} | Истекает: {expiry_text}"
        )
        # action buttons per client
        kb_rows.append([
            types.InlineKeyboardButton(text="Продлить", callback_data=AdminVPNCallback(action="extend", client_uuid=c['client_uuid']).pack()),
            types.InlineKeyboardButton(text="Добавить GB", callback_data=AdminVPNCallback(action="add_gb", client_uuid=c['client_uuid']).pack())
        ])
        kb_rows.append([
            types.InlineKeyboardButton(text="🗑️ Удалить подписку", callback_data=AdminVPNCallback(action="revoke", client_uuid=c['client_uuid']).pack()),
            types.InlineKeyboardButton(text="Открыть профиль", url=(f"{'https' if (config.xui.https if c['panel']=='primary' else config.xui2.https) else 'http'}://{(config.xui.host if c['panel']=='primary' else config.xui2.host)}:2096/sub/{c['client_uuid']}"))
        ])

    kb_rows.append([types.InlineKeyboardButton(text="⬅️ Назад", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=target_user_id).pack())])
    kb = types.InlineKeyboardMarkup(inline_keyboard=kb_rows)

    await call.message.edit_text('\n'.join(text_lines), reply_markup=kb)


@router.callback_query(AdminVPNCallback.filter())
async def admin_vpn_action(call: types.CallbackQuery, callback_data: AdminVPNCallback, state: FSMContext, repo: Repository, config: Config):
    action = callback_data.action
    client_uuid = callback_data.client_uuid

    if action == 'revoke':
        # delete whole subscription from panels and DB
        client = await repo.get_vpn_subscription(client_uuid)
        if not client:
            await call.answer('Клиент не найден', show_alert=True)
            return
        logging.info(f"Admin {call.from_user.id} revoke requested for client {client_uuid} (user {client.get('user_id')})")
        clients_to_delete = await repo.get_subscription_clients(client['subscription_id'])
        for sub_client in clients_to_delete:
            panel = sub_client.get('panel')
            xui = xui_from_config(config, secondary=(panel != 'primary'))
            await xui.login()
            try:
                await xui.delete_client(sub_client['inbound_id'], sub_client['email'])
            finally:
                await xui.close()
        await repo.delete_vpn_subscription(client_uuid)
        logging.info(f"Admin {call.from_user.id} revoked subscription {client['subscription_id']} via client {client_uuid}")
        await call.answer('Подписка удалена', show_alert=False)
        await admin_show_vpn_clients(call, state, repo, config)
        return

    if action == 'add_gb':
        logging.info(f"Admin {call.from_user.id} initiated add_gb for client {client_uuid}")
        await state.set_state(AdminUserManagementStates.adding_vpn_gb)
        await state.update_data(target_client_uuid=client_uuid)
        await call.message.edit_text('Введите количество ГБ для добавления (целое число):', reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Отмена", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=(await state.get_data()).get('target_user_id') ).pack())]]))
        return

    if action == 'extend':
        logging.info(f"Admin {call.from_user.id} initiated extend for client {client_uuid}")
        await state.set_state(AdminUserManagementStates.extending_vpn_months)
        await state.update_data(target_subscription_client=client_uuid)
        await call.message.edit_text('Введите количество дней для продления (например, 30):', reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Отмена", callback_data=AdminUserNavCallback(action="back_to_menu", target_user_id=(await state.get_data()).get('target_user_id') ).pack())]]))
        return

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


@router.message(AdminUserManagementStates.adding_vpn_gb)
async def admin_process_add_vpn_gb(message: types.Message, state: FSMContext, repo: Repository, config: Config):
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer('❗ Введите корректное целое положительное количество ГБ.')
        return

    data = await state.get_data()
    client_uuid = data.get('target_client_uuid')
    if not client_uuid:
        await message.answer('Клиент не найден в состоянии.')
        await state.clear()
        return

    client = await repo.get_vpn_subscription(client_uuid)
    if not client:
        await message.answer('Клиент не найден в БД.')
        await state.clear()
        return

    new_total = (client.get('total_gb') or 0) + amount
    logging.info(f"Admin {message.from_user.id} adding {amount}GB to client {client_uuid} (was {client.get('total_gb')})")
    await repo.extend_vpn_subscription(client_uuid, added_gb=amount)

    # Update client in panel
    panel = client.get('panel')
    xui = xui_from_config(config, secondary=(panel != 'primary'))
    await xui.login()
    expires = client.get('expires_at')
    expire_ms = int(expires.timestamp() * 1000) if expires else 0
    await xui.update_client(inbound_id=client['inbound_id'], client_uuid=client_uuid, email=client['email'], enable=True, expire_time=expire_ms, total_gb=new_total)
    await xui.close()
    logging.info(f"Admin {message.from_user.id} added {amount}GB to client {client_uuid}, new_total={new_total}")
    await message.answer(f'✅ Добавлено {amount} ГБ. Новый лимит: {new_total} ГБ')
    await state.clear()
    await show_user_info_menu(message, state, repo)


@router.message(AdminUserManagementStates.extending_vpn_months)
async def admin_process_extend_vpn_months(message: types.Message, state: FSMContext, repo: Repository, config: Config):
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer('❗ Введите корректное целое количество дней.')
        return

    data = await state.get_data()
    client_uuid = data.get('target_subscription_client')
    if not client_uuid:
        await message.answer('Клиент не найден в состоянии.')
        await state.clear()
        return

    client = await repo.get_vpn_subscription(client_uuid)
    if not client:
        await message.answer('Клиент не найден в БД.')
        await state.clear()
        return

    logging.info(f"Admin {message.from_user.id} extending client {client_uuid} by {days} days")
    # compute new expiry
    current_expiry = client.get('expires_at')
    from datetime import timedelta
    if current_expiry and current_expiry > datetime.utcnow():
        new_expiry = current_expiry + timedelta(days=days)
    else:
        new_expiry = datetime.utcnow() + timedelta(days=days)
    new_expiry_ms = int(new_expiry.timestamp() * 1000)

    # fetch all clients for the subscription
    sub_id = client.get('subscription_id')
    clients = await repo.get_subscription_clients(sub_id)

    # prepare xui instances
    xui_primary = xui_from_config(config)
    xui_secondary = None
    if getattr(config, 'xui2', None):
        xui_secondary = xui_from_config(config, secondary=True)

    # update each client in panels and DB
    for c in clients:
        panel = c.get('panel')
        if panel == 'primary':
            xui = xui_primary
        else:
            xui = xui_secondary or xui_primary

        logging.info(f"Updating client {c['client_uuid']} on panel={panel} to expiry {new_expiry}")
        await xui.login()
        await xui.update_client(inbound_id=c['inbound_id'], client_uuid=c['client_uuid'], email=c['email'], enable=True, expire_time=new_expiry_ms, total_gb=c.get('total_gb', 0))
        await repo.extend_vpn_subscription(c['client_uuid'], new_expires_at=new_expiry)
        await xui.close()

    logging.info(f"Admin {message.from_user.id} extended subscription {sub_id} to {new_expiry}")
    await message.answer(f'✅ Подписка продлена на {days} дней до {new_expiry.strftime("%Y-%m-%d %H:%M")}')
    await state.clear()
    await show_user_info_menu(message, state, repo)