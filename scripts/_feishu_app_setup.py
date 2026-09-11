# -*- coding: utf-8 -*-
"""飞书应用 API 配置辅助：给定 App ID + Secret，列出应用所在的群（拿 chat_id）。

用法：
  backend\\.venv\\Scripts\\python.exe scripts\\_feishu_app_setup.py <APP_ID> <APP_SECRET>

输出：
  - tenant_access_token 是否能拿到（验证凭据有效）
  - 应用所在的群列表（chat_id + 群名），直接复制 chat_id 即可
"""
import json
import sys

import httpx

BASE = "https://open.feishu.cn/open-apis"


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python scripts/_feishu_app_setup.py <APP_ID> <APP_SECRET>")
        return 2
    app_id, app_secret = sys.argv[1].strip(), sys.argv[2].strip()

    # 1) 拿 tenant_access_token（验证凭据）
    with httpx.Client(timeout=15.0) as c:
        r = c.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                   json={"app_id": app_id, "app_secret": app_secret})
        data = r.json()
        if data.get("code") != 0:
            print(f"[失败] 凭据无效: code={data.get('code')} msg={data.get('msg')}")
            print("排查：App ID / App Secret 是否复制完整、应用是否已发布（版本管理中创建版本并发布）。")
            return 1
        token = data["tenant_access_token"]
        print(f"[OK] tenant_access_token 获取成功（有效期 {data.get('expire', 7200)}s）")

        headers = {"Authorization": f"Bearer {token}"}

        # 2) 列出应用所在的群（需要 im:chat:readonly 权限）
        r = c.get(f"{BASE}/im/v1/chats", headers=headers,
                  params={"page_size": 100})
        data = r.json()
        if data.get("code") != 0:
            print(f"[提示] 列群失败: code={data.get('code')} msg={data.get('msg')}")
            print("排查：应用缺 im:chat:readonly 权限。可去开放平台「权限管理」开通后重新发布版本；")
            print("      或手动拿 chat_id：把应用拉进目标群后，在群里 @应用 或看群信息。")
            return 0
        items = (data.get("data") or {}).get("items") or []
        if not items:
            print("[提示] 应用目前不在任何群里。请先把应用拉进目标群（群设置 → 群机器人/应用 → 添加），再重跑本脚本。")
            return 0
        print(f"[OK] 应用所在群（{len(items)} 个）：")
        for it in items:
            print(f"  chat_id={it.get('chat_id')}  群名={it.get('name')}")
        print()
        print("把目标群的 chat_id 复制给我即可（连同 App ID / App Secret）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
