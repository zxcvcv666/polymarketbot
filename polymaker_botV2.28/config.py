#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Polymaker 配置模块
使用 Pydantic Settings 管理配置，支持 .env 文件
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """全局配置管理"""
    
    # ============================================
    # Polymarket API 配置
    # ============================================
    PRIVATE_KEY: str = Field(default="", description="Polymarket 钱包私钥")
    ADDRESS: str = Field(default="", description="Polymarket 平台交易地址")
    FUNDER_ADDRESS: str = Field(default="", description="Funder 地址（用于 gasless 交易）")
    
    # ============================================
    # Builder Program 配置（Gasless 交易）
    # ============================================
    POLY_BUILDER_API_KEY: str = Field(default="", description="Builder API Key")
    POLY_BUILDER_SECRET: str = Field(default="", description="Builder Secret")
    POLY_BUILDER_PASSPHRASE: str = Field(default="", description="Builder Passphrase")
    
    # 签名类型：1 = POLY_PROXY, 2 = GNOSIS_SAFE
    SIGNATURE_TYPE: int = Field(default=1, description="签名类型")
    
    # ============================================
    # Telegram 配置
    # ============================================
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Telegram Bot Token")
    TELEGRAM_CHAT_ID: str = Field(default="", description="Telegram 用户 ID")
    
    # ============================================
    # 交易参数配置
    # ============================================
    TRADE_SIZE: int = Field(default=6, description="每次交易的份额数量")
    MAX_EXPOSURE_USD: float = Field(default=100.0, description="最大持仓价值上限（USDC）")
    MIN_SETTLE_SECONDS: int = Field(default=120, description="最小距离结算时间（秒）")
    MAX_LOSS_RATIO: float = Field(default=0.10, description="最大未实现亏损比例")
    
    # ============================================
    # 套利参数配置
    # ============================================
    MIN_DEPTH_USD: float = Field(default=300.0, description="最小深度要求（USDC）")
    MAX_BID_SUM: float = Field(default=0.99, description="最大双边 bid 价格和")
    SAFE_PRICE_SUM: float = Field(default=0.98, description="安全价格和上限")
    ORDER_EXPIRATION_SECONDS: int = Field(default=15, description="订单有效期（秒）")
    ORDER_POLL_INTERVAL: int = Field(default=1, description="订单轮询间隔（秒）")
    
    # ============================================
    # 风控参数配置
    # ============================================
    MAX_SLIPPAGE_RATIO: float = Field(default=0.05, description="最大滑点容忍度")
    API_RETRY_COUNT: int = Field(default=3, description="API 重试次数")
    API_RETRY_BASE_DELAY: int = Field(default=1, description="API 重试间隔基数（秒）")
    
    # ============================================
    # 系统配置
    # ============================================
    LOG_FILE: str = Field(default="trades.log", description="日志文件路径")
    LOG_LEVEL: str = Field(default="INFO", description="日志级别")
    DATABASE_PATH: str = Field(default="polymaker.db", description="SQLite 数据库路径")
    REPORTS_DIR: str = Field(default="reports", description="报表目录")
    
    # ============================================
    # 市场配置
    # ============================================
    # 支持的市场类型
    MARKET_TYPES: list = Field(
        default=["btc-5m", "btc-15m"],
        description="支持的市场类型列表"
    )
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
    
    def validate_required(self) -> tuple[bool, list[str]]:
        """验证必要配置"""
        required = [
            ('PRIVATE_KEY', self.PRIVATE_KEY),
            ('ADDRESS', self.ADDRESS),
            ('FUNDER_ADDRESS', self.FUNDER_ADDRESS),
            ('POLY_BUILDER_API_KEY', self.POLY_BUILDER_API_KEY),
            ('POLY_BUILDER_SECRET', self.POLY_BUILDER_SECRET),
            ('POLY_BUILDER_PASSPHRASE', self.POLY_BUILDER_PASSPHRASE),
            ('TELEGRAM_BOT_TOKEN', self.TELEGRAM_BOT_TOKEN),
            ('TELEGRAM_CHAT_ID', self.TELEGRAM_CHAT_ID)
        ]
        
        missing = [name for name, value in required if not value]
        return len(missing) == 0, missing


# 全局配置实例
settings = Settings()
