#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 策略基类模块
定义抽象基类，便于后期添加新策略
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
import asyncio

from models import MarketInfo, Opportunity, Order, ArbitragePosition
from config import settings
from logger import logger


@dataclass
class StrategyConfig:
    """策略配置"""
    name: str
    market_type: str
    trade_size: int
    min_depth_usd: float
    max_bid_sum: float
    safe_price_sum: float
    order_expiration_seconds: int
    
    @classmethod
    def from_settings(cls, name: str, market_type: str) -> 'StrategyConfig':
        """从全局配置创建"""
        return cls(
            name=name,
            market_type=market_type,
            trade_size=settings.TRADE_SIZE,
            min_depth_usd=settings.MIN_DEPTH_USD,
            max_bid_sum=settings.MAX_BID_SUM,
            safe_price_sum=settings.SAFE_PRICE_SUM,
            order_expiration_seconds=settings.ORDER_EXPIRATION_SECONDS
        )


class BaseStrategy(ABC):
    """
    策略抽象基类
    
    所有交易策略必须继承此类并实现抽象方法。
    这确保了策略接口的一致性，便于多策略同时运行。
    """
    
    def __init__(self, config: StrategyConfig):
        self.config = config
        self.is_active = True
        self.last_check_time: Optional[datetime] = None
        self.opportunities_found = 0
        self.trades_executed = 0
    
    @property
    def name(self) -> str:
        """策略名称"""
        return self.config.name
    
    @property
    def market_type(self) -> str:
        """市场类型"""
        return self.config.market_type
    
    @abstractmethod
    async def scan_market(self) -> List[Opportunity]:
        """
        扫描市场寻找机会
        
        Returns:
            发现的机会列表
        """
        pass
    
    @abstractmethod
    async def check_entry_conditions(self, market: MarketInfo) -> Tuple[bool, str]:
        """
        检查入场条件
        
        Args:
            market: 市场信息
            
        Returns:
            (是否满足条件, 原因说明)
        """
        pass
    
    @abstractmethod
    async def calculate_prices(self, market: MarketInfo) -> Tuple[float, float]:
        """
        计算下单价格
        
        Args:
            market: 市场信息
            
        Returns:
            (UP价格, DOWN价格)
        """
        pass
    
    @abstractmethod
    async def on_order_filled(self, order: Order, position: ArbitragePosition):
        """
        订单成交回调
        
        Args:
            order: 成交的订单
            position: 相关持仓
        """
        pass
    
    @abstractmethod
    async def on_position_closed(self, position: ArbitragePosition, pnl: float):
        """
        持仓关闭回调
        
        Args:
            position: 关闭的持仓
            pnl: 实现盈亏
        """
        pass
    
    def activate(self):
        """激活策略"""
        self.is_active = True
        logger.info(f"策略 [{self.name}] 已激活")
    
    def deactivate(self):
        """停用策略"""
        self.is_active = False
        logger.info(f"策略 [{self.name}] 已停用")
    
    def should_skip(self) -> Tuple[bool, str]:
        """
        检查是否应该跳过本轮
        
        Returns:
            (是否跳过, 原因)
        """
        if not self.is_active:
            return True, "策略未激活"
        return False, ""
    
    def update_check_time(self):
        """更新检查时间"""
        self.last_check_time = datetime.now()
    
    def get_stats(self) -> Dict:
        """获取策略统计"""
        return {
            'name': self.name,
            'market_type': self.market_type,
            'is_active': self.is_active,
            'opportunities_found': self.opportunities_found,
            'trades_executed': self.trades_executed,
            'last_check_time': self.last_check_time.isoformat() if self.last_check_time else None
        }
    
    async def on_error(self, error: Exception, context: str):
        """
        错误处理回调
        
        Args:
            error: 异常对象
            context: 错误上下文
        """
        logger.error(f"策略 [{self.name}] 错误 - {context}: {error}")
    
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name} market={self.market_type}>"


class StrategyManager:
    """策略管理器 - 管理多个策略的运行"""
    
    def __init__(self):
        self.strategies: Dict[str, BaseStrategy] = {}
        self._running = False
    
    def register(self, strategy: BaseStrategy):
        """注册策略"""
        self.strategies[strategy.name] = strategy
        logger.info(f"已注册策略: {strategy.name}")
    
    def unregister(self, name: str):
        """注销策略"""
        if name in self.strategies:
            del self.strategies[name]
            logger.info(f"已注销策略: {name}")
    
    def get_strategy(self, name: str) -> Optional[BaseStrategy]:
        """获取策略"""
        return self.strategies.get(name)
    
    def get_active_strategies(self) -> List[BaseStrategy]:
        """获取所有活跃策略"""
        return [s for s in self.strategies.values() if s.is_active]
    
    def activate_all(self):
        """激活所有策略"""
        for strategy in self.strategies.values():
            strategy.activate()
    
    def deactivate_all(self):
        """停用所有策略"""
        for strategy in self.strategies.values():
            strategy.deactivate()
    
    def get_all_stats(self) -> List[Dict]:
        """获取所有策略统计"""
        return [s.get_stats() for s in self.strategies.values()]
    
    async def run_all_scans(self) -> Dict[str, List[Opportunity]]:
        """
        运行所有活跃策略的市场扫描
        
        Returns:
            {策略名: 机会列表}
        """
        results = {}
        for strategy in self.get_active_strategies():
            try:
                opportunities = await strategy.scan_market()
                results[strategy.name] = opportunities
                strategy.opportunities_found += len(opportunities)
            except Exception as e:
                await strategy.on_error(e, "市场扫描")
                results[strategy.name] = []
        return results
