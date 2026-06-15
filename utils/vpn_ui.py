"""Shared UI for delivering a subscription to the user: QR + one-tap import."""

import logging

from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from utils.qr import make_qr_png

logger = logging.getLogger(__name__)


def connect_keyboard(sub_url: str | None) -> InlineKeyboardMarkup:
    rows = []
    if sub_url:
        # Opens the Remnawave subscription page: built-in step-by-step
        # instructions + import buttons for the user's app/OS.
        rows.append([InlineKeyboardButton(text="🌐 Открыть страницу подключения", url=sub_url)])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_subscription_card(bot, chat_id: int, sub_url: str | None, header: str):
    """Send the subscription as a link to the Remnawave sub-page (with QR aid)."""
    caption = (
        f"{header}\n\n"
        "👇 Нажмите <b>«Открыть страницу подключения»</b> — на ней пошаговая "
        "инструкция и кнопки установки под ваше устройство.\n"
        "🖥 С компьютера — отсканируйте QR-код телефоном.\n\n"
        "<b>Ссылка-подписка:</b>\n"
        f"<code>{sub_url or '—'}</code>"
    )
    png = make_qr_png(sub_url) if sub_url else None
    kb = connect_keyboard(sub_url)
    try:
        if png:
            await bot.send_photo(
                chat_id,
                BufferedInputFile(png, filename="vpn_qr.png"),
                caption=caption,
                reply_markup=kb,
            )
        else:
            await bot.send_message(chat_id, caption, reply_markup=kb)
    except Exception:
        logger.exception("Failed to send subscription card to %s", chat_id)
        await bot.send_message(chat_id, caption, reply_markup=kb)
