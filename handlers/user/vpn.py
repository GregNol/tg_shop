import uuid
from datetime import datetime, timedelta
from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
import logging

from services.repository import Repository
from keyboards import user_kb
from config import Config
from services.remnawave import remnawave_from_config, squads_for_tariff, make_username
from services.vpn_service import provision_vpn
from utils.vpn_ui import send_subscription_card
from utils.safe_message import safe_delete_and_send_photo, safe_edit_message
from services.fragment_sender import FragmentSender
from services.profit_calculator import ProfitCalculator
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot

router = Router()


def _group_vpn_clients(clients):
    grouped = {}
    order = []
    for client in clients:
        subscription_id = client.get('subscription_id')
        if subscription_id not in grouped:
            grouped[subscription_id] = []
            order.append(subscription_id)
        grouped[subscription_id].append(client)
    return [grouped[subscription_id] for subscription_id in order]


def _render_vpn_subscription_blocks(clients, config):
    """Render one block per subscription. With Remnawave a subscription maps to a
    single user with one aggregated subscription URL."""
    blocks = []
    for index, group in enumerate(_group_vpn_clients(clients), 1):
        primary = group[0]
        tariff_name = primary.get('tariff_name') or 'VPN'
        status = '✅ Активна' if any(int(client.get('is_active') or 0) == 1 for client in group) else '❌ Выключена'
        expiry = primary['expires_at'].strftime('%Y-%m-%d %H:%M') if primary.get('expires_at') else 'Бессрочно'
        sub_url = primary.get('subscription_url') or ''
        total_gb = int(primary.get('total_gb') or 0)
        traffic_text = "Безлимит" if total_gb == 0 else f"{total_gb} ГБ"

        lines = [
            f"<b>✦ ПРЕМИУМ-ПОДПИСКА #{index}</b>",
            f"<b>Тариф:</b> «{tariff_name}»",
            f"<b>Статус:</b> {status}",
            f"<b>Действует до:</b> <b>{expiry}</b>",
            f"<b>Трафик:</b> <b>{traffic_text}</b>",
            "<b>━━━━━━━━━━━━━━━━━━</b>",
            "<b>Подключение:</b>",
            f"<code>{sub_url}</code>",
        ]
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def _has_premium_subscription(clients):
    for client in clients:
        tariff_name = (client.get('tariff_name') or '').lower()
        if 'прем' in tariff_name or 'premium' in tariff_name:
            return True
    return False

@router.callback_query(F.data == "vpn_menu")
async def vpn_menu_callback(call: types.CallbackQuery, repo: Repository, config: Config):
    user_id = call.from_user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)
    has_subs = len(subs) > 0
    is_premium = _has_premium_subscription(subs)
    vpn_price = float(await repo.get_setting('vpn_standard_price') or 100)
    premium_price = int(await repo.get_setting('vpn_premium_price') or 400)

    if has_subs:
        text = "<b>👑 VPN PREMIUM CABINET</b>\n"
        text += "<i>Элегантный доступ к защищенной сети</i>\n\n"
        text += _render_vpn_subscription_blocks(subs, config)

        await safe_delete_and_send_photo(
            call, config, config.visuals.img_url_main,
            text,
            user_kb.get_vpn_menu_kb(has_subs, vpn_price, premium_price, show_upgrade=not is_premium, is_premium_user=is_premium)
        )
    else:
        trial_enabled = (await repo.get_setting('vpn_trial_enabled') or '1') == '1'
        trial_available = trial_enabled and not await repo.has_used_trial(user_id)
        trial_days = int(await repo.get_setting('vpn_trial_days') or 3)
        await safe_delete_and_send_photo(
            call, config, config.visuals.img_url_main,
            "<b>🔐 ВПН Сервис</b>\n\nУ вас еще нет подписки. Купите её для безопасного доступа к сети:",
            user_kb.get_vpn_menu_kb(has_subs, vpn_price, premium_price, show_trial=trial_available, trial_days=trial_days)
        )

