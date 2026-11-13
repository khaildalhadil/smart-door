#!/usr/bin/env python3
"""Check the Supabase set-up from the Pi, step by step.

    python3 tools/cloud_check.py

Reads SUPABASE_URL / SUPABASE_SERVICE_KEY from .env, then writes and removes
one test row and one test picture.  Safe to run while the app is running.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from app.cloud import auth_headers, iso_utc  # noqa: E402
from app.config import Config, env_value  # noqa: E402


def main() -> int:
    cfg = Config()
    url = env_value("SUPABASE_URL").rstrip("/")
    key = env_value("SUPABASE_SERVICE_KEY") or env_value("SUPABASE_KEY")
    table, bucket = cfg.get("cloud", "table"), cfg.get("cloud", "bucket")
    status_table = cfg.get("cloud", "status_table")
    if not url or not key:
        print("✗ .env: SUPABASE_URL أو SUPABASE_SERVICE_KEY غير موجود.")
        return 1
    if "publishable" in key or key.count(".") == 2 and '"anon"' in key:
        print("✗ هذا مفتاح publishable/anon. الـ Pi يحتاج المفتاح السري (sb_secret_...).")
        return 1
    print(f"• المشروع: {url}")
    s = requests.Session()
    s.headers.update(auth_headers(key))
    ok = True

    def step(name: str, response, hint: str) -> bool:
        nonlocal ok
        if response.status_code < 400:
            print(f"✓ {name}")
            return True
        ok = False
        print(f"✗ {name}: HTTP {response.status_code} {response.text[:160]}\n   ← {hint}")
        return False

    try:
        uid = f"check-{int(time.time())}"
        row = {"uid": uid, "ts": iso_utc(time.time()), "category": "system",
               "event": "cloud.check", "detail": "tools/cloud_check.py"}
        hint = "نفّذ supabase/schema.sql في SQL Editor، وتأكد من المفتاح السري."
        if step(f"الكتابة في الجدول {table}",
                s.post(f"{url}/rest/v1/{table}", params={"on_conflict": "uid"}, json=[row],
                       headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                       timeout=20), hint):
            step("حذف سطر التجربة",
                 s.delete(f"{url}/rest/v1/{table}", params={"uid": f"eq.{uid}"}, timeout=20), hint)
        step(f"قراءة الجدول {status_table}",
             s.get(f"{url}/rest/v1/{status_table}", params={"select": "id"}, timeout=20), hint)
        path = f"check/{uid}.jpg"
        hint = f"الـ bucket «{bucket}» غير موجود: نفّذ supabase/schema.sql."
        if step(f"رفع صورة إلى {bucket}",
                s.post(f"{url}/storage/v1/object/{bucket}/{path}", data=b"\xff\xd8\xff\xd9",
                       headers={"Content-Type": "image/jpeg", "x-upsert": "true"}, timeout=20),
                hint):
            step("حذف صورة التجربة",
                 s.delete(f"{url}/storage/v1/object/{bucket}", json={"prefixes": [path]},
                          timeout=20), hint)
    except requests.RequestException as exc:
        print(f"✗ لا يوجد اتصال: {exc}")
        return 1
    print("✓ كل شيء جاهز. أعد تشغيل البرنامج: systemctl --user restart smartlock" if ok
          else "✗ أصلح ما سبق ثم أعد الفحص.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
