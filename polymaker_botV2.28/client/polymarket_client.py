#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 客户端模块
封装 ClobClient + RelayClient，支持 Gasless 交易和 CTF merge/split 操作
"""

import os
import json
import time
import base64
import hmac
import hashlib
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from decimal import Decimal

import requests
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

from config import settings
from models import OrderSide, TokenInfo, MarketInfo
from utils.helpers import ADDRESSES, GAMMA_API_BASE, CLOB_API_BASE, price_helper
from logger import logger

# 官方 SDK
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType, OpenOrderParams
    from py_builder_signing_sdk.config import BuilderConfig
    from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
    from py_builder_relayer_client.client import RelayClient
    HAS_SDK = True
except ImportError as e:
    HAS_SDK = False
    logger.warning(f"SDK 导入失败: {e}，部分功能不可用")


class PolymarketClient:
    """
    Polymarket API 客户端
    
    集成功能：
    - ClobClient: 订单簿操作（下单、取消、查询）
    - RelayClient: Gasless 链上操作（merge、split、approve）
    """
    
    def __init__(self):
        self.host = CLOB_API_BASE
        self.chain_id = 137  # Polygon 主网
        self.address = settings.ADDRESS
        self.funder_address = settings.FUNDER_ADDRESS or settings.ADDRESS
        self.private_key = settings.PRIVATE_KEY
        self.signature_type = settings.SIGNATURE_TYPE  # 1 = POLY_PROXY, 2 = GNOSIS_SAFE
        
        # Builder 凭证
        self.builder_api_key = settings.POLY_BUILDER_API_KEY
        self.builder_secret = settings.POLY_BUILDER_SECRET
        self.builder_passphrase = settings.POLY_BUILDER_PASSPHRASE
        
        # Web3 实例
        self.w3 = Web3()
        
        # 账户实例
        if self.private_key:
            self.account = Account.from_key(self.private_key)
        else:
            self.account = None
        
        # SDK 客户端
        self.clob_client = None
        self.relay_client = None
        
        # 初始化 SDK
        if HAS_SDK:
            self._init_sdk_clients()
        
        logger.info(f"Polymarket 客户端初始化完成")
        logger.info(f"  地址: {self.address}")
        logger.info(f"  Funder: {self.funder_address}")
        logger.info(f"  签名类型: {self.signature_type}")
    
    def _init_sdk_clients(self):
        """初始化 SDK 客户端"""
        # Builder 配置
        builder_config = None
        if self.builder_api_key and self.builder_secret and self.builder_passphrase:
            try:
                builder_creds = BuilderApiKeyCreds(
                    key=self.builder_api_key,
                    secret=self.builder_secret,
                    passphrase=self.builder_passphrase
                )
                builder_config = BuilderConfig(local_builder_creds=builder_creds)
                logger.info("Builder 配置创建成功")
            except Exception as e:
                logger.warning(f"Builder 配置创建失败: {e}")
        
        # 初始化 ClobClient
        try:
            # 创建临时客户端获取 API 凭证
            temp_client = ClobClient(
                host=self.host,
                key=self.private_key,
                chain_id=self.chain_id,
                signature_type=self.signature_type,
                funder=self.funder_address,
                builder_config=builder_config
            )
            
            # 创建或获取 API 凭证
            creds = temp_client.create_or_derive_api_creds()
            logger.info(f"API 凭证创建成功: {creds.api_key[:10]}...")
            
            # 创建正式客户端
            self.clob_client = ClobClient(
                host=self.host,
                key=self.private_key,
                chain_id=self.chain_id,
                signature_type=self.signature_type,
                funder=self.funder_address,
                creds=creds,
                builder_config=builder_config
            )
            logger.info("ClobClient 初始化成功")
        except Exception as e:
            logger.error(f"ClobClient 初始化失败: {e}")
        
        # 初始化 RelayClient（用于 Gasless 链上操作）
        try:
            if builder_config and self.private_key:
                self.relay_client = RelayClient(
                    relayer_url="https://relayer-v2.polymarket.com/",
                    chain_id=self.chain_id,
                    private_key=self.private_key,
                    builder_config=builder_config
                )
                logger.info("RelayClient 初始化成功")
        except Exception as e:
            logger.warning(f"RelayClient 初始化失败: {e}")
    
    # ============================================
    # CTF 代币 Approve（Gasless）
    # ============================================
    
    def approve_ctf_token(self, token_id: str) -> bool:
        """
        Approve CTF 代币给 Exchange 合约（Gasless）
        
        在卖出 CTF 代币前必须调用此方法
        
        Args:
            token_id: Token ID
            
        Returns:
            是否成功
        """
        if not self.relay_client:
            logger.error("RelayClient 未初始化，无法 approve CTF")
            return False
        
        try:
            # CTF 合约地址
            ctf_address = ADDRESSES["CTF"]
            # Exchange 合约地址
            exchange_address = ADDRESSES["CTF_EXCHANGE"]
            
            # setApprovalForAll ABI 编码
            # function setApprovalForAll(address operator, bool approved)
            # selector: 0xa22cb465
            approve_selector = "a22cb465"
            operator_padded = exchange_address[2:].lower().zfill(64)
            approved_padded = "0" * 63 + "1"  # true
            
            approve_data = f"0x{approve_selector}{operator_padded}{approved_padded}"
            
            logger.info(f"正在 approve CTF 代币给 Exchange...")
            
            from py_builder_relayer_client.models import SafeTransaction, OperationType
            
            tx = SafeTransaction(
                to=ctf_address,
                operation=OperationType.Call,
                data=approve_data,
                value="0"
            )
            
            response = self.relay_client.execute([tx], "Approve CTF for trading")
            result = response.wait()
            tx_hash = result.get("transactionHash")
            
            if tx_hash:
                logger.info(f"✅ CTF approve 成功: {tx_hash}")
                return True
            else:
                logger.warning("CTF approve 返回空交易哈希")
                return False
                
        except Exception as e:
            logger.error(f"CTF approve 失败: {e}")
            # 可能已经 approve 过了，尝试继续
            return True
    
    def check_ctf_approval(self) -> bool:
        """
        检查 CTF 代币是否已 approve 给 Exchange
        
        Returns:
            是否已 approve
        """
        try:
            w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
            
            ctf_address = ADDRESSES["CTF"]
            exchange_address = ADDRESSES["CTF_EXCHANGE"]
            
            # isApprovedForAll ABI
            # function isApprovedForAll(address account, address operator) returns (bool)
            check_data = f"0xe985e9c5{self.address[2:].lower().zfill(64)}{exchange_address[2:].lower().zfill(64)}"
            
            result = w3.eth.call({
                'to': ctf_address,
                'data': check_data
            })
            
            is_approved = int(result.hex(), 16) == 1
            logger.info(f"CTF approval 状态: {'已授权' if is_approved else '未授权'}")
            return is_approved
            
        except Exception as e:
            logger.warning(f"检查 CTF approval 失败: {e}")
            return False
    
    # ============================================
    # 市场数据 API
    # ============================================
    
    def get_market_by_slug(self, slug: str) -> Optional[Dict]:
        """通过 slug 获取市场信息"""
        url = f"{GAMMA_API_BASE}/markets/slug/{slug}"
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.json()
            logger.debug(f"未找到市场: {slug}")
            return None
        except Exception as e:
            logger.error(f"获取市场信息失败: {e}")
            return None
    
    def get_order_book(self, token_id: str) -> Optional[Dict]:
        """获取订单簿"""
        url = f"{CLOB_API_BASE}/book"
        params = {'token_id': token_id}
        try:
            response = requests.get(url, params=params, timeout=30)
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.error(f"获取订单簿失败: {e}")
            return None
    
    def get_tick_size(self, token_id: str) -> float:
        """获取 tick size"""
        if self.clob_client:
            try:
                result = self.clob_client.get_tick_size(token_id)
                return float(result) if result else 0.01
            except Exception as e:
                logger.warning(f"获取 tick size 失败: {e}")
        
        # Fallback
        url = f"{CLOB_API_BASE}/tick-size"
        params = {'token_id': token_id}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return float(response.json().get('tick_size', 0.01))
        except:
            pass
        return 0.01
    
    def get_midpoint_price(self, token_id: str) -> Optional[float]:
        """获取中间价"""
        if self.clob_client:
            try:
                result = self.clob_client.get_midpoint(token_id)
                return float(result.get('mid', 0.5)) if result else None
            except Exception as e:
                logger.warning(f"获取中间价失败: {e}")
        return None
    
    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """获取最近成交价"""
        url = f"{CLOB_API_BASE}/last-trade-price"
        params = {'token_id': token_id}
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return float(response.json().get('price', 0))
        except:
            pass
        return None
    
    # ============================================
    # 订单操作 API
    # ============================================
    
    def get_open_orders(self) -> List[Dict]:
        """获取当前挂单"""
        if self.clob_client:
            try:
                return self.clob_client.get_orders(OpenOrderParams())
            except Exception as e:
                logger.error(f"获取挂单失败: {e}")
        return []
    
    def get_order(self, order_id: str) -> Optional[Dict]:
        """获取订单信息"""
        if self.clob_client:
            try:
                return self.clob_client.get_order(order_id)
            except Exception as e:
                logger.warning(f"获取订单失败: {e}")
        return None
    
    def create_limit_order(self, token_id: str, side: OrderSide, 
                          price: float, size: float, 
                          post_only: bool = True) -> Optional[Dict]:
        """
        创建限价单 - 优先 Maker 单赚取返佣
        
        逻辑：
        1. 尝试 post_only=True
        2. 如果被拒绝，调整价格为 best_ask - tick（买入）或 best_bid + tick（卖出）
        3. 再次尝试 post_only=True
        4. 如果仍失败，降级为 Taker
        
        Args:
            token_id: Token ID
            side: BUY 或 SELL
            price: 价格
            size: 数量
            post_only: 是否只做 Maker
            
        Returns:
            订单结果
        """
        if not self.clob_client:
            logger.error("ClobClient 未初始化")
            return None
        
        tick_size = self.get_tick_size(token_id)
        
        def submit_order(order_price: float, is_post_only: bool) -> Optional[Dict]:
            """提交订单的内部函数"""
            try:
                order_args = OrderArgs(
                    token_id=token_id,
                    price=float(order_price),
                    size=float(size),
                    side=side.value,
                    expiration=0  # GTC 订单
                )
                signed_order = self.clob_client.create_order(order_args)
                result = self.clob_client.post_order(
                    signed_order,
                    orderType=OrderType.GTC,
                    post_only=is_post_only
                )
                return result
            except Exception as e:
                return None
        
        # 第一次尝试：原始价格 + post_only
        if post_only:
            result = submit_order(price, True)
            if result:
                logger.info(f"限价单下单成功: {result}")
                return result
            
            # Post-Only 被拒绝，调整价格强制 Maker
            book = self.get_order_book(token_id)
            if book:
                if side == OrderSide.BUY:
                    # 买入：调整为 best_ask - tick（比最低卖价低一个 tick）
                    asks = book.get('asks', [])
                    if asks:
                        best_ask = float(asks[0].get('price', 1.0))
                        adjusted_price = round(best_ask - tick_size, 4)
                        adjusted_price = max(adjusted_price, 0.01)  # 价格下限
                        
                        logger.info(f"Post-Only 被拒绝，调整买入价: {price} -> {adjusted_price} (best_ask={best_ask})")
                        
                        result = submit_order(adjusted_price, True)
                        if result:
                            logger.info(f"限价单下单成功（调整后 Maker）: {result}")
                            return result
                else:
                    # 卖出：调整为 best_bid + tick（比最高买价高一个 tick）
                    bids = book.get('bids', [])
                    if bids:
                        best_bid = float(bids[0].get('price', 0.0))
                        adjusted_price = round(best_bid + tick_size, 4)
                        adjusted_price = min(adjusted_price, 0.99)  # 价格上限
                        
                        logger.info(f"Post-Only 被拒绝，调整卖出价: {price} -> {adjusted_price} (best_bid={best_bid})")
                        
                        result = submit_order(adjusted_price, True)
                        if result:
                            logger.info(f"限价单下单成功（调整后 Maker）: {result}")
                            return result
            
            # 最后降级：Taker 单
            logger.warning(f"Maker 单失败，降级为 Taker...")
            result = submit_order(price, False)
            if result:
                logger.info(f"限价单下单成功（Taker）: {result}")
                return result
            else:
                logger.error(f"Taker 单也失败")
                return None
        else:
            # 非 post_only 模式，直接提交
            result = submit_order(price, False)
            if result:
                logger.info(f"限价单下单成功: {result}")
                return result
            else:
                logger.error(f"限价单下单失败")
                return None
    
    def create_market_order(self, token_id: str, side: OrderSide, 
                           size: float) -> Optional[Dict]:
        """
        创建市价单（使用 GTC + 最佳价格）
        
        Args:
            token_id: Token ID
            side: BUY 或 SELL
            size: 数量
            
        Returns:
            订单结果
        """
        if not self.clob_client:
            logger.error("ClobClient 未初始化")
            return None
        
        # 获取最佳价格
        market_price = 0.5
        try:
            book = self.get_order_book(token_id)
            if book:
                if side == OrderSide.SELL and book.get('bids'):
                    # 卖单使用最佳买价
                    market_price = float(book['bids'][0].get('price', 0.5))
                elif side == OrderSide.BUY and book.get('asks'):
                    # 买单使用最佳卖价
                    market_price = float(book['asks'][0].get('price', 0.5))
                logger.info(f"市价单价格: {market_price}")
        except Exception as e:
            logger.warning(f"获取订单簿失败: {e}")
        
        # 确保价格在有效范围
        market_price = max(0.01, min(market_price, 0.99))
        
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=market_price,
                size=float(size),
                side=side.value,
                expiration=0
            )
            
            signed_order = self.clob_client.create_order(order_args)
            
            result = self.clob_client.post_order(
                signed_order,
                orderType=OrderType.GTC,
                post_only=False  # 允许立即成交
            )
            
            logger.info(f"市价单下单成功: {result}")
            return result
            
        except Exception as e:
            logger.error(f"市价单下单失败: {e}")
            return None
    
    def cancel_order(self, order_id: str) -> bool:
        """取消订单"""
        if self.clob_client:
            try:
                result = self.clob_client.cancel(order_id)
                logger.info(f"取消订单成功: {order_id[:16]}...")
                return True
            except Exception as e:
                logger.error(f"取消订单失败: {e}")
        return False
    
    def cancel_all_orders(self) -> bool:
        """取消所有订单"""
        if self.clob_client:
            try:
                self.clob_client.cancel_all()
                logger.info("已取消所有订单")
                return True
            except Exception as e:
                logger.error(f"取消所有订单失败: {e}")
        return False
    
    # ============================================
    # CTF 操作（Gasless）
    # ============================================
    
    def merge_positions(self, condition_id: str, amount: float) -> Optional[str]:
        """
        合并 YES/NO 代币对为 USDC（Gasless）
        
        Args:
            condition_id: 市场 condition ID
            amount: 合并数量
            
        Returns:
            交易哈希或 None
        """
        if not self.relay_client:
            logger.error("RelayClient 未初始化，无法执行 merge")
            return None
        
        try:
            # CTF mergePositions 函数 ABI
            merge_abi = [{
                "name": "mergePositions",
                "type": "function",
                "inputs": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "parentCollectionId", "type": "bytes32"},
                    {"name": "conditionId", "type": "bytes32"},
                    {"name": "partition", "type": "uint256[]"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": []
            }]
            
            # 导入 SafeTransaction
            from py_builder_relayer_client.models import SafeTransaction, OperationType
            
            # 构建交易数据
            usdc_address = ADDRESSES["USDCe"]
            ctf_address = ADDRESSES["CTF"]
            amount_wei = int(amount * 10**6)  # USDC 有 6 位小数
            
            # 编码调用数据
            contract = self.w3.eth.contract(address=ctf_address, abi=merge_abi)
            call_data = contract.encode_abi(
                abi_element_identifier="mergePositions",
                args=[
                    usdc_address,
                    bytes(32),  # parentCollectionId always zero
                    bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id),
                    [1, 2],  # partition: [YES, NO]
                    amount_wei
                ]
            )
            
            # 创建 SafeTransaction 对象
            merge_tx = SafeTransaction(
                to=ctf_address,
                operation=OperationType.Call,
                data=call_data,
                value='0'
            )
            
            # 执行 Gasless 交易
            response = self.relay_client.execute([merge_tx], "Merge positions")
            result = response.wait()
            
            tx_hash = result.get("transactionHash") if result else None
            if tx_hash:
                logger.info(f"Merge 成功: {tx_hash}")
            return tx_hash
            
        except Exception as e:
            logger.error(f"Merge 失败: {e}")
            return None
    
    def split_position(self, condition_id: str, amount: float) -> Optional[str]:
        """
        将 USDC 拆分为 YES/NO 代币对（Gasless）
        
        Args:
            condition_id: 市场 condition ID
            amount: 拆分数量
            
        Returns:
            交易哈希或 None
        """
        if not self.relay_client:
            logger.error("RelayClient 未初始化，无法执行 split")
            return None
        
        try:
            split_abi = [{
                "name": "splitPosition",
                "type": "function",
                "inputs": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "parentCollectionId", "type": "bytes32"},
                    {"name": "conditionId", "type": "bytes32"},
                    {"name": "partition", "type": "uint256[]"},
                    {"name": "amount", "type": "uint256"}
                ],
                "outputs": []
            }]
            
            # 导入 SafeTransaction
            from py_builder_relayer_client.models import SafeTransaction, OperationType
            
            usdc_address = ADDRESSES["USDCe"]
            ctf_address = ADDRESSES["CTF"]
            amount_wei = int(amount * 10**6)
            
            # 编码调用数据
            contract = self.w3.eth.contract(address=ctf_address, abi=split_abi)
            call_data = contract.encode_abi(
                abi_element_identifier="splitPosition",
                args=[
                    usdc_address,
                    bytes(32),
                    bytes.fromhex(condition_id[2:] if condition_id.startswith('0x') else condition_id),
                    [1, 2],
                    amount_wei
                ]
            )
            
            # 创建 SafeTransaction 对象
            split_tx = SafeTransaction(
                to=ctf_address,
                operation=OperationType.Call,
                data=call_data,
                value='0'
            )
            
            response = self.relay_client.execute([split_tx], "Split position")
            result = response.wait()
            
            tx_hash = result.get("transactionHash") if result else None
            if tx_hash:
                logger.info(f"Split 成功: {tx_hash}")
            return tx_hash
            
        except Exception as e:
            logger.error(f"Split 失败: {e}")
            return None
    
    # ============================================
    # 账户信息 API
    # ============================================
    
    def get_balance(self) -> Optional[Dict]:
        """
        获取账户余额
        
        对于 Builder Program 账户，资金在 Polymarket 内部账户中管理。
        通过分析交易记录来计算当前持仓和余额。
        """
        try:
            # 获取 Builder 交易记录来分析持仓
            trades = self.get_builder_trades() or []
            
            # 按市场+方向统计持仓
            positions_map = {}  # key: market_outcome -> net_size
            
            total_buy_usd = 0
            total_sell_usd = 0
            
            for trade in trades:
                market = trade.get('market', '')
                outcome = trade.get('outcome', '')  # 'Up' or 'Down'
                side = trade.get('side', '')  # 'BUY' or 'SELL'
                size = float(trade.get('size', 0))
                size_usdc = float(trade.get('sizeUsdc', 0))
                
                key = f"{market}_{outcome}"
                
                if key not in positions_map:
                    positions_map[key] = {'up': 0, 'down': 0, 'market': market}
                
                if side == 'BUY':
                    if outcome == 'Up':
                        positions_map[key]['up'] += size
                    else:
                        positions_map[key]['down'] += size
                    total_buy_usd += size_usdc
                elif side == 'SELL':
                    if outcome == 'Up':
                        positions_map[key]['up'] -= size
                    else:
                        positions_map[key]['down'] -= size
                    total_sell_usd += size_usdc
            
            # 计算当前持仓数量和可 merge 数量
            positions_count = 0
            total_matched_size = 0
            
            for key, pos in positions_map.items():
                up_size = pos['up']
                down_size = pos['down']
                
                # 只计算有持仓的
                if up_size > 0 or down_size > 0:
                    positions_count += 1
                    # 可 merge 的数量 = min(up, down)
                    matched = min(up_size, down_size)
                    if matched > 0:
                        total_matched_size += matched
            
            # 估算持仓价值（简化：用买入金额减去卖出金额）
            position_value = total_buy_usd - total_sell_usd
            
            # 通过 Builder trades 估算已用资金
            # 注意：这是近似值，实际余额需要从 Polymarket 查询
            
            return {
                'usdc_balance': '查看 Polymarket 网站',  # Builder 账户余额需在网站查看
                'position_value': position_value,
                'total_value': position_value,  # 近似值
                'positions_count': positions_count,
                'total_trades': len(trades),
                'total_matched': total_matched_size,
                'total_buy_usd': total_buy_usd,
                'total_sell_usd': total_sell_usd
            }
            
        except Exception as e:
            logger.error(f"获取余额失败: {e}")
            return None
    
    def get_builder_trades(self) -> Optional[List[Dict]]:
        """获取 Builder 交易记录"""
        if self.clob_client:
            try:
                return self.clob_client.get_builder_trades()
            except Exception as e:
                logger.warning(f"获取 Builder 交易失败: {e}")
        return []
    
    def get_user_positions(self) -> Optional[List[Dict]]:
        """
        获取用户持仓（通过交易记录计算）
        
        返回格式: [{'market': str, 'up_size': float, 'down_size': float, 'matched': float}]
        """
        trades = self.get_builder_trades() or []
        
        positions_map = {}
        for trade in trades:
            market = trade.get('market', '')
            outcome = trade.get('outcome', '')
            side = trade.get('side', '')
            size = float(trade.get('size', 0))
            
            if market not in positions_map:
                positions_map[market] = {'up': 0, 'down': 0}
            
            if side == 'BUY':
                if outcome == 'Up':
                    positions_map[market]['up'] += size
                else:
                    positions_map[market]['down'] += size
            elif side == 'SELL':
                if outcome == 'Up':
                    positions_map[market]['up'] -= size
                else:
                    positions_map[market]['down'] -= size
        
        result = []
        for market, pos in positions_map.items():
            if pos['up'] > 0 or pos['down'] > 0:
                result.append({
                    'market': market,
                    'up_size': pos['up'],
                    'down_size': pos['down'],
                    'matched': min(pos['up'], pos['down'])
                })
        
        return result


# 全局客户端实例
_client_instance: Optional[PolymarketClient] = None


def get_client() -> PolymarketClient:
    """获取全局客户端实例"""
    global _client_instance
    if _client_instance is None:
        _client_instance = PolymarketClient()
    return _client_instance
