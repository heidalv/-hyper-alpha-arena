"""
钉钉机器人API客户端
"""
import asyncio
import hashlib
import hmac
import base64
import time
import logging
from typing import Dict, List, Optional
import aiohttp
from config.dingtalk_config import config

logger = logging.getLogger(__name__)


class DingTalkBotClient:
    """钉钉机器人API客户端"""

    def __init__(self, webhook_url: str, sign_secret: Optional[str] = None):
        """
        初始化客户端

        Args:
            webhook_url: Webhook地址
            sign_secret: 签名密钥（可选）
        """
        self.webhook_url = webhook_url
        self.sign_secret = sign_secret
        self.timeout = aiohttp.ClientTimeout(total=config.request_timeout_seconds)

    async def send_text(
        self,
        content: str,
        at_mobiles: Optional[List[str]] = None,
        at_all: bool = False
    ) -> Dict:
        """
        发送文本消息

        Args:
            content: 消息内容
            at_mobiles: @的手机号列表
            at_all: 是否@所有人

        Returns:
            响应结果
        """
        message = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }

        # 添加@信息
        if at_mobiles or at_all:
            message["at"] = {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all
            }

        return await self._send(message)

    async def send_markdown(self, title: str, text: str) -> Dict:
        """
        发送Markdown消息

        Args:
            title: 消息标题
            text: Markdown格式的文本

        Returns:
            响应结果
        """
        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            }
        }

        return await self._send(message)

    async def send_card(
        self,
        title: str,
        message_url: str,
        pic_url: Optional[str] = None
    ) -> Dict:
        """
        发送FeedCard消息

        Args:
            title: 标题
            message_url: 跳转链接
            pic_url: 图片链接（可选）

        Returns:
            响应结果
        """
        link = {
            "title": title,
            "messageURL": message_url
        }

        if pic_url:
            link["picURL"] = pic_url

        message = {
            "msgtype": "feedCard",
            "feedCard": {
                "links": [link]
            }
        }

        return await self._send(message)

    async def send_action_card(
        self,
        title: str,
        text: str,
        btn_orientation: str = "1",
        btns: Optional[List[Dict]] = None
    ) -> Dict:
        """
        发送ActionCard消息

        Args:
            title: 标题
            text: Markdown格式的文本
            btn_orientation: 按钮排列方向 0-竖直，1-横向
            btns: 按钮列表 [{"title": "按钮", "actionURL": "url"}]

        Returns:
            响应结果
        """
        message = {
            "msgtype": "actionCard",
            "actionCard": {
                "title": title,
                "text": text,
                "btnOrientation": btn_orientation
            }
        }

        if btns:
            message["actionCard"]["btns"] = btns

        return await self._send(message)

    async def _send(self, message: Dict) -> Dict:
        """
        发送消息到钉钉

        Args:
            message: 消息内容

        Returns:
            钉钉API响应
        """
        url = self.webhook_url

        # 如果配置了签名密钥，添加签名
        if self.sign_secret:
            timestamp = int(time.time() * 1000)
            sign = self._generate_signature(timestamp)
            url = f"{url}&timestamp={timestamp}&sign={sign}"

        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(url, json=message) as response:
                    result = await response.json()

                    # 记录响应
                    if result.get("errcode") == 0:
                        logger.debug(f"钉钉推送成功: {message.get('msgtype')}")
                    else:
                        logger.error(f"钉钉推送失败: {result}")

                    return result

        except asyncio.TimeoutError:
            logger.error("钉钉推送超时")
            return {"errcode": -1, "errmsg": "请求超时"}

        except aiohttp.ClientError as e:
            logger.error(f"钉钉推送网络错误: {e}")
            return {"errcode": -2, "errmsg": f"网络错误: {str(e)}"}

        except Exception as e:
            logger.error(f"钉钉推送未知错误: {e}")
            return {"errcode": -3, "errmsg": f"未知错误: {str(e)}"}

    def _generate_signature(self, timestamp: int) -> str:
        """
        生成签名

        Args:
            timestamp: 时间戳（毫秒）

        Returns:
            URL编码的签名
        """
        if not self.sign_secret:
            return ""

        # 构造签名字符串
        string_to_sign = f"{timestamp}\n{self.sign_secret}"

        # HMAC-SHA256加密
        hmac_code = hmac.new(
            self.sign_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()

        # Base64编码
        signature = base64.b64encode(hmac_code).decode('utf-8')

        # URL编码
        import urllib.parse
        return urllib.parse.quote(signature)


# 同步版本（用于同步环境）
class DingTalkBotClientSync:
    """钉钉机器人API客户端（同步版本）"""

    def __init__(self, webhook_url: str, sign_secret: Optional[str] = None):
        """
        初始化客户端

        Args:
            webhook_url: Webhook地址
            sign_secret: 签名密钥（可选）
        """
        self.webhook_url = webhook_url
        self.sign_secret = sign_secret
        import requests
        self.requests = requests

    def send_text(
        self,
        content: str,
        at_mobiles: Optional[List[str]] = None,
        at_all: bool = False
    ) -> Dict:
        """
        发送文本消息（同步）

        Args:
            content: 消息内容
            at_mobiles: @的手机号列表
            at_all: 是否@所有人

        Returns:
            响应结果
        """
        message = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }

        if at_mobiles or at_all:
            message["at"] = {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all
            }

        return self._send(message)

    def send_markdown(self, title: str, text: str) -> Dict:
        """
        发送Markdown消息（同步）

        Args:
            title: 消息标题
            text: Markdown格式的文本

        Returns:
            响应结果
        """
        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            }
        }

        return self._send(message)

    def send_card(
        self,
        title: str,
        message_url: str,
        pic_url: Optional[str] = None
    ) -> Dict:
        """
        发送FeedCard消息（同步）

        Args:
            title: 标题
            message_url: 跳转链接
            pic_url: 图片链接（可选）

        Returns:
            响应结果
        """
        link = {
            "title": title,
            "messageURL": message_url
        }

        if pic_url:
            link["picURL"] = pic_url

        message = {
            "msgtype": "feedCard",
            "feedCard": {
                "links": [link]
            }
        }

        return self._send(message)

    def _send(self, message: Dict) -> Dict:
        """
        发送消息到钉钉（同步）

        Args:
            message: 消息内容

        Returns:
            钉钉API响应
        """
        url = self.webhook_url

        # 如果配置了签名密钥，添加签名
        if self.sign_secret:
            timestamp = int(time.time() * 1000)
            sign = self._generate_signature(timestamp)
            url = f"{url}&timestamp={timestamp}&sign={sign}"

        try:
            response = self.requests.post(
                url,
                json=message,
                timeout=config.request_timeout_seconds
            )
            result = response.json()

            # 记录响应
            if result.get("errcode") == 0:
                logger.debug(f"钉钉推送成功: {message.get('msgtype')}")
            else:
                logger.error(f"钉钉推送失败: {result}")

            return result

        except self.requests.exceptions.Timeout:
            logger.error("钉钉推送超时")
            return {"errcode": -1, "errmsg": "请求超时"}

        except self.requests.exceptions.RequestException as e:
            logger.error(f"钉钉推送网络错误: {e}")
            return {"errcode": -2, "errmsg": f"网络错误: {str(e)}"}

        except Exception as e:
            logger.error(f"钉钉推送未知错误: {e}")
            return {"errcode": -3, "errmsg": f"未知错误: {str(e)}"}

    def _generate_signature(self, timestamp: int) -> str:
        """
        生成签名（同步）

        Args:
            timestamp: 时间戳（毫秒）

        Returns:
            URL编码的签名
        """
        if not self.sign_secret:
            return ""

        # 构造签名字符串
        string_to_sign = f"{timestamp}\n{self.sign_secret}"

        # HMAC-SHA256加密
        hmac_code = hmac.new(
            self.sign_secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()

        # Base64编码
        signature = base64.b64encode(hmac_code).decode('utf-8')

        # URL编码
        import urllib.parse
        return urllib.parse.quote(signature)
