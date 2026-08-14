#!/usr/bin/env bash
# Feature 前置准备：确保基于最新的 master 且工作区干净，然后创建 feature/<slug> 分支。
# 这是 SKILL.md 第 1~3 步的机械化封装（守卫校验 + 分支切换），供 agent 直接调用。
#
# 用法: .claude/skills/feature-create/scripts/prepare-feature-branch.sh <slug>
# 例:   .claude/skills/feature-create/scripts/prepare-feature-branch.sh daily-task-time
#   -> 创建/切换到分支 feature/daily-task-time（基于 origin/master）
set -euo pipefail

SLUG="${1:?用法: $0 <slug>}"
BRANCH="feature/${SLUG}"

# 1) 工作区必须干净：不干净就拒绝，绝不夹带无关改动进 feature 分支
if [ -n "$(git status --porcelain)" ]; then
  echo "ERROR: 工作区不干净，请先处理无关改动（git stash push / commit）后再跑本脚本：" >&2
  git status --porcelain
  exit 1
fi

# 2) 基于最新的 master
git fetch origin
git checkout master
git pull origin master

# 3) 创建或复用分支
if git show-ref --verify --quiet "refs/heads/${BRANCH}"; then
  echo "分支已存在，切换过去: ${BRANCH}"
  git switch "${BRANCH}"
else
  git switch -c "${BRANCH}"
fi

echo "分支就绪: ${BRANCH}（基于 origin/master，工作区干净）"
