from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext

from services.repository import Repository
from keyboards import user_kb
from states.user import CalculatorStates
from utils.safe_message import safe_answer_photo, safe_delete_message
from config import Config

router = Router()

@router.callback_query(F.data == "calculator")
async def calculator_menu_callback(call: types.CallbackQuery, state: FSMContext, config: Config):
    """
    Открывает главное меню калькулятора.
    Сбрасывает текущее состояние пользователя и выводит меню с выбором режима расчета.
    """
    await state.clear()  # Сбрасываем любые активные состояния (FSM)
    await safe_delete_message(call)  # Удаляем сообщение с предыдущим меню
    await safe_answer_photo(
        call,
        photo=config.visuals.img_url_calculator,
        caption="<b>🧮 Калькулятор</b>\n\nВыберите, как вы хотите рассчитать стоимость:",
        reply_markup=user_kb.get_calculator_kb()
    )

@router.callback_query(F.data == "calc_by_stars")
async def calc_by_stars_start(call: types.CallbackQuery, state: FSMContext):
    """
    Обрабатывает нажатие на кнопку расчета стоимости по количеству звезд.
    Переводит пользователя в состояние ожидания ввода количества.
    """
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="calculator")]])
    await call.message.edit_caption(caption="Введите количество звезд (минимум 50):", reply_markup=kb)
    await state.set_state(CalculatorStates.waiting_for_stars_amount)  # Переводим в режим ввода звезд

@router.message(CalculatorStates.waiting_for_stars_amount)
async def calc_by_stars_process(message: types.Message, state: FSMContext, repo: Repository):
    """
    Обрабатывает текстовый ввод пользователя с количеством звезд.
    Рассчитывает итоговую стоимость в рублях исходя из текущей цены.
    """
    try:
        stars_amount = int(message.text)
        if stars_amount < 50:
            await message.answer("❗️ Минимальное количество для расчета — 50 звёзд.")
            return
    except ValueError:
        await message.answer("❗️ Пожалуйста, введите целое число.")
        return

    # Получаем актуальную цену одной звезды из базы данных (настроек)
    star_price_str = await repo.get_setting('star_price')
    star_price = float(star_price_str) if star_price_str else 1.8
    total_cost = round(stars_amount * star_price, 2)  # Высчитываем итоговую сумму
    
    await message.answer(f"⭐ <b>{stars_amount:,}</b> звёзд ≈ <b>{total_cost:.2f} ₽</b>")
    await state.clear()  # Очищаем состояние после успешного расчета

@router.callback_query(F.data == "calc_by_rub")
async def calc_by_rub_start(call: types.CallbackQuery, state: FSMContext):
    """
    Обрабатывает нажатие на кнопку расчета по бюджету в рублях.
    Переводит пользователя в состояние ожидания ввода суммы.
    """
    kb = types.InlineKeyboardMarkup(inline_keyboard=[[types.InlineKeyboardButton(text="⬅️ Назад", callback_data="calculator")]])
    await call.message.edit_caption(caption="Введите сумму в рублях (₽):", reply_markup=kb)
    await state.set_state(CalculatorStates.waiting_for_rub_amount)  # Переводим в режим ввода суммы

@router.message(CalculatorStates.waiting_for_rub_amount)
async def calc_by_rub_process(message: types.Message, state: FSMContext, repo: Repository):
    """
    Обрабатывает текстовый ввод пользователя суммы в рублях.
    Рассчитывает, какое максимальное количество звезд можно купить на эти деньги.
    """
    try:
        rub_amount = float(message.text.replace(",", "."))
        if rub_amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❗️ Пожалуйста, введите корректное положительное число.")
        return

    # Достаем актуальную стоимость звезды из БД
    star_price_str = await repo.get_setting('star_price')
    star_price = float(star_price_str) if star_price_str else 1.8
    if star_price == 0:
        await message.answer("❗️ Невозможно рассчитать, так как цена звезды равна нулю.")
        return
        
    stars_count = int(rub_amount / star_price)  # Рассчитываем количество доступных звезд

    await message.answer(f"₽ <b>{rub_amount:.2f}</b> ≈ <b>{stars_count:,} ⭐</b>")
    await state.clear()  # Очищаем состояние после успешного расчета