# X Grok 会话状态

## 身份层级

按以下优先级识别一次会话：

1. `conversation` 查询参数中的数字 ID 是 X Grok 会话的持久身份。
2. 完整的 `https://x.com/i/grok?conversation=<ID>` URL 是持久恢复地址。
3. `task_space_id` 和 tab ID 只是可丢弃的浏览器执行缓存。

不要把 Ego 任务空间当作会话状态的唯一副本。即使浏览器空间被关闭，也应能从已登记的精确 URL 恢复。

## 本地登记表

使用相对于本 Skill 的 `scripts/conversation_registry.py`，默认保存到：

```text
~/.codex/state/x-grok-chat/conversations.json
```

登记表只保存路由元数据：逻辑键、显示名称、`conversation_id`、完整 URL、最近一次任务空间 ID、状态与时间戳。禁止保存 Cookie、凭据、提问正文或回复正文。目录权限为 `0700`，登记表和锁文件权限为 `0600`；写入有文件锁且采用原子替换。

常用命令：

```bash
python3 scripts/conversation_registry.py current
python3 scripts/conversation_registry.py list
python3 scripts/conversation_registry.py get --key KEY
python3 scripts/conversation_registry.py upsert --key KEY --label LABEL \
  --conversation-id ID --task-space-id TASK_ID --make-current
python3 scripts/conversation_registry.py set-current --key KEY
python3 scripts/conversation_registry.py close --key KEY
```

## 恢复已有会话

1. 先从登记表解析目标会话。“继续刚才的”使用 `current`。若存在多个候选且没有当前项，列出名称和链接让用户选择。
2. 查询 Ego 任务空间。仅当存活 tab 的 URL 含有相同 `conversation=<ID>` 时，才复用登记的任务空间。
3. 若任务空间已消失，新建任务空间并打开登记的完整 URL。这是重建浏览器执行环境，不是新建 Grok 会话。
4. 验证最终 URL 仍包含预期 ID、页面中存在历史消息和真实输入框。若跳回裸 `/i/grok`，恢复失败；不要把后续提问发进空白会话。
5. 完成新一轮问答后，更新完整 URL 和最新任务空间 ID，并将该逻辑会话设为当前项。

用户要求继续时，绝不静默打开空白新会话。若因会话删除、登录失效、订阅限制或无权限而恢复失败，应说明原因并保留登记项供诊断。

## 新建会话

仅当用户明确要求新话题，或上下文中不存在可恢复的旧会话时，才打开裸 `/i/grok`。首轮成功后，从实时 URL 读取新的数字 ID，立即用清晰稳定的逻辑键登记，并设为当前项。

History 弹窗中的会话链接形如 `https://x.com/i/grok?conversation=<ID>`，可用于发现已有会话；不要未经用户选择就把最近一条历史记录当成当前会话。

## 并发规则

登记表写入受锁保护，但浏览器发送动作不是。发送前确认没有其他 agent 或用户正在占用同一 Ego 任务空间；若存在占用，停止发送并报告冲突，避免重复提交。
