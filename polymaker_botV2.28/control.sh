#!/bin/bash
# Polymaker 量化机器人控制脚本
# 用法: ./control.sh {start|stop|restart|status|log|balance|approve|merge}

set -e

# 配置
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/polymaker.pid"
LOG_FILE="$SCRIPT_DIR/bot_output.log"
PYTHON="/opt/miniconda/bin/python"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查进程是否运行
is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            return 0
        fi
    fi
    return 1
}

# 获取进程ID
get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    else
        echo "N/A"
    fi
}

# 获取进程信息
get_process_info() {
    if is_running; then
        local pid=$(cat "$PID_FILE")
        local info=$(ps -p "$pid" -o pid,vsz,rss,%cpu,%mem,etime --no-headers 2>/dev/null)
        echo "$info"
    else
        echo "N/A"
    fi
}

# 启动机器人
start() {
    if is_running; then
        print_warning "机器人已在运行中 (PID: $(get_pid))"
        return 1
    fi
    
    print_info "正在启动机器人..."
    cd "$SCRIPT_DIR"
    nohup "$PYTHON" main.py > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 3
    
    if is_running; then
        print_success "机器人启动成功 (PID: $(get_pid))"
        echo ""
        tail -20 "$LOG_FILE"
    else
        print_error "机器人启动失败，请检查日志:"
        tail -30 "$LOG_FILE"
        return 1
    fi
}

# 停止机器人
stop() {
    if ! is_running; then
        print_warning "机器人未运行"
        return 0
    fi
    
    local pid=$(cat "$PID_FILE")
    print_info "正在停止机器人 (PID: $pid)..."
    
    kill "$pid" 2>/dev/null || true
    
    # 等待进程结束
    local count=0
    while ps -p "$pid" > /dev/null 2>&1 && [ $count -lt 10 ]; do
        sleep 1
        count=$((count + 1))
    done
    
    if ps -p "$pid" > /dev/null 2>&1; then
        print_warning "进程未响应，强制终止..."
        kill -9 "$pid" 2>/dev/null || true
    fi
    
    rm -f "$PID_FILE"
    print_success "机器人已停止"
}

# 重启机器人
restart() {
    print_info "正在重启机器人..."
    stop
    sleep 2
    start
}

# 查看状态
status() {
    echo "========================================"
    echo "       Polymaker 量化机器人状态"
    echo "========================================"
    echo ""
    
    # 进程状态
    if is_running; then
        local pid=$(cat "$PID_FILE")
        print_success "状态: 运行中"
        echo ""
        echo "进程信息:"
        echo "  PID: $pid"
        ps -p "$pid" -o pid,vsz,rss,%cpu,%mem,etime,cmd --no-headers 2>/dev/null | awk '{
            printf "  内存: %.1f MB (RSS)\n", $3/1024
            printf "  CPU: %s%%\n", $4
            printf "  运行时间: %s\n", $6
        }'
        echo ""
        
        # 交易统计
        echo "交易统计:"
        "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
import logging
logging.disable(logging.CRITICAL)  # 禁用所有日志
from client.polymarket_client import get_client
try:
    client = get_client()
    trades = client.get_builder_trades() or []
    total_buy = sum(float(t.get('sizeUsdc', 0)) for t in trades if t.get('side') == 'BUY')
    total_sell = sum(float(t.get('sizeUsdc', 0)) for t in trades if t.get('side') == 'SELL')
    print(f'  总交易数: {len(trades)}')
    print(f'  总买入: {total_buy:.2f} USDC')
    print(f'  总卖出: {total_sell:.2f} USDC')
    print(f'  已实现盈亏: {total_sell - total_buy:.2f} USDC')
except Exception as e:
    print(f'  获取统计失败: {e}')
" 2>/dev/null || echo "  无法获取交易统计"
        
        # 链上余额
        echo ""
        echo "链上余额:"
        "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from web3 import Web3
