#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 数据库模块
SQLite 持久化 + CSV 导出
"""

import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
import aiosqlite
import pandas as pd

from config import settings
from models import TradeLog, ArbitragePosition, PositionStatus
from logger import logger


class Database:
    """异步 SQLite 数据库管理"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or settings.DATABASE_PATH
        self.reports_dir = Path(settings.REPORTS_DIR)
        self._initialized = False
    
    async def init(self):
        """初始化数据库表"""
        if self._initialized:
            return
        
        # 确保报表目录存在
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        
        async with aiosqlite.connect(self.db_path) as db:
            # 交易日志表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    usd_amount REAL NOT NULL,
                    pnl REAL DEFAULT 0,
                    status TEXT DEFAULT 'completed',
                    note TEXT,
                    order_id TEXT,
                    token_id TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 持仓表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL,
                    condition_id TEXT,
                    token_up_id TEXT,
                    token_down_id TEXT,
                    up_size REAL DEFAULT 0,
                    up_price REAL DEFAULT 0,
                    up_invested REAL DEFAULT 0,
                    down_size REAL DEFAULT 0,
                    down_price REAL DEFAULT 0,
                    down_invested REAL DEFAULT 0,
                    total_invested REAL DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    closed_at TEXT,
                    UNIQUE(slug)
                )
            ''')
            
            # 订单表
            await db.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL UNIQUE,
                    slug TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    filled_size REAL DEFAULT 0,
                    status TEXT DEFAULT 'LIVE',
                    created_at TEXT,
                    updated_at TEXT
                )
            ''')
            
            # 创建索引
            await db.execute('CREATE INDEX IF NOT EXISTS idx_trades_slug ON trades(slug)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)')
            
            await db.commit()
        
        self._initialized = True
        logger.info(f"数据库初始化完成: {self.db_path}")
    
    # ============================================
    # 交易日志操作
    # ============================================
    
    async def log_trade(self, trade: TradeLog) -> int:
        """记录交易日志"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO trades (timestamp, slug, event_type, side, price, size, 
                                   usd_amount, pnl, status, note, order_id, token_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade.timestamp.isoformat(),
                trade.slug,
                trade.event_type,
                trade.side,
                trade.price,
                trade.size,
                trade.usd_amount,
                trade.pnl,
                trade.status,
                trade.note,
                trade.order_id,
                trade.token_id
            ))
            await db.commit()
            return cursor.lastrowid
    
    async def log_order_placed(self, slug: str, side: str, price: float, size: float, 
                               order_id: str, token_id: str):
        """记录下单事件"""
        trade = TradeLog(
            timestamp=datetime.now(),
            slug=slug,
            event_type='BUY',
            side=side,
            price=price,
            size=size,
            usd_amount=price * size,
            status='placed',
            order_id=order_id,
            token_id=token_id
        )
        return await self.log_trade(trade)
    
    async def log_order_filled(self, slug: str, side: str, price: float, filled_size: float,
                               order_id: str, token_id: str, partial: bool = False):
        """记录成交事件"""
        trade = TradeLog(
            timestamp=datetime.now(),
            slug=slug,
            event_type='PARTIAL' if partial else 'BUY',
            side=side,
            price=price,
            size=filled_size,
            usd_amount=price * filled_size,
            status='partial' if partial else 'filled',
            order_id=order_id,
            token_id=token_id
        )
        return await self.log_trade(trade)
    
    async def log_merge(self, slug: str, size: float, profit: float, condition_id: str):
        """记录 Merge 事件"""
        trade = TradeLog(
            timestamp=datetime.now(),
            slug=slug,
            event_type='MERGE',
            side='both',
            price=1.0,  # merge 后价值为 1
            size=size,
            usd_amount=size,
            pnl=profit,
            status='completed',
            note=f'condition_id: {condition_id}'
        )
        return await self.log_trade(trade)
    
    async def log_close(self, slug: str, side: str, price: float, size: float, 
                        pnl: float, token_id: str):
        """记录平仓事件"""
        trade = TradeLog(
            timestamp=datetime.now(),
            slug=slug,
            event_type='SELL',
            side=side,
            price=price,
            size=size,
            usd_amount=price * size,
            pnl=pnl,
            status='closed',
            token_id=token_id
        )
        return await self.log_trade(trade)
    
    async def log_cancel(self, slug: str, order_id: str, reason: str = ""):
        """记录取消事件"""
        trade = TradeLog(
            timestamp=datetime.now(),
            slug=slug,
            event_type='CANCEL',
            side='N/A',
            price=0,
            size=0,
            usd_amount=0,
            status='cancelled',
            note=reason,
            order_id=order_id
        )
        return await self.log_trade(trade)
    
    async def get_trades(self, slug: str = None, limit: int = 100) -> List[Dict]:
        """获取交易记录"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            if slug:
                cursor = await db.execute(
                    'SELECT * FROM trades WHERE slug = ? ORDER BY timestamp DESC LIMIT ?',
                    (slug, limit)
                )
            else:
                cursor = await db.execute(
                    'SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?',
                    (limit,)
                )
            
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ============================================
    # 持仓操作
    # ============================================
    
    async def save_position(self, position: ArbitragePosition) -> int:
        """保存持仓"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT OR REPLACE INTO positions 
                (slug, condition_id, token_up_id, token_down_id, 
                 up_size, up_price, up_invested, 
                 down_size, down_price, down_invested, 
                 total_invested, status, created_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                position.slug,
                position.condition_id,
                position.position_up.token_id if position.position_up else None,
                position.position_down.token_id if position.position_down else None,
                position.position_up.filled_size if position.position_up else 0,
                position.position_up.avg_price if position.position_up else 0,
                position.position_up.invested_usd if position.position_up else 0,
                position.position_down.filled_size if position.position_down else 0,
                position.position_down.avg_price if position.position_down else 0,
                position.position_down.invested_usd if position.position_down else 0,
                position.total_invested_usd,
                position.status,
                position.created_at.isoformat() if position.created_at else None,
                position.closed_at.isoformat() if position.closed_at else None
            ))
            await db.commit()
            return cursor.lastrowid
    
    async def get_position(self, slug: str) -> Optional[Dict]:
        """获取持仓"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                'SELECT * FROM positions WHERE slug = ?',
                (slug,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None
    
    async def get_active_positions(self) -> List[Dict]:
        """获取所有活跃持仓"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM positions WHERE status NOT IN ('closed', 'merged')"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    async def update_position_status(self, slug: str, status: str):
        """更新持仓状态"""
        async with aiosqlite.connect(self.db_path) as db:
            closed_at = datetime.now().isoformat() if status in ['closed', 'merged'] else None
            await db.execute(
                'UPDATE positions SET status = ?, closed_at = ? WHERE slug = ?',
                (status, closed_at, slug)
            )
            await db.commit()
    
    async def delete_position(self, slug: str):
        """删除持仓"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM positions WHERE slug = ?', (slug,))
            await db.commit()
    
    # ============================================
    # 订单操作
    # ============================================
    
    async def save_order(self, order_id: str, slug: str, token_id: str, side: str,
                        price: float, size: float):
        """保存订单"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO orders 
                (order_id, slug, token_id, side, price, size, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                order_id, slug, token_id, side, price, size,
                datetime.now().isoformat(), datetime.now().isoformat()
            ))
            await db.commit()
    
    async def update_order(self, order_id: str, filled_size: float, status: str):
        """更新订单状态"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE orders SET filled_size = ?, status = ?, updated_at = ?
                WHERE order_id = ?
            ''', (filled_size, status, datetime.now().isoformat(), order_id))
            await db.commit()
    
    async def delete_order(self, order_id: str):
        """删除订单"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('DELETE FROM orders WHERE order_id = ?', (order_id,))
            await db.commit()
    
    async def get_active_orders(self) -> List[Dict]:
        """获取活跃订单"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM orders WHERE status = 'LIVE'"
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
    
    # ============================================
    # CSV 导出
    # ============================================
    
    async def export_csv(self, date: datetime = None) -> str:
        """导出 CSV 文件"""
        date = date or datetime.now()
        filename = f"trades_{date.strftime('%Y%m%d')}.csv"
        filepath = self.reports_dir / filename
        
        trades = await self.get_trades(limit=10000)
        
        if not trades:
            logger.warning("没有交易记录可导出")
            return None
        
        # 转换为 DataFrame
        df = pd.DataFrame(trades)
        
        # 选择需要的列
        columns = ['timestamp', 'slug', 'event_type', 'side', 'price', 'size', 
                  'usd_amount', 'pnl', 'status', 'note']
        df = df[[c for c in columns if c in df.columns]]
        
        # 格式化
        df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # 保存
        df.to_csv(filepath, index=False, encoding='utf-8-sig')
        logger.info(f"CSV 导出成功: {filepath}")
        
        return str(filepath)
    
    async def get_daily_summary(self, date: datetime = None) -> Dict:
        """获取每日汇总"""
        date = date or datetime.now()
        date_str = date.strftime('%Y-%m-%d')
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # 总交易数
            cursor = await db.execute(
                "SELECT COUNT(*) as count FROM trades WHERE date(timestamp) = ?",
                (date_str,)
            )
            total_trades = (await cursor.fetchone())['count']
            
            # 总 PnL
            cursor = await db.execute(
                "SELECT SUM(pnl) as total_pnl FROM trades WHERE date(timestamp) = ?",
                (date_str,)
            )
            total_pnl = (await cursor.fetchone())['total_pnl'] or 0
            
            # Merge 次数
            cursor = await db.execute(
                "SELECT COUNT(*) as count FROM trades WHERE event_type = 'MERGE' AND date(timestamp) = ?",
                (date_str,)
            )
            merge_count = (await cursor.fetchone())['count']
            
            # 总交易额
            cursor = await db.execute(
                "SELECT SUM(usd_amount) as total_usd FROM trades WHERE date(timestamp) = ?",
                (date_str,)
            )
            total_usd = (await cursor.fetchone())['total_usd'] or 0
        
        return {
            'date': date_str,
            'total_trades': total_trades,
            'total_pnl': total_pnl,
            'merge_count': merge_count,
            'total_usd': total_usd
        }


# 全局数据库实例
db = Database()
