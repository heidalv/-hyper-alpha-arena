"""
飞书通知服务 — FeishuNotifier

支持两种推送模式（可同时启用）：
  1. Webhook 机器人 — 最简单，只需群里添加自定义机器人获取 webhook URL
  2. 飞书应用 API — 复用 OpenClaw 配置的 appId/appSecret，可发送到指定群或用户

所有发送均异步 + 静默降级，绝不阻塞交易主流程。
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ─── 配置文件路径 ───
_CONFIG_DIR = Path(os.environ.get("ALPHA_DATA_DIR", "data"))
_NOTIFY_CONFIG_PATH = _CONFIG_DIR / "notification_config.json"

# ─── 飞书 API 基地址 ───
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"


class NotifyLevel(str, Enum):
    """通知优先级"""
    INFO = "info"           # 常规信息（开仓等）
    WARNING = "warning"     # 警告（止损、浮亏较大）
    CRITICAL = "critical"   # 紧急（爆仓预警、系统异常）


@dataclass
class NotifyConfig:
    """通知配置"""
    enabled: bool = False

    # 模式 1: Webhook 机器人
    webhook_url: str = ""

    # 模式 2: 飞书应用 API
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_chat_id: str = ""       # 目标群 chat_id

    # 通知级别过滤
    min_level: str = "info"        # 最低通知级别
    enable_open: bool = True       # 开仓通知
    enable_close: bool = True      # 平仓通知
    enable_tp_sl: bool = True      # 止盈止损通知
    enable_liquidation: bool = True  # 爆仓预警通知
    enable_system: bool = True     # 系统事件通知

    # Alpha 助手飞书
    feishu_assistant_enabled: bool = False
    assistant_notify_actions: bool = True
    assistant_notify_p0: bool = True
    assistant_daily_report_enabled: bool = True

    # 频率控制
    min_interval_seconds: int = 5  # 同类消息最小间隔(秒)


class FeishuNotifier:
    """飞书通知服务单例"""

    _instance: Optional["FeishuNotifier"] = None

    def __init__(self):
        self._config = NotifyConfig()
        self._tenant_token: str = ""
        self._tenant_token_expire: float = 0
        self._last_send_time: Dict[str, float] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._load_config()
        self._try_load_openclaw_credentials()

    @classmethod
    def get_instance(cls) -> "FeishuNotifier":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ─── 配置管理 ───

    def _load_config(self):
        """从本地 JSON 加载配置"""
        try:
            if _NOTIFY_CONFIG_PATH.exists():
                raw = json.loads(_NOTIFY_CONFIG_PATH.read_text(encoding="utf-8"))
                for k, v in raw.items():
                    if hasattr(self._config, k):
                        setattr(self._config, k, v)
                logger.info("[Notify] 配置已加载: enabled=%s webhook=%s app=%s",
                            self._config.enabled,
                            bool(self._config.webhook_url),
                            bool(self._config.feishu_app_id))
        except Exception as e:
            logger.warning("[Notify] 加载配置失败: %s", e)

    def _try_load_openclaw_credentials(self):
        """尝试从 OpenClaw 配置中自动读取飞书 appId/appSecret"""
        if self._config.feishu_app_id:
            return
        try:
            oc_path = Path.home() / ".openclaw" / "openclaw.json"
            if not oc_path.exists():
                return
            oc = json.loads(oc_path.read_text(encoding="utf-8"))
            feishu_cfg = oc.get("channels", {}).get("feishu", {})
            if not feishu_cfg.get("enabled"):
                return
            app_id = feishu_cfg.get("appId", "")
            app_secret = feishu_cfg.get("appSecret", "")
            if app_id and app_secret:
                self._config.feishu_app_id = app_id
                self._config.feishu_app_secret = app_secret
                logger.info("[Notify] 已从 OpenClaw 配置自动获取飞书凭据: appId=%s***", app_id[:8])
        except Exception as e:
            logger.debug("[Notify] 读取 OpenClaw 配置失败: %s", e)

    def save_config(self, patch: Dict[str, Any]):
        """更新并保存配置"""
        for k, v in patch.items():
            if hasattr(self._config, k):
                setattr(self._config, k, v)
        try:
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _NOTIFY_CONFIG_PATH.write_text(
                json.dumps(self._config.__dict__, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[Notify] 配置已保存")
        except Exception as e:
            logger.error("[Notify] 保存配置失败: %s", e)

    def get_config(self) -> Dict[str, Any]:
        """返回当前配置（脱敏）"""
        cfg = self._config.__dict__.copy()
        if cfg.get("feishu_app_secret"):
            cfg["feishu_app_secret"] = cfg["feishu_app_secret"][:4] + "****"
        if cfg.get("webhook_url"):
            url = cfg["webhook_url"]
            if len(url) > 30:
                cfg["webhook_url"] = url[:30] + "****"
        return cfg

    # ─── HTTP 客户端 ───

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    # ─── 飞书 tenant_access_token ───

    async def _ensure_tenant_token(self) -> str:
        """获取或刷新 tenant_access_token"""
        if self._tenant_token and time.time() < self._tenant_token_expire - 300:
            return self._tenant_token

        if not self._config.feishu_app_id or not self._config.feishu_app_secret:
            return ""

        try:
            client = await self._get_client()
            resp = await client.post(
                f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self._config.feishu_app_id,
                    "app_secret": self._config.feishu_app_secret,
                },
            )
            data = resp.json()
            if data.get("code") == 0:
                self._tenant_token = data["tenant_access_token"]
                self._tenant_token_expire = time.time() + data.get("expire", 7200)
                logger.info("[Notify] 飞书 tenant_access_token 获取成功，有效期 %ds",
                            data.get("expire", 7200))
                return self._tenant_token
            else:
                logger.error("[Notify] 获取 tenant_token 失败: code=%s msg=%s",
                             data.get("code"), data.get("msg"))
                return ""
        except Exception as e:
            logger.error("[Notify] 获取 tenant_token 异常: %s", e)
            return ""

    # ─── 发送方法 ───

    async def _send_webhook(self, text: str, title: str = "") -> bool:
        """通过 Webhook 机器人发送"""
        if not self._config.webhook_url:
            return False
        try:
            client = await self._get_client()
            # 飞书自定义机器人 Webhook 格式
            payload: Dict[str, Any]
            if title:
                payload = {
                    "msg_type": "interactive",
                    "card": {
                        "header": {
                            "title": {"tag": "plain_text", "content": title},
                            "template": "blue",
                        },
                        "elements": [
                            {"tag": "markdown", "content": text}
                        ],
                    },
                }
            else:
                payload = {
                    "msg_type": "text",
                    "content": {"text": text},
                }

            resp = await client.post(self._config.webhook_url, json=payload)
            data = resp.json()
            ok = data.get("code") == 0 or data.get("StatusCode") == 0
            if not ok:
                logger.warning("[Notify] Webhook 发送失败: %s", data)
            return ok
        except Exception as e:
            logger.error("[Notify] Webhook 发送异常: %s", e)
            return False

    async def _send_app_api_to_chat(self, chat_id: str, text: str, title: str = "") -> bool:
        """通过飞书应用 API 发送到指定 chat_id（助手回复 / 双向对话）。"""
        if not chat_id:
            return False
        token = await self._ensure_tenant_token()
        if not token:
            return False
        try:
            client = await self._get_client()
            if title:
                post_content = {
                    "zh_cn": {
                        "title": title,
                        "content": [[{"tag": "text", "text": text}]],
                    }
                }
                payload = {
                    "receive_id": chat_id,
                    "msg_type": "post",
                    "content": json.dumps(post_content),
                }
            else:
                payload = {
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                }
            resp = await client.post(
                f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            data = resp.json()
            ok = data.get("code") == 0
            if not ok:
                logger.warning("[Notify] 飞书 reply 失败: code=%s msg=%s", data.get("code"), data.get("msg"))
            return ok
        except Exception as e:
            logger.error("[Notify] 飞书 reply 异常: %s", e)
            return False

    async def _send_app_api(self, text: str, title: str = "") -> bool:
        """通过飞书应用 API 发送到指定群"""
        if not self._config.feishu_chat_id:
            return False

        token = await self._ensure_tenant_token()
        if not token:
            return False

        try:
            client = await self._get_client()

            if title:
                content = json.dumps({
                    "type": "template",
                    "data": {
                        "template_id": "",
                        "template_variable": {},
                    },
                })
                # 使用富文本消息
                post_content = {
                    "zh_cn": {
                        "title": title,
                        "content": [[{"tag": "text", "text": text}]],
                    }
                }
                payload = {
                    "receive_id": self._config.feishu_chat_id,
                    "msg_type": "post",
                    "content": json.dumps(post_content),
                }
            else:
                payload = {
                    "receive_id": self._config.feishu_chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}),
                }

            resp = await client.post(
                f"{FEISHU_API_BASE}/im/v1/messages?receive_id_type=chat_id",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            data = resp.json()
            ok = data.get("code") == 0
            if not ok:
                logger.warning("[Notify] 飞书 App API 发送失败: code=%s msg=%s",
                               data.get("code"), data.get("msg"))
            return ok
        except Exception as e:
            logger.error("[Notify] 飞书 App API 发送异常: %s", e)
            return False

    # ─── 统一发送入口 ───

    async def send(
        self,
        text: str,
        title: str = "",
        level: NotifyLevel = NotifyLevel.INFO,
        event_type: str = "general",
    ) -> bool:
        """
        发送通知消息。

        :param text: 消息正文
        :param title: 标题（可选，webhook 会渲染为卡片）
        :param level: 通知级别
        :param event_type: 事件类型 (open/close/tp_sl/liquidation/system)
        :return: 是否至少有一个渠道发送成功
        """
        if not self._config.enabled:
            return False

        # 级别过滤
        level_order = {"info": 0, "warning": 1, "critical": 2}
        if level_order.get(level.value, 0) < level_order.get(self._config.min_level, 0):
            return False

        # 事件类型过滤
        type_map = {
            "open": self._config.enable_open,
            "close": self._config.enable_close,
            "tp_sl": self._config.enable_tp_sl,
            "liquidation": self._config.enable_liquidation,
            "system": self._config.enable_system,
        }
        if event_type in type_map and not type_map[event_type]:
            return False

        # 频率控制
        now = time.time()
        dedup_key = f"{event_type}:{title[:50]}"
        last = self._last_send_time.get(dedup_key, 0)
        if now - last < self._config.min_interval_seconds:
            logger.debug("[Notify] 频率控制跳过: %s (间隔 %.1fs < %ds)",
                         dedup_key, now - last, self._config.min_interval_seconds)
            return False
        self._last_send_time[dedup_key] = now
        # 防止去重字典无限增长
        if len(self._last_send_time) > 500:
            cutoff = now - 600
            self._last_send_time = {k: v for k, v in self._last_send_time.items()
                                    if v > cutoff}

        # 添加时间戳
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        full_text = f"[{ts}]\n{text}"

        # 并行尝试两种渠道
        results = await asyncio.gather(
            self._send_webhook(full_text, title),
            self._send_app_api(full_text, title),
            return_exceptions=True,
        )

        success = any(r is True for r in results)
        if success:
            logger.info("[Notify] ✅ 通知已发送: [%s] %s", event_type, title or text[:30])
        else:
            logger.warning("[Notify] ⚠️ 所有渠道发送失败: %s", results)

        return success

    async def test_connection(self) -> Dict[str, Any]:
        """测试所有已配置渠道的连通性"""
        results: Dict[str, Any] = {
            "webhook": {"configured": bool(self._config.webhook_url), "ok": False},
            "app_api": {
                "configured": bool(self._config.feishu_app_id and self._config.feishu_chat_id),
                "ok": False,
            },
        }

        test_text = "🔔 Alpha Arena 通知测试 — 如果你看到这条消息，说明通知配置成功！"

        if self._config.webhook_url:
            results["webhook"]["ok"] = await self._send_webhook(test_text, "通知测试")

        if self._config.feishu_app_id and self._config.feishu_chat_id:
            results["app_api"]["ok"] = await self._send_app_api(test_text, "通知测试")

        # 即使 chat_id 未配置，也测试 token 获取
        if self._config.feishu_app_id and not self._config.feishu_chat_id:
            token = await self._ensure_tenant_token()
            results["app_api"]["token_ok"] = bool(token)
            results["app_api"]["note"] = "凭据有效但未配置目标群 chat_id"

        return results

    def send_sync(
        self,
        text: str,
        title: str = "",
        *,
        level: str = "info",
        event_type: str = "system",
    ) -> bool:
        """同步发送（供飞书 bridge / 定时任务调用）。"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(
                        asyncio.run,
                        self.send(text, title=title, level=NotifyLevel(level), event_type=event_type),
                    )
                    return bool(fut.result(timeout=30))
            return loop.run_until_complete(
                self.send(text, title=title, level=NotifyLevel(level), event_type=event_type)
            )
        except RuntimeError:
            return asyncio.run(
                self.send(text, title=title, level=NotifyLevel(level), event_type=event_type)
            )
        except Exception as exc:
            logger.warning("[Notify] send_sync failed: %s", exc)
            return False

    def send_sync_text_to_chat(self, chat_id: str, text: str, title: str = "") -> bool:
        """同步回复指定飞书群（双向对话）。"""
        try:
            return asyncio.run(self._send_app_api_to_chat(chat_id, text, title=title))
        except Exception as exc:
            logger.warning("[Notify] send_sync_text_to_chat failed: %s", exc)
            return False

    async def close(self):
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ─── 便捷函数（供其他模块调用）───

