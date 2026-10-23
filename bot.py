# -*- coding: utf-8 -*-
"""
VK-бот для пиара.
Длинный polling, callback-кнопки, оплата через баланс, роли, рассылка.

Переменные окружения (BotHost → Environment):
    VK_TOKEN        — токен группы (Управление → Работа с API → Ключи доступа)
    VK_GROUP_ID     — ID группы (без минуса или с ним)
    OWNER_ID        — ваш user_id в VK
    COMMUNITY_LINK  — ссылка на сообщество (для бонуса 100₽)
"""
import os
import sys
import re
import json
import random
import logging
import threading
import time
from datetime import datetime

import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
from vk_api.exceptions import ApiError

import db

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("piar-vk")


# ============================================================
# КОНФИГ + ВАЛИДАЦИЯ
# ============================================================
class ConfigError(Exception):
    pass


def _env(name: str, required: bool = True, default: str = "") -> str:
    val = os.getenv(name, "").strip()
    if required and not val:
        raise ConfigError(
            f"❌ Не задана переменная окружения {name}\n"
            f"   Добавьте её в панели BotHost → Environment / Secrets"
        )
    return val or default


def _parse_int(name: str, value: str, positive: bool = True) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"❌ {name} = '{value}' — должно быть целым числом")
    if positive and n <= 0:
        raise ConfigError(f"❌ {name} = {n} — должно быть положительным (> 0)")
    return n


