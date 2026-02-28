#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymarket 持仓处理脚本
- 合并可合并的持仓（UP + DOWN）
- 赎回已结束市场的获胜代币
"""

import os
import sys
from dotenv import load_dotenv
from web3 import Web3
from py_builder_relayer_client.client import RelayClient
from py_builder_relayer_client.models import SafeTransaction, OperationType
from py_builder_signing_sdk.config import BuilderConfig
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
from py_clob_client.client import ClobClient

load_dotenv()

# Polymarket 合约地址
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Conditional Tokens Framework
EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"  # CTF Exchange

# Polygon RPC
RPC_URL = "https://polygon-bor-rpc.publicnode.com"


def get_relay_client():
    """获取 RelayClient"""
    builder_creds = BuilderApiKeyCreds(
        key=os.getenv("POLY_BUILDER_API_KEY"),
        secret=os.getenv("POLY_BUILDER_SECRET"),
        passphrase=os.getenv("POLY_BUILDER_PASSPHRASE")
    )
    builder_config = BuilderConfig(local_builder_creds=builder_creds)
    
    return RelayClient(
        relayer_url="https://relayer-v2.polymarket.com/",
        chain_id=137,
        private_key=os.getenv("PRIVATE_KEY"),
        builder_config=builder_config
    )


def get_clob_client():
    """获取 ClobClient"""
    builder_creds = BuilderApiKeyCreds(
        key=os.getenv("POLY_BUILDER_API_KEY"),
        secret=os.getenv("POLY_BUILDER_SECRET"),
        passphrase=os.getenv("POLY_BUILDER_PASSPHRASE")
    )
    builder_config = BuilderConfig(local_builder_creds=builder_creds)
    
    return ClobClient(
        host='https://clob.polymarket.com',
        key=os.getenv("PRIVATE_KEY"),
        chain_id=137,
        signature_type=2,
        funder=os.getenv("ADDRESS"),
        builder_config=builder_config
    )


def merge_positions(condition_id: str, amount: float):
    """
    合并 UP + DOWN 代币为 USDC
    
    Args:
        condition_id: 市场条件 ID
        amount: 合并数量
    """
    relay_client = get_relay_client()
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    
    # mergePositions ABI 编码
    # function mergePositions(
    #     address collateralToken,
    #     bytes32 parentCollectionId,
    #     bytes32 conditionId,
    #     uint256[] calldata partition,
    #     uint256 amount
    # )
    
    merge_selector = "0x4a65a3ec"  # mergePositions selector
    
    # 参数编码
    collateral_padded = USDC_ADDRESS[2:].lower().zfill(64)
    parent_collection = "0" * 64  # bytes32(0)
    condition_padded = condition_id.lower().zfill(64) if not condition_id.startswith("0x") else condition_id[2:].lower().zfill(64)
    
    # partition: [1, 2] 表示 UP 和 DOWN
    partition_offset = "0" * 62 + "60"  # offset to partition array
    partition_length = "0" * 62 + "02"  # 2 elements
    partition_1 = "0" * 62 + "01"  # UP
    partition_2 = "0" * 62 + "02"  # DOWN
    
    amount_raw = int(amount * 1e18)  # CTF uses 18 decimals
    amount_hex = hex(amount_raw)[2:].zfill(64)
    
    # 构建调用数据
    merge_data = f"0x{merge_selector}{collateral_padded}{parent_collection}{condition_padded}{partition_offset}{amount_hex}{partition_length}{partition_1}{partition_2}"
    
    print(f"正在合并 {amount} 代币...")
    print(f"Condition ID: {condition_id}")
    
    tx = SafeTransaction(
        to=CTF_ADDRESS,
        operation=OperationType.Call,
        data=merge_data,
        value="0"
    )
    
    try:
        response = relay_client.execute([tx], "Merge positions")
        print("交易已提交，等待确认...")
        result = response.wait()
        tx_hash = result.get("transactionHash")
        print(f"✅ 合并成功!")
        print(f"交易哈希: {tx_hash}")
        print(f"查看: https://polygonscan.com/tx/{tx_hash}")
        return True
    except Exception as e:
        print(f"❌ 合并失败: {e}")
        return False


def redeem_positions(condition_id: str, winning_index: int, amount: float):
    """
    赎回已结束市场的获胜代币
    
    Args:
        condition_id: 市场条件 ID
        winning_index: 获胜方索引 (1=UP, 2=DOWN)
        amount: 赎回数量
    """
    relay_client = get_relay_client()
    
    # redeemPositions ABI 编码
    # function redeemPositions(
    #     address collateralToken,
    #     bytes32 parentCollectionId,
    #     bytes32 conditionId,
    #     uint256[] calldata partition,
    #     uint256 amount
    # )
    
    redeem_selector = "0x3a4b48f7"  # redeemPositions selector
    
    collateral_padded = USDC_ADDRESS[2:].lower().zfill(64)
    parent_collection = "0" * 64
    condition_padded = condition_id.lower().zfill(64) if not condition_id.startswith("0x") else condition_id[2:].lower().zfill(64)
    
    # partition: 只包含获胜方
    partition_offset = "0" * 62 + "60"
    partition_length = "0" * 62 + "01"
    winning_padded = "0" * 62 + str(winning_index).zfill(2)
    
    amount_raw = int(amount * 1e18)
    amount_hex = hex(amount_raw)[2:].zfill(64)
    
    redeem_data = f"0x{redeem_selector}{collateral_padded}{parent_collection}{condition_padded}{partition_offset}{amount_hex}{partition_length}{winning_padded}"
    
    print(f"正在赎回获胜代币...")
    print(f"Condition ID: {condition_id}")
    print(f"获胜方: {'UP' if winning_index == 1 else 'DOWN'}")
    
    tx = SafeTransaction(
        to=CTF_ADDRESS,
        operation=OperationType.Call,
        data=redeem_data,
        value="0"
    )
    
    try:
        response = relay_client.execute([tx], "Redeem positions")
        print("交易已提交，等待确认...")
        result = response.wait()
        tx_hash = result.get("transactionHash")
        print(f"✅ 赎回成功!")
        print(f"交易哈希: {tx_hash}")
        print(f"查看: https://polygonscan.com/tx/{tx_hash}")
        return True
    except Exception as e:
        print(f"❌ 赎回失败: {e}")
        return False


def check_positions():
    """检查当前持仓"""
    client = get_clob_client()
    trades = client.get_builder_trades() or []
    
    print("=== 当前持仓分析 ===\n")
    
    # 按市场分组
    markets = {}
    for t in trades:
        condition_id = t.get('marketSlug', t.get('conditionId', 'Unknown'))
        side = t.get('side')
        size = float(t.get('size', 0))
        outcome = t.get('outcome', 'Unknown')
        
        if condition_id not in markets:
            markets[condition_id] = {'UP': 0, 'DOWN': 0}
        
        if side == 'BUY':
            if 'Up' in outcome:
                markets[condition_id]['UP'] += size
            elif 'Down' in outcome:
                markets[condition_id]['DOWN'] += size
        elif side == 'SELL':
            if 'Up' in outcome:
                markets[condition_id]['UP'] -= size
            elif 'Down' in outcome:
                markets[condition_id]['DOWN'] -= size
    
    for condition_id, positions in markets.items():
        up = positions['UP']
        down = positions['DOWN']
        
        if up <= 0 and down <= 0:
            continue
        
        print(f"Condition ID: {condition_id}")
        print(f"  UP 持仓: {up:.1f}")
        print(f"  DOWN 持仓: {down:.1f}")
        
        # 判断状态
        if up > 0 and down > 0:
            merge_amount = min(up, down)
            print(f"  ✅ 可合并: {merge_amount:.1f}")
        elif up > 0:
            print(f"  ⚠️ 单边 UP 持仓（需等待市场结束或卖出）")
        elif down > 0:
            print(f"  ⚠️ 单边 DOWN 持仓（需等待市场结束或卖出）")
        print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python redeem.py check              # 检查持仓")
        print("  python redeem.py merge <condition_id> <amount>  # 合并持仓")
        print("  python redeem.py redeem <condition_id> <winning_index> <amount>  # 赎回")
        print()
        print("示例:")
        print("  python redeem.py check")
        print("  python redeem.py merge 0x11d164fe... 5.0")
        print("  python redeem.py redeem 0x33a2e3bc... 1 5.0  # 1=UP, 2=DOWN")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "check":
        check_positions()
    elif command == "merge":
        if len(sys.argv) < 4:
            print("用法: python redeem.py merge <condition_id> <amount>")
            sys.exit(1)
        condition_id = sys.argv[2]
        amount = float(sys.argv[3])
        merge_positions(condition_id, amount)
    elif command == "redeem":
        if len(sys.argv) < 5:
            print("用法: python redeem.py redeem <condition_id> <winning_index> <amount>")
            print("winning_index: 1=UP, 2=DOWN")
            sys.exit(1)
        condition_id = sys.argv[2]
        winning_index = int(sys.argv[3])
        amount = float(sys.argv[4])
        redeem_positions(condition_id, winning_index, amount)
    else:
        print(f"未知命令: {command}")
