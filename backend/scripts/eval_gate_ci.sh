#!/bin/bash
# eval_gate_ci.sh — Stage 2 WI-S2.3 / PRD D10 v2
#
# CI 看 git diff 自动判断是否需要 strict 模式。召回相关 .py 改动 →
# `--strict` 自动加上，不靠人工 PR template 提醒。
#
# 用法（CI）::
#     BASE_REF=origin/master bash backend/scripts/eval_gate_ci.sh
#
# 触发 strict 的模式（PRD D10）：
#     enhanced_retriever.py / *_retriever.py / *_extractor.py /
#     facts.py / retriever.py / reranker.py / query_rewriter.py /
#     chunker.py / memory_v2_schema.py
set -euo pipefail

# 切到 backend/ 让 `python -m scripts.eval_gate` 能找到包
cd "$(dirname "$0")/.."

# 默认与 main 分支对比，CI 注入 BASE_REF=origin/${{ github.base_ref }}
BASE_REF="${BASE_REF:-origin/master}"

# 拿改动文件名（fallback: HEAD~1..HEAD，再 fallback: 空）
CHANGED=$(
    git diff --name-only "$BASE_REF...HEAD" 2>/dev/null \
        || git diff --name-only HEAD~1 HEAD 2>/dev/null \
        || echo ""
)

STRICT_FLAG=""
RECALL_PATTERN='(enhanced_retriever|.*_retriever|.*_extractor|facts|retriever|reranker|query_rewriter|chunker|memory_v2_schema)\.py$'

if echo "$CHANGED" | grep -qE "$RECALL_PATTERN"; then
    echo "[eval_gate_ci] 召回相关改动检测 → --strict"
    echo "[eval_gate_ci] changed:"
    echo "$CHANGED" | grep -E "$RECALL_PATTERN"
    STRICT_FLAG="--strict"
else
    echo "[eval_gate_ci] 未检测到召回相关改动 → 默认 gate"
fi

# shellcheck disable=SC2086  # STRICT_FLAG 故意不引号，空时不传 arg
python -m scripts.eval_gate $STRICT_FLAG
