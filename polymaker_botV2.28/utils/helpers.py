#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 工具函数模块
时间戳计算、slug 生成、价格安全检查等
"""

import time
from datetime import datetime, timedelta
from typing import Tuple, Optional
import pytz

from config import settings
from logger import logger


# Polygon 主网合约地址
ADDRESSES = {
    "USDCe": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
    "CTF": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    "CTF_EXCHANGE": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
    "NEG_RISK_CTF_EXCHANGE": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
    "NEG_RISK_ADAPTER": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
}

# API 端点
GAMMA_API_BASE = 'https://gamma-api.polymarket.com'
CLOB_API_BASE = 'https://clob.polymarket.com'


class TimeHelper:
    """时间处理工具类"""
    
    def __init__(self, timezone: str = 'US/Eastern'):
        self.tz = pytz.timezone(timezone)
    
    def get_est_time(self) -> datetime:
        """获取当前 EST 时间"""
        return datetime.now(self.tz)
    
    def get_utc_time(self) -> datetime:
        """获取当前 UTC 时间"""
        return datetime.now(pytz.UTC)
    
    def calculate_btc_timestamps(self) -> Tuple[int, int]:
        """
        计算 BTC 5m 和 15m 市场时间戳
        
        Returns:
            (ts_5m, ts_15m): 5分钟和15分钟市场的时间戳
        """
        now = self.get_est_time()
        
        # 5分钟时间戳
        minutes_5 = (now.minute // 5) * 5
        ts_5m = now.replace(minute=minutes_5, second=0, microsecond=0)
        
        # 15分钟时间戳
        minutes_15 = (now.minute // 15) * 15
        ts_15m = now.replace(minute=minutes_15, second=0, microsecond=0)
        
        return int(ts_5m.timestamp()), int(ts_15m.timestamp())
    
    def calculate_slug(self, market_type: str, timestamp: int = None) -> str:
        """
        计算市场 slug
        
        Args:
            market_type: 市场类型 (btc-5m, btc-15m)
            timestamp: 时间戳，默认使用当前
            
        Returns:
            市场 slug
        """
        if timestamp is None:
            if market_type == 'btc-5m':
                timestamp, _ = self.calculate_btc_timestamps()
            elif market_type == 'btc-15m':
                _, timestamp = self.calculate_btc_timestamps()
        
        return f"{market_type.replace('-', '-updown-')}-{timestamp}"
    
    def seconds_to_settlement(self, end_date: datetime) -> float:
        """计算距离结算的秒数"""
        if end_date:
            return (end_date - datetime.now(pytz.UTC)).total_seconds()
        return 0
    
    def format_duration(self, seconds: float) -> str:
        """格式化持续时间"""
        if seconds < 60:
            return f"{int(seconds)}秒"
        elif seconds < 3600:
            return f"{int(seconds / 60)}分钟"
        else:
            return f"{int(seconds / 3600)}小时"


class PriceHelper:
    """价格处理工具类"""
    
    @staticmethod
    def adjust_price_to_tick(price: float, tick_size: float = 0.01) -> float:
        """
        将价格调整为符合 tick size
        
        Args:
            price: 原始价格
            tick_size: tick 大小
            
        Returns:
            调整后的价格
        """
        return round(price / tick_size) * tick_size
    
    @staticmethod
    def check_price_safety(price_up: float, price_down: float, 
                          safe_sum: float = None) -> Tuple[bool, float]:
        """
        检查价格安全性
        
        Args:
            price_up: UP 价格
            price_down: DOWN 价格
            safe_sum: 安全价格和阈值
            
        Returns:
            (is_safe, price_sum): 是否安全，价格和
        """
        safe_sum = safe_sum or settings.SAFE_PRICE_SUM
        price_sum = price_up + price_down
        return price_sum <= safe_sum, price_sum
    
    @staticmethod
    def calculate_profit(price_up: float, price_down: float, 
                        size: int) -> Tuple[float, float]:
        """
        计算预期利润
        
        Args:
            price_up: UP 价格
            price_down: DOWN 价格
            size: 交易数量
            
        Returns:
            (profit, profit_ratio): 利润金额，利润率
        """
        cost = (price_up + price_down) * size
        # merge 后获得 size USDC
        profit = size - cost
        profit_ratio = profit / cost if cost > 0 else 0
        return profit, profit_ratio
    
    @staticmethod
    def check_cross_book(price: float, side: str, best_bid: float, 
                        best_ask: float, tick_size: float = 0.01) -> Tuple[bool, float]:
        """
        检查是否会 cross 订单簿
        
        Args:
            price: 下单价格
            side: BUY 或 SELL
            best_bid: 最佳买价
            best_ask: 最佳卖价
            tick_size: tick 大小
            
        Returns:
            (will_cross, adjusted_price): 是否会 cross，调整后的价格
        """
        adjusted_price = price
        
        if side.upper() == 'BUY':
            # 买单价格 >= best_ask 会立即成交（cross）
            if price >= best_ask - 0.001:
                adjusted_price = max(price - tick_size, 0.01)
                return True, adjusted_price
        else:
            # 卖单价格 <= best_bid 会立即成交（cross）
            if price <= best_bid + 0.001:
                adjusted_price = min(price + tick_size, 0.99)
                return True, adjusted_price
        
        return False, adjusted_price
    
    @staticmethod
    def validate_price_range(price: float, min_price: float = 0.01, 
                            max_price: float = 0.99) -> float:
        """
        验证价格范围
        
        Args:
            price: 价格
            min_price: 最小价格
            max_price: 最大价格
            
        Returns:
            调整后的价格
        """
        return max(min_price, min(max_price, price))


class OrderHelper:
    """订单处理工具类"""
    
    @staticmethod
    def calculate_expiration(seconds: int = None) -> int:
        """计算订单过期时间戳"""
        seconds = seconds or settings.ORDER_EXPIRATION_SECONDS
        return int(time.time()) + seconds
    
    @staticmethod
    def is_order_filled(filled_size: float, order_size: float, 
                       threshold: float = 0.99) -> bool:
        """检查订单是否完全成交"""
        return filled_size >= order_size * threshold
    
    @staticmethod
    def is_order_partial(filled_size: float, order_size: float) -> bool:
        """检查订单是否部分成交"""
        return 0 < filled_size < order_size


class FormatHelper:
    """格式化工具类"""
    
    @staticmethod
    def format_usd(amount: float, decimals: int = 2) -> str:
        """格式化 USD 金额"""
        return f"${amount:.{decimals}f}"
    
    @staticmethod
    def format_price(price: float, decimals: int = 4) -> str:
        """格式化价格"""
        return f"{price:.{decimals}f}"
    
    @staticmethod
    def format_size(size: float, decimals: int = 2) -> str:
        """格式化数量"""
        return f"{size:.{decimals}f}"
    
    @staticmethod
    def format_pnl(pnl: float) -> str:
        """格式化 PnL"""
        if pnl >= 0:
            return f"+{pnl:.4f}"
        return f"{pnl:.4f}"
    
    @staticmethod
    def format_percentage(value: float, decimals: int = 2) -> str:
        """格式化百分比"""
        return f"{value * 100:.{decimals}f}%"


# 全局实例
time_helper = TimeHelper()
price_helper = PriceHelper()
order_helper = OrderHelper()
format_helper = FormatHelper()
