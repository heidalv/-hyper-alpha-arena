"""
钉钉消息格式化器
将交易事件转换为钉钉消息格式
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 北京时区（UTC+8）
BEIJING_TZ = timezone(timedelta(hours=8))


class MessageFormatter:
    """消息格式化器基类"""

    @staticmethod
    def format_position_opened(position_data: Dict) -> Dict:
        """
        格式化开仓消息

        Args:
            position_data: 持仓数据

        Returns:
            格式化后的消息
        """
        try:
            symbol = position_data.get('symbol', 'N/A')
            side = position_data.get('side', 'N/A')
            size = position_data.get('size', 0)
            entry_price = position_data.get('entry_price', 0)
            leverage = position_data.get('leverage', 1)
            account_name = position_data.get('account_name', '未知账户')
            exchange = position_data.get('exchange', 'Binance')

            # 格式化价格
            price_str = f"${entry_price:,.2f}" if entry_price > 1 else f"${entry_price:.4f}"

            # 方向图标
            side_icon = "📈" if side.lower() in ['long', 'buy', '买入'] else "📉"
            side_text = "做多" if side.lower() in ['long', 'buy'] else "做空"

            # 格式化数量
            if size < 1:
                size_str = f"{size:.4f}"
            else:
                size_str = f"{size:.2f}"

            # 构建Markdown消息
            title = f"🔔 开仓通知 - {symbol}"
            # 使用北京时间（UTC+8）
            beijing_time = datetime.now(BEIJING_TZ)
            text = f"""## 🔔 开仓通知

**交易所**: {exchange}
**账户**: {account_name}
**交易对**: {symbol}
**方向**: {side_text} {side_icon}
**数量**: {size_str}
**开仓价**: {price_str}
**杠杆**: {leverage}x
**时间**: {beijing_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)"""

            return {
                "message_type": "markdown",
                "title": title,
                "content": text,
                "raw_data": position_data
            }

        except Exception as e:
            logger.error(f"格式化开仓消息失败: {e}")
            return MessageFormatter._format_error("开仓通知", str(e))

    @staticmethod
    def format_position_closed(position_data: Dict) -> Dict:
        """
        格式化平仓消息

        Args:
            position_data: 持仓数据（包含盈亏信息）

        Returns:
            格式化后的消息
        """
        try:
            symbol = position_data.get('symbol', 'N/A')
            side = position_data.get('side', 'N/A')
            size = position_data.get('size', 0)
            entry_price = position_data.get('entry_price', 0)
            exit_price = position_data.get('exit_price', 0)
            pnl = position_data.get('pnl', 0)
            pnl_percent = position_data.get('pnl_percent', 0)
            hold_duration = position_data.get('hold_duration', 'N/A')
            account_name = position_data.get('account_name', '未知账户')
            exchange = position_data.get('exchange', 'Binance')

            # 格式化价格
            entry_str = f"${entry_price:,.2f}" if entry_price > 1 else f"${entry_price:.4f}"
            exit_str = f"${exit_price:,.2f}" if exit_price > 1 else f"${exit_price:.4f}"

            # 盈亏格式化
            pnl_icon = "✅" if pnl >= 0 else "❌"
            pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
            pnl_percent_str = f"+{pnl_percent:.2f}%" if pnl_percent >= 0 else f"{pnl_percent:.2f}%"

            # 方向
            side_text = "做多" if side.lower() in ['long', 'buy'] else "做空"

            # 构建Markdown消息
            title = f"🔕 平仓通知 - {symbol}"
            # 使用北京时间（UTC+8）
            beijing_time = datetime.now(BEIJING_TZ)
            text = f"""## 🔕 平仓通知

