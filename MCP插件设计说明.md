# MCP 插件设计说明

## 插件名称

`interview-agent-tools`

## 设计目的

这个插件最初用于学习 MCP 的核心思想：把外部能力标准化成 Agent 可发现、可调用、可复用的工具。当前企业版已经把这条线升级为动态工具发现与统一 dispatch：工具不再只靠手工 `if/else` 路由，而是通过 decorator-driven registry 暴露能力声明，再由 ReAct / legacy planner / MCP JSON-lines 共享调用。

## 工具列表

| 工具 | 作用 |
| --- | --- |
| `privacy_scan` | 脱敏并判断敏感消息风险 |
| `enterprise_rag_query` | 企业知识问答检索 |
| `mail_*` / `send_email_*` | 邮件草稿、确认、发送相关能力 |
| `memory_*` | 读取 conversation / workspace / user memory |
| `create_reminder` / `list_reminders` | 本地提醒能力 |

## 为什么这些工具适合 MCP

这些能力有共同特征：

- 输入输出可以结构化。
- 可以被多个 Agent 工作流复用。
- 不应该全部写进 prompt 或核心 router。
- 需要清晰的权限、确认和审计边界。
- 副作用工具必须标记 `side_effectful / requires_confirmation`，不能绕过 DLP、审批或用户确认。

## 本地实现

当前本地实现包含：

- `mcp/interview-agent-tools/server.py`
- `app/orchestration/tool_discovery.py`
- `app/orchestration/tools/`
- `app/mcp_tools.py`
- `app/orchestration/registry.py`

Docker 内测试：

```powershell
docker compose exec -T api python -m compileall -q app mcp
```

发送 JSON line：

```json
{"tool":"privacy_scan","args":{"message":"手机号13812345678和api_key=abcdef1234567890外发"}}
```

统一 dispatch 返回结构化结果：

```json
{
  "ok": true,
  "action": "privacy_scan",
  "result": {},
  "error": null,
  "observation_type": "privacy_scan",
  "side_effectful": false,
  "requires_confirmation": false
}
```

发送类工具默认不可被 MCP JSON-lines 直接执行，除非进入受控确认链路。

## 面试回答

> MCP 的价值是把工具接入标准化。Agent 不应该把所有业务能力都写在 prompt 或核心 router 里，而应该通过 registry / manifest / dispatch 调用外部工具。这样工具可以被多个 Agent 复用，输入输出更容易审计，副作用能力也可以统一放到确认、权限和 DLP 边界之后。