def get_notifier() -> FeishuNotifier:
    return FeishuNotifier.get_instance()


async def notify_trade_open(
    symbol: str,
    side: str,
    price: float,
    leverage: int,
    margin: float,
    confidence: float = 0,
    strategy: str = "",
    tp: float = 0,
    sl: float = 0,
):
    """开仓通知"""
    n = get_notifier()
    direction = "做多" if side.lower() == "long" else "做空"
    lines = [
        f"**{symbol}** {direction}",
        f"入场价: ${price:,.4f} | 杠杆: {leverage}x | 保证金: ${margin:.2f}",
    ]
    if confidence:
        lines.append(f"置信度: {confidence:.0f}%")
    if strategy:
        lines.append(f"策略: {strategy}")
    if tp:
        lines.append(f"止盈: ${tp:,.4f}")
    if sl:
        lines.append(f"止损: ${sl:,.4f}")

    await n.send(
        text="\n".join(lines),
        title=f"📈 开仓 | {symbol} {direction}",
        level=NotifyLevel.INFO,
        event_type="open",
    )


async def notify_trade_close(
    symbol: str,
    side: str,
    pnl: float,
    reason: str = "",
    hold_hours: float = 0,
):
    """平仓通知"""
    n = get_notifier()
    direction = "多" if side.lower() == "long" else "空"
    emoji = "🟢" if pnl >= 0 else "🔴"
    lines = [
        f"**{symbol}** {direction}单平仓",
        f"盈亏: {emoji} ${pnl:+.2f}",
    ]
    if reason:
        reason_map = {
            "sl": "止损", "tp": "止盈", "ai_take_profit": "AI止盈",
            "ai_cut_loss": "AI止损", "safety_tp": "安全网止盈",
            "trailing_stop": "追踪止损", "breakeven_sl": "保本止损",
            "liquidation": "强制平仓",
        }
        lines.append(f"原因: {reason_map.get(reason, reason)}")
    if hold_hours:
        lines.append(f"持仓时长: {hold_hours:.1f}h")

    level = NotifyLevel.INFO if pnl >= 0 else NotifyLevel.WARNING
    await n.send(
        text="\n".join(lines),
        title=f"📊 平仓 | {symbol} {direction}单 ${pnl:+.2f}",
        level=level,
        event_type="close",
    )