**交易所**: {exchange}
**账户**: {account_name}
**交易对**: {symbol}
**方向**: {side_text}
**数量**: {size:.4f}
**开仓价**: {entry_str}
**平仓价**: {exit_str}
**持仓时长**: {hold_duration}
**收益**: {pnl_str} ({pnl_percent_str}) {pnl_icon}
**时间**: {beijing_time.strftime('%Y-%m-%d %H:%M:%S')} (北京时间)"""

            return {
                "message_type": "markdown",
                "title": title,
                "content": text,
                "raw_data": position_data
            }

        except Exception as e:
            logger.error(f"格式化平仓消息失败: {e}")
            return MessageFormatter._format_error("平仓通知", str(e))

    @staticmethod
    def format_stop_loss_triggered(position_data: Dict) -> Dict:
        """
        格式化止损触发消息

        Args:
            position_data: 持仓数据

        Returns:
            格式化后的消息
        """
        try:
            symbol = position_data.get('symbol', 'N/A')
            stop_loss_price = position_data.get('stop_loss_price', 0)
            size = position_data.get('size', 0)
            pnl = position_data.get('pnl', 0)
            account_name = position_data.get('account_name', '未知账户')

            price_str = f"${stop_loss_price:,.2f}" if stop_loss_price > 1 else f"${stop_loss_price:.4f}"
            pnl_str = f"-${abs(pnl):,.2f}" if pnl < 0 else f"${pnl:,.2f}"

            title = f"🛡️ 止损触发 - {symbol}"
            text = f"""## 🛡️ 止损触发通知

**交易所**: 交易通知
**账户**: {account_name}
**交易对**: {symbol}
**止损价格**: {price_str}
**平仓数量**: {size:.4f}
**亏损**: {pnl_str}
**时间**: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}"""

            return {
                "message_type": "markdown",
                "title": title,
                "content": text,
                "raw_data": position_data
            }

        except Exception as e:
            logger.error(f"格式化止损消息失败: {e}")
            return MessageFormatter._format_error("止损触发", str(e))

    @staticmethod
    def format_take_profit_triggered(position_data: Dict) -> Dict:
        """
        格式化止盈触发消息

        Args:
            position_data: 持仓数据

        Returns:
            格式化后的消息
        """
        try:
            symbol = position_data.get('symbol', 'N/A')
            take_profit_price = position_data.get('take_profit_price', 0)
            size = position_data.get('size', 0)
            pnl = position_data.get('pnl', 0)
            account_name = position_data.get('account_name', '未知账户')

            price_str = f"${take_profit_price:,.2f}" if take_profit_price > 1 else f"${take_profit_price:.4f}"
            pnl_str = f"+${pnl:,.2f}" if pnl > 0 else f"${pnl:,.2f}"

            title = f"💰 止盈触发 - {symbol}"
            text = f"""## 💰 止盈触发通知

**交易所**: 交易通知
**账户**: {account_name}
**交易对**: {symbol}
**止盈价格**: {price_str}
**平仓数量**: {size:.4f}
**盈利**: {pnl_str} ✅
**时间**: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}"""

            return {
                "message_type": "markdown",
                "title": title,
                "content": text,
                "raw_data": position_data
            }

        except Exception as e:
            logger.error(f"格式化止盈消息失败: {e}")
            return MessageFormatter._format_error("止盈触发", str(e))

    @staticmethod
    def format_volatility_alert(symbol: str, change_percent: float, current_price: float, timeframe: int = 300) -> Dict:
        """
        格式化波动预警消息

        Args:
            symbol: 交易对
            change_percent: 变化百分比
            current_price: 当前价格
            timeframe: 时间窗口（秒）

        Returns:
            格式化后的消息
        """
        try:
            price_str = f"${current_price:,.2f}" if current_price > 1 else f"${current_price:.4f}"
            change_str = f"+{change_percent:.2f}%" if change_percent > 0 else f"{change_percent:.2f}%"
            timeframe_minutes = timeframe // 60

            # 波动图标
            if abs(change_percent) >= 10:
                icon = "🔴🔴🔴"  # 剧烈波动
            elif abs(change_percent) >= 5:
                icon = "🔴🔴"  # 大幅波动
            else:
                icon = "🔴"  # 一般波动

            title = f"⚠️ 价格波动预警 - {symbol}"
            text = f"""## {icon} 价格波动预警

**交易所**: 价格提醒
**交易对**: {symbol}
**当前价格**: {price_str}
**{timeframe_minutes}分钟涨跌**: {change_str}
**预警时间**: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}