try:
    w3 = Web3(Web3.HTTPProvider('https://polygon-bor-rpc.publicnode.com'))
    safe_address = '0x05076013fd6f657b0488aefe64dcefd458047c08'
    usdc_address = '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174'
    erc20_abi = '[{\"inputs\":[{\"name\":\"account\",\"type\":\"address\"}],\"name\":\"balanceOf\",\"outputs\":[{\"name\":\"\",\"type\":\"uint256\"}],\"stateMutability\":\"view\",\"type\":\"function\"}]'
    usdc = w3.eth.contract(address=Web3.to_checksum_address(usdc_address), abi=erc20_abi)
    balance = usdc.functions.balanceOf(Web3.to_checksum_address(safe_address)).call()
    print(f'  USDC.e: {balance / 1e6:.2f}')
except Exception as e:
    print(f'  获取余额失败: {e}')
" 2>/dev/null || echo "  无法获取链上余额"
        
    else
        print_warning "状态: 未运行"
        if [ -f "$PID_FILE" ]; then
            print_warning "发现残留 PID 文件，已清理"
            rm -f "$PID_FILE"
        fi
    fi
    
    echo ""
    echo "========================================"
}

# 查看日志
log() {
    local lines=${1:-50}
    print_info "最近 $lines 行日志:"
    echo ""
    tail -n "$lines" "$LOG_FILE"
}

# 实时日志
log_follow() {
    print_info "实时日志 (Ctrl+C 退出):"
    tail -f "$LOG_FILE"
}

# 查询余额
balance() {
    "$PYTHON" "$SCRIPT_DIR/query.py"
}

# 授权 CTF 代币
approve() {
    echo "========================================"
    echo "       CTF 代币授权"
    echo "========================================"
    echo ""
    
    "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from client.polymarket_client import get_client

client = get_client()

# 检查当前状态
print('检查授权状态...')
is_approved = client.check_ctf_approval()

if is_approved:
    print('CTF 代币已授权给 Exchange，无需重复授权')
else:
    print('CTF 代币未授权，正在授权...')
    result = client.approve_ctf_token('')
    if result:
        print('授权成功!')
    else:
        print('授权失败，请查看日志')
" 2>/dev/null
    
    echo ""
    echo "========================================"
}

# 合并持仓
merge() {
    echo "========================================"
    echo "       合并持仓"
    echo "========================================"
    echo ""
    
    "$PYTHON" -c "
import sys
sys.path.insert(0, '.')
from client.polymarket_client import get_client

client = get_client()
trades = client.get_builder_trades() or []

# 分析持仓
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

# 显示可合并持仓
print('可合并持仓:')
has_merge = False
for condition_id, pos in markets.items():
    up = pos['UP']
    down = pos['DOWN']
    if up > 0 and down > 0:
        has_merge = True
        merge_amount = min(up, down)
        print(f'  {condition_id[:30]}...')
        print(f'    UP: {up:.1f}, DOWN: {down:.1f} -> 可合并: {merge_amount:.1f}')

if not has_merge:
    print('  无可合并持仓')
    sys.exit(0)

print('')
answer = input('是否执行合并? (y/n): ')
if answer.lower() != 'y':
    print('已取消')
    sys.exit(0)

# 执行合并
print('')
print('正在合并...')
# 这里可以调用合并逻辑
print('请使用 Telegram Bot 的「一键平仓」功能执行合并')
" 2>/dev/null
    
    echo ""
    echo "========================================"
}

# 显示帮助
show_help() {
    echo "Polymaker 量化机器人控制脚本"
    echo ""
    echo "用法: $0 {命令} [参数]"
    echo ""
    echo "命令:"
    echo "  start       启动机器人"
    echo "  stop        停止机器人"
    echo "  restart     重启机器人"
    echo "  status      查看运行状态"
    echo "  log [N]     查看最近 N 行日志 (默认 50)"
    echo "  logf        实时查看日志"
    echo "  balance     查询账户余额"
    echo "  approve     授权 CTF 代币 (卖出前必须)"
    echo "  merge       合并持仓"
    echo "  help        显示帮助"
    echo ""
    echo "示例:"
    echo "  $0 start            # 启动机器人"
    echo "  $0 status           # 查看状态"
    echo "  $0 log 100          # 查看最近 100 行日志"
    echo "  $0 logf             # 实时查看日志"
    echo "  $0 balance          # 查询余额"
}

# 主入口
case "${1:-help}" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    log)
        log "${2:-50}"
        ;;
    logf)
        log_follow
        ;;
    balance|bal)
        balance
        ;;
    approve)
        approve
        ;;
    merge)
        merge
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        print_error "未知命令: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
