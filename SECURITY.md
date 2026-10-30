# 安全说明

## 一、绝不要上传的内容

| 路径 | 风险 |
|---|---|
| `data/` | 含 `cookies.json`（登录态，泄露=他人可操作你的学习通账号）、`progress.json`、`run.log`、学习通页面快照 |
| `config.json` | 含账号/密码、AI `api_key` |
| `answers/ai_cache.json` | AI 作答记录，可能含题目与你的答案 |
| `build/` `dist/` | 构建产物（exe 仅 137MB，且超过 GitHub 单文件 100MB 限制，应放 Releases） |

仓库已内置 `.gitignore` 自动排除以上目录。**clone 后第一次运行会自动生成这些目录**，无需手动创建。

## 二、已内置的保护

- `.gitignore` 排除 `data/`、`answers/`、`config.json`、`build/`、`dist/`、`__pycache__/`
- 随仓库提供 `config.example.json`，敏感字段全部留空，需自行复制为 `config.json` 后填写
- 源码中不硬编码任何密钥；看门狗 `tools/watchdog.py` 的安装目录通过环境变量
  `CX_HELPER_DIR` 或常见路径自动探测，不写死用户名

## 三、误提交了敏感信息怎么办

`.gitignore` 只能防止**未来**的文件被提交，已经进入 Git 历史的文件仍会留在仓库里。

1. 立刻去对应平台**吊销/重置**泄露的密钥（AI API Key、账号密码）
2. 从历史中清除：
   ```bash
   git filter-repo --path data/ --path config.json --invert-paths
   git push --force
   ```
   （需先安装 `git-filter-repo`）

## 四、使用建议

- 不要在运行本工具的机器上把 `data/`、`config.json` 同步到任何公开网盘或仓库
- 定期清理 `data/edge_profile`（浏览器配置，含登录痕迹）与 `data/cookies.json`
- AI API Key 建议使用额度受限的子账号/专用 Key
- 尽量使用扫码登录，避免在配置文件中长期保存明文密码

## 五、合规提醒

本工具仅供个人学习自动化与编程研究使用。请遵守所在学校/机构关于在线课程的学习规范与诚信要求。
如发现安全漏洞或隐私泄露问题，请在仓库提交 Issue 并隐去个人信息。
