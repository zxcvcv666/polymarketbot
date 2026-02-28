#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 执行引擎模块
下单、监控、立即 merge、FOK 平仓
"""

import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass

from config import settings
from models import (
    Order, ArbitragePosition, Position, Opportunity, OrderSide, OrderStatus, PositionStatus
)
from client.polymarket_client import get_client, PolymarketClient
from position.position_manager import position_manager
from database import db
from logger import logger, trade_logger
from notifier.telegram_notifier import notifier


@dataclass
class OrderResult:
    """订单结果"""
    success: bool
    order_id: str = ""
    error: str = ""


class ExecutionEngine:
    """
    执行引擎
    
    核心功能：
    - 下单执行（限价单、市价单）
    - 订单监控
    - 双边立即 Merge
    - 单边 FOK 平仓
    - 所有操作集中管理，防止重复
    """
    
    def __init__(self):
        self.client = get_client()
        self.active_orders: Dict[str, Order] = {}  # order_id -> Order
        self.pending_merges: Dict[str, asyncio.Task] = {}  # slug -> merge task
        self._running = False
        self._trading_paused = False  # 交易暂停标志
    
    async def start(self):
        """启动执行引擎"""
        self._running = True
        self._trading_paused = False
        await position_manager.init()
        logger.info("执行引擎已启动")
    
    def stop(self):
        """停止执行引擎"""
        self._running = False
        self._trading_paused = True
        # 取消所有待处理的 merge 任务
        for task in self.pending_merges.values():
            task.cancel()
        logger.info("执行引擎已停止")
    
    def pause_trading(self):
        """暂停交易（不停止引擎，只是暂停新交易）"""
        self._trading_paused = True
        logger.info("交易已暂停")
    
    def resume_trading(self):
        """恢复交易"""
        self._trading_paused = False
        logger.info("交易已恢复")
    
    def is_trading_paused(self) -> bool:
        """检查交易是否暂停"""
        return self._trading_paused
    
    # ============================================
    # 下单操作
    # ============================================
    
    async def place_limit_orders(self, opportunity: Opportunity) -> Tuple[Optional[str], Optional[str]]:
        """
        下双边限价单
        
        Args:
            opportunity: 套利机会
            
        Returns:
            (UP订单ID, DOWN订单ID)
        """
        market = opportunity.market
        slug = opportunity.slug
        
        # 检查是否已有持仓
        if position_manager.has_position(slug):
            logger.info(f"[{slug}] 已有持仓，跳过下单")
            return None, None
        
        # 下 UP 订单
        up_result = await self._place_single_order(
            slug=slug,
            token_id=market.token_up.token_id,
            side=OrderSide.BUY,
            price=opportunity.price_up,
            size=settings.TRADE_SIZE
        )
        
        # 下 DOWN 订单
        down_result = await self._place_single_order(
            slug=slug,
            token_id=market.token_down.token_id,
            side=OrderSide.BUY,
            price=opportunity.price_down,
            size=settings.TRADE_SIZE
        )
        
        if up_result.success and down_result.success:
            logger.info(f"[{slug}] 双边下单成功: UP@{opportunity.price_up:.4f}, DOWN@{opportunity.price_down:.4f}")
            
            # 保存订单到数据库
            await db.save_order(up_result.order_id, slug, market.token_up.token_id, 
                              'BUY', opportunity.price_up, settings.TRADE_SIZE)
            await db.save_order(down_result.order_id, slug, market.token_down.token_id,
                              'BUY', opportunity.price_down, settings.TRADE_SIZE)
            
            return up_result.order_id, down_result.order_id
        else:
            # 如果一边失败，取消另一边
            if up_result.success:
                await self.cancel_order(up_result.order_id)
            if down_result.success:
                await self.cancel_order(down_result.order_id)
            
            logger.error(f"[{slug}] 双边下单失败: UP={up_result.error}, DOWN={down_result.error}")
            return None, None
    
    async def _place_single_order(self, slug: str, token_id: str, side: OrderSide,
                                  price: float, size: float) -> OrderResult:
        """下单个订单"""
        try:
            result = self.client.create_limit_order(
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                post_only=True
            )
            
            if result and result.get('orderID'):
                order_id = result['orderID']
                
                # 记录订单
                order = Order(
                    order_id=order_id,
                    slug=slug,
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size
                )
                self.active_orders[order_id] = order
                
                # 记录日志
                trade_logger.log_order_placed(slug, side.value, price, size, order_id)
                await db.log_order_placed(slug, side.value, price, size, order_id, token_id)
                
                return OrderResult(success=True, order_id=order_id)
            else:
                return OrderResult(success=False, error="下单返回空结果")
                
        except Exception as e:
            logger.error(f"下单失败: {e}")
            return OrderResult(success=False, error=str(e))
    
    async def place_market_order(self, token_id: str, side: OrderSide, 
                                size: float, slug: str = "") -> OrderResult:
        """下市价单"""
        try:
            result = self.client.create_market_order(
                token_id=token_id,
                side=side,
                size=size
            )
            
            if result and result.get('orderID'):
                order_id = result['orderID']
                
                trade_logger.log_order_filled(slug, side.value, 0, size, order_id)
                
                return OrderResult(success=True, order_id=order_id)
            else:
                return OrderResult(success=False, error="市价单返回空结果")
                
        except Exception as e:
            logger.error(f"市价单失败: {e}")
            return OrderResult(success=False, error=str(e))
    
    # ============================================
    # 订单监控
    # ============================================
    
    async def monitor_orders(self, slug: str, order_id_up: str, order_id_down: str,
                            opportunity: Opportunity, callback: Callable = None):
        """
        监控订单成交
        
        Args:
            slug: 市场 slug
            order_id_up: UP 订单 ID
            order_id_down: DOWN 订单 ID
            opportunity: 套利机会
            callback: 完成回调
        """
        start_time = time.time()
        market = opportunity.market
        
        filled_up = 0.0
        filled_down = 0.0
        avg_price_up = opportunity.price_up
        avg_price_down = opportunity.price_down
        
        logger.info(f"[{slug}] 开始监控订单")
        
        while time.time() - start_time < settings.ORDER_EXPIRATION_SECONDS:
            if not self._running:
                break
            
            # 检查 UP 订单
            order_up = await self._check_order(order_id_up)
            if order_up:
                filled_up = order_up.get('filled_size', filled_up)
                avg_price_up = order_up.get('price', avg_price_up)
            
            # 检查 DOWN 订单
            order_down = await self._check_order(order_id_down)
            if order_down:
                filled_down = order_down.get('filled_size', filled_down)
                avg_price_down = order_down.get('price', avg_price_down)
            
            # 检查双边完全成交
            if filled_up >= settings.TRADE_SIZE and filled_down >= settings.TRADE_SIZE:
                logger.info(f"[{slug}] 双边完全成交")
                
                # 创建持仓
                position = await self._create_position(
                    slug, market, filled_up, avg_price_up, filled_down, avg_price_down
                )
                
                # 立即执行 merge
                await self._execute_merge(position)
                
                if callback:
                    await callback(slug, 'filled', position)
                return
            
            # 检查部分成交
            if filled_up > 0 or filled_down > 0:
                if filled_up != filled_down:
                    # 不平衡成交，更新持仓
                    await self._update_partial_position(
                        slug, market, filled_up, avg_price_up, filled_down, avg_price_down
                    )
            
            await asyncio.sleep(settings.ORDER_POLL_INTERVAL)
        
        # 超时处理
        logger.warning(f"[{slug}] 订单监控超时")
        await self._handle_timeout(slug, order_id_up, order_id_down, 
                                  market, filled_up, avg_price_up, filled_down, avg_price_down)
        
        if callback:
            await callback(slug, 'timeout', None)
    
    async def _check_order(self, order_id: str) -> Optional[Dict]:
        """检查订单状态"""
        try:
            order_data = self.client.get_order(order_id)
            if order_data:
                filled_size = float(order_data.get('size_matched', 0))
                price = float(order_data.get('price', 0))
                
                # 更新本地订单
                if order_id in self.active_orders:
                    self.active_orders[order_id].filled_size = filled_size
                    self.active_orders[order_id].status = OrderStatus.FILLED.value if filled_size >= self.active_orders[order_id].size else OrderStatus.PARTIAL.value
                
                # 更新数据库
                status = 'FILLED' if filled_size > 0 else 'LIVE'
                await db.update_order(order_id, filled_size, status)
                
                return {
                    'filled_size': filled_size,
                    'price': price
                }
        except Exception as e:
            logger.warning(f"检查订单失败: {e}")
        return None
    
    # ============================================
    # Merge 操作
    # ============================================
    
    async def _execute_merge(self, position: ArbitragePosition):
        """
        执行 Merge 操作
        
        当 UP 和 DOWN 持仓数量相等时，可以 merge 为 USDC
        """
        if not position.is_matched:
            logger.warning(f"[{position.slug}] 持仓不匹配，无法 merge")
            return
        
        matched_size = position.matched_size
        condition_id = position.condition_id
        
        logger.info(f"[{position.slug}] 执行 Merge: {matched_size} 份")
        
        # 调用客户端 merge
        tx_hash = self.client.merge_positions(condition_id, matched_size)
        
        if tx_hash:
            # 计算利润
            profit = matched_size - position.total_invested_usd
            
            # 记录日志
            trade_logger.log_merge_success(position.slug, matched_size, profit)
            await db.log_merge(position.slug, matched_size, profit, condition_id)
            
            # 更新持仓状态
            await position_manager.merge_position(position.slug)
            
            logger.info(f"[{position.slug}] Merge 成功: 利润 +{profit:.4f} USDC, tx: {tx_hash}")
        else:
            logger.error(f"[{position.slug}] Merge 失败")
    
    async def check_and_merge_all(self):
        """检查所有可 merge 的持仓"""
        active_positions = position_manager.get_active_positions()
        
        for slug, position in active_positions.items():
            if position.is_matched:
                logger.info(f"[{slug}] 发现可 merge 持仓")
                await self._execute_merge(position)
    
    # ============================================
    # 平仓操作
    # ============================================
    
    async def close_position(self, slug: str, reason: str = ""):
        """
        平仓
        
        Args:
            slug: 市场 slug
            reason: 平仓原因
        """
        position = position_manager.get_position(slug)
        if not position:
            logger.warning(f"[{slug}] 未找到持仓")
            return
        
        logger.info(f"[{slug}] 执行平仓: {reason}")
        
        # 平 UP
        if position.position_up and position.position_up.filled_size > 0:
            await self._market_sell(
                position.position_up.token_id,
                position.position_up.filled_size,
                slug,
                'UP'
            )
        
        # 平 DOWN
        if position.position_down and position.position_down.filled_size > 0:
            await self._market_sell(
                position.position_down.token_id,
                position.position_down.filled_size,
                slug,
                'DOWN'
            )
        
        # 关闭持仓
        await position_manager.close_position(slug)
    
    async def close_all_positions(self, reason: str = "手动平仓"):
        """平掉所有持仓"""
        active_positions = position_manager.get_active_positions()
        
        for slug in list(active_positions.keys()):
            await self.close_position(slug, reason)
    
    async def _market_sell(self, token_id: str, size: float, slug: str, side: str, market=None):
        """
        快速卖出单边仓位
        
        策略：直接按 tick 值挂限价单，确保最快成交
        """
        logger.info(f"[{slug}] 快速卖出 {side}: {size}")
        
        # 检查并 approve CTF 代币（卖出前必须授权）
        try:
            is_approved = self.client.check_ctf_approval()
            if not is_approved:
                logger.info(f"[{slug}] CTF 未授权，正在 approve...")
                self.client.approve_ctf_token(token_id)
                await asyncio.sleep(2)  # 等待交易确认
        except Exception as e:
            logger.warning(f"[{slug}] 检查/授权 CTF 失败: {e}，尝试继续卖出")
        
        # 尝试按 tick 挂限价单
        limit_success = False
        sell_price = 0.0
        
        try:
            # 获取 tick_size
            tick_size = self.client.get_tick_size(token_id)
            
            if tick_size:
                # 卖价直接设为 tick 值（最小价格），确保最快成交
                sell_price = tick_size
                
                logger.info(f"[{slug}] 按 tick 限价卖出 {side}: {size}@{sell_price:.4f}")
                
                # 下限价卖单 (post_only=False 允许立即成交)
                result = self.client.create_limit_order(
                    token_id=token_id,
                    side=OrderSide.SELL,
                    price=sell_price,
                    size=size,
                    post_only=False
                )
                
                if result and result.get('orderID'):
                    order_id = result['orderID']
                    limit_success = True
                    
                    # 短暂等待成交
                    await asyncio.sleep(1)
                    
                    # 检查成交情况
                    order_data = self.client.get_order(order_id)
                    if order_data:
                        filled_size = float(order_data.get('size_matched', 0))
                        
                        if filled_size >= size * 0.99:  # 完全成交
                            # 获取实际成交价格
                            actual_price = float(order_data.get('price', sell_price))
                            logger.info(f"[{slug}] 限价卖单完全成交: {side} {filled_size}@{actual_price:.4f}")
                            pnl = 0
                            trade_logger.log_position_closed(slug, side, filled_size, pnl)
                            await db.log_close(slug, side, actual_price, filled_size, pnl, token_id)
                            # 推送单边平仓通知
                            notifier.send_position_closed(slug, side, filled_size, pnl, "单边持仓快速平仓")
                            return
                        
                        elif filled_size > 0:  # 部分成交
                            logger.warning(f"[{slug}] 限价卖单部分成交: {filled_size}/{size}")
                            
                            # 取消剩余订单
                            await self.cancel_order(order_id, "部分成交，改用市价单")
                            
                            # 剩余数量用市价单
                            remaining = size - filled_size
                            if remaining > 0:
                                logger.info(f"[{slug}] 剩余 {remaining} 改用市价单")
                                await self._execute_market_sell(token_id, remaining, slug, side, token_id)
                            
                            actual_price = float(order_data.get('price', sell_price))
                            pnl = 0
                            trade_logger.log_position_closed(slug, side, filled_size, pnl)
                            await db.log_close(slug, side, actual_price, filled_size, pnl, token_id)
                            # 推送单边平仓通知
                            notifier.send_position_closed(slug, side, filled_size, pnl, "单边持仓部分平仓")
                            return
                        
                        else:  # 未成交
                            logger.warning(f"[{slug}] 限价卖单未成交，改用市价单")
                            await self.cancel_order(order_id, "未成交，改用市价单")
                            limit_success = False
                else:
                    logger.warning(f"[{slug}] 限价卖单下单失败，改用市价单")
                    limit_success = False
            else:
                logger.warning(f"[{slug}] 获取 tick_size 失败，改用市价单")
                
        except Exception as e:
            logger.warning(f"[{slug}] 限价卖出失败: {e}，改用市价单")
            limit_success = False
        
        # 如果限价单失败或未成交，使用市价单
        if not limit_success:
            await self._execute_market_sell(token_id, size, slug, side, token_id)
    
    async def _execute_market_sell(self, token_id: str, size: float, slug: str, side: str, token_id_for_log: str = None):
        """执行市价卖出"""
        logger.info(f"[{slug}] 市价卖出 {side}: {size}")
        
        result = await self.place_market_order(token_id, OrderSide.SELL, size, slug)
        
        if result.success:
            pnl = 0
            trade_logger.log_position_closed(slug, side, size, pnl)
            await db.log_close(slug, side, 0, size, pnl, token_id_for_log or token_id)
            # 推送单边平仓通知
            notifier.send_position_closed(slug, side, size, pnl, "单边持仓市价平仓")
        else:
            logger.error(f"[{slug}] 市价卖出失败: {result.error}")
    
    # ============================================
    # 订单取消
    # ============================================
    
    async def cancel_order(self, order_id: str, reason: str = ""):
        """取消订单"""
        if self.client.cancel_order(order_id):
            # 从活跃订单中移除
            if order_id in self.active_orders:
                order = self.active_orders.pop(order_id)
                trade_logger.log_order_cancelled(order.slug, order_id, reason)
                await db.log_cancel(order.slug, order_id, reason)
                await db.delete_order(order_id)
    
    async def cancel_all_orders(self):
        """取消所有订单"""
        if self.client.cancel_all_orders():
            # 清空活跃订单
            for order_id, order in list(self.active_orders.items()):
                trade_logger.log_order_cancelled(order.slug, order_id, "取消所有")
                await db.log_cancel(order.slug, order_id, "取消所有")
            
            self.active_orders.clear()
    
    # ============================================
    # 辅助方法
    # ============================================
    
    async def _create_position(self, slug: str, market, filled_up: float, 
                               avg_price_up: float, filled_down: float, 
                               avg_price_down: float) -> ArbitragePosition:
        """创建持仓"""
        position = ArbitragePosition(
            slug=slug,
            condition_id=market.condition_id,
            position_up=Position(
                token_id=market.token_up.token_id,
                side='up',
                filled_size=filled_up,
                avg_price=avg_price_up,
                invested_usd=filled_up * avg_price_up,
                status=PositionStatus.ARBITRAGE.value
            ),
            position_down=Position(
                token_id=market.token_down.token_id,
                side='down',
                filled_size=filled_down,
                avg_price=avg_price_down,
                invested_usd=filled_down * avg_price_down,
                status=PositionStatus.ARBITRAGE.value
            ),
            status=PositionStatus.ARBITRAGE.value
        )
        
        await position_manager.add_position(position)
        return position
    
    async def _update_partial_position(self, slug: str, market, filled_up: float,
                                       avg_price_up: float, filled_down: float,
                                       avg_price_down: float):
        """更新部分成交持仓"""
        position = position_manager.get_position(slug)
        
        if not position:
            # 创建新持仓
            position = ArbitragePosition(
                slug=slug,
                condition_id=market.condition_id,
                status=PositionStatus.PARTIAL.value
            )
        
        if filled_up > 0:
            position.position_up = Position(
                token_id=market.token_up.token_id,
                side='up',
                filled_size=filled_up,
                avg_price=avg_price_up,
                invested_usd=filled_up * avg_price_up,
                status=PositionStatus.PARTIAL.value
            )
        
        if filled_down > 0:
            position.position_down = Position(
                token_id=market.token_down.token_id,
                side='down',
                filled_size=filled_down,
                avg_price=avg_price_down,
                invested_usd=filled_down * avg_price_down,
                status=PositionStatus.PARTIAL.value
            )
        
        await position_manager.add_position(position)
    
    async def _handle_timeout(self, slug: str, order_id_up: str, order_id_down: str,
                             market, filled_up: float, avg_price_up: float,
                             filled_down: float, avg_price_down: float):
        """处理订单超时"""
        # 取消未成交订单
        if filled_up < settings.TRADE_SIZE:
            await self.cancel_order(order_id_up, "超时取消")
        if filled_down < settings.TRADE_SIZE:
            await self.cancel_order(order_id_down, "超时取消")
        
        # 处理单边成交
        if filled_up > 0 and filled_down == 0:
            logger.warning(f"[{slug}] 仅 UP 成交，执行平仓")
            await self._market_sell(market.token_up.token_id, filled_up, slug, 'UP')
        elif filled_down > 0 and filled_up == 0:
            logger.warning(f"[{slug}] 仅 DOWN 成交，执行平仓")
            await self._market_sell(market.token_down.token_id, filled_down, slug, 'DOWN')
        elif filled_up > 0 and filled_down > 0:
            # 部分双边成交，记录持仓
            await self._update_partial_position(
                slug, market, filled_up, avg_price_up, filled_down, avg_price_down
            )


# 全局执行引擎实例
execution_engine = ExecutionEngine()