def _topup_kb(deficit: float) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"💳 Пополнить на {deficit:.0f}₽ и подключить", callback_data="profile_topup_menu")],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="vpn_menu")],
    ])


async def _run_vpn_purchase(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender, tariff_key: str):
    """Shared buy/extend flow with insufficient-balance auto-resume intent."""
    is_premium = tariff_key == 'premium'
    price_key = 'vpn_premium_price' if is_premium else 'vpn_standard_price'
    price = float(await repo.get_setting(price_key) or (400 if is_premium else 100))
    user = call.from_user

    if is_premium and not config.remnawave.squads_premium:
        await safe_edit_message(call, text="❌ Премиум-тариф не настроен. Администратор должен задать REMNAWAVE_SQUADS_PREMIUM.")
        return

    user_db = await repo.get_user(user.id)
    balance = float(user_db["balance"])
    if balance < price:
        deficit = price - balance
        # remember intent so the purchase auto-completes after top-up
        await repo.upsert_vpn_intent(user.id, tariff_key, price)
        tariff_label = 'Premium+' if is_premium else 'Стандартный'
        await safe_edit_message(
            call,
            text=(
                f"💼 Тариф «{tariff_label}» — <b>{price:.0f}₽</b>\n"
                f"На балансе: <b>{balance:.2f}₽</b>, не хватает <b>{deficit:.2f}₽</b>.\n\n"
                "Пополните баланс — подписка оформится <b>автоматически</b>, как только средств станет достаточно."
            ),
            reply_markup=_topup_kb(deficit),
        )
        return

    # Deduct, provision, refund on failure (paid purchase lifts any trial cap)
    await repo.update_user_balance(user.id, price, operation='sub')
    result = await provision_vpn(repo, config, user.id, tariff_key, days=30, set_total_gb=0)

    if not result.get('ok'):
        await repo.update_user_balance(user.id, price, operation='add')
        await safe_edit_message(call, text="❌ Ошибка при оформлении в Remnawave. Средства возвращены.")
        try:
            await fragment_sender._notify_admins(f"❌ Ошибка покупки VPN ({tariff_key}) для @{user.username or user.id}: {result.get('error')}")
        except Exception:
            logging.exception("Failed to notify admins about VPN purchase failure")
        return

    # any pending intent is now satisfied
    pending = await repo.get_pending_vpn_intent(user.id)
    if pending:
        await repo.mark_vpn_intent(pending['id'], 'fulfilled')

    new_expiry = result['new_expiry']
    tariff_label = result['tariff_name']
    action = 'продление' if result.get('extended') else 'новая'

    await call.answer("✅ Готово!")
    await safe_edit_message(
        call,
        text=f"✅ Подписка «{tariff_label}» оформлена до {new_expiry.strftime('%Y-%m-%d %H:%M')}.",
        reply_markup=user_kb.get_vpn_menu_kb(True, float(await repo.get_setting('vpn_standard_price') or 100), int(await repo.get_setting('vpn_premium_price') or 400), show_upgrade=not is_premium, is_premium_user=is_premium),
    )
    await send_subscription_card(call.bot, user.id, result.get('subscription_url'), f"<b>🔐 {tariff_label}</b> — действует до {new_expiry.strftime('%Y-%m-%d %H:%M')}")

    hist_type = 'vpn_premium' if is_premium else 'vpn_standard'
    try:
        await repo.add_purchase_to_history(user.id, hist_type, f'{tariff_label} {action} to {new_expiry.strftime("%Y-%m-%d %H:%M")}', 30, price, 0.0)
    except Exception:
        logging.exception('Failed to write purchase history for VPN purchase')
    try:
        await fragment_sender._notify_admins(
            f"🔐 <b>Покупка VPN</b>\n👤 @{user.username or user.id}\n📅 {tariff_label} ({action})\n💵 {price:.2f}₽\n📆 До {new_expiry.strftime('%Y-%m-%d %H:%M')}"
        )
    except Exception:
        logging.exception("Failed to notify admins about VPN purchase")


