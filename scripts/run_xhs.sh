#!/bin/bash
# XHS Pipeline Quick Start
# 用法:
#   bash scripts/run_xhs.sh generate "自定义灵感"           # 单篇生成
#   bash scripts/run_xhs.sh batch                           # 批量生成全部18篇
#   bash scripts/run_xhs.sh schedule 3                      # 定时生成3篇
#   bash scripts/run_xhs.sh status                          # 查看排期

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python3}"

cd "$PROJECT_DIR"

case "${1:-help}" in
    generate)
        shift
        "${PYTHON}" tools/xhs_pipeline.py generate --inspiration "$*" --schedule
        ;;
    batch)
        "${PYTHON}" tools/xhs_pipeline.py batch --config configs/xhs_batch.json --schedule
        ;;
    schedule)
        COUNT="${2:-3}"
        "${PYTHON}" tools/xhs_pipeline.py schedule --count "$COUNT"
        ;;
    status)
        "${PYTHON}" tools/xhs_pipeline.py status
        ;;
    test)
        # Quick test: generate 1 sweet story
        "${PYTHON}" tools/xhs_pipeline.py generate \
            --template 甜宠_高糖轻虐 \
            --inspiration "闪婚后发现老公是帝国继承人" \
            --schedule
        ;;
    *)
        echo "Usage: $0 {generate|batch|schedule|status|test}"
        echo ""
        echo "  generate <inspiration>  - 单篇生成"
        echo "  batch                   - 批量生成全部18篇"
        echo "  schedule <count>        - 定时生成N篇"
        echo "  status                  - 查看排期"
        echo "  test                    - 快速测试"
        ;;
esac
