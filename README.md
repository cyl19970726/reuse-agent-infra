# reuse-agent-infra

可复用的 Agent 技能：处理未知与决策、核对会话证据、沉淀工作流，以及维护浏览器 AI 对话的上下文。

## 技能分类

| 分类 | 技能 | 用途 |
| --- | --- | --- |
| 决策方法 `decision-making` | [unknowns-to-gates](skills/decision-making/unknowns-to-gates/SKILL.md) | 将未知转成决策、探索步骤与可执行的防错检查。 |
| 会话复盘 `session-analysis` | [session-forensics-v4-2](skills/session-analysis/session-forensics-v4-2/SKILL.md) | 最新会话观察版本：建立任务地图，回答进度、接手、经验提炼、审计与流程改进问题，生成 HTML 报告。 |
| 会话复盘 `session-analysis` | [session-audit-v4-2](skills/session-analysis/session-audit-v4-2/SKILL.md) | 在 v4.2 任务地图上核对执行问题、原因与本该停止的位置。 |
| 会话复盘 `session-analysis` | [session-handoff-v4-2](skills/session-analysis/session-handoff-v4-2/SKILL.md) | 在 v4.2 任务地图上核对现场状态，交接未完成工作并继续执行。 |
| 会话复盘 `session-analysis` | [session-forensics](skills/session-analysis/session-forensics/SKILL.md) | 保留旧版入口与脚本，供已有安装及 session-to-workflow 继续使用。 |
| 会话复盘 `session-analysis` | [session-to-workflow](skills/session-analysis/session-to-workflow/SKILL.md) | 从历史会话、产物和反馈重建工作流，沉淀技能与工作台。 |
| 浏览器对话 `browser-chat` | [x-grok-chat](skills/browser-chat/x-grok-chat/SKILL.md) | 在 X 中与 Grok 多轮聊天，获取完整回答与引用来源。 |
| 浏览器对话 `browser-chat` | [xhs-ai-chat](skills/browser-chat/xhs-ai-chat/SKILL.md) | 与小红书点点多轮聊天，提取回复并保留会话上下文。 |

## 使用

每个技能目录包含 `SKILL.md`，以及所需的 `references/`、`scripts/`、`agents/` 等资源。分类目录用于仓库组织；安装时复制完整的单个技能目录。

默认按项目安装。例如在目标项目根目录执行（替换仓库路径）：

```sh
mkdir -p .agents/skills
cp -R /path/to/reuse-agent-infra/skills/decision-making/unknowns-to-gates .agents/skills/
```

不要只复制 `SKILL.md`。安装 `session-to-workflow` 时也应安装 `session-forensics`，两者放在同级目录。全局安装需要用户对具体技能与全局范围的明确授权。

新使用会话审计时，安装 v4.2 三个完整包，保留目录名和同级关系：

```sh
mkdir -p .agents/skills
for skill in session-forensics-v4-2 session-audit-v4-2 session-handoff-v4-2; do
  cp -R "/path/to/reuse-agent-infra/skills/session-analysis/$skill" .agents/skills/
done
```

v4.2 将任务地图与答案呈现放在底座中，审计和接手分别使用配套 Skill。它与旧版并列发布，不会自动替换用户的全局安装。

## 依赖与边界

- 脚本使用 Python 3.10+；部分脚本或辅助命令面向 macOS/Linux。
- `session-forensics` 需要用户授权访问的原始会话日志；读取 DeepSeek Harness 压缩日志及运行其自测需要 `zstd`。本地校准数据由使用者自行生成。
- `session-to-workflow` 依赖本仓库的 `session-forensics`。
- `session-audit-v4-2` 与 `session-handoff-v4-2` 依赖同级 `session-forensics-v4-2`；v4.2 使用 `ledger.py`、`inventory.py`、`map_check.py` 和 `render.py` 生成与检查任务地图。
- 两个浏览器对话技能依赖另行提供的 `ego-browser` 技能及其运行环境，并需要对应网站的已登录会话。本仓库不包含该依赖，也不自动安装它。
- 对话注册表属于本地私有状态；默认位于用户的 `~/.codex/state/`，也可通过脚本 `--registry` 指定路径。
- `session-forensics/scripts/sync_skill.sh` 是可选维护工具，依赖 macOS `md5`、`rsync` 和 Git；必须显式指定源、目标与仓库，普通使用无需运行。

## 发布说明

最初五个技能从现有 Codex 全局版本整理而来。保留原有方法、脚本及参考资料；仓库副本调整了本机路径和跨技能链接，匿名化了案例中的会话标识和项目名称，并排除了缓存、本地校准数据与对话状态。参考资料中的历史案例与日期描述其观察背景，不代表当前产品行为保证；其中提及的 `local/` 笔记未随仓库发布。

新增的 v4.2 三个包来自已安装、内容一致的版本化技能，保留版本名及原始草案标记。随包提供的书架项目、两份 `examples/synthetic/*.jsonl` 和示例报告均为虚构测试材料，不包含真实私人会话。`answer.type = distill` 指从会话中提炼项目理解和经验，输出为报告中的 `insights`。

v4.2 发布检查（不调用模型、不读取真实会话；DeepSeek 压缩格式自测需要 `zstd`）：

```sh
python3 -B skills/session-analysis/session-forensics-v4-2/scripts/selfcheck.py --siblings --privacy
python3 -B skills/session-analysis/session-forensics-v4-2/scripts/selftest_dsh.py
```
