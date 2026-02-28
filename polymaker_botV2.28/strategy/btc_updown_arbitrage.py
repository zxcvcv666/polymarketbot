#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker BTC Up-Down 套利策略模块
BTC 5m/15m 双边套利策略实现
"""

import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config import settings
from models import (
    MarketInfo, TokenInfo, Opportunity, Order, ArbitragePosition, Position,
    OrderSide, PositionStatus
)
from strategy.base_strategy import BaseStrategy, StrategyConfig
from client.polymarket_client import get_client, PolymarketClient
from utils.helpers import time_helper, price_helper
from logger import logger


class BtcUpdownArbitrageStrategy(BaseStrategy):
    """
    BTC Up-Down 套利策略
    
    核心逻辑：
    1. 监控 BTC 5m/15m up-down 市场
    2. 当 UP bid + DOWN bid < 1 时存在套利机会
    3. 在 bid 侧挂单等待成交
    4. 双边成交后立即 merge 锁定利润
    5. 单边成交则市价平仓
    """
    
    def __init__(self, market_type: str = 'btc-5m'):
        """
        初始化策略
        
        Args:
            market_type: 市场类型 ('btc-5m' 或 'btc-15m')
        """
        config = StrategyConfig.from_settings(
            name=f"btc-updown-{market_type.split('-')[1]}",
            market_type=market_type
        )
        super().__init__(config)
        
        self.client = get_client()
        self._market_type = market_type
    
    async def scan_market(self) -> List[Opportunity]:
        """
        扫描市场寻找套利机会
        
        Returns:
            发现的机会列表
        """
        opportunities = []
        
        # 获取当前市场 slug
        slug = self._get_current_slug()
        if not slug:
            return opportunities
        
        # 获取市场信息
        market = await self._get_market_info(slug)
        if not market:
            return opportunities
        
        # 检查入场条件
        can_trade, reason = await self.check_entry_conditions(market)
        if not can_trade:
            logger.debug(f"[{slug}] 不满足入场条件: {reason}")
            return opportunities
        
        # 计算价格
        price_up, price_down = await self.calculate_prices(market)
        
        # 检查价格安全性
        is_safe, price_sum = price_helper.check_price_safety(
            price_up, price_down, self.config.safe_price_sum
        )
        
        if not is_safe:
            logger.debug(f"[{slug}] 价格不安全: {price_sum:.4f}")
            return opportunities
        
        # 计算预期利润
        profit, profit_ratio = price_helper.calculate_profit(
            price_up, price_down, self.config.trade_size
        )
        
        # 创建机会对象
        opportunity = Opportunity(
            slug=slug,
            market=market,
            price_up=price_up,
            price_down=price_down,
            bid_sum=market.token_up.best_bid + market.token_down.best_bid,
            potential_profit=profit
        )
        
        opportunities.append(opportunity)
        logger.info(f"[{slug}] 发现套利机会: bid_sum={opportunity.bid_sum:.4f}, 利润={profit:.4f} USDC")
        
        return opportunities
    
    async def check_entry_conditions(self, market: MarketInfo) -> Tuple[bool, str]:
        """
        检查入场条件
        
        条件：
        1. 距离结算时间足够
        2. Token 信息完整
        3. 流动性深度足够
        4. bid 价格和存在套利空间
        """
        # 检查结算时间
        if market.settle_seconds < settings.MIN_SETTLE_SECONDS:
            return False, f"距离结算时间不足: {market.settle_seconds:.0f}秒"
        
        # 检查 token 信息
        if not market.token_up or not market.token_down:
            return False, "Token 信息不完整"
        
        # 检查流动性深度
        if market.token_up.bid_depth_usd < self.config.min_depth_usd:
            return False, f"UP bid 深度不足: ${market.token_up.bid_depth_usd:.0f}"
        
        if market.token_down.bid_depth_usd < self.config.min_depth_usd:
            return False, f"DOWN bid 深度不足: ${market.token_down.bid_depth_usd:.0f}"
        
        # 检查 bid 价格和
        bid_sum = market.token_up.best_bid + market.token_down.best_bid
        if bid_sum >= self.config.max_bid_sum:
            return False, f"bid 价格和过高: {bid_sum:.4f}"
        
        return True, "条件满足"
    
    async def calculate_prices(self, market: MarketInfo) -> Tuple[float, float]:
        """
        计算下单价格
        
        策略：挂单价格 = best_bid + tick
        """
        tick = market.token_up.tick_size
        
        # 挂单价格 = best_bid + tick
        price_up = market.token_up.best_bid + tick
        price_down = market.token_down.best_bid + tick
        
        # 精度对齐
        price_up = price_helper.adjust_price_to_tick(price_up, tick)
        price_down = price_helper.adjust_price_to_tick(price_down, tick)
        
        # 确保价格和安全
        if price_up + price_down > self.config.safe_price_sum:
            # 优先降低深度更好的一边价格
            if market.token_up.bid_depth_usd > market.token_down.bid_depth_usd:
                price_up = price_helper.adjust_price_to_tick(
                    self.config.safe_price_sum - price_down - tick, tick
                )
            else:
                price_down = price_helper.adjust_price_to_tick(
                    self.config.safe_price_sum - price_up - tick, tick
                )
        
        # 确保价格在合理范围
        price_up = price_helper.validate_price_range(price_up)
        price_down = price_helper.validate_price_range(price_down)
        
        return price_up, price_down
    
    async def on_order_filled(self, order: Order, position: ArbitragePosition):
        """订单成交回调"""
        logger.info(f"[{order.slug}] 订单成交: {order.side.value} {order.filled_size}@{order.price}")
        self.trades_executed += 1
    
    async def on_position_closed(self, position: ArbitragePosition, pnl: float):
        """持仓关闭回调"""
        pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
        logger.info(f"[{position.slug}] 持仓关闭: PnL = {pnl_str} USDC")
    
    def _get_current_slug(self) -> Optional[str]:
        """获取当前市场 slug"""
        return time_helper.calculate_slug(self.market_type)
    
    async def _get_market_info(self, slug: str) -> Optional[MarketInfo]:
        """获取市场信息"""
        try:
            # 获取市场数据
            market_data = self.client.get_market_by_slug(slug)
            if not market_data:
                return None
            
            # 提取关键信息
            condition_id = market_data.get('conditionId', '')
            clob_token_ids = market_data.get('clobTokenIds', '[]')
            
            # 解析 token IDs
            try:
                if isinstance(clob_token_ids, str):
                    token_ids = json.loads(clob_token_ids)
                else:
                    token_ids = clob_token_ids
            except:
                logger.error(f"解析 token IDs 失败: {clob_token_ids}")
                return None
            
            if len(token_ids) < 2:
                logger.error(f"token IDs 数量不足: {token_ids}")
                return None
            
            # 解析结束时间
            end_date_str = market_data.get('endDate') or market_data.get('closeTime')
            end_date = None
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                except:
                    pass
            
            # 计算距离结算时间
            settle_seconds = 0
            if end_date:
                settle_seconds = time_helper.seconds_to_settlement(end_date)
            
            market_info = MarketInfo(
                slug=slug,
                condition_id=condition_id,
                end_date=end_date,
                settle_seconds=settle_seconds
            )
            
            # 获取两个 token 的订单簿信息
            for idx, token_id in enumerate(token_ids[:2]):
                token_info = await self._get_token_info(token_id)
                if token_info:
                    if idx == 0:
                        market_info.token_up = token_info
                    else:
                        market_info.token_down = token_info
            
            return market_info
            
        except Exception as e:
            logger.error(f"获取市场信息失败: {e}")
            return None
    
    async def _get_token_info(self, token_id: str) -> Optional[TokenInfo]:
        """获取 Token 信息"""
        try:
            # 获取订单簿
            order_book = self.client.get_order_book(token_id)
            if not order_book:
                return None
            
            bids = order_book.get('bids', [])
            asks = order_book.get('asks', [])
            
            # 获取 tick size
            tick_size = self.client.get_tick_size(token_id)
            
            # 计算深度
            ask_depth_usd = sum(
                float(a.get('price', 0)) * float(a.get('size', 0))
                for a in asks[:5]
            )
            bid_depth_usd = sum(
                float(b.get('price', 0)) * float(b.get('size', 0))
                for b in bids[:5]
            )
            
            # 获取最佳买卖价
            orderbook_best_bid = float(bids[0].get('price', 0)) if bids else 0.0
            orderbook_best_ask = float(asks[0].get('price', 1.0)) if asks else 1.0
            
            # 使用 Midpoint API 获取真实价格
            midpoint = self.client.get_midpoint_price(token_id)
            if midpoint:
                best_bid = max(orderbook_best_bid, midpoint - tick_size)
                best_ask = min(orderbook_best_ask, midpoint + tick_size)
            else:
                best_bid = orderbook_best_bid
                best_ask = orderbook_best_ask
            
            return TokenInfo(
                token_id=token_id,
                best_bid=best_bid,
                best_ask=best_ask,
                ask_depth_usd=ask_depth_usd,
                bid_depth_usd=bid_depth_usd,
                tick_size=tick_size,
                bids=bids,
                asks=asks
            )
            
        except Exception as e:
            logger.error(f"获取 Token 信息失败: {e}")
            return None


class Btc5mStrategy(BtcUpdownArbitrageStrategy):
    """BTC 5分钟市场策略"""
    
    def __init__(self):
        super().__init__(market_type='btc-5m')


class Btc15mStrategy(BtcUpdownArbitrageStrategy):
    """BTC 15分钟市场策略"""
    
    def __init__(self):
        super().__init__(market_type='btc-15m')
