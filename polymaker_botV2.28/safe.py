#!/usr/bin/env python3
from py_builder_relayer_client.client import RelayClient
from py_builder_signing_sdk.config import BuilderConfig
from py_builder_signing_sdk.sdk_types import BuilderApiKeyCreds
from eth_account import Account
from dotenv import load_dotenv
import os

load_dotenv()

# 加载 Builder 凭证
builder_creds = BuilderApiKeyCreds(
    key=os.getenv("POLY_BUILDER_API_KEY"),
    secret=os.getenv("POLY_BUILDER_SECRET"),
    passphrase=os.getenv("POLY_BUILDER_PASSPHRASE")
)
builder_config = BuilderConfig(local_builder_creds=builder_creds)

# 加载私钥
private_key = os.getenv("PRIVATE_KEY")

# 初始化 RelayClient
relay_client = RelayClient(
    relayer_url="https://relayer-v2.polymarket.com/",
    chain_id=137,
    private_key=private_key,
    builder_config=builder_config
)

# 部署 Safe 钱包
print("正在部署 Safe 钱包...")
response = relay_client.deploy()
result = response.wait()

print(f"Safe 地址: {result.get('proxyAddress')}")
print(f"交易哈希: {result.get('transactionHash')}")