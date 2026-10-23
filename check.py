# -*- coding: utf-8 -*-
"""Проверка env-переменных без запуска бота."""
import os, sys, vk_api
from vk_api.exceptions import ApiError

TOKEN    = os.getenv("VK_TOKEN", "").strip()
GROUP_ID = os.getenv("VK_GROUP_ID", "242540253").lstrip("-").strip() or "242540253"
OWNER    = os.getenv("OWNER_ID", "1062211087").lstrip("-").removeprefix("id").strip() or "1062211087"

if not TOKEN:
    print("❌ Не задан VK_TOKEN"); sys.exit(1)

try:
    vk = vk_api.VkApi(token=TOKEN, api_version='5.199').get_api()
    g = vk.groups.getById(group_id=GROUP_ID)
    print(f"✅ Группа: {g[0]['name']} (id={GROUP_ID})")
    u = vk.users.get(user_ids=OWNER)
    print(f"✅ Владелец: {u[0]['first_name']} {u[0]['last_name']} (id={OWNER})")
    p = vk.groups.getTokenPermissions()
    print(f"✅ Права токена: mask={p.get('mask')}")
    print("\n🎉 Всё в порядке — запускайте main.py")
except ApiError as e:
    print(f"❌ VK API: {e} (code={e.code})"); sys.exit(1)