⚠️ 请注意风险控制！"""

            return {
                "message_type": "markdown",
                "title": title,
                "content": text,
                "raw_data": {
                    "symbol": symbol,
                    "change_percent": change_percent,
                    "current_price": current_price,
                    "timeframe": timeframe
                }
            }

        except Exception as e:
            logger.error(f"格式化波动预警失败: {e}")
            return MessageFormatter._format_error("波动预警", str(e))

    @staticmethod
    def format_position_summary(account_data: Dict, positions: List[Dict]) -> Dict:
        """
        格式化持仓汇总消息

        Args:
            account_data: 账户数据
            positions: 持仓列表

        Returns:
            格式化后的消息
        """
        try:
            account_name = account_data.get('name', '未知账户')
            total_balance = account_data.get('total_balance', 0)
            available_balance = account_data.get('available_balance', 0)
            margin_used = account_data.get('margin_used', 0)
            total_pnl = account_data.get('total_unrealized_pnl', 0)

            # 计算保证金使用率
            margin_percent = (margin_used / total_balance * 100) if total_balance > 0 else 0

            # 构建汇总文本
            text = f"""## 📊 持仓汇总报告

**交易所**: 持仓报告
**账户**: {account_name}
**总权益**: ${total_balance:,.2f}
**可用余额**: ${available_balance:,.2f}
**保证金使用**: {margin_percent:.1f}%
**持仓数**: {len(positions)}"""

            if total_pnl != 0:
                pnl_icon = "✅" if total_pnl > 0 else "❌"
                pnl_str = f"+${total_pnl:,.2f}" if total_pnl > 0 else f"${total_pnl:,.2f}"
                pnl_percent = (total_pnl / total_balance * 100) if total_balance > 0 else 0
                text += f"\n**未实现盈亏**: {pnl_str} ({pnl_percent:+.2f}%) {pnl_icon}"

            # 持仓明细
            if positions:
                text += "\n\n---\n\n### 持仓明细\n\n"

                for i, pos in enumerate(positions[:10], 1):  # 最多显示10个
                    symbol = pos.get('symbol', 'N/A')
                    side = pos.get('side', 'N/A')
                    size = pos.get('size', 0)
                    entry_price = pos.get('entry_price', 0)
                    mark_price = pos.get('mark_price', 0)
                    leverage = pos.get('leverage', 1)
                    pnl = pos.get('unrealized_pnl', 0)

                    side_icon = "📈" if side.lower() == 'long' else "📉"
                    side_text = "多" if side.lower() == 'long' else "空"
                    pnl_icon = "✅" if pnl >= 0 else "❌"
                    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

                    text += f"""{i}. **{symbol}** {side_text} {leverage}x {side_icon}
   - 数量: {size:.4f}
   - 均价: ${entry_price:,.2f}
   - 现价: ${mark_price:,.2f}
   - 盈亏: {pnl_str} {pnl_icon}\n\n"""

                if len(positions) > 10:
                    text += f"_...还有 {len(positions) - 10} 个持仓_\n\n"

            text += f"\n\n**报告时间**: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}"

            return {
                "message_type": "markdown",
                "title": f"📊 持仓汇总 - {account_name}",
                "content": text,
                "raw_data": {
                    "account": account_data,
                    "positions": positions
                }
            }

        except Exception as e:
            logger.error(f"格式化持仓汇总失败: {e}")
            return MessageFormatter._format_error("持仓汇总", str(e))

    @staticmethod
    def format_error_message(operation: str, error: str) -> Dict:
        """
        格式化错误消息

        Args:
            operation: 操作名称
            error: 错误信息

        Returns:
            格式化后的消息
        """
        return MessageFormatter._format_error(operation, error)

    @staticmethod
    def _format_error(operation: str, error: str) -> Dict:
        """
        内部方法：格式化错误消息

        Args:
            operation: 操作名称
            error: 错误信息

        Returns:
            格式化后的消息
        """
        title = f"❌ 错误 - {operation}"
        text = f"""## ❌ 错误通知

**交易所**: 系统消息
**操作**: {operation}
**错误信息**: {error}
**时间**: {datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}"""

        return {
            "message_type": "markdown",
            "title": title,
            "content": text,
            "raw_data": {"operation": operation, "error": error}
        }
