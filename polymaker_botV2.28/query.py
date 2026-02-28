#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 账户查询脚本
"""

import sys
sys.path.insert(0, '.')

import logging
logging.disable(logging.CRITICAL)

from client.polymarket_client import get_client
from web3 import Web3

SAFE_ADDRESS = '0x05076013fd6f657b0488aefe64dcefd458047c08'
USDC_ADDRESS = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'

def main():
    print('========================================')
    print('       账户余额详情')
    print('========================================')
    print('')
    print(f'Safe 钱包地址: {SAFE_ADDRESS}')
    print('')
    
    # 链上余额
    print('链上资产:')
    try:
        w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
        erc20_abi = '[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]'
        usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_ADDRESS), abi=erc20_abi)
        balance = usdc.functions.balanceOf(Web3.to_checksum_address(SAFE_ADDRESS)).call()
        print(f'  USDC.e: {balance / 1e6:.2f}')
    except Exception as e:
        print(f'  获取失败: {e}')
    
    print('')
    
    # 交易统计
    print('交易统计:')
    try:
        client = get_client()
        trades = client.get_builder_trades() or []
        
        # 按市场分组
        markets = {}
        for t in trades:
            condition_id = t.get('market', 'Unknown')
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
        
        # 显示持仓
        print('当前持仓:')
        has_position = False
        for condition_id, pos in markets.items():
            up = pos['UP']
            down = pos['DOWN']
            if up > 0 or down > 0:
                has_position = True
                print(f'  {condition_id[:30]}...')
                print(f'    UP: {up:.1f}, DOWN: {down:.1f}')
                if up > 0 and down > 0:
                    print(f'    可合并: {min(up, down):.1f}')
        
        if not has_position:
            print('  无持仓')
        
        print('')
        
        total_buy = sum(float(t.get('sizeUsdc', 0)) for t in trades if t.get('side') == 'BUY')
        total_sell = sum(float(t.get('sizeUsdc', 0)) for t in trades if t.get('side') == 'SELL')
        
        print(f'总交易数: {len(trades)}')
        print(f'总买入: {total_buy:.2f} USDC')
        print(f'总卖出: {total_sell:.2f} USDC')
        print(f'已实现盈亏: {total_sell - total_buy:.2f} USDC')
        
    except Exception as e:
        print(f'获取失败: {e}')
    
    print('')
    print('========================================')

if __name__ == '__main__':
    main()
