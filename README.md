# 学习通（超星）自动学习助手

一个基于 **Python + Playwright** 的浏览器自动化工具，帮你自动完成学习通（超星）课程里的视频、文档和测验任务点，支持断点续跑、验证码自动识别、AI 自动答题。

> ⚠️ 仅供个人学习自动化与编程研究使用。请遵守学校 / 机构关于在线课程的学习规范与诚信要求，因平台改版、反自动化策略或 AI 作答偏差导致的学习记录异常由使用者自行承担。

---

## ✨ 功能特性

### 视频与任务点
- **自动登录**：账号密码 / 二维码扫码，登录态 Cookie 持久化，下次免登录
- **自动识别目录与任务点**：自动扫描课程目录，区分视频 / 文档 / PPT / 测验等类型
- **自动播放与自动切换**：播放完成后自动切到下一个任务点，无需人工干预
- **多任务点小节逐块完成**：一节里的多个视频 / 文档 / 测验按块逐个处理，只认“当前块”的完成状态，**绝不重复播放已完成内容**
- **断点续跑**：中断后再次运行自动续播、跳过已完成部分

### 倍速与自适应
- **可调倍速**：默认 1.25x（可改 `player.speed`）
- **自适应倍速**：检测到播放无进度 / 弹验证码时自动降速（每次 0.25x，最低 `auto_speed_min`），连续稳定播放 90 秒后自动升回目标倍速——高倍速是卡顿与风控的常见诱因，自适应能显著提高稳定性

### 验证码
- **验证码自动识别**：长时间观看弹出的验证码，用本地离线 OCR（ddddocr）识别并自动填写、提交，失败自动刷新重试（**无需联网、不依赖打码平台**）

### 文档 / PPT
- **自动阅读文档**：文档 / PPT / PDF / 图片类任务点自动滚动到底，触发学习通“阅读完成”上报

### AI 自动答题（可选）
- 测验 / 作业题目自动提取「题型 + 题干 + 选项」，调用 **任意 OpenAI 兼容** 的大模型接口作答并提交
- 支持 DeepSeek、通义千问、OpenAI、本地 Ollama 等
- 作答结果按题目哈希缓存，重复题不重复消耗 token
- 识别不到答题界面的测验会**安全跳过**，不误标完成、不乱提交

### 稳定性
- **防重复播放**：按“任务点块”而非整节判定完成，杜绝误判
- **看门狗（可选）**：`tools/watchdog.py` 常驻监控，进程崩溃 / 视频卡死自动重启续播
- 去除 Edge 的 `--no-sandbox` 警告、修复打包后浏览器渲染进程崩溃问题

### 打包
- PyInstaller 单文件 Windows exe（无需装 Python），依赖系统 Edge

---

## 一、安装（源码方式）

要求 Python 3.10+（Windows）。

```bash
cd chaoxing-helper
pip install -r requirements.txt
# 使用系统 Edge（默认）无需额外下载浏览器；如需 Chromium：
# playwright install chromium
```

> 直接下载打包好的 exe 见 Releases（体积较大，未代码签名，SmartScreen 可能提示，选“仍要运行”即可）。

---

## 二、配置

1. 复制配置模板：
   ```bash
   cp config.example.json config.json
   ```
2. 编辑 `config.json`（以下为常用项）：

| 配置项 | 说明 |
|--------|------|
| `account.type` | `password`（账号密码）或 `qrcode`（扫码，推荐） |
| `account.username` / `password` | 账号密码登录时填写 |
| `courses.keywords` | 为空=处理所有课程；否则只处理名称含这些关键词的课程 |
| `courses.exclude_keywords` | 排除某些课程（如 `["英语"]`） |
| `player.speed` | 目标倍速，默认 `1.25` |
| `player.auto_speed` | 是否开启自适应倍速（默认 `true`） |
| `player.auto_speed_min` | 自适应最低倍速（默认 `1.0`） |
| `player.browser` | `edge`（默认，Win10/11 自带）或 `chromium` |
| `player.headless` | 是否无头运行，首次建议 `false` 便于观察 |
| `captcha.enabled` | 是否自动识别验证码（默认 `true`） |
| `quiz.enabled` / `quiz.auto_submit` | 是否开启自动答题 / 作答后自动提交 |
| `ai.enabled` | 是否启用 AI（不填 Key 时保持 `false`，测验会跳过） |
| `ai.base_url` | 大模型接口地址（OpenAI 兼容） |
| `ai.api_key` | 大模型 API Key |
| `ai.model` | 模型名，如 `deepseek-chat` / `qwen-plus` / `gpt-4o-mini` |

