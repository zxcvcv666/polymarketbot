#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 日志模块
统一日志系统 + CSV 导出
"""

import os
import sys
import logging
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from config import settings


def setup_logging(name: str = 'polymaker') -> logging.Logger:
    """
    配置日志系统
    
    Args:
        name: 日志器名称
        
    Returns:
        配置好的日志器
    """
    log_format = '%(asctime)s [%(levelname)s] %(name)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # 创建日志器
    logger = logging.getLogger(name)
    
    # 避免重复添加 handler
    if logger.handlers:
        return logger
    
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(console_handler)
    
    # 文件处理器
    log_dir = Path(settings.LOG_FILE).parent
    if log_dir and not log_dir.exists():
        log_dir.mkdir(parents=True, exist_ok=True)
    
    file_handler = logging.FileHandler(settings.LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(file_handler)
    
    return logger


# 全局日志器
logger = setup_logging()


class TradeLogger:
    """交易日志管理器 - 负责内存中的日志记录，CSV 导出由 Database 类处理"""
    
    def __init__(self):
        self.logger = logging.getLogger('polymaker.trades')
    
    def log_order_placed(self, slug: str, side: str, price: float, size: float, order_id: str):
        """记录下单"""
        self.logger.info(f"📤 下单成功 | {slug} | {side} | 价格: {price:.4f} | 数量: {size:.2f} | 订单ID: {order_id[:16]}...")
    
    def log_order_filled(self, slug: str, side: str, price: float, filled_size: float, order_id: str):
        """记录成交"""
        self.logger.info(f"✅ 订单成交 | {slug} | {side} | 价格: {price:.4f} | 成交: {filled_size:.2f} | 订单ID: {order_id[:16]}...")
    
    def log_order_partial(self, slug: str, side: str, price: float, filled_size: float, remaining: float):
        """记录部分成交"""
        self.logger.info(f"📊 部分成交 | {slug} | {side} | 价格: {price:.4f} | 成交: {filled_size:.2f} | 剩余: {remaining:.2f}")
    
    def log_order_cancelled(self, slug: str, order_id: str, reason: str = ""):
        """记录取消"""
        self.logger.info(f"❌ 订单取消 | {slug} | 订单ID: {order_id[:16]}... | 原因: {reason}")
    
    def log_merge_success(self, slug: str, size: float, profit: float):
        """记录合并成功"""
        self.logger.info(f"🔄 Merge 成功 | {slug} | 数量: {size:.2f} | 利润: +{profit:.4f} USDC")
    
    def log_position_closed(self, slug: str, side: str, size: float, pnl: float):
        """记录平仓"""
        pnl_str = f"+{pnl:.4f}" if pnl >= 0 else f"{pnl:.4f}"
        self.logger.info(f"🔓 平仓完成 | {slug} | {side} | 数量: {size:.2f} | PnL: {pnl_str} USDC")
    
    def log_opportunity(self, slug: str, bid_sum: float, potential_profit: float):
        """记录套利机会"""
        self.logger.info(f"💡 套利机会 | {slug} | bid和: {bid_sum:.4f} | 预期利润: {potential_profit:.4f} USDC")
    
    def log_risk_alert(self, slug: str, reason: str, detail: str):
        """记录风控警报"""
        self.logger.warning(f"⚠️ 风控警报 | {slug} | {reason} | {detail}")
    
    def log_error(self, context: str, error: Exception):
        """记录错误"""
        self.logger.error(f"❌ 错误 | {context} | {type(error).__name__}: {str(error)}")
    
    def log_system_status(self, status: str, exposure: float, positions_count: int):
        """记录系统状态"""
        self.logger.info(f"📊 系统状态 | {status} | 持仓: {exposure:.2f} USDC | 持仓数: {positions_count}")


# 全局交易日志器
trade_logger = TradeLogger()
