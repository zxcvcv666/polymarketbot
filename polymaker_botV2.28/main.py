#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 量化机器人主入口
启动 bot + trading loop
"""

import os
import sys
import asyncio
import signal
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from config import settings
from strategy.base_strategy import StrategyManager
from strategy.btc_updown_arbitrage import Btc5mStrategy, Btc15mStrategy
from execution.execution_engine import execution_engine
from position.position_manager import position_manager
from telegram.bot import get_telegram_bot
from database import db
from notifier.telegram_notifier import notifier
from logger import logger, trade_logger


class PolymakerBot:
    """
    Polymaker 机器人主程序
    
    功能：
    - 初始化所有组件
    - 运行交易循环
    - 管理 Telegram Bot
    - 处理优雅关闭
    """
    
    def __init__(self):
        self.is_running = False
        self.strategy_manager = StrategyManager()
        self.telegram_bot = None
        self._shutdown_event = asyncio.Event()
    
    async def start(self):
        """启动机器人"""
        logger.info("=" * 50)
        logger.info("Polymaker 量化机器人启动")
        logger.info("=" * 50)
        
        # 验证配置
        is_valid, missing = settings.validate_required()
        if not is_valid:
            logger.error(f"配置验证失败，缺少: {', '.join(missing)}")
            return
        
        logger.info("配置验证通过")
        
        # 初始化数据库
        await db.init()
        logger.info("数据库初始化完成")
        
        # 初始化持仓管理器
        await position_manager.init()
        logger.info("持仓管理器初始化完成")
        
        # 初始化执行引擎
        await execution_engine.start()
        logger.info("执行引擎初始化完成")
        
        # 注册策略
        self.strategy_manager.register(Btc5mStrategy())
        self.strategy_manager.register(Btc15mStrategy())
        logger.info(f"已注册 {len(self.strategy_manager.strategies)} 个策略")
        
        # 激活所有策略
        self.strategy_manager.activate_all()
        
        # 初始化 Telegram Bot
        self.telegram_bot = get_telegram_bot()
        
        # 设置信号处理
        self._setup_signal_handlers()
        
        # 标记为运行中
        self.is_running = True
        
        # 发送启动通知
        notifier.send_startup()
        
        # 启动任务
        try:
            await asyncio.gather(
                self._run_trading_loop(),
                self._run_telegram_bot(),
                self._run_periodic_tasks()
            )
        except asyncio.CancelledError:
            logger.info("收到取消信号")
        except Exception as e:
            logger.error(f"运行异常: {e}")
        finally:
            await self.stop()
    
    async def stop(self):
        """停止机器人"""
        if not self.is_running:
            return
        
        logger.info("正在停止机器人...")
        self.is_running = False
        
        # 停止执行引擎
        execution_engine.stop()
        
        # 停用所有策略
        self.strategy_manager.deactivate_all()
        
        # 取消所有挂单
        await execution_engine.cancel_all_orders()
        
        # 发送停止通知
        notifier.send_shutdown()
        
        # 停止 Telegram Bot
        if self.telegram_bot:
            await self.telegram_bot.stop()
        
        logger.info("机器人已停止")
    
    async def _run_trading_loop(self):
        """运行交易循环"""
        logger.info("交易循环启动")
        
        while self.is_running:
            try:
                # 检查交易是否暂停
                if execution_engine.is_trading_paused():
                    # 暂停时只检查 merge，不下新单
                    await execution_engine.check_and_merge_all()
                    await asyncio.sleep(5)
                    continue
                
                # 扫描所有活跃策略
                opportunities = await self.strategy_manager.run_all_scans()
                
                # 处理发现的机会
                for strategy_name, opps in opportunities.items():
                    for opp in opps:
                        await self._handle_opportunity(opp)
                
                # 检查是否需要 merge
                await execution_engine.check_and_merge_all()
                
                # 等待下一轮
                await asyncio.sleep(5)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"交易循环异常: {e}")
                trade_logger.log_error("交易循环", e)
                await asyncio.sleep(10)
        
        logger.info("交易循环停止")
    
    async def _handle_opportunity(self, opportunity):
        """处理套利机会"""
        slug = opportunity.slug
        
        # 检查是否已有持仓
        if position_manager.has_position(slug):
            return
        
        # 检查总持仓限制
        total_exposure = position_manager.get_total_exposure()
        if total_exposure >= settings.MAX_EXPOSURE_USD:
            logger.debug(f"[{slug}] 已达最大持仓限制")
            return
        
        # 下单（不再推送机会通知，只在实际资金变动时推送）
        order_id_up, order_id_down = await execution_engine.place_limit_orders(opportunity)
        
        if order_id_up and order_id_down:
            # 监控订单
            await execution_engine.monitor_orders(
                slug=slug,
                order_id_up=order_id_up,
                order_id_down=order_id_down,
                opportunity=opportunity,
                callback=self._on_order_complete
            )
    
    async def _on_order_complete(self, slug: str, status: str, position):
        """订单完成回调"""
        if status == 'filled' and position:
            # 发送成功通知
            if position.is_matched:
                profit = position.locked_profit
                notifier.send_merge_success(
                    slug=slug,
                    size=position.matched_size,
                    profit=profit,
                    total_invested=position.total_invested_usd
                )
        elif status == 'timeout':
            logger.warning(f"[{slug}] 订单超时")
    
    async def _run_telegram_bot(self):
        """运行 Telegram Bot"""
        try:
            await self.telegram_bot.start()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Telegram Bot 异常: {e}")
    
    async def _run_periodic_tasks(self):
        """运行周期性任务"""
        last_report_hour = datetime.now().hour
        
        while self.is_running:
            try:
                now = datetime.now()
                
                # 每小时生成报表
                if now.hour != last_report_hour:
                    last_report_hour = now.hour
                    
                    # 生成 CSV
                    csv_path = await db.export_csv()
                    if csv_path:
                        summary = await db.get_daily_summary()
                        notifier.send_daily_report(summary)
                        logger.info(f"已发送每小时报表")
                
                # 等待
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"周期任务异常: {e}")
                await asyncio.sleep(60)
    
    def _setup_signal_handlers(self):
        """设置信号处理器"""
        loop = asyncio.get_event_loop()
        
        def handle_signal():
            logger.info("收到关闭信号")
            self._shutdown_event.set()
            self.is_running = False
        
        # Unix 信号处理
        try:
            loop.add_signal_handler(signal.SIGINT, handle_signal)
            loop.add_signal_handler(signal.SIGTERM, handle_signal)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass


async def main():
    """主函数"""
    bot = PolymakerBot()
    await bot.start()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到键盘中断，退出")
