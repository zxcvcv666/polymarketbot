#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 数据模型模块
定义所有数据类和枚举
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any


# ============================================
# 枚举定义
# ============================================

class PositionStatus(Enum):
    """持仓状态枚举"""
    PENDING = 'pending'          # 待成交
    PARTIAL = 'partial'          # 部分成交
    ARBITRAGE = 'arbitrage'      # 双边套利持仓
    MERGED = 'merged'            # 已合并
    CLOSED = 'closed'            # 已平仓


class OrderSide(Enum):
    """订单方向"""
    BUY = 'BUY'
    SELL = 'SELL'


class OrderStatus(Enum):
    """订单状态"""
    LIVE = 'LIVE'        # 活跃
    FILLED = 'FILLED'    # 完全成交
    PARTIAL = 'PARTIAL'  # 部分成交
    CANCELLED = 'CANCELLED'  # 已取消
    EXPIRED = 'EXPIRED'  # 已过期


class EventType(Enum):
    """交易事件类型"""
    BUY = 'BUY'          # 买入
    SELL = 'SELL'        # 卖出
    MERGE = 'MERGE'      # 合并
    CANCEL = 'CANCEL'    # 取消
    PARTIAL = 'PARTIAL'  # 部分成交


# ============================================
# 数据类定义
# ============================================

@dataclass
class TokenInfo:
    """Token 信息"""
    token_id: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    ask_depth_usd: float = 0.0
    bid_depth_usd: float = 0.0
    tick_size: float = 0.01
    bids: List[Dict] = field(default_factory=list)
    asks: List[Dict] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'token_id': self.token_id,
            'best_bid': self.best_bid,
            'best_ask': self.best_ask,
            'ask_depth_usd': self.ask_depth_usd,
            'bid_depth_usd': self.bid_depth_usd,
            'tick_size': self.tick_size
        }


@dataclass
class MarketInfo:
    """市场信息"""
    slug: str
    condition_id: str
    token_up: Optional[TokenInfo] = None
    token_down: Optional[TokenInfo] = None
    end_date: Optional[datetime] = None
    settle_seconds: int = 0
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'slug': self.slug,
            'condition_id': self.condition_id,
            'token_up': self.token_up.to_dict() if self.token_up else None,
            'token_down': self.token_down.to_dict() if self.token_down else None,
            'settle_seconds': self.settle_seconds
        }


@dataclass
class Position:
    """持仓信息"""
    token_id: str
    side: str  # 'up' or 'down'
    filled_size: float = 0.0
    avg_price: float = 0.0
    invested_usd: float = 0.0
    status: str = PositionStatus.PENDING.value
    created_at: datetime = field(default_factory=datetime.now)
    
    @property
    def current_value(self) -> float:
        """当前价值（假设价格 0.5）"""
        return self.filled_size * 0.5
    
    @property
    def pnl(self) -> float:
        """未实现盈亏"""
        return self.current_value - self.invested_usd
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'token_id': self.token_id,
            'side': self.side,
            'filled_size': self.filled_size,
            'avg_price': self.avg_price,
            'invested_usd': self.invested_usd,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


@dataclass
class ArbitragePosition:
    """套利持仓（包含 UP 和 DOWN）"""
    slug: str
    condition_id: str
    position_up: Optional[Position] = None
    position_down: Optional[Position] = None
    status: str = PositionStatus.PENDING.value
    created_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None
    
    @property
    def total_invested_usd(self) -> float:
        """总投入"""
        up_invested = self.position_up.invested_usd if self.position_up else 0
        down_invested = self.position_down.invested_usd if self.position_down else 0
        return up_invested + down_invested
    
    @property
    def total_size(self) -> float:
        """总持仓数量"""
        up_size = self.position_up.filled_size if self.position_up else 0
        down_size = self.position_down.filled_size if self.position_down else 0
        return up_size + down_size
    
    @property
    def is_matched(self) -> bool:
        """是否双边匹配（可以 merge）"""
        up_size = self.position_up.filled_size if self.position_up else 0
        down_size = self.position_down.filled_size if self.position_down else 0
        return up_size > 0 and down_size > 0 and up_size == down_size
    
    @property
    def matched_size(self) -> float:
        """匹配数量（取较小值）"""
        up_size = self.position_up.filled_size if self.position_up else 0
        down_size = self.position_down.filled_size if self.position_down else 0
        return min(up_size, down_size)
    
    @property
    def locked_profit(self) -> float:
        """锁定利润（merge 后）"""
        if not self.is_matched:
            return 0
        # 利润 = 匹配数量 - 总投入
        return self.matched_size - self.total_invested_usd
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'slug': self.slug,
            'condition_id': self.condition_id,
            'position_up': self.position_up.to_dict() if self.position_up else None,
            'position_down': self.position_down.to_dict() if self.position_down else None,
            'status': self.status,
            'total_invested_usd': self.total_invested_usd,
            'is_matched': self.is_matched,
            'matched_size': self.matched_size,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'closed_at': self.closed_at.isoformat() if self.closed_at else None
        }


@dataclass
class Order:
    """订单信息"""
    order_id: str
    slug: str
    token_id: str
    side: OrderSide
    price: float
    size: float
    filled_size: float = 0.0
    status: str = OrderStatus.LIVE.value
    created_at: datetime = field(default_factory=datetime.now)
    
    @property
    def is_fully_filled(self) -> bool:
        """是否完全成交"""
        return self.filled_size >= self.size
    
    @property
    def is_partial_filled(self) -> bool:
        """是否部分成交"""
        return 0 < self.filled_size < self.size
    
    @property
    def remaining_size(self) -> float:
        """剩余数量"""
        return max(0, self.size - self.filled_size)
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'order_id': self.order_id,
            'slug': self.slug,
            'token_id': self.token_id,
            'side': self.side.value,
            'price': self.price,
            'size': self.size,
            'filled_size': self.filled_size,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


@dataclass
class TradeLog:
    """交易日志"""
    timestamp: datetime
    slug: str
    event_type: str  # EventType
    side: str  # 'up', 'down', 'both'
    price: float
    size: float
    usd_amount: float
    pnl: float = 0.0
    status: str = 'completed'
    note: str = ''
    order_id: str = ''
    token_id: str = ''
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'slug': self.slug,
            'event_type': self.event_type,
            'side': self.side,
            'price': self.price,
            'size': self.size,
            'usd_amount': self.usd_amount,
            'pnl': self.pnl,
            'status': self.status,
            'note': self.note,
            'order_id': self.order_id,
            'token_id': self.token_id
        }
    
    def to_csv_row(self) -> Dict:
        """转换为 CSV 行"""
        return {
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S') if self.timestamp else '',
            'slug': self.slug,
            'event_type': self.event_type,
            'side': self.side,
            'price': f"{self.price:.4f}",
            'size': f"{self.size:.2f}",
            'usd_amount': f"{self.usd_amount:.4f}",
            'pnl': f"{self.pnl:.4f}",
            'status': self.status,
            'note': self.note
        }


@dataclass
class Opportunity:
    """套利机会"""
    slug: str
    market: MarketInfo
    price_up: float
    price_down: float
    bid_sum: float
    potential_profit: float
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'slug': self.slug,
            'price_up': self.price_up,
            'price_down': self.price_down,
            'bid_sum': self.bid_sum,
            'potential_profit': self.potential_profit,
            'timestamp': self.timestamp.isoformat()
        }
