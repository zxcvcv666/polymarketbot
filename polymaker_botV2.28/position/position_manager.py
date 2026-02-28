#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 持仓管理模块
持仓管理、PNL 计算
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
import threading

from config import settings
from models import (
    Position, ArbitragePosition, PositionStatus, OrderSide
)
from database import db
from logger import logger


class PositionManager:
    """
    持仓管理器
    
    功能：
    - 管理内存中的持仓状态
    - 同步持久化到数据库
    - 计算 PNL
    - 提供持仓查询接口
    """
    
    def __init__(self):
        self.positions: Dict[str, ArbitragePosition] = {}
        self.lock = threading.Lock()
        self._initialized = False
    
    async def init(self):
        """初始化 - 从数据库加载持仓"""
        if self._initialized:
            return
        
        await db.init()
        
        # 从数据库加载活跃持仓
        try:
            active_positions = await db.get_active_positions()
            for pos_data in active_positions:
                position = self._position_from_dict(pos_data)
                if position:
                    self.positions[position.slug] = position
            logger.info(f"从数据库加载 {len(self.positions)} 个持仓")
        except Exception as e:
            logger.error(f"加载持仓失败: {e}")
        
        self._initialized = True
    
    def _position_from_dict(self, data: Dict) -> Optional[ArbitragePosition]:
        """从字典创建 ArbitragePosition"""
        try:
            position = ArbitragePosition(
                slug=data['slug'],
                condition_id=data.get('condition_id', ''),
                status=data.get('status', PositionStatus.PENDING.value),
                created_at=datetime.fromisoformat(data['created_at']) if data.get('created_at') else None,
                closed_at=datetime.fromisoformat(data['closed_at']) if data.get('closed_at') else None
            )
            
            # UP 持仓
            if data.get('token_up_id') and data.get('up_size', 0) > 0:
                position.position_up = Position(
                    token_id=data['token_up_id'],
                    side='up',
                    filled_size=data['up_size'],
                    avg_price=data['up_price'],
                    invested_usd=data['up_invested'],
                    status=data.get('status', PositionStatus.PENDING.value)
                )
            
            # DOWN 持仓
            if data.get('token_down_id') and data.get('down_size', 0) > 0:
                position.position_down = Position(
                    token_id=data['token_down_id'],
                    side='down',
                    filled_size=data['down_size'],
                    avg_price=data['down_price'],
                    invested_usd=data['down_invested'],
                    status=data.get('status', PositionStatus.PENDING.value)
                )
            
            return position
        except Exception as e:
            logger.error(f"解析持仓数据失败: {e}")
            return None
    
    async def add_position(self, position: ArbitragePosition):
        """添加新持仓"""
        with self.lock:
            self.positions[position.slug] = position
        
        # 持久化
        await db.save_position(position)
        logger.info(f"添加持仓: {position.slug}")
    
    async def update_position(self, slug: str, side: str, filled_size: float,
                             avg_price: float, invested_usd: float):
        """更新持仓"""
        with self.lock:
            if slug not in self.positions:
                return
            
            position = self.positions[slug]
            
            if side == 'up' and position.position_up:
                position.position_up.filled_size = filled_size
                position.position_up.avg_price = avg_price
                position.position_up.invested_usd = invested_usd
            elif side == 'down' and position.position_down:
                position.position_down.filled_size = filled_size
                position.position_down.avg_price = avg_price
                position.position_down.invested_usd = invested_usd
        
        # 持久化
        await db.save_position(position)
    
    async def update_status(self, slug: str, status: str):
        """更新持仓状态"""
        with self.lock:
            if slug in self.positions:
                self.positions[slug].status = status
                if status in [PositionStatus.CLOSED.value, PositionStatus.MERGED.value]:
                    self.positions[slug].closed_at = datetime.now()
        
        # 持久化
        await db.update_position_status(slug, status)
    
    def get_position(self, slug: str) -> Optional[ArbitragePosition]:
        """获取持仓"""
        with self.lock:
            return self.positions.get(slug)
    
    def has_position(self, slug: str) -> bool:
        """检查是否有持仓"""
        with self.lock:
            return slug in self.positions
    
    def get_active_positions(self) -> Dict[str, ArbitragePosition]:
        """获取所有活跃持仓"""
        with self.lock:
            return {
                k: v for k, v in self.positions.items()
                if v.status not in [PositionStatus.CLOSED.value, PositionStatus.MERGED.value]
            }
    
    def get_total_exposure(self) -> float:
        """获取总持仓价值"""
        with self.lock:
            return sum(
                p.total_invested_usd for p in self.positions.values()
                if p.status not in [PositionStatus.CLOSED.value, PositionStatus.MERGED.value]
            )
    
    def get_positions_count(self) -> int:
        """获取活跃持仓数量"""
        with self.lock:
            return sum(
                1 for p in self.positions.values()
                if p.status not in [PositionStatus.CLOSED.value, PositionStatus.MERGED.value]
            )
    
    async def close_position(self, slug: str):
        """关闭持仓"""
        await self.update_status(slug, PositionStatus.CLOSED.value)
        logger.info(f"关闭持仓: {slug}")
    
    async def merge_position(self, slug: str):
        """标记持仓为已合并"""
        await self.update_status(slug, PositionStatus.MERGED.value)
        logger.info(f"持仓已合并: {slug}")
    
    async def remove_position(self, slug: str):
        """移除持仓记录"""
        with self.lock:
            if slug in self.positions:
                del self.positions[slug]
        
        await db.delete_position(slug)
    
    def calculate_pnl(self, slug: str, sell_price_up: float = None, 
                     sell_price_down: float = None) -> Dict:
        """
        计算 PNL
        
        Args:
            slug: 市场 slug
            sell_price_up: UP 卖出价格（可选）
            sell_price_down: DOWN 卖出价格（可选）
            
        Returns:
            PNL 详情字典
        """
        position = self.get_position(slug)
        if not position:
            return {}
        
        result = {
            'slug': slug,
            'total_invested': position.total_invested_usd,
            'up_invested': position.position_up.invested_usd if position.position_up else 0,
            'down_invested': position.position_down.invested_usd if position.position_down else 0,
        }
        
        # 如果双边匹配，计算 merge 利润
        if position.is_matched:
            matched_size = position.matched_size
            merge_value = matched_size  # merge 后获得 matched_size USDC
            merge_profit = merge_value - position.total_invested_usd
            result['matched_size'] = matched_size
            result['merge_profit'] = merge_profit
            result['merge_profit_ratio'] = merge_profit / position.total_invested_usd if position.total_invested_usd > 0 else 0
        
        # 如果提供卖出价格，计算平仓 PNL
        if sell_price_up is not None and position.position_up:
            up_pnl = position.position_up.filled_size * sell_price_up - position.position_up.invested_usd
            result['up_pnl'] = up_pnl
            result['up_sell_value'] = position.position_up.filled_size * sell_price_up
        
        if sell_price_down is not None and position.position_down:
            down_pnl = position.position_down.filled_size * sell_price_down - position.position_down.invested_usd
            result['down_pnl'] = down_pnl
            result['down_sell_value'] = position.position_down.filled_size * sell_price_down
        
        # 总 PNL
        total_pnl = result.get('merge_profit', 0)
        total_pnl += result.get('up_pnl', 0)
        total_pnl += result.get('down_pnl', 0)
        result['total_pnl'] = total_pnl
        
        return result
    
    def get_summary(self) -> Dict:
        """获取持仓汇总"""
        active = self.get_active_positions()
        
        total_invested = 0
        total_matched = 0
        positions_list = []
        
        for slug, pos in active.items():
            total_invested += pos.total_invested_usd
            if pos.is_matched:
                total_matched += pos.matched_size
            
            positions_list.append({
                'slug': slug,
                'up_size': pos.position_up.filled_size if pos.position_up else 0,
                'up_price': pos.position_up.avg_price if pos.position_up else 0,
                'down_size': pos.position_down.filled_size if pos.position_down else 0,
                'down_price': pos.position_down.avg_price if pos.position_down else 0,
                'total_invested': pos.total_invested_usd,
                'is_matched': pos.is_matched,
                'status': pos.status
            })
        
        return {
            'total_positions': len(active),
            'total_invested': total_invested,
            'total_matched': total_matched,
            'positions': positions_list
        }


# 全局持仓管理器实例
position_manager = PositionManager()
