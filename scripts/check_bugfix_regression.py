#!/usr/bin/env python3
"""BF 归档 ↔ 回归测试一致性检查（docs/regression-ci.md §1 步骤②）。

规则：
- docs/bugfix/README.md 索引表中的每个 BF-XXX，必须能在测试源码（docstring/注释）
  中找到引用 —— 即"重要 Bug 修复必须附带回归测试"。引用来源含后端 pytest
  （backend/tests/**.py）与前端 vitest（frontend/tests/**.ts(x)）两类：纯前端
  缺陷的回归 seam 在前端状态机，不应强行在后端伪造弱 seam；
- 反向：测试里引用的 BF 编号必须已在索引表登记（先登记再引用，含已退役条目）；
- 状态含「已退役」的条目免于双向校验（退役规则见 docs/regression-ci.md §6.1）：
  无测试引用不红灯、残留引用不算孤儿——退役白名单与档案「退役记录」由 review 把关；
- 任一方向缺失即退出码 1（CI 红灯），并打印完整映射表充当回归清单。

标准库实现，零依赖；路径以脚本自身位置定位仓库根，与执行 cwd 无关。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INDEX_FILE = REPO_ROOT / "docs" / "bugfix" / "README.md"
# 回归测试引用来源：后端 pytest 与前端 vitest（纯前端缺陷的 seam 在前端）
TEST_GLOBS = (
    (REPO_ROOT / "backend" / "tests", ("*.py",)),
    (REPO_ROOT / "frontend" / "tests", ("*.ts", "*.tsx")),
)

# 索引表行形如 "| [BF-001](2026-09-30-xxx.md) | ... |"，只认表格里的链接形式，
# 避免把正文/模板中顺带提到的编号误当索引项
BF_LINK_RE = re.compile(r"\[(BF-\d{3})\]\(")
BF_REF_RE = re.compile(r"BF-\d{3}")
# 索引表行内出现该字样即视为已退役条目，免于双向校验（规则见 docs/regression-ci.md §6.1）
RETIRED_MARK = "已退役"


def indexed_bug_rows() -> list[tuple[str, bool]]:
    """解析索引表，返回 (BF 编号, 是否已退役)。"""
    if not INDEX_FILE.is_file():
        print(f"[FAIL] 索引文件不存在：{INDEX_FILE}")
        return []
    rows: list[tuple[str, bool]] = []
    for line in INDEX_FILE.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("|"):
            retired = RETIRED_MARK in line
            rows.extend((bf, retired) for bf in BF_LINK_RE.findall(line))
    return sorted(set(rows))


def test_references() -> dict[str, list[Path]]:
    refs: dict[str, list[Path]] = {}
    for tests_dir, patterns in TEST_GLOBS:
        if not tests_dir.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(tests_dir.rglob(pattern)):
                for bf in sorted(set(BF_REF_RE.findall(path.read_text(encoding="utf-8")))):
                    refs.setdefault(bf, []).append(path)
    return refs


def main() -> int:
    rows = indexed_bug_rows()
    if not rows:
        print(f"[FAIL] 索引表为空或无法解析：{INDEX_FILE}")
        return 1

    indexed = [bf for bf, _ in rows]
    retired = {bf for bf, is_retired in rows if is_retired}
    refs = test_references()
    missing = [bf for bf in indexed if bf not in refs and bf not in retired]
    orphans = sorted(set(refs) - set(indexed))

    print(f"BF 归档 ↔ 回归测试映射（索引 {len(indexed)} 项，已退役 {len(retired)} 项）：")
    for bf, is_retired in rows:
        files = refs.get(bf, [])
        rel = ", ".join(str(f.relative_to(REPO_ROOT)) for f in files) or "—"
        note = "　（已退役，免检）" if is_retired else ""
        print(f"  {bf}: {rel}{note}")

    ok = True
    if missing:
        ok = False
        print("\n[FAIL] 以下现役 BF 没有任何回归测试引用（规则见 docs/regression-ci.md §6）：")
        for bf in missing:
            print(f"  - {bf}")
        print("补法：测试注明编号（后端 @pytest.mark.regression + docstring 写 BF-XXX；")
        print("前端用例名/注释含 BF-XXX），归档「影响范围与注意事项」登记测试节点 ID。")
    if orphans:
        ok = False
        print("\n[FAIL] 测试引用了未登记的 BF 编号（先在 docs/bugfix/README.md 索引表登记）：")
        for bf in orphans:
            print(f"  - {bf}")

    if ok:
        print("\nOK：BF 归档与回归测试双向一致。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