### AI 接口说明
`ai.base_url` 接受任意 **OpenAI 兼容** 的 Chat Completions 接口：
- DeepSeek：`https://api.deepseek.com/v1`，模型 `deepseek-chat`
- 阿里通义：`https://dashscope.aliyuncs.com/compatible-mode/v1`，模型 `qwen-plus`
- OpenAI：`https://api.openai.com/v1`
- 本地 Ollama：`http://127.0.0.1:11434/v1`

---

## 三、运行

```bash
python main.py
```

- 扫码登录：浏览器会弹出二维码，用学习通 App 扫码。
- 日志打印到控制台并写入 `data/run.log`。
- 已完成章节记录在 `data/progress.json`，中断后重跑自动续跑。

---

## 四、AI 答题机制

题目提取后，工具把「题型 + 题干 + 选项」发给大模型，要求只输出最终答案（单选=序号、多选=序号组合、判断=正确/错误、填空=文本），程序自动勾选 / 填写并提交。

- 模型推理能力越强正确率越高。
- 作答结果按题目哈希缓存于 `answers/ai_cache.json`。
- AI 未解析成功或接口异常时**不提交**，留待人工核对，避免乱答。

---

## 五、看门狗（可选，稳定性增强）

```bash
python tools/watchdog.py
```

- 每 60 秒检查一次进程与日志
- 进程崩溃 → 自动重启
- 视频到预计完成时间仍无完成信号 / 卡死 → 杀掉并重启续播（学习通服务端会记住播放位置）
- 日志写入 `data/watchdog.log`；放一个 `data/watchdog.stop` 空文件即可停止
- 安装目录自动探测：环境变量 `CX_HELPER_DIR` 优先，其次桌面 / `%LOCALAPPDATA%\Programs` 下的 `学习通自动学习助手` 文件夹

---

## 六、构建 exe

```bash
pip install pyinstaller
python -m PyInstaller --clean 学习通自动学习助手.spec
# 产物在 dist/学习通自动学习助手.exe
```

需提前 `pip install ddddocr onnxruntime Pillow`（已含在 requirements.txt）。

---

## 七、维护与更新（把本地改动同步到 GitHub）

日常改动后，在项目目录执行：

```bash
git status                       # 看看改了哪些文件
git add -A                       # 加入暂存（data/ config.json 等已被 .gitignore 自动排除）
git commit -m "说明这次改了什么"
git push                         # 推到 GitHub
```

如果是第一次推送已有仓库：

```bash
git remote add origin https://github.com/<你的用户名>/chaoxing-helper.git
git branch -M main
git push -u origin main
```

发布新版本 exe（不要直接放进 Git，137MB 超过 GitHub 单文件限制）：

```bash
python -m PyInstaller --clean 学习通自动学习助手.spec
git tag -a v1.0.0 -m "版本说明"
git push --tags
# 然后在 GitHub → Releases → Draft a new release，上传 dist/学习通自动学习助手.exe
```

多人协作或换机器时，先 `git pull` 再改，避免冲突。

> 安全相关请阅读 [SECURITY.md](SECURITY.md)：`data/`、`config.json`、`answers/` 绝不能提交。

---

## 八、免责声明

本工具仅供个人学习自动化之便利，请遵守所在学校 / 机构关于在线课程的学习规范与诚信要求。因平台改版、反自动化策略或 AI 作答偏差导致的学习记录异常、成绩问题，由使用者自行承担。请勿将 `data/` 目录（含登录 Cookie）、`config.json`（含账号与 API Key）上传到公开仓库。