@router.callback_query(F.data == "buy_vpn_plan_standard_1")
async def buy_vpn_plan_callback(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender):
    await _run_vpn_purchase(call, repo, config, fragment_sender, tariff_key='standard')


@router.callback_query(F.data == "buy_vpn_plan_premium_1")
async def buy_vpn_premium_callback(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender):
    await _run_vpn_purchase(call, repo, config, fragment_sender, tariff_key='premium')


@router.callback_query(F.data == "buy_vpn_trial")
async def buy_vpn_trial_callback(call: types.CallbackQuery, repo: Repository, config: Config, fragment_sender: FragmentSender):
    user = call.from_user
    if (await repo.get_setting('vpn_trial_enabled') or '1') != '1':
        await call.answer("Пробный период сейчас недоступен.", show_alert=True)
        return
    if await repo.has_used_trial(user.id):
        await call.answer("Вы уже использовали пробный период.", show_alert=True)
        return
    if await repo.get_user_vpn_subscriptions(user.id):
        await call.answer("Пробный период доступен только новым пользователям.", show_alert=True)
        return

    trial_days = int(await repo.get_setting('vpn_trial_days') or 3)
    trial_gb = int(await repo.get_setting('vpn_trial_gb') or 5)

    result = await provision_vpn(repo, config, user.id, 'standard', days=trial_days, total_gb=trial_gb)
    if not result.get('ok'):
        await call.answer("❌ Не удалось активировать пробный период. Попробуйте позже.", show_alert=True)
        try:
            await fragment_sender._notify_admins(f"❌ Ошибка триала VPN для @{user.username or user.id}: {result.get('error')}")
        except Exception:
            logging.exception("Failed to notify admins about trial failure")
        return

    new_expiry = result['new_expiry']
    await call.answer("🎁 Пробный период активирован!")
    await safe_edit_message(
        call,
        text=f"🎁 Пробный доступ на {trial_days} дн. ({trial_gb} ГБ) активирован до {new_expiry.strftime('%Y-%m-%d %H:%M')}.",
        reply_markup=user_kb.get_vpn_menu_kb(True, float(await repo.get_setting('vpn_standard_price') or 100), int(await repo.get_setting('vpn_premium_price') or 400), is_premium_user=False),
    )
    await send_subscription_card(call.bot, user.id, result.get('subscription_url'), f"<b>🎁 Пробный VPN</b> — до {new_expiry.strftime('%Y-%m-%d %H:%M')}")
    try:
        await repo.add_purchase_to_history(user.id, 'vpn_trial', f'Trial {trial_days}d/{trial_gb}gb', trial_days, 0.0, 0.0)
    except Exception:
        logging.exception('Failed to write purchase history for trial')
    try:
        await fragment_sender._notify_admins(f"🎁 Триал VPN для @{user.username or user.id}: {trial_days} дн., {trial_gb} ГБ")
    except Exception:
        logging.exception("Failed to notify admins about trial")


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

    if not config.remnawave.squads_premium:
        await call.answer('Премиум-тариф не настроен. Обратитесь к администратору.', show_alert=True)
        return

    # Deduct balance
    await repo.update_user_balance(user_id, upgrade_cost, operation='sub')

    # Move the existing Remnawave user into the premium squads
    remna = remnawave_from_config(config)
    updated = await remna.update_user(
        active_sub['client_uuid'],
        squad_uuids=squads_for_tariff(config, premium=True),
    )
    await remna.close()

    if not updated:
        # refund
        await repo.update_user_balance(user_id, upgrade_cost, operation='add')
        await call.message.answer('❌ Ошибка при апгрейде в Remnawave. Средства возвращены.')
        try:
            await fragment_sender._notify_admins(f"❌ Ошибка апгрейда VPN для @{user.username or user.id}: не удалось обновить сквады пользователя.")
        except Exception:
            logging.exception("Failed to notify admins about upgrade failure")
        return

    if updated.get('subscriptionUrl'):
        await repo.set_subscription_url(active_sub['client_uuid'], updated['subscriptionUrl'])

    # update subscription tariff name to Premium+
    await repo.change_vpn_subscription_tariff(active_sub['client_uuid'], 'Premium+')

    # notify admins
    profit_text = (
        f"🔐 <b>Апгрейд VPN</b>\n\n"
        f"👤 Пользователь: @{user.username or user.id}\n"
        f"📅 Тариф: Premium+\n"
        f"💵 Оплата апгрейда: {upgrade_cost:.2f}₽"
    )
    await fragment_sender._notify_admins(profit_text)
    try:
        await repo.add_purchase_to_history(user_id, 'vpn_upgrade', f'Upgrade to Premium+ sub={active_sub["subscription_id"]}', 1, upgrade_cost, 0.0)
    except Exception:
        logging.exception('Failed to write purchase history for upgrade')

    await call.message.answer(f"✅ Апгрейд выполнен. С вашего баланса списано {upgrade_cost:.2f}₽.")
    kb = user_kb.get_vpn_menu_kb(True, float(await repo.get_setting('vpn_standard_price') or 100), int(await repo.get_setting('vpn_premium_price') or 400), show_upgrade=False, is_premium_user=True)
    await call.message.answer('Тариф обновлён до Premium+. Ссылка для подключения осталась прежней.', reply_markup=kb)

