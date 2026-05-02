# DLP 外发审批 Agent

> 最新状态：本企业版已经从“模拟外发”升级为“审批后真实邮件外发”。用户可以在中间聊天区输入“总结这份日志并外发到 xxx@example.com”，也可以上传 `.txt/.log/.md/.json/.csv` 文件；Agent 会自动识别收件邮箱，未识别时默认使用 `17388861183@163.com`。

真实发送由本地 MCP-style 工具 `send_email_163` 完成，底层使用 163 SMTP SSL。SMTP 授权码只允许放在 `.env` 或容器环境变量中，不写入源码、文档、SQLite、Chroma 或截图。如果没有配置 SMTP，系统不会伪装成功，而是把 workflow 标记为 `send_failed` 并展示失败原因。

```env
EMAIL_SEND_ENABLED=true
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_USERNAME=你的163发件邮箱
SMTP_PASSWORD=你的163邮箱SMTP授权码
SMTP_FROM=你的163发件邮箱
```

本目录是企业版实验副本，基于原 `LeetCode RAG Agent` 扩展出一个更贴近企业流程的场景：员工上传或粘贴日志/文本，让 Agent 总结并准备外发到目标邮箱。如果内容包含手机号、身份证、API Key、客户名单等敏感信息，系统会挂起任务，等待人工审批后再真实发送脱敏摘要。

当前版本接入 163 SMTP 邮件服务；未配置 SMTP 时会进入 `send_failed`，不会伪装成功。原稳定项目仍保留在 `E:\数据\leetcode-rag-agent`，本副本路径为 `E:\数据\leetcode-rag-agent-enterprise`。

## 企业版入口

- Web: `http://localhost:8511`
- API docs: `http://localhost:8010/docs`
- Chroma: `http://localhost:8011`
- Prometheus: `http://localhost:9091`

## DLP 外发审批流程

1. 用户粘贴文本或上传 `.txt/.log/.md/.json/.csv` 文件。
2. 系统合并“补充说明 + 文件文本”，作为拟外发内容。
3. Agent 先做隐私扫描和脱敏。
4. 低风险内容直接生成摘要并调用 MCP 邮件工具真实发送。
5. 中高风险内容生成脱敏预览和摘要草稿，但状态进入 `pending_approval`。
6. 审批通过后，系统只使用脱敏摘要真实发送。
7. 审批驳回后，流程终止，并写入审计日志。

## 验收样例

- 低风险：`请总结这段公开会议纪要并发给邮箱。`
- 高风险：`User 张三, Phone 13800000000, API_KEY sk-123456, 数据库连接超时，请总结并发给邮箱。`
- 文件上传：上传包含手机号和 API Key 的 `.log` 文件，应进入 `pending_approval`。

## 当前边界

- 首版只支持文本类文件，不做 PDF、Word、Excel 解析。
- 真实外发依赖 `.env` 中的 SMTP 发件邮箱和授权码。
- 原始敏感文本只保存在本地 SQLite 演示库，不写入 Chroma。
- 后续可扩展为知识库防投毒审批、批量删除二次确认、多租户隔离和异步任务队列。

# LeetCode RAG Agent

这是一个面向面试准备和 Agent/RAG 学习的工程化 PoC。项目现在已经从“多个独立 Labs 演示页”升级为“一个统一 Agent”，用户通过同一个聊天入口提问，后端用 `LangGraph` 负责意图路由、工具调用、统一 RAG 检索、答案生成和可观测性记录。

## 当前能力

- `LeetCode RAG`：支持少量算法题的思路解释、复杂度说明、局部代码问答。
- `统一 Agent`：同一个入口自动处理长文档上下文预算、隐私预警、Apple 歧义消解、框架观点问答、提醒助手。
- `混合路由`：先走规则，再在模糊场景下调用 `GLM-4.5-Air` 做 LLM routing。
- `统一 RAG 语料层`：LeetCode、长文档、隐私、歧义消解、框架观点都写入同一个 `Chroma` collection。
- `过程可见`：前端可看到 intent、routing source、tool calls、retrieved evidence、privacy、context budget。
- `指标监控`：Prometheus 记录请求数、延迟、token、成本、路由来源、证据命中、反思次数等指标。

