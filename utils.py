# /opt/telegram_wireguard/utils.py
import os
import time
import aiosqlite
from config import CONFIG
from telebot import types, asyncio_filters
import emoji as e
from datetime import datetime
from typing import List
from payment import getCostBySale   

# Базовые скидки по длительности (месяцы -> %)
_BASE_DISCOUNT = {1: 0, 3: 5, 6: 10, 12: 15}

def find_config_path(tgid: int, index: int = 1) -> str | None:
    """Search for WireGuard config path for a user."""
    nic = CONFIG.get("SERVER_WG_NIC", "wg0")
    new_path = f"/root/{nic}-client-{tgid}-{index}.conf"
    if os.path.exists(new_path) and os.path.getsize(new_path) > 0:
        return new_path
    if index == 1:
        old_path = f"/root/{nic}-client-{tgid}.conf"
        if os.path.exists(old_path) and os.path.getsize(old_path) > 0:
            return old_path
    return None


async def is_user_subscribed(bot, user_id: int) -> bool:
    """Check if user subscribed to promo channel."""
    channel = CONFIG.get("promo_channel")
    if not channel:
        return False
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status in ("member", "creator", "administrator")
    except Exception:
        return False


async def get_user_promo_percent(tgid: int) -> int:
    """Return promo percent for user if promo code active."""
    async with aiosqlite.connect(CONFIG["DATABASE_NAME"]) as db:
        c = await db.execute("SELECT active_promocode FROM userss WHERE tgid=?", (tgid,))
        row = await c.fetchone()
        if not row or not row[0]:
            return 0
        code = row[0]
        c = await db.execute(
            "SELECT discount_percent, active, expires_at, max_uses, used_count "
            "FROM promocodes WHERE code=?",
            (code,),
        )
        promo = await c.fetchone()
        if not promo:
            return 0
        now = int(time.time())
        if not promo[1]:
            return 0
        if promo[2] and promo[2] < now:
            return 0
        if promo[3] and promo[4] >= promo[3]:
            return 0
        return promo[0] or 0


# ────────────────────────────────────────────────────────────────────────────
#  И З М Е Н Ё Н Н А Я   Ф У Н К Ц И Я
# ────────────────────────────────────────────────────────────────────────────
async def apply_promocode_to_user(
    tgid: int, code: str
) -> tuple[bool, int, str]:
    """
    Попытаться применить промокод к пользователю.

    Returns:
        ok                – True, если применили
        discount_percent  – величина скидки или 0
        reason            – объяснение в случае неудачи / 'OK' при успехе
    """
    async with aiosqlite.connect(CONFIG["DATABASE_NAME"]) as db:
        # 1. Получаем сам промокод
        c = await db.execute(
            "SELECT discount_percent, active, expires_at, max_uses, used_count "
            "FROM promocodes WHERE code=?",
            (code,),
        )
        promo = await c.fetchone()
        if not promo:
            return False, 0, "Промокод не найден"

        discount, active, expires_at, max_uses, used_count = promo
        now = int(time.time())

        if not active:
            return False, 0, "Промокод не активен"
        if expires_at and expires_at < now:
            return False, 0, "Срок действия промокода истёк"
        if max_uses and used_count >= max_uses:
            return False, 0, "Лимит использования промокода исчерпан"

        # 2. Проверяем, нет ли уже промокода у пользователя
        c = await db.execute(
            "SELECT active_promocode FROM userss WHERE tgid=?", (tgid,)
        )
        row = await c.fetchone()
        if row and row[0]:
            return False, 0, "У вас уже активирован другой промокод"

        # 3. Применяем: записываем пользователю и увеличиваем счётчик
        await db.execute(
            "UPDATE userss SET active_promocode=? WHERE tgid=?", (code, tgid)
        )
        await db.execute(
            "UPDATE promocodes SET used_count = used_count + 1 WHERE code=?",
            (code,),
        )
        await db.commit()

        return True, discount, "OK"


def effective_discount(months: int, promo: int) -> int:
    """
    Возвращает тот процент, который будет отображаться на кнопке:
    берем max(базовая_скидка, promo) — без суммирования.
    """
    return max(_BASE_DISCOUNT.get(months, 0), promo)

def make_tariff_keyboard(
    promo_percent: int,
    show_one_device: bool = True,
    show_two_device: bool = True
) -> types.InlineKeyboardMarkup:
    """
    Генерирует InlineKeyboardMarkup с кнопками тарифов,
    учитывая кол-во устройств и промо-скидку.
    """
    kb = types.InlineKeyboardMarkup(row_width=1)
    months_list: List[int] = [1, 3, 6, 12]

    # helper-функция для одной строки
    def _add(device_cnt: int):
        for m in months_list:
            price = getCostBySale(m, device_cnt, promo_percent)  # уже есть в твоём коде
            disc = effective_discount(m, promo_percent)
            disc_txt = f" (-{disc}%)" if disc else ""
            icon = "📱" if device_cnt == 1 else "📱💻"
            kb.add(
                types.InlineKeyboardButton(
                    e.emojize(f"{icon} {device_cnt} устройство{'а' if device_cnt==2 else ''} - "
                              f"{m} мес. {price} руб.{disc_txt}"),
                    callback_data=f"SelectPlan:{m}:{device_cnt}"
                )
            )

    if show_one_device:
        _add(1)
    if show_two_device:
        _add(2)

    # общая кнопка рефералов
    kb.add(types.InlineKeyboardButton(e.emojize("🎁 Бесплатно +1 неделя за нового друга"),
                                      callback_data="Referrer"))
    return kb