@router.callback_query(F.data == "vpn_connect_now")
async def vpn_connect_now_cb(call: types.CallbackQuery, repo: Repository, config: Config):
    user_id = call.from_user.id
    subs = await repo.get_user_vpn_subscriptions(user_id)

    if not subs:
        await call.answer("У вас нет активной подписки!", show_alert=True)
        return

    await call.answer()
    for sub in subs:
        expiry = sub['expires_at'].strftime('%Y-%m-%d %H:%M') if sub.get('expires_at') else 'Бессрочно'
        header = f"<b>🔐 {sub.get('tariff_name') or 'VPN'}</b> — действует до {expiry}"
        await send_subscription_card(call.bot, call.from_user.id, sub.get('subscription_url'), header)


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
    new_total = (client['total_gb'] or 0) + amount
    remna = remnawave_from_config(config)
    try:
        await remna.update_user(client_uuid, total_gb=new_total)
    except Exception:
        logging.exception("Failed to update user trafficLimit on Remnawave")
    finally:
        await remna.close()

    # clear low_gb notification for subscription
    try:
        low_gb_key = f"low_gb:{client_uuid}"
        await repo.db.execute("DELETE FROM vpn_expiry_notifications WHERE subscription_id = $1 AND notify_when = $2", client['subscription_id'], low_gb_key)
    except Exception:
        logging.exception("Failed to clear low_gb notification record")

    await call.answer(f"✅ Куплено {amount} ГБ за {total_cost:.2f}₽")
    is_premium = _has_premium_subscription([client])
    await call.message.edit_text(
        f"✅ Куплено {amount} ГБ. Новый лимит: {new_total} ГБ",
        reply_markup=user_kb.get_vpn_menu_kb(
            True,
            float(await repo.get_setting('vpn_standard_price') or 100),
            int(await repo.get_setting('vpn_premium_price') or 400),
            show_upgrade=not is_premium,
            is_premium_user=is_premium,
        ),
    )

    # notify admins
    try:
        await fragment_sender._notify_admins(f"🔄 Пользователь @{call.from_user.username or call.from_user.id} докупил {amount}ГБ за {total_cost:.2f}₽ (client={client_uuid})")
    except Exception:
        logging.exception("Failed to notify admins about GB top-up")
    try:
        await repo.add_purchase_to_history(call.from_user.id, 'vpn_gb', f'GB topup client={client_uuid}', amount, total_cost, 0.0)
    except Exception:
        logging.exception('Failed to write purchase history for GB topup')
