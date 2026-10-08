// 提交信息门禁规则（对齐 CLAUDE.md「代码规范 → Conventional Commits」）。
// 规则内联而非 extends @commitlint/config-conventional：本文件在仓库根，
// 不在 frontend/node_modules 的解析链上，内联可少装一个预设包且白名单显式可见。
// 触发点：.husky/commit-msg → frontend/node_modules/.bin/commitlint --config 本文件。
export default {
  rules: {
    'header-max-length': [2, 'always', 100],
    'type-empty': [2, 'never'],
    'type-case': [2, 'always', 'lower-case'],
    'scope-case': [2, 'always', 'lower-case'],
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'docs', 'style', 'refactor', 'perf', 'test', 'chore', 'ci', 'build', 'revert'],
    ],
    'subject-empty': [2, 'never'],
    'subject-full-stop': [2, 'never', ['.', '。']],
  },
}