## 技术栈

- `LangGraph`：统一 Agent 工作流和 LeetCode Agent 状态图
- `LangChain`：模型与向量库集成
- `GLM-4.5-Air`：主模型与 LLM router
- `Chroma`：统一向量库
- `FastAPI`：后端 API
- `Streamlit`：演示前端
- `Prometheus`：监控与指标查看
- `Docker Compose`：全容器化运行

## 启动方式

```powershell
cd E:\数据\leetcode-rag-agent
$env:COMPOSE_BAKE='false'
$env:DOCKER_BUILDKIT='0'
docker compose up --build
```

启动后访问：

- `http://localhost:8501`：统一 Agent 前端
- `http://localhost:8000/docs`：FastAPI 文档
- `http://localhost:9090`：Prometheus

## 推荐体验路径

先进入 `http://localhost:8501`，使用同一个聊天框测试下面几类问题：

- `一本1.5M的书要放入10K memory怎么做？`
- `超大 PDF 怎么放进上下文？`
- `客户手机号13812345678和api_key=abcdef1234567890外发，怎么预警？`
- `apple 手机怎么样？`
- `apple 含糖量高吗？`
- `apple 怎么样？`
- `LangChain 是不是没用了？`

这几类问题会分别触发不同 intent、不同工具，以及不同的统一 RAG 检索证据。

## 核心工作流

统一 Agent 的主流程是：

1. `classify_intent`
2. `plan_tool_use`
3. `run_tools`
4. `compose_answer`
5. `optional_reflection`
6. `finalize_agent_response`

其中关键升级点有两个：

- `intent 分类` 不再只靠关键词，而是“规则 + LLM 混合路由”。
- `工具证据` 不再只是 Python 常量返回，而是优先从 `Chroma` 的统一语料层检索。

## 目录说明

- [app/main.py](</E:/数据/leetcode-rag-agent/app/main.py>)：FastAPI 入口与接口编排
- [app/unified_agent.py](</E:/数据/leetcode-rag-agent/app/unified_agent.py>)：统一 Agent 的 `LangGraph` 工作流
- [app/hybrid_router.py](</E:/数据/leetcode-rag-agent/app/hybrid_router.py>)：规则 + LLM 混合路由
- [app/unified_tools.py](</E:/数据/leetcode-rag-agent/app/unified_tools.py>)：统一工具层
- [app/unified_corpus.py](</E:/数据/leetcode-rag-agent/app/unified_corpus.py>)：统一 RAG 语料构建与检索
- [app/vectorstore.py](</E:/数据/leetcode-rag-agent/app/vectorstore.py>)：Chroma 接入与批量 upsert
- [app/metrics.py](</E:/数据/leetcode-rag-agent/app/metrics.py>)：Prometheus 指标
- [web/streamlit_app.py](</E:/数据/leetcode-rag-agent/web/streamlit_app.py>)：单页聊天前端

## 主要接口

### 统一 Agent

- `POST /agent/chat`
  - 单入口聊天接口
  - 返回 `intent`、`routing_source`、`routing_confidence`、`tool_calls`、`retrieved_evidence`、`privacy`、`context_budget`

### LeetCode RAG

- `GET /health`
- `GET /problems`
- `POST /ingest`
- `POST /chat`
- `POST /plan`
- `POST /execute`
- `GET /metrics`

`/plan + /execute` 仍然保留，用来展示显式 `Plan-and-Execute` 过程。

### 调试型 Labs 接口

- `POST /labs/long-doc/query`
- `POST /labs/privacy/scan`
- `POST /labs/disambiguation/query`
- `POST /labs/framework/compare`

这些接口仍然保留，但现在更适合作为调试接口，而不是主体验入口。

## 统一 RAG 语料层

项目当前使用一个 collection：`leetcode_rag_v1`。

其中包含这些 domain：

- `leetcode`
- `long_doc`
- `privacy`
- `disambiguation`
- `framework`
- `conversation`

新加入的 `long_doc / privacy / disambiguation / framework` 语料已经和 LeetCode 题库一起写入同一个 Chroma collection，统一由工具层做 filter 检索。

## Prometheus 指标

常用指标包括：

