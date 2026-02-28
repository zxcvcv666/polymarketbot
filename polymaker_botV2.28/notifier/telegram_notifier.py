#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker Telegram 推送模块
所有推送使用富文本 HTML 格式，中文显示
"""

import asyncio
from typing import Optional, Dict, List
import requests

from config import settings
from logger import logger


class TelegramNotifier:
    """
    Telegram 消息推送
    
    所有消息使用 HTML 格式，支持 <b>粗体</b>、<code>代码</code> 等格式
    所有用户可见内容使用中文
    """
    
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
    
    def _send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """
        发送消息
        
        Args:
            text: 消息文本（支持 HTML）
            parse_mode: 解析模式
            
        Returns:
            是否成功
        """
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram 配置不完整，跳过发送")
            return False
        
        url = f"{self.base_url}/sendMessage"
        data = {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode
        }
        
        try:
            response = requests.post(url, data=data, timeout=10)
            if response.status_code == 200:
                return True
            else:
                logger.error(f"Telegram 发送失败: {response.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram 发送异常: {e}")
            return False
    
    async def send_message_async(self, text: str, parse_mode: str = "HTML") -> bool:
        """异步发送消息"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._send_message, text, parse_mode)
    
    # ============================================
    # 系统状态通知
    # ============================================
    
    def send_startup(self):
        """发送启动通知"""
        text = f"""🚀 <b>Polymaker 量化机器人已启动</b>

✅ Gasless 模式已启用
📊 签名类型: <code>{'GNOSIS_SAFE' if settings.SIGNATURE_TYPE == 2 else 'POLY_PROXY'}</code>
💰 交易数量: <code>{settings.TRADE_SIZE}</code>
📈 最大持仓: <code>{settings.MAX_EXPOSURE_USD} USDC</code>

💡 使用键盘按钮快速操作"""
        return self._send_message(text)
    
    def send_shutdown(self):
        """发送停止通知"""
        text = "🛑 <b>Polymaker 量化机器人已停止</b>"
        return self._send_message(text)
    
    def send_system_status(self, status: str, exposure: float, 
                          positions_count: int, orders_count: int = 0):
        """发送系统状态"""
        text = f"""📊 <b>系统状态</b>

状态: <code>{status}</code>
总持仓: <code>{exposure:.2f} USDC</code>
持仓数: <code>{positions_count}</code>
挂单数: <code>{orders_count}</code>"""
        return self._send_message(text)
    
    # ============================================
    # 交易通知
    # ============================================
    
    def send_opportunity(self, slug: str, bid_sum: float, price_up: float, 
                        price_down: float, potential_profit: float, 
                        ask_depth_up: float, ask_depth_down: float):
        """发送套利机会通知"""
        text = f"""💡 <b>发现套利机会</b>

市场: <code>{slug}</code>
Bid 和: <code>{bid_sum:.4f}</code>

挂单价:
  UP: <code>{price_up:.4f}</code>
  DOWN: <code>{price_down:.4f}</code>
  合计: <code>{price_up + price_down:.4f}</code>

预期利润: <b>+{potential_profit:.4f} USDC</b>
深度: UP ${ask_depth_up:.0f} | DOWN ${ask_depth_down:.0f}"""
        return self._send_message(text)
    
    def send_order_placed(self, slug: str, side: str, price: float, 
                         size: float, order_id: str):
        """发送下单通知"""
        text = f"""📤 <b>订单已挂出</b>

市场: <code>{slug}</code>
方向: <code>{side}</code>
价格: <code>{price:.4f}</code>
数量: <code>{size:.2f}</code>
订单ID: <code>{order_id[:16]}...</code>"""
        return self._send_message(text)
    
    def send_order_filled(self, slug: str, side: str, price: float, 
                         filled_size: float, usd: float, order_id: str):
        """发送成交通知"""
        text = f"""✅ <b>订单成交</b>

市场: <code>{slug}</code>
方向: <code>{side}</code>
价格: <code>{price:.4f}</code>
成交: <code>{filled_size:.2f}</code>
金额: <code>{usd:.4f} USDC</code>"""
        return self._send_message(text)
    
    def send_merge_success(self, slug: str, size: float, profit: float, 
                          total_invested: float):
        """发送 Merge 成功通知"""
        profit_ratio = (profit / total_invested * 100) if total_invested > 0 else 0
        text = f"""🎉 <b>套利成功！</b>

市场: <code>{slug}</code>
合并数量: <code>{size:.2f}</code>
成本合计: <code>{total_invested:.4f} USDC</code>
锁定利润: <b>+{profit:.4f} USDC</b> ({profit_ratio:.2f}%)"""
        return self._send_message(text)
    
    def send_position_closed(self, slug: str, side: str, size: float, 
                            pnl: float, reason: str = ""):
        """发送平仓通知"""
        pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
        text = f"""🔓 <b>持仓已平仓</b>

市场: <code>{slug}</code>
方向: <code>{side}</code>
数量: <code>{size:.2f}</code>
PnL: <code>{pnl_str} USDC</code>
原因: <code>{reason or '手动平仓'}</code>"""
        return self._send_message(text)
    
    # ============================================
    # 风控警报
    # ============================================
    
    def send_risk_alert(self, slug: str, reason: str, detail: str, 
                       total_exposure: float):
        """发送风控警报"""
        text = f"""⚠️ <b>风控警报</b>

市场: <code>{slug}</code>
原因: <code>{reason}</code>
详情: <code>{detail}</code>
当前持仓: <code>{total_exposure:.2f} USDC</code>"""
        return self._send_message(text)
    
    def send_error(self, context: str, error: str):
        """发送错误通知"""
        text = f"""❌ <b>错误</b>

上下文: <code>{context}</code>
错误: <code>{error}</code>"""
        return self._send_message(text)
    
    # ============================================
    # 查询结果
    # ============================================
    
    def send_balance(self, usdc_balance: float, position_value: float, 
                    total_value: float, positions_count: int):
        """发送余额查询结果"""
        text = f"""💰 <b>账户余额</b>

USDC 余额: <code>{usdc_balance:.2f}</code>
持仓价值: <code>{position_value:.2f}</code>
总价值: <code>{total_value:.2f}</code>
持仓数量: <code>{positions_count}</code>"""
        return self._send_message(text)
    
    def send_positions(self, positions: List[Dict]):
        """发送持仓查询结果"""
        if not positions:
            return self._send_message("📭 <b>当前无活跃持仓</b>")
        
        lines = ["📋 <b>当前持仓</b>\n"]
        
        for pos in positions[:10]:  # 最多显示10个
            slug = pos.get('slug', 'N/A')
            up_size = pos.get('up_size', 0)
            up_price = pos.get('up_price', 0)
            down_size = pos.get('down_size', 0)
            down_price = pos.get('down_price', 0)
            total = pos.get('total_invested', 0)
            
            lines.append(f"\n<code>{slug}</code>")
            lines.append(f"  UP: {up_size:.1f}@{up_price:.2f}")
            lines.append(f"  DOWN: {down_size:.1f}@{down_price:.2f}")
            lines.append(f"  投入: ${total:.2f}")
        
        return self._send_message('\n'.join(lines))
    
    def send_orders(self, orders: List[Dict]):
        """发送挂单查询结果"""
        if not orders:
            return self._send_message("📭 <b>当前无活跃挂单</b>")
        
        lines = ["📋 <b>当前挂单</b>\n"]
        
        for order in orders[:10]:
            order_id = order.get('order_id', 'N/A')[:16]
            side = order.get('side', 'N/A')
            price = order.get('price', 0)
            size = order.get('size', 0)
            
            lines.append(f"\nID: <code>{order_id}...</code>")
            lines.append(f"  方向: {side}")
            lines.append(f"  价格: {price:.4f}")
            lines.append(f"  数量: {size:.2f}")
        
        return self._send_message('\n'.join(lines))
    
    def send_daily_report(self, summary: Dict):
        """发送每日报告"""
        text = f"""📊 <b>每日报告</b>

日期: <code>{summary.get('date', 'N/A')}</code>
总交易数: <code>{summary.get('total_trades', 0)}</code>
Merge 次数: <code>{summary.get('merge_count', 0)}</code>
总 PnL: <code>{summary.get('total_pnl', 0):.4f} USDC</code>
交易额: <code>{summary.get('total_usd', 0):.2f} USDC</code>"""
        return self._send_message(text)
    
    def send_file(self, file_path: str, caption: str = ""):
        """发送文件"""
        if not self.bot_token or not self.chat_id:
            return False
        
        url = f"{self.base_url}/sendDocument"
        
        try:
            with open(file_path, 'rb') as f:
                files = {'document': f}
                data = {
                    'chat_id': self.chat_id,
                    'caption': caption,
                    'parse_mode': 'HTML'
                }
                response = requests.post(url, files=files, data=data, timeout=30)
                
                if response.status_code == 200:
                    return True
                else:
                    logger.error(f"发送文件失败: {response.text}")
                    return False
        except Exception as e:
            logger.error(f"发送文件异常: {e}")
            return False
    
    async def send_file_async(self, file_path: str, caption: str = "") -> bool:
        """异步发送文件"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.send_file, file_path, caption)


# 全局通知器实例
notifier = TelegramNotifier()
