#!/usr/bin/env bash
# Bugfix 前置准备：确保基于最新的 master 且工作区干净，然后创建 bugfix/<issue>-<slug> 分支。
# 这是 SKILL.md 第 2/3 步的机械化封装（守卫校验 + 分支切换），供 agent 直接调用。
#
# 用法: .claude/skills/bugfix/scripts/prepare-bugfix-branch.sh <issue-number> [slug]
# 例:   .claude/skills/bugfix/scripts/prepare-bugfix-branch.sh 42 fix-null-pointer
#   -> 创建/切换到分支 bugfix/42-fix-null-pointer（基于 origin/master）
set -euo pipefail

ISSUE="${1:?用法: $0 <issue-number> [slug]}"
SLUG="${2:-fix}"
BRANCH="bugfix/${ISSUE}-${SLUG}"

# 1) 工作区必须干净：不干净就拒绝，绝不夹带无关改动进 bugfix 分支
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