- `agent_unified_requests_total`
- `agent_intent_runs_total`
- `agent_tool_calls_total`
- `agent_router_runs_total`
- `agent_router_fallbacks_total`
- `agent_router_llm_confidence`
- `agent_unified_evidence_hits_total`
- `agent_request_latency_ms`
- `agent_tokens_input_total`
- `agent_tokens_output_total`
- `agent_estimated_cost_total`
- `agent_privacy_guardrails_total`

## 多对话记忆与合并

项目现在支持一个更接近真实问答产品的记忆工作区：

- 每个聊天都有独立的 `conversation_id`
- 原始对话写入本地 `SQLite`，默认文件是 `data/conversations.db`
- 每轮成功问答会生成 `turn_summary`，写入 `Chroma`
- 用户可以在左侧选择多个对话并合并
- 合并后会生成新的 merged conversation，并把 `merged_summary` 写入 `Chroma`
- 后续提问会同时检索当前对话记忆和相关合并记忆
- 长回答会默认折叠，完整内容可展开查看

推荐体验：

1. 在左侧点击“新建对话”，讨论一个主题，例如 `worktree 替代方案`
2. 再新建一个对话，讨论另一个相关主题，例如 `多对话合并记忆`
3. 在左侧多选两个对话并点击“合并所选对话”
4. 切换到合并后的对话，提问 `我们之前关于不用 worktree 的方案是什么？`

相关接口：

- `GET /conversations`
- `POST /conversations`
- `GET /conversations/{conversation_id}`
- `GET /conversations/{conversation_id}/turns`
- `GET /conversations/{conversation_id}/summary`
- `POST /conversations/merge`

新增记忆指标：

- `agent_conversations_total`
- `agent_conversation_turns_total`
- `agent_conversation_merges_total`
- `agent_turn_summary_writes_total`
- `agent_merged_summary_writes_total`
- `agent_memory_retrieval_hits_total`
- `agent_answer_collapses_total`

## 其他文档

- [技术文档.md](</E:/数据/leetcode-rag-agent/技术文档.md>)
- [面试开放题知识库.md](</E:/数据/leetcode-rag-agent/面试开放题知识库.md>)
- [LangChain_vs_RawLLM_观点.md](</E:/数据/leetcode-rag-agent/LangChain_vs_RawLLM_观点.md>)
- [MCP插件设计说明.md](</E:/数据/leetcode-rag-agent/MCP插件设计说明.md>)

## 当前定位

这个项目是一个学习型、面试型 PoC，不是生产级平台。它的重点不是做出完整业务系统，而是把常见的 Agent/RAG 开放题变成“能运行、能观察、能解释、能继续扩展”的工程实验台。
# Enterprise Workflow Prototype

This copy is the enterprise-oriented branch of the original LeetCode RAG Agent. The stable baseline remains in `E:\数据\leetcode-rag-agent`; this folder adds a Human-in-the-loop workflow for sensitive outbound messages.

Enterprise ports:

- Web: `http://localhost:8511`
- API docs: `http://localhost:8010/docs`
- Chroma: `http://localhost:8011`
- Prometheus: `http://localhost:9091`

The local embedding model is mounted from `E:\数据\bge-small-zh-v1.5` to `/app/external-models/bge-small-zh-v1.5`.

Sensitive outbound workflow:

1. User submits outbound content.
2. The workflow detects PII and secrets.
3. The workflow redacts sensitive fields.
4. The workflow classifies risk.
5. Low-risk tasks complete automatically.
6. Medium/high-risk tasks enter `pending_approval`.
7. A human reviewer approves or rejects the task.
8. The system records an audit log.

API endpoints:

- `POST /workflows/sensitive-outbound`
- `GET /workflows/sensitive-outbound`
- `GET /workflows/sensitive-outbound/{workflow_id}`
- `POST /workflows/sensitive-outbound/{workflow_id}/approve`
- `POST /workflows/sensitive-outbound/{workflow_id}/reject`

Prometheus metrics:

- `agent_workflow_created_total`
- `agent_workflow_pending_approvals_total`
- `agent_workflow_approved_total`
- `agent_workflow_rejected_total`
- `agent_workflow_risk_total`
