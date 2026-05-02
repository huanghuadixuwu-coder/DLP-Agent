# MCP 插件设计说明

## 插件名称

`interview-agent-tools`

## 设计目的

这个插件不是为了接入复杂外部系统，而是为了学习 MCP 的核心思想：把外部能力标准化成 Agent 可发现、可调用、可复用的工具。

## 工具列表

| 工具 | 作用 |
| --- | --- |
| `create_reminder` | 从自然语言创建本地提醒 |
| `list_reminders` | 查看本地提醒 |
| `privacy_scan` | 脱敏并判断敏感消息风险 |
| `budget_context` | 模拟大文档上下文预算分配 |
| `disambiguate_entity` | 判断 apple 是公司还是水果 |

## 为什么这些工具适合 MCP

这些能力有共同特征：

- 输入输出可以结构化
- 可以被多个 Agent 工作流复用
- 不应该全部写进 prompt
- 需要清晰的权限和边界

## 本地实现

当前本地骨架在：

- `mcp/interview-agent-tools/server.py`
- `mcp/interview-agent-tools/tools_manifest.json`
- `app/mcp_tools.py`

本地测试：

```powershell
python mcp/interview-agent-tools/server.py
```

发送 JSON line：

```json
{"tool":"privacy_scan","args":{"message":"手机号13812345678和api_key=abcdef1234567890外发"}}
```

## 面试回答

> MCP 的价值是把工具接入标准化。Agent 不应该把所有业务能力都写在 prompt 里，而应该通过协议调用外部工具。这样工具可以被多个 Agent 复用，输入输出也更容易审计和治理。

