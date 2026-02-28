#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker Telegram Bot 模块
aiogram v3 + 中文键盘 + 中文指令
"""

import asyncio
import os
import re
import requests
from datetime import datetime
from typing import Optional, List, Dict

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from client.polymarket_client import get_client
from position.position_manager import position_manager
from execution.execution_engine import execution_engine
from database import db
from notifier.telegram_notifier import notifier
from logger import logger
from web3 import Web3


# 键盘按钮文字
KEYBOARD_BUTTONS = {
    'status': '📊 当前状态',
    'positions': '💼 我的持仓',
    'orders': '📋 当前挂单',
    'start': '▶️ 启动交易',
    'stop': '⏸️ 暂停交易',
    'close_all': '🛑 一键全部平仓',
    'balance': '💰 查询余额',
    'withdraw': '💸 提现',
    'report': '📈 查看报表',
    'download': '📥 下载交易记录',
    'cancel_all': '❌ 取消所有挂单',
    'help': '❓ 帮助信息'
}


def get_main_keyboard(trading_active: bool = True) -> ReplyKeyboardMarkup:
    """获取主键盘"""
    builder = ReplyKeyboardBuilder()
    
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['status']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['positions']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['orders']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['balance']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['withdraw']))
    
    # 根据交易状态显示启停按钮
    if trading_active:
        builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['stop']))
    else:
        builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['start']))
    
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['close_all']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['cancel_all']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['report']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['download']))
    builder.add(KeyboardButton(text=KEYBOARD_BUTTONS['help']))
    
    builder.adjust(2, 2, 2, 2, 2, 1)
    
    return builder.as_markup(resize_keyboard=True)


def get_confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    """获取确认键盘"""
    builder = InlineKeyboardBuilder()
    
    builder.add(InlineKeyboardButton(
        text="✅ 确认",
        callback_data=f"confirm_{action}"
    ))
    builder.add(InlineKeyboardButton(
        text="❌ 取消",
        callback_data="cancel_action"
    ))
    
    return builder.as_markup()


class TelegramBot:
    """
    Telegram Bot 控制器
    
    使用 aiogram v3 实现
    所有指令和按钮使用中文
    """
    
    def __init__(self):
        self.bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        self.dp = Dispatcher()
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.client = None
        self._running = False
        self._trading_paused = False  # 交易暂停状态
        
        # 注册处理器
        self._register_handlers()
    
    def _register_handlers(self):
        """注册消息处理器"""
        
        # 中文指令
        @self.dp.message(Command("状态"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['status'])
        async def cmd_status(message: types.Message):
            await self._handle_status(message)
        
        @self.dp.message(Command("持仓"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['positions'])
        async def cmd_positions(message: types.Message):
            await self._handle_positions(message)
        
        @self.dp.message(Command("挂单"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['orders'])
        async def cmd_orders(message: types.Message):
            await self._handle_orders(message)
        
        @self.dp.message(Command("余额"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['balance'])
        async def cmd_balance(message: types.Message):
            await self._handle_balance(message)
        
        @self.dp.message(Command("提现"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['withdraw'])
        async def cmd_withdraw(message: types.Message):
            await self._handle_withdraw(message)
        
        # TX 命令：/TX 地址 金额
        @self.dp.message(Command("TX"))
        async def cmd_tx(message: types.Message):
            await self._handle_tx(message)
        
        @self.dp.message(Command("平仓全部"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['close_all'])
        async def cmd_close_all(message: types.Message):
            await self._handle_close_all(message)
        
        @self.dp.message(Command("取消挂单"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['cancel_all'])
        async def cmd_cancel_all(message: types.Message):
            await self._handle_cancel_all(message)
        
        @self.dp.message(Command("报表"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['report'])
        async def cmd_report(message: types.Message):
            await self._handle_report(message)
        
        @self.dp.message(Command("下载"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['download'])
        async def cmd_download(message: types.Message):
            await self._handle_download(message)
        
        @self.dp.message(Command("帮助"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['help'])
        async def cmd_help(message: types.Message):
            await self._handle_help(message)
        
        # 启停命令
        @self.dp.message(Command("启动"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['start'])
        async def cmd_start_trading(message: types.Message):
            await self._handle_start_trading(message)
        
        @self.dp.message(Command("暂停"))
        @self.dp.message(F.text == KEYBOARD_BUTTONS['stop'])
        async def cmd_stop_trading(message: types.Message):
            await self._handle_stop_trading(message)
        
        # 回调查询
        @self.dp.callback_query(F.data.startswith("confirm_"))
        async def handle_confirm(callback: types.CallbackQuery):
            await self._handle_confirm(callback)
        
        @self.dp.callback_query(F.data == "cancel_action")
        async def handle_cancel(callback: types.CallbackQuery):
            await callback.message.edit_text("❌ 操作已取消")
            await callback.answer()
    
    async def _handle_status(self, message: types.Message):
        """处理状态查询"""
        exposure = position_manager.get_total_exposure()
        positions_count = position_manager.get_positions_count()
        
        status = "运行中" if self._running else "已停止"
        trading_status = "🟢 交易中" if not self._trading_paused else "🔴 已暂停"
        
        await message.answer(
            f"📊 <b>系统状态</b>\n\n"
            f"Bot 状态: <code>{status}</code>\n"
            f"交易状态: <code>{trading_status}</code>\n"
            f"总持仓: <code>{exposure:.2f} USDC</code>\n"
            f"持仓数: <code>{positions_count}</code>",
            reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
        )
    
    async def _handle_positions(self, message: types.Message):
        """处理持仓查询 - 从链上交易记录查询实际持仓"""
        if not self.client:
            self.client = get_client()
        
        # 从 Builder 交易记录分析实际持仓
        trades = self.client.get_builder_trades() or []
        
        # 按市场分组统计
        markets = {}
        for t in trades:
            condition_id = t.get('market', t.get('marketSlug', 'Unknown'))
            side = t.get('side')
            size = float(t.get('size', 0))
            price = float(t.get('price', 0))
            outcome = t.get('outcome', 'Unknown')
            size_usdc = float(t.get('sizeUsdc', 0))
            
            if condition_id not in markets:
                markets[condition_id] = {
                    'UP': {'size': 0, 'value': 0, 'price_avg': 0, 'total_size': 0},
                    'DOWN': {'size': 0, 'value': 0, 'price_avg': 0, 'total_size': 0}
                }
            
            outcome_key = 'UP' if 'Up' in outcome else 'DOWN'
            
            if side == 'BUY':
                markets[condition_id][outcome_key]['size'] += size
                markets[condition_id][outcome_key]['value'] += size_usdc
            elif side == 'SELL':
                markets[condition_id][outcome_key]['size'] -= size
                markets[condition_id][outcome_key]['value'] -= size_usdc
        
        # 过滤有持仓的市场
        active_positions = []
        for condition_id, pos in markets.items():
            up_size = pos['UP']['size']
            down_size = pos['DOWN']['size']
            
            if up_size > 0 or down_size > 0:
                active_positions.append({
                    'condition_id': condition_id,
                    'up_size': up_size,
                    'down_size': down_size,
                    'up_value': pos['UP']['value'],
                    'down_value': pos['DOWN']['value']
                })
        
        if not active_positions:
            await message.answer("📭 <b>当前无活跃持仓</b>\n\n💡 持仓数据来自链上交易记录", reply_markup=get_main_keyboard())
            return
        
        lines = ["📋 <b>当前持仓（链上查询）</b>\n"]
        
        for pos in active_positions[:10]:
            condition_id = pos['condition_id']
            up = pos['up_size']
            down = pos['down_size']
            
            lines.append(f"\n<code>{condition_id[:20]}...</code>")
            lines.append(f"  UP: {up:.1f} (${pos['up_value']:.2f})")
            lines.append(f"  DOWN: {down:.1f} (${pos['down_value']:.2f})")
            
            # 判断状态
            if up > 0 and down > 0:
                merge_amount = min(up, down)
                lines.append(f"  ✅ 可合并: {merge_amount:.1f}")
            elif up > 0:
                lines.append(f"  ⚠️ 单边 UP（等待市场结束或卖出）")
            elif down > 0:
                lines.append(f"  ⚠️ 单边 DOWN（等待市场结束或卖出）")
        
        lines.append(f"\n\n💡 点击「🛑 一键平仓」可合并/赎回持仓")
        
        await message.answer('\n'.join(lines), reply_markup=get_main_keyboard(trading_active=not self._trading_paused))
    
    async def _handle_orders(self, message: types.Message):
        """处理挂单查询"""
        orders = await db.get_active_orders()
        
        if not orders:
            await message.answer(
                "📭 <b>当前无活跃挂单</b>\n\n"
                "💡 提示：订单通过 CLOB API 查询",
                reply_markup=get_main_keyboard()
            )
            return
        
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
        
        await message.answer('\n'.join(lines), reply_markup=get_main_keyboard())
    
    async def _handle_balance(self, message: types.Message):
        """处理余额查询 - 显示链上余额、持仓数、盈利情况"""
        if not self.client:
            self.client = get_client()
        
        # 获取链上余额
        w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
        safe_address = settings.ADDRESS
        usdc_address = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
        
        erc20_abi = '[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]'
        usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_address), abi=erc20_abi)
        
        try:
            usdc_balance_raw = usdc.functions.balanceOf(Web3.to_checksum_address(safe_address)).call()
            usdc_balance = usdc_balance_raw / 1e6
        except:
            usdc_balance = 0
        
        try:
            matic_balance = w3.eth.get_balance(Web3.to_checksum_address(safe_address))
            matic_balance = w3.from_wei(matic_balance, 'ether')
        except:
            matic_balance = 0
        
        # 获取持仓信息
        positions_data = position_manager.get_summary()
        positions = positions_data.get('positions', [])
        positions_count = len(positions)
        total_invested = sum(p.get('total_invested', 0) for p in positions)
        
        # 获取交易统计
        trades = self.client.get_builder_trades() or []
        total_buy = sum(float(t.get('sizeUsdc', 0)) for t in trades if t.get('side') == 'BUY')
        total_sell = sum(float(t.get('sizeUsdc', 0)) for t in trades if t.get('side') == 'SELL')
        total_trades = len(trades)
        
        # 计算盈利（简化计算：卖出 - 买入）
        pnl = total_sell - total_buy
        
        await message.answer(
            f"💰 <b>账户余额详情</b>\n\n"
            f"📍 <b>Safe 钱包地址</b>\n"
            f"<code>{safe_address}</code>\n\n"
            f"💎 <b>链上余额</b>\n"
            f"USDC.e: <code>{usdc_balance:.2f}</code>\n"
            f"MATIC: <code>{matic_balance:.4f}</code>\n\n"
            f"💼 <b>持仓情况</b>\n"
            f"持仓市场数: <code>{positions_count}</code>\n"
            f"总投入: <code>{total_invested:.2f} USDC</code>\n\n"
            f"📊 <b>交易统计</b>\n"
            f"总交易数: <code>{total_trades}</code>\n"
            f"总买入: <code>{total_buy:.2f} USDC</code>\n"
            f"总卖出: <code>{total_sell:.2f} USDC</code>\n\n"
            f"📈 <b>盈亏情况</b>\n"
            f"已实现盈亏: <code>{pnl:.2f} USDC</code>\n"
            f"{'🟢 盈利' if pnl >= 0 else '🔴 亏损'}\n\n"
            f"💡 <b>提现命令</b>\n"
            f"<code>/TX 地址 金额</code>",
            reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
        )
    
    async def _handle_withdraw(self, message: types.Message):
        """处理提现帮助请求"""
        await message.answer(
            f"💸 <b>提现功能</b>\n\n"
            f"<b>用法：</b>\n"
            f"<code>/TX 地址 金额</code>\n\n"
            f"<b>示例：</b>\n"
            f"<code>/TX 0x1234...abcd 5.0</code>\n"
            f"→ 转出 5 USDC.e 到指定地址\n\n"
            f"<code>/TX 0x1234...abcd all</code>\n"
            f"→ 转出全部 USDC.e\n\n"
            f"⚠️ <b>注意：</b>\n"
            f"• 目标地址必须是 Polygon 链地址\n"
            f"• 转账免 Gas（由 Polymarket 支付）\n"
            f"• 请仔细核对地址",
            reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
        )
    
    async def _handle_tx(self, message: types.Message):
        """处理提现命令 /TX 地址 金额"""
        if not self.client:
            self.client = get_client()
        
        # 解析命令参数
        text = message.text or ""
        text = text.strip()
        parts = text.split()
        
        if len(parts) < 3:
            await message.answer(
                "❌ <b>参数错误</b>\n\n"
                "用法: <code>/TX 地址 金额</code>\n"
                "示例: <code>/TX 0x1234...abcd 5.0</code>",
                reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
            )
            return
        
        target_address = parts[1]
        amount_str = parts[2]
        
        # 验证地址格式
        if not re.match(r'^0x[a-fA-F0-9]{40}$', target_address):
            await message.answer(
                f"❌ <b>地址格式错误</b>\n\n"
                f"<code>{target_address}</code>\n"
                f"请输入有效的 Polygon 地址（0x 开头的 42 位十六进制）",
                reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
            )
            return
        
        # 获取链上余额
        w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
        safe_address = settings.ADDRESS
        usdc_address = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
        
        erc20_abi = '[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]'
        usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_address), abi=erc20_abi)
        
        try:
            usdc_balance_raw = usdc.functions.balanceOf(Web3.to_checksum_address(safe_address)).call()
            usdc_balance = usdc_balance_raw / 1e6
        except:
            usdc_balance = 0
        
        if usdc_balance <= 0:
            await message.answer(
                "❌ <b>余额不足</b>\n\n"
                "Safe 钱包 USDC.e 余额为 0",
                reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
            )
            return
        
        # 解析金额
        if amount_str.lower() == 'all':
            amount = usdc_balance
        else:
            try:
                amount = float(amount_str)
            except ValueError:
                await message.answer(
                    f"❌ <b>金额格式错误</b>\n\n"
                    f"<code>{amount_str}</code> 不是有效数字",
                    reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
                )
                return
        
        if amount <= 0:
            await message.answer(
                "❌ <b>金额必须大于 0</b>",
                reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
            )
            return
        
        if amount > usdc_balance:
            await message.answer(
                f"❌ <b>余额不足</b>\n\n"
                f"请求: {amount:.2f} USDC.e\n"
                f"余额: {usdc_balance:.2f} USDC.e",
                reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
            )
            return
        
        # 发送确认消息
        await message.answer(
            f"⏳ <b>正在处理提现...</b>\n\n"
            f"金额: <code>{amount:.2f} USDC.e</code>\n"
            f"目标地址:\n<code>{target_address}</code>",
            reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
        )
        
        # 执行转账
        try:
            from py_builder_relayer_client.client import RelayClient
            from py_builder_relayer_client.models import SafeTransaction, OperationType
            from py_builder_signing_sdk.config import BuilderConfig
            from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
            import os
            
            builder_creds = BuilderApiKeyCreds(
                key=os.getenv("POLY_BUILDER_API_KEY"),
                secret=os.getenv("POLY_BUILDER_SECRET"),
                passphrase=os.getenv("POLY_BUILDER_PASSPHRASE")
            )
            builder_config = BuilderConfig(local_builder_creds=builder_creds)
            
            relay_client = RelayClient(
                relayer_url="https://relayer-v2.polymarket.com/",
                chain_id=137,
                private_key=os.getenv("PRIVATE_KEY"),
                builder_config=builder_config
            )
            
            # 构建 transfer 交易
            transfer_selector = "a9059cbb"
            to_padded = target_address[2:].lower().zfill(64)
            amount_raw = int(amount * 1e6)
            amount_hex = hex(amount_raw)[2:].zfill(64)
            transfer_data = f"0x{transfer_selector}{to_padded}{amount_hex}"
            
            tx = SafeTransaction(
                to=usdc_address,
                operation=OperationType.Call,
                data=transfer_data,
                value="0"
            )
            
            response = relay_client.execute([tx], "Transfer USDC.e")
            result = response.wait()
            tx_hash = result.get("transactionHash")
            
            await message.answer(
                f"✅ <b>提现成功！</b>\n\n"
                f"金额: <code>{amount:.2f} USDC.e</code>\n"
                f"目标地址:\n<code>{target_address}</code>\n\n"
                f"交易哈希:\n<code>{tx_hash}</code>\n\n"
                f"🔗 <a href=\"https://polygonscan.com/tx/{tx_hash}\">查看交易</a>",
                reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
            )
            logger.info(f"Telegram 提现成功: {amount:.2f} USDC.e -> {target_address}")
            
        except Exception as e:
            await message.answer(
                f"❌ <b>提现失败</b>\n\n"
                f"错误: <code>{str(e)[:100]}</code>",
                reply_markup=get_main_keyboard(trading_active=not self._trading_paused)
            )
            logger.error(f"Telegram 提现失败: {e}")
    
    async def _handle_close_all(self, message: types.Message):
        """处理平仓请求 - 合并/赎回持仓"""
        if not self.client:
            self.client = get_client()
        
        # 从 Builder 交易记录分析实际持仓
        trades = self.client.get_builder_trades() or []
        
        # 按市场分组统计
        markets = {}
        for t in trades:
            condition_id = t.get('market', t.get('marketSlug', 'Unknown'))
            side = t.get('side')
            size = float(t.get('size', 0))
            outcome = t.get('outcome', 'Unknown')
            
            if condition_id not in markets:
                markets[condition_id] = {'UP': 0, 'DOWN': 0}
            
            outcome_key = 'UP' if 'Up' in outcome else 'DOWN'
            
            if side == 'BUY':
                markets[condition_id][outcome_key] += size
            elif side == 'SELL':
                markets[condition_id][outcome_key] -= size
        
        # 统计可合并和单边持仓
        merge_positions = []  # 可合并
        single_positions = []  # 单边
        
        for condition_id, pos in markets.items():
            up = pos['UP']
            down = pos['DOWN']
            
            if up > 0 and down > 0:
                merge_amount = min(up, down)
                merge_positions.append({
                    'condition_id': condition_id,
                    'amount': merge_amount,
                    'up_remaining': up - merge_amount,
                    'down_remaining': down - merge_amount
                })
            elif up > 0:
                single_positions.append({
                    'condition_id': condition_id,
                    'side': 'UP',
                    'amount': up
                })
            elif down > 0:
                single_positions.append({
                    'condition_id': condition_id,
                    'side': 'DOWN',
                    'amount': down
                })
        
        if not merge_positions and not single_positions:
            await message.answer("📭 当前无持仓需要处理", reply_markup=get_main_keyboard())
            return
        
        # 显示持仓状态并确认
        lines = ["⚠️ <b>确认处理持仓</b>\n"]
        
        if merge_positions:
            lines.append(f"\n✅ <b>可合并持仓 ({len(merge_positions)}个)</b>")
            for pos in merge_positions[:5]:
                lines.append(f"<code>{pos['condition_id'][:20]}...</code>: {pos['amount']:.1f}")
        
        if single_positions:
            lines.append(f"\n⚠️ <b>单边持仓 ({len(single_positions)}个)</b>")
            for pos in single_positions[:5]:
                lines.append(f"<code>{pos['condition_id'][:20]}...</code>: {pos['side']} {pos['amount']:.1f}")
            lines.append("\n💡 单边持仓需要市场结束后才能赎回")
        
        lines.append("\n\n此操作不可撤销！")
        
        await message.answer(
            '\n'.join(lines),
            reply_markup=get_confirm_keyboard("close_all")
        )
    
    async def _handle_cancel_all(self, message: types.Message):
        """处理取消挂单请求"""
        orders = await db.get_active_orders()
        
        if not orders:
            await message.answer("📭 当前无挂单可取消", reply_markup=get_main_keyboard())
            return
        
        await message.answer(
            f"⚠️ <b>确认取消</b>\n\n"
            f"将取消 <code>{len(orders)}</code> 个挂单\n"
            f"此操作不可撤销！",
            reply_markup=get_confirm_keyboard("cancel_all")
        )
    
    async def _handle_report(self, message: types.Message):
        """处理报表请求"""
        summary = await db.get_daily_summary()
        
        await message.answer(
            f"📊 <b>每日报告</b>\n\n"
            f"日期: <code>{summary.get('date', 'N/A')}</code>\n"
            f"总交易数: <code>{summary.get('total_trades', 0)}</code>\n"
            f"Merge 次数: <code>{summary.get('merge_count', 0)}</code>\n"
            f"总 PnL: <code>{summary.get('total_pnl', 0):.4f} USDC</code>\n"
            f"交易额: <code>{summary.get('total_usd', 0):.2f} USDC</code>",
            reply_markup=get_main_keyboard()
        )
    
    async def _handle_download(self, message: types.Message):
        """处理下载请求"""
        csv_path = await db.export_csv()
        
        if csv_path:
            await message.answer_document(
                types.FSInputFile(csv_path),
                caption=f"📊 交易记录导出\n\n文件: {csv_path.split('/')[-1]}"
            )
        else:
            await message.answer("❌ 导出失败，没有交易记录", reply_markup=get_main_keyboard())
    
    async def _handle_help(self, message: types.Message):
        """处理帮助请求"""
        text = """📖 <b>命令帮助</b>