def _fail(errors):
    print("=" * 60, file=sys.stderr)
    print("ПРОВЕРКА КОНФИГА НЕ ПРОЙДЕНА", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    for e in errors:
        print(e, file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)


try:
    VK_TOKEN       = _env("VK_TOKEN")
    GROUP_ID_RAW   = _env("VK_GROUP_ID")
    OWNER_ID_RAW   = _env("OWNER_ID")
    COMMUNITY_LINK = _env("COMMUNITY_LINK", required=False, default="https://vk.com/")

    if not re.match(r"^(vk1\.[a-z0-9]\.[A-Za-z0-9_\-\.]+|[0-9a-fA-F]{32,})$", VK_TOKEN):
        raise ConfigError(
            "❌ VK_TOKEN не похож на токен VK.\n"
            "   Ожидается 'vk1.a.xxx…' или 32+ hex-символов.\n"
            "   Получите токен: Управление → Работа с API → Ключи доступа."
        )
    if len(VK_TOKEN) < 30:
        raise ConfigError("❌ VK_TOKEN слишком короткий — скопируйте полностью.")

    GROUP_ID = _parse_int("VK_GROUP_ID", GROUP_ID_RAW.lstrip("-"), positive=True)
    OWNER_ID = _parse_int(
        "OWNER_ID", OWNER_ID_RAW.lstrip("-").removeprefix("id"), positive=True
    )
    if OWNER_ID > 2_147_483_647:
        raise ConfigError(f"❌ OWNER_ID = {OWNER_ID} вне диапазона VK.")

    if not COMMUNITY_LINK.startswith(("https://", "http://")):
        raise ConfigError(f"❌ COMMUNITY_LINK = '{COMMUNITY_LINK}' — нужна https://-ссылка.")

except ConfigError as e:
    _fail([str(e)])


# ============================================================
# VK СЕССИЯ
# ============================================================
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()
longpoll = None  # создаём в main() после startup_check()

# состояния пользователей: user_id -> {"state": ..., данные}
user_states: dict = {}


# ============================================================
# ХЕЛПЕРЫ
# ============================================================
def price_for(interval: int) -> int:
    """1 мин → 1000₽ ... 60 мин → 50₽ (линейно)."""
    interval = max(1, min(60, int(interval)))
    return round(1000 - (interval - 1) * (950 / 59))


def send(peer_id: int, message: str, keyboard=None, attachment=None):
    params = {
        "peer_id": peer_id,
        "message": message,
        "random_id": random.randint(1, 2**31 - 1),
        "dont_parse_links": 0,
    }
    if keyboard is not None:
        params["keyboard"] = keyboard.get_keyboard()
    if attachment:
        params["attachment"] = attachment
    try:
        vk.messages.send(**params)
    except ApiError as e:
        log.warning(f"send to {peer_id} failed: {e}")


def ack_event(event):
    """Обязательный ответ на callback — иначе кнопка «крутится»."""
    try:
        vk.messages.sendMessageEventAnswer(
            event_id=event.object["event_id"],
            user_id=event.object["user_id"],
            peer_id=event.object["peer_id"],
        )
    except Exception as e:
        log.warning(f"ack failed: {e}")


def notify_staff(text: str):
    targets = set()
    if OWNER_ID:
        targets.add(OWNER_ID)
    for r in db.fetchall("SELECT user_id FROM roles WHERE role='deputy'"):
        targets.add(r["user_id"])
    for t in targets:
        send(t, text)


# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def kb_main():
    k = VkKeyboard(one_time=False)
    k.add_button("📢 Пиар",      color=VkKeyboardColor.POSITIVE)
    k.add_button("💰 Баланс",    color=VkKeyboardColor.PRIMARY)
    k.add_line()
    k.add_button("📋 Мои пиары", color=VkKeyboardColor.SECONDARY)
    k.add_button("🟢 Активные",  color=VkKeyboardColor.SECONDARY)
    k.add_line()
    k.add_button("🎁 Подписка",  color=VkKeyboardColor.PRIMARY)
    return k


def kb_piar_times():
    popular = [1, 2, 5, 10, 15, 20, 30, 45, 60]
    k = VkKeyboard(inline=True)
    for i, m in enumerate(popular):
        k.add_callback_button(
            f"{m}м · {price_for(m)}₽",
            color=VkKeyboardColor.PRIMARY,
            payload={"cmd": "piar_time", "i": m},
        )
        if (i + 1) % 3 == 0 and i < len(popular) - 1:
            k.add_line()
    k.add_line()
    k.add_callback_button(
        "❓ Другой интервал",
        color=VkKeyboardColor.SECONDARY,
        payload={"cmd": "piar_custom"},
    )
    return k


def kb_sub():
    k = VkKeyboard(inline=True)
    k.add_openlink_button("📢 Подписаться", link=COMMUNITY_LINK)
    k.add_callback_button(
        "✅ Я подписался",
        color=VkKeyboardColor.POSITIVE,
        payload={"cmd": "check_sub"},
    )
    return k


# ============================================================
# КОМАНДЫ
# ============================================================
def cmd_start(peer_id, user_id, first_name=""):
    user_states.pop(user_id, None)
    db.ensure_user(user_id, None, first_name or None)
    send(
        peer_id,
        f"👋 Привет, {first_name or 'друг'}!\n\n"
        f"💰 Баланс: {db.get_balance(user_id)}₽\n"
        f"🆔 Ваш ID: {user_id}\n\n"
        f"Команды:\n"
        f"• /пиар — создать пиар\n"
        f"• /моипиары — ваши пиары\n"
        f"• /активныепиары — активные пиары\n"
        f"• /баланс — баланс\n"
        f"• /подписка — бонус 100₽\n"
        f"• /cancel — отмена",
        keyboard=kb_main(),
    )


def cmd_balance(peer_id, user_id):
    db.ensure_user(user_id)
    send(peer_id, f"💰 Баланс: {db.get_balance(user_id)}₽")


def cmd_sub(peer_id, user_id):
    db.ensure_user(user_id)
    r = db.fetchone("SELECT subscribed FROM users WHERE user_id=?", (user_id,))
    if r and r["subscribed"]:
        send(peer_id, "✅ Вы уже получили бонус.")
        return
    send(peer_id, "🎁 Подпишитесь на сообщество и получите 100₽!", keyboard=kb_sub())


def check_subscription(peer_id, user_id):
    try:
        ok = vk.groups.isMember(group_id=GROUP_ID, user_id=user_id)
    except Exception as e:
        send(peer_id, f"Ошибка проверки подписки: {e}")
        return
    if not ok:
        send(peer_id, "❌ Вы не подписаны на сообщество.")
        return
    if db.set_subscribed(user_id):
        send(
            peer_id,
            f"✅ Спасибо! Начислено 100₽\n"
            f"💰 Баланс: {db.get_balance(user_id)}₽",
        )
    else:
        send(peer_id, "✅ Бонус уже получен.")


def cmd_piar(peer_id, user_id):
    user_states.pop(user_id, None)
    db.ensure_user(user_id)
    send(
        peer_id,
        f"💰 Баланс: {db.get_balance(user_id)}₽\n\n"
        f"Выберите интервал повтора пиара.\n"
        f"Чем чаще — тем дороже: 1м = 1000₽ … 60м = 50₽",
        keyboard=kb_piar_times(),
    )


def cmd_my_ads(peer_id, user_id):
    db.ensure_user(user_id)
    ads = db.fetchall("SELECT * FROM ads WHERE user_id=? ORDER BY id DESC", (user_id,))
    if not ads:
        send(peer_id, "У вас пока нет пиаров. Введите /пиар")
        return
    lines = ["📋 Ваши пиары:\n"]
    for a in ads:
        icon = "🟢" if a["status"] == "active" else "🔴"
        lines.append(f"{icon} #{a['id']} · {a['interval_min']}м · {a['price']}₽")
        lines.append(f"   {a['text'][:60]}")
    send(peer_id, "\n".join(lines))


def cmd_active_ads(peer_id):
    ads = db.fetchall("SELECT * FROM ads WHERE status='active' ORDER BY id DESC")
    if not ads:
        send(peer_id, "Нет активных пиаров.")
        return
    lines = ["🟢 Активные пиары:\n"]
    for a in ads:
        lines.append(
            f"#{a['id']} · id{a['user_id']} · {a['interval_min']}м · {a['price']}₽"
        )
    send(peer_id, "\n".join(lines))


def start_piar_payment(peer_id, user_id, interval):
    interval = int(interval)
    price = price_for(interval)
    db.ensure_user(user_id)

    if db.get_balance(user_id) >= price:
        if not db.deduct_balance(user_id, price):
            send(peer_id, "Ошибка списания.")
            return
        user_states[user_id] = {
            "state": "waiting_ad_text",
            "interval": interval,
            "price": price,
        }
        send(
            peer_id,
            f"✅ Списано {price}₽ (интервал {interval} мин)\n"
            f"💰 Остаток: {db.get_balance(user_id)}₽\n\n"
            f"Отправьте текст пиара следующим сообщением.",
        )
        return

    oid = db.create_order(user_id, interval, price)
    send(
        peer_id,
        f"💸 Недостаточно средств.\n\n"
        f"Заказ #{oid} · {interval} мин · {price}₽\n\n"
        f"1️⃣ Оплатите {price}₽ владельцу\n"
        f"2️⃣ Владелец подтвердит: /piar_ok {oid}\n"
        f"3️⃣ Затем снова нажмите «📢 Пиар»",
    )
    notify_staff(
        f"🔔 Новый заказ #{oid}\n\n"
        f"👤 id{user_id} · на балансе {db.get_balance(user_id)}₽\n"
        f"⏱ {interval} мин · 💰 {price}₽\n\n"
        f"После оплаты: /piar_ok {oid}"
    )


def handle_ad_text(peer_id, user_id, text):
    data = user_states.get(user_id, {})
    interval = data.get("interval")
    price = data.get("price")
    user_states.pop(user_id, None)
    if not interval:
        send(peer_id, "Сессия истекла. Начните заново: /пиар")
        return
    ad_id = db.create_ad(user_id, text, interval, price)
    send(peer_id, f"✅ Пиар #{ad_id} активирован\n⏱ {interval} мин · 💰 {price}₽")


def cmd_piar_ok(peer_id, user_id, args):
    if not db.has_min_role(user_id, "deputy", OWNER_ID):
        send(peer_id, "❌ Нет прав.")
        return
    if len(args) != 1 or not args[0].isdigit():
        send(peer_id, "Использование: /piar_ok <номер>")
        return
    order = db.fund_order(int(args[0]), user_id)
    if not order:
        send(peer_id, "❌ Заказ не найден или уже обработан.")
        return
    send(peer_id, f"✅ Заказ #{order['id']} подтверждён. Зачислено {order['price']}₽")
    send(
        order["user_id"],
        f"✅ Оплата подтверждена!\n"
        f"Заказ #{order['id']}\n"
        f"Зачислено: {order['price']}₽\n"
        f"💰 Баланс: {db.get_balance(order['user_id'])}₽\n\n"
        f"Теперь нажмите «📢 Пиар»",
    )


def cmd_role(peer_id, user_id, args):
    if not db.has_min_role(user_id, "admin", OWNER_ID):
        send(peer_id, "❌ Нет прав.")
        return
    if len(args) != 2:
        send(peer_id, "Использование: /role <id|@id> <deputy|admin|moderator>")
        return
    target, role = args[0], args[1].lower()
    if role not in ("deputy", "admin", "moderator"):
        send(peer_id, "Роль: deputy | admin | moderator")
        return
    try:
        uid = int(target.replace("@", "").replace("id", "").strip())
    except ValueError:
        send(peer_id, "❌ Неверный ID.")
        return
    if role == "deputy" and user_id != OWNER_ID:
        send(peer_id, "Только владелец назначает заместителей.")
        return
    if role == "admin" and not db.has_min_role(user_id, "deputy", OWNER_ID):
        send(peer_id, "Нужны права заместителя.")
        return
    db.grant_role(uid, role, user_id)
    send(peer_id, f"✅ id{uid} → {role}")


def cmd_roles(peer_id):
    lines = ["👥 Роли:\n"]
    if OWNER_ID:
        lines.append(f"👑 owner: id{OWNER_ID}")
    rows = db.fetchall("SELECT user_id, role FROM roles ORDER BY role, user_id")
    if not rows:
        lines.append("(нет других)")
    for r in rows:
        ic = {"deputy": "🥈", "admin": "🥉", "moderator": "🔹"}.get(r["role"], "")
        lines.append(f"{ic} {r['role']}: id{r['user_id']}")
    send(peer_id, "\n".join(lines))


def cmd_stats(peer_id):
    s = db.stats()
    send(
        peer_id,
        f"📊 Статистика\n\n"
        f"👥 Пользователей: {s['users']}\n"
        f"💬 Чатов: {s['chats']}\n"
        f"🟢 Активных пиаров: {s['active']}\n"
        f"🧾 Заказов: {s['orders']}\n"
        f"💰 Подтверждено: {s['funded']}₽",
    )


# ============================================================
# ДИСПЕТЧЕР ТЕКСТА
# ============================================================
BUTTON_MAP = {
    "📢 пиар":      "piar",
    "💰 баланс":    "balance",
    "📋 мои пиары": "myads",
    "🟢 активные":  "active",
    "🎁 подписка":  "sub",
}


def dispatch_text(peer_id, user_id, text, first_name=""):
    st = user_states.get(user_id, {})

    if st.get("state") == "waiting_ad_text":
        if text.lower() in ("/cancel", "отмена", "/отмена"):
            user_states.pop(user_id, None)
            send(peer_id, "❌ Отменено.")
            return
        handle_ad_text(peer_id, user_id, text)
        return

    if st.get("state") == "waiting_custom_interval":
        try:
            iv = int(text)
            if 1 <= iv <= 60:
                user_states.pop(user_id, None)
                start_piar_payment(peer_id, user_id, iv)
            else:
                send(peer_id, "Введите число от 1 до 60.")
        except ValueError:
            send(peer_id, "Введите число от 1 до 60.")
        return

    low = text.strip().lower()

    if low in BUTTON_MAP:
        cmd = BUTTON_MAP[low]
        if cmd == "piar":      cmd_piar(peer_id, user_id)
        elif cmd == "balance": cmd_balance(peer_id, user_id)
        elif cmd == "myads":   cmd_my_ads(peer_id, user_id)
        elif cmd == "active":  cmd_active_ads(peer_id)
        elif cmd == "sub":     cmd_sub(peer_id, user_id)
        return

    if not low.startswith("/"):
        send(peer_id, "Неизвестная команда. Список: /start")
        return

    parts = low[1:].split()
    if not parts:
        return
    cmd, args = parts[0], parts[1:]

    if cmd in ("start", "начать", "меню"):         cmd_start(peer_id, user_id, first_name)
    elif cmd in ("пиар", "piar"):                   cmd_piar(peer_id, user_id)
    elif cmd in ("моипиары", "myads"):              cmd_my_ads(peer_id, user_id)
    elif cmd in ("активныепиары", "active"):        cmd_active_ads(peer_id)
    elif cmd in ("баланс", "balance"):              cmd_balance(peer_id, user_id)
    elif cmd in ("подписка", "subscribe"):          cmd_sub(peer_id, user_id)
    elif cmd in ("cancel", "отмена"):               user_states.pop(user_id, None); send(peer_id, "❌ Отменено.")
    elif cmd == "piar_ok":                          cmd_piar_ok(peer_id, user_id, args)
    elif cmd == "role":                             cmd_role(peer_id, user_id, args)
    elif cmd in ("roles", "staff"):                 cmd_roles(peer_id)
    elif cmd in ("стата", "stats", "stat"):         cmd_stats(peer_id)
    else:
        send(peer_id, "Неизвестная команда. Список: /start")


# ============================================================
# ОБРАБОТКА СОБЫТИЙ
# ============================================================
def handle_message(event):
    msg = event.object.message
    peer_id = msg["peer_id"]
    user_id = msg["from_id"]
    text = (msg.get("text") or "").strip()

    if user_id <= 0:
        return

    # групповые беседы: peer_id >= 2_000_000_000
    if peer_id >= 2_000_000_000:
        db.add_chat(peer_id, "")

    first = ""
    if text.lower().startswith(("/start", "/начать")):
        try:
            users = vk.users.get(user_ids=user_id)
            if users:
                first = users[0].get("first_name", "")
        except Exception:
            pass

    dispatch_text(peer_id, user_id, text, first)


def handle_callback(event):
    ack_event(event)
    payload = json.loads(event.object.get("payload") or "{}")
    cmd = payload.get("cmd")
    user_id = event.object["user_id"]
    peer_id = event.object["peer_id"]

    if cmd == "piar_time":
        start_piar_payment(peer_id, user_id, payload["i"])
    elif cmd == "piar_custom":
        user_states[user_id] = {"state": "waiting_custom_interval"}
        send(peer_id, "Введите желаемый интервал (1–60 минут):")
    elif cmd == "check_sub":
        check_subscription(peer_id, user_id)


# ============================================================
# ФОНОВАЯ РАССЫЛКА
# ============================================================
def broadcast_loop():
    log.info("Broadcast loop started")
    while True:
        try:
            for ad in db.list_active_ads():
                last = ad["last_sent"]
                if last:
                    dt = datetime.fromisoformat(last)
                    if (datetime.now() - dt).total_seconds() < ad["interval_min"] * 60:
                        continue
                text = f"📢 Пиар #{ad['id']}\n\n{ad['text']}"
                sent_any = False
                for peer in db.list_chats():
                    try:
                        vk.messages.send(
                            peer_id=peer,
                            message=text,
                            random_id=random.randint(1, 2**31 - 1),
                        )
                        sent_any = True
                    except ApiError as e:
                        log.debug(f"peer {peer}: {e}")
                    time.sleep(0.05)
                if sent_any:
                    db.mark_ad_sent(ad["id"])
        except Exception:
            log.exception("broadcast error")
        time.sleep(30)


# ============================================================
# ПРОВЕРКА КОНФИГА ПЕРЕД ЗАПУСКОМ
# ============================================================
def startup_check():
    errors = []

    try:
        me = vk.groups.getById(group_id=GROUP_ID)
        if not me:
            errors.append(f"❌ Группа {GROUP_ID} не найдена или нет доступа")
        else:
            log.info(f"✅ Токен валиден. Группа: «{me[0]['name']}» (id={GROUP_ID})")
    except ApiError as e:
        errors.append(f"❌ VK API: {e} (code={e.code})")
    except Exception as e:
        errors.append(f"❌ Сеть/VK: {e}")

    if errors:
        _fail(errors)

    try:
        perms = vk.groups.getTokenPermissions()
        mask = perms.get("mask", 0)
        if not (mask & 4):
            errors.append(
                "❌ У токена нет права 'messages'.\n"
                "   Пересоздайте токен и отметьте «Сообщения сообщества»."
            )
        else:
            log.info("✅ Право 'messages' есть.")
    except Exception as e:
        log.warning(f"Не удалось проверить права (не критично): {e}")

    try:
        users = vk.users.get(
            user_ids=OWNER_ID,
            fields="can_write_private_message,first_name,last_name",
        )
        if not users:
            errors.append(f"❌ Пользователь {OWNER_ID} не найден")
        else:
            u = users[0]
            name = f"{u.get('first_name','')} {u.get('last_name','')}".strip()
            log.info(f"✅ Владелец: {name} (id={OWNER_ID})")
            if u.get("can_write_private_message") == 0:
                log.warning(
                    f"⚠️ Владелец {OWNER_ID} запретил ЛС от сообществ — "
                    f"уведомления не придут, пока он не разрешит."
                )
    except ApiError as e:
        errors.append(f"❌ Проверка OWNER_ID: {e}")

    if errors:
        _fail(errors)

    log.info("✅ Все проверки пройдены. Запускаю LongPoll…")


# ============================================================
# MAIN
# ============================================================
def main():
    global longpoll
    log.info("=" * 50)
    log.info("VK PIAR BOT — запуск")
    log.info(f"  GROUP_ID: {GROUP_ID}")
    log.info(f"  OWNER_ID: {OWNER_ID}")
    log.info(f"  TOKEN:    {VK_TOKEN[:8]}…{VK_TOKEN[-4:]} (len={len(VK_TOKEN)})")
    log.info("=" * 50)

    startup_check()

    longpoll = VkBotLongPoll(vk_session, GROUP_ID)
    log.info("✅ LongPoll подключён")

    threading.Thread(target=broadcast_loop, daemon=True).start()

    log.info("🎧 Слушаю события…")
    for event in longpoll.listen():
        try:
            if event.type == VkBotEventType.MESSAGE_NEW:
                handle_message(event)
            elif event.type == VkBotEventType.MESSAGE_EVENT:
                handle_callback(event)
        except Exception:
            log.exception("event error")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Остановлено вручную.")
    except SystemExit:
        raise
    except Exception as e:
        log.exception(f"Фатальная ошибка: {e}")
        sys.exit(1)