async def notify_tp_sl_trigger(
    symbol: str,
    side: str,
    trigger_type: str,
    price: float,
    pnl: float,
):
    """止盈止损触发通知"""
    n = get_notifier()
    direction = "多" if side.lower() == "long" else "空"
    is_tp = "tp" in trigger_type.lower() or "profit" in trigger_type.lower()
    label = "止盈" if is_tp else "止损"
    emoji = "🎯" if is_tp else "🛑"

    await n.send(
        text=f"**{symbol}** {direction}单 {label}触发\n"
             f"触发价: ${price:,.4f} | 盈亏: ${pnl:+.2f}",
        title=f"{emoji} {label}触发 | {symbol}",
        level=NotifyLevel.INFO if is_tp else NotifyLevel.WARNING,
        event_type="tp_sl",
    )


async def notify_liquidation_warning(
    symbol: str,
    side: str,
    distance_pct: float,
    risk_level: str,
):
    """爆仓预警通知"""
    n = get_notifier()
    direction = "多" if side.lower() == "long" else "空"
    emoji = "⚠️" if risk_level != "critical" else "🚨"

    await n.send(
        text=f"**{symbol}** {direction}单爆仓风险\n"
             f"距爆仓价: {distance_pct:.1f}% | 风险等级: {risk_level.upper()}",
        title=f"{emoji} 爆仓预警 | {symbol}",
        level=NotifyLevel.CRITICAL if risk_level == "critical" else NotifyLevel.WARNING,
        event_type="liquidation",
    )


async def notify_system_event(message: str, level: NotifyLevel = NotifyLevel.INFO):
    """系统事件通知"""
    n = get_notifier()
    await n.send(
        text=message,
        title="🔧 系统通知",
        level=level,
        event_type="system",
    )