<b>中文指令：</b>
/状态 - 查询系统状态
/持仓 - 查询当前持仓
/挂单 - 查询当前挂单
/余额 - 查询账户余额（链上余额+持仓+盈亏）
/启动 - 启动量化交易
/暂停 - 暂停量化交易
/平仓全部 - 平掉所有持仓
/取消挂单 - 取消所有挂单
/提现 - 查看提现帮助
/TX 地址 金额 - 提现到指定地址
/报表 - 查看每日报表
/下载 - 下载交易记录
/帮助 - 显示帮助信息

<b>提现示例：</b>
<code>/TX 0x1234...abcd 5.0</code>
<code>/TX 0x1234...abcd all</code>

💡 使用下方按钮快速操作"""
        
        await message.answer(text, reply_markup=get_main_keyboard(trading_active=not self._trading_paused))
    
    async def _handle_start_trading(self, message: types.Message):
        """处理启动交易请求"""
        if not self._trading_paused:
            await message.answer(
                "✅ 交易已经在运行中",
                reply_markup=get_main_keyboard(trading_active=True)
            )
            return
        
        self._trading_paused = False
        execution_engine.resume_trading()
        
        await message.answer(
            "▶️ <b>量化交易已启动</b>\n\n"
            "机器人将继续扫描套利机会并执行交易",
            reply_markup=get_main_keyboard(trading_active=True)
        )
        logger.info("交易已通过 Telegram 命令启动")
    
    async def _handle_stop_trading(self, message: types.Message):
        """处理暂停交易请求"""
        if self._trading_paused:
            await message.answer(
                "⏸️ 交易已经暂停",
                reply_markup=get_main_keyboard(trading_active=False)
            )
            return
        
        self._trading_paused = True
        execution_engine.pause_trading()
        
        await message.answer(
            "⏸️ <b>量化交易已暂停</b>\n\n"
            "机器人将停止扫描新机会\n"
            "现有持仓和挂单不受影响",
            reply_markup=get_main_keyboard(trading_active=False)
        )
        logger.info("交易已通过 Telegram 命令暂停")
    
    async def _handle_confirm(self, callback: types.CallbackQuery):
        """处理确认操作"""
        action = callback.data.replace("confirm_", "")
        
        if action == "close_all":
            await callback.message.edit_text("⏳ 正在处理持仓...")
            result = await self._execute_merge_and_redeem()
            await callback.message.edit_text(result)
        
        elif action == "cancel_all":
            await callback.message.edit_text("⏳ 正在取消挂单...")
            await execution_engine.cancel_all_orders()
            await callback.message.edit_text("✅ 已取消所有挂单")
        
        await callback.answer()
    
    async def _execute_merge_and_redeem(self) -> str:
        """执行合并和赎回操作 - 支持市场已结算的赎回"""
        from py_builder_relayer_client.client import RelayClient
        from py_builder_relayer_client.models import SafeTransaction, OperationType
        from py_builder_signing_sdk.config import BuilderConfig
        from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
        
        if not self.client:
            self.client = get_client()
        
        # 分析持仓
        trades = self.client.get_builder_trades() or []
        markets = {}
        for t in trades:
            condition_id = t.get('market', t.get('marketSlug', 'Unknown'))
            side = t.get('side')
            size = float(t.get('size', 0))
            outcome = t.get('outcome', 'Unknown')
            
            if condition_id not in markets:
                markets[condition_id] = {'UP': 0, 'DOWN': 0}
            
            outcome_key = 'UP' if 'Up' in outcome else 'DOWN'
            if side == 'BUY':
                markets[condition_id][outcome_key] += size
            elif side == 'SELL':
                markets[condition_id][outcome_key] -= size
        
        # 初始化 RelayClient
        builder_creds = BuilderApiKeyCreds(
            key=settings.POLY_BUILDER_API_KEY,
            secret=settings.POLY_BUILDER_SECRET,
            passphrase=settings.POLY_BUILDER_PASSPHRASE
        )
        builder_config = BuilderConfig(local_builder_creds=builder_creds)
        
        relay_client = RelayClient(
            relayer_url='https://relayer-v2.polymarket.com/',
            chain_id=137,
            private_key=settings.PRIVATE_KEY,
            builder_config=builder_config
        )
        
        # 合约地址
        USDC_ADDRESS = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
        CTF_ADDRESS = '0x4D97DCd97eC945f40cF65F87097ACe5EA0476045'
        
        # 统计
        merge_count = 0
        merge_failed = 0
        redeem_count = 0
        redeem_failed = 0
        single_count = 0
        
        lines = ["📊 <b>持仓处理结果</b>\n"]
        
        def check_market_status(cond_id: str) -> Dict:
            """检查市场状态"""
            try:
                url = f'https://gamma-api.polymarket.com/markets?condition_id={cond_id}'
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        market = data[0]
                        return {
                            'resolved': market.get('resolved', False),
                            'winning_outcome': market.get('winningOutcome'),
                            'question': market.get('question', 'N/A')[:40]
                        }
            except Exception as e:
                logger.warning(f"查询市场状态失败: {e}")
            return {'resolved': False, 'winning_outcome': None, 'question': 'N/A'}
        
        def execute_redeem(cond_id: str, winning_index: int, amount: float) -> bool:
            """执行赎回获胜代币
            winning_index: 1=UP(Yes), 2=DOWN(No)
            """
            try:
                # redeemPositions 编码
                # function redeemPositions(address collateralToken, bytes32 parentCollectionId, bytes32 conditionId, uint256[] calldata partition, uint256 amount)
                redeem_selector = '3a4b48f7'
                collateral_padded = USDC_ADDRESS[2:].lower().zfill(64)
                parent_collection = '0' * 64
                condition_padded = cond_id[2:].lower().zfill(64) if cond_id.startswith('0x') else cond_id.lower().zfill(64)
                partition_offset = '0' * 62 + '60'
                amount_raw = int(amount * 1e18)
                amount_hex = hex(amount_raw)[2:].zfill(64)
                partition_length = '0' * 62 + '01'
                winning_padded = '0' * 62 + str(winning_index).zfill(2)
                
                redeem_data = f'0x{redeem_selector}{collateral_padded}{parent_collection}{condition_padded}{partition_offset}{amount_hex}{partition_length}{winning_padded}'
                
                tx = SafeTransaction(
                    to=CTF_ADDRESS,
                    operation=OperationType.Call,
                    data=redeem_data,
                    value='0'
                )
                
                response = relay_client.execute([tx], f'Redeem {cond_id[:16]}')
                result = response.wait()
                tx_hash = result.get('transactionHash')
                
                if tx_hash:
                    logger.info(f"赎回成功: {cond_id} {amount}")
                    return True
                return False
                
            except Exception as e:
                logger.error(f"赎回失败: {cond_id} {e}")
                return False
        
        for condition_id, pos in markets.items():
            up = pos['UP']
            down = pos['DOWN']
            
            if up <= 0 and down <= 0:
                continue
            
            # 检查市场状态
            market_status = check_market_status(condition_id)
            is_resolved = market_status['resolved']
            winning_outcome = market_status['winning_outcome']
            question = market_status['question']
            
            lines.append(f"\n📁 {question}...")
            
            if is_resolved:
                # 市场已结算 - 赎回获胜代币
                lines.append(f"   状态: 已结算 (获胜方: {winning_outcome})")
                
                if winning_outcome == 'Yes' and up > 0:
                    # UP 获胜
                    lines.append(f"   赎回 UP: {up:.1f}")
                    if execute_redeem(condition_id, 1, up):
                        redeem_count += 1
                        lines.append(f"   ✅ 赎回成功")
                    else:
                        redeem_failed += 1
                        lines.append(f"   ❌ 赎回失败")
                        
                elif winning_outcome == 'No' and down > 0:
                    # DOWN 获胜
                    lines.append(f"   赎回 DOWN: {down:.1f}")
                    if execute_redeem(condition_id, 2, down):
                        redeem_count += 1
                        lines.append(f"   ✅ 赎回成功")
                    else:
                        redeem_failed += 1
                        lines.append(f"   ❌ 赎回失败")
                        
                else:
                    # 持有的是失败方代币
                    if up > 0:
                        lines.append(f"   ❌ UP 已归零 ({up:.1f})")
                    if down > 0:
                        lines.append(f"   ❌ DOWN 已归零 ({down:.1f})")
                        
            else:
                # 市场未结算
                lines.append(f"   状态: 进行中")
                
                # 尝试合并 UP + DOWN
                if up > 0 and down > 0:
                    merge_amount = min(up, down)
                    lines.append(f"   合并: {merge_amount:.1f}")
                    
                    try:
                        # mergePositions 编码
                        merge_selector = '4a65a3ec'
                        collateral_padded = USDC_ADDRESS[2:].lower().zfill(64)
                        parent_collection = '0' * 64
                        condition_padded = condition_id[2:].lower().zfill(64) if condition_id.startswith('0x') else condition_id.lower().zfill(64)
                        partition_offset = '0' * 62 + '60'
                        amount_raw = int(merge_amount * 1e18)
                        amount_hex = hex(amount_raw)[2:].zfill(64)
                        partition_length = '0' * 62 + '02'
                        partition_1 = '0' * 62 + '01'
                        partition_2 = '0' * 62 + '02'
                        
                        merge_data = f'0x{merge_selector}{collateral_padded}{parent_collection}{condition_padded}{partition_offset}{amount_hex}{partition_length}{partition_1}{partition_2}'
                        
                        tx = SafeTransaction(
                            to=CTF_ADDRESS,
                            operation=OperationType.Call,
                            data=merge_data,
                            value='0'
                        )
                        
                        response = relay_client.execute([tx], f'Merge {condition_id[:16]}')
                        result = response.wait()
                        tx_hash = result.get('transactionHash')
                        
                        merge_count += 1
                        lines.append(f"   ✅ 合并成功")
                        logger.info(f"合并成功: {condition_id} {merge_amount}")
                        
                        # 剩余单边持仓
                        remaining_up = up - merge_amount
                        remaining_down = down - merge_amount
                        if remaining_up > 0:
                            single_count += 1
                            lines.append(f"   ⚠️ 剩余 UP: {remaining_up:.1f}")
                        if remaining_down > 0:
                            single_count += 1
                            lines.append(f"   ⚠️ 剩余 DOWN: {remaining_down:.1f}")
                            
                    except Exception as e:
                        merge_failed += 1
                        lines.append(f"   ❌ 合并失败: {str(e)[:30]}")
                        logger.error(f"合并失败: {condition_id} {e}")
                
                elif up > 0:
                    single_count += 1
                    lines.append(f"   ⚠️ 单边 UP: {up:.1f} (等待市场结束或卖出)")
                elif down > 0:
                    single_count += 1
                    lines.append(f"   ⚠️ 单边 DOWN: {down:.1f} (等待市场结束或卖出)")
        
        lines.append(f"\n📈 <b>统计</b>")
        lines.append(f"合并成功: {merge_count}")
        lines.append(f"合并失败: {merge_failed}")
        lines.append(f"赎回成功: {redeem_count}")
        lines.append(f"赎回失败: {redeem_failed}")
        lines.append(f"单边持仓: {single_count}")
        
        return '\n'.join(lines)
    
    async def start(self):
        """启动 Bot"""
        self._running = True
        self._trading_paused = False
        self.client = get_client()
        
        # 发送启动消息
        await self.bot.send_message(
            self.chat_id,
            f"🚀 <b>Polymaker 量化机器人已启动</b>\n\n"
            f"✅ Gasless 模式已启用\n"
            f"📊 签名类型: <code>{'GNOSIS_SAFE' if settings.SIGNATURE_TYPE == 2 else 'POLY_PROXY'}</code>\n"
            f"💰 交易数量: <code>{settings.TRADE_SIZE}</code>\n"
            f"📈 最大持仓: <code>{settings.MAX_EXPOSURE_USD} USDC</code>\n\n"
            f"💡 使用下方按钮快速操作",
            reply_markup=get_main_keyboard(trading_active=True)
        )
        
        # 开始轮询
        logger.info("Telegram Bot 已启动")
        await self.dp.start_polling(self.bot)
    
    async def stop(self):
        """停止 Bot"""
        self._running = False
        await self.bot.send_message(self.chat_id, "🛑 <b>Polymaker 量化机器人已停止</b>")
        await self.bot.session.close()
        logger.info("Telegram Bot 已停止")
    
    def set_running(self, running: bool):
        """设置运行状态"""
        self._running = running


# 全局 Bot 实例
telegram_bot: Optional[TelegramBot] = None


def get_telegram_bot() -> TelegramBot:
    """获取 Telegram Bot 实例"""
    global telegram_bot
    if telegram_bot is None:
        telegram_bot = TelegramBot()
    return telegram_bot
