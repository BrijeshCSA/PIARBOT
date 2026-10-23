# -*- coding: utf-8 -*-
"""Быстрая проверка env-переменных без запуска бота."""
import os
import sys
import vk_api
from vk_api.exceptions import ApiError

TOKEN    = os.getenv("VK_TOKEN", "").strip()
GROUP_ID = os.getenv("VK_GROUP_ID", "").lstrip("-").strip()
OWNER    = os.getenv("OWNER_ID", "").lstrip("-").removeprefix("id").strip()

missing = [n for n, v in
           [("VK_TOKEN", TOKEN), ("VK_GROUP_ID", GROUP_ID), ("OWNER_ID", OWNER)]
           if not v]
if missing:
    print("❌ Не заданы:", ", ".join(missing))
    sys.exit(1)

try:
    vk = vk_api.VkApi(token=TOKEN).get_api()

    g = vk.groups.getById(group_id=GROUP_ID)
    print(f"✅ Группа: {g[0]['name']} (id={GROUP_ID})")

    u = vk.users.get(user_ids=OWNER)
    print(f"✅ Владелец: {u[0]['first_name']} {u[0]['last_name']} (id={OWNER})")

    p = vk.groups.getTokenPermissions()
    print(f"✅ Права токена: mask={p.get('mask')}")

    print("\n🎉 Всё в порядке — можно запускать bot.py")
except ApiError as e:
    print(f"❌ VK API: {e} (code={e.code})")
    sys.exit(1)
