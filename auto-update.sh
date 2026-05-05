#!/bin/bash
# Weekly Tech Show — 自动更新脚本
# 用途：crontab 定时调用，抓取资讯并推送到 GitHub

set -e
cd "$(dirname "$0")"
LOG_FILE="./auto-update.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

log "========== 开始本周更新 =========="

# Step 1: 抓取 + 生成 HTML
if python3 main.py >> "$LOG_FILE" 2>&1; then
    log "抓取 & 生成完成"
else
    log "抓取出错，跳过提交"
    exit 1
fi

# Step 2: 提交变更
git add data/ docs/

if git diff --cached --quiet; then
    log "没有新内容，跳过提交"
    exit 0
fi

git commit -m "weekly update $(date +%Y-%m-%d)" >> "$LOG_FILE" 2>&1
log "提交完成"

# Step 3: 推送到 GitHub（最多重试 3 次）
for i in 1 2 3; do
    if git push github main >> "$LOG_FILE" 2>&1; then
        log "GitHub 推送成功"
        break
    fi
    log "推送失败，等待 60 秒后重试 ($i/3)..."
    sleep 60
done
