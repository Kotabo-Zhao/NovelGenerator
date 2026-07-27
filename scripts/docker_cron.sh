#!/bin/bash
# Docker Cron: 每天自动生成 N 篇小说并排期
# 配合 crontab: 0 8 * * * /scripts/docker_cron.sh >> /var/log/xhs_cron.log 2>&1

set -e
COUNT="${1:-3}"
DELAY="${2:-15}"

echo "[$(date)] XHS Pipeline Daily Run — generating $COUNT stories"

cd /app
python tools/xhs_pipeline.py schedule --count "$COUNT" --delay "$DELAY"

echo "[$(date)] Done. Output: $(ls /app/output/xhs_queue/ready/ | wc -l) files ready"
