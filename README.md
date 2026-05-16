# 安全外发与邮件协作 Agent

> 最新定位：本企业版从单一“DLP 外发审批 Agent”升级为“安全外发与邮件协作 Agent”。它坐在员工和外部邮箱之间，负责读取新邮件、生成每日邮件早报、总结或起草外发稿，并在真实发送前执行 DLP 检测、必要审批和 SMTP 发送。

第一阶段主线不是泛化邮箱客户端，也不做自动回复。系统只读同步企业邮箱 IMAP 邮件，生成 `daily_mail_digest` / `new_mail_received` 通知事件；用户明确要求“总结后外发”“把回复发给对方”时，才进入现有 DLP 外发审批链路。没有明确收件人或正文时会创建可治理任务并等待补充，不再静默回退默认邮箱。

真实发送由本地 SMTP 工具完成，底层使用 `.env` 中配置的 SMTP/IMAP 服务商。SMTP / IMAP 密码或授权码只允许放在 `.env` 或容器环境变量中，不写入源码、文档、SQLite、Chroma 或截图。如果没有配置 SMTP，系统不会伪装成功，而是把任务标记为 `send_failed` 并展示治理化失败原因。

```env
EMAIL_SEND_ENABLED=true
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_USERNAME=你的发件邮箱
SMTP_PASSWORD=你的邮箱SMTP密码或授权码
SMTP_FROM=你的发件邮箱
IMAP_ENABLED=false
IMAP_HOST=imap.163.com
IMAP_PORT=993
IMAP_USERNAME=你的收件邮箱
IMAP_PASSWORD=你的邮箱IMAP密码或授权码
IMAP_MAILBOX=INBOX
```

本目录是企业版实验副本，基于原 `LeetCode RAG Agent` 转向更贴近企业外部沟通治理的场景：员工处理邮件、会议纪要、日志、客户沟通、合同片段、工单记录、项目进展时，让 Agent 先理解内容并生成外发版本，再由 DLP 与审批状态机决定能否真实发送。如果内容包含手机号、身份证、API Key、客户名单等敏感信息，系统会挂起任务，等待人工审批后再真实发送脱敏摘要。

当前联调环境已切换到腾讯企业邮箱 SMTP/IMAP；未配置 SMTP 时会进入 `send_failed`，不会伪装成功。原稳定项目仍保留在 `E:\数据\leetcode-rag-agent`，本副本路径为 `E:\数据\leetcode-rag-agent-enterprise`。

## 企业版入口

- Web: `http://localhost:8511`
- API docs: `http://localhost:8010/docs`
- Chroma: `http://localhost:8011`
- Prometheus: `http://localhost:9091`

## 核心闭环

### 外发前治理

1. 用户粘贴文本或上传 `.txt/.log/.md/.json/.csv` 文件。
2. 系统合并“补充说明 + 文件文本”，作为拟外发内容。
3. Agent 先做隐私扫描和脱敏。
4. 低风险内容直接生成摘要并调用 MCP 邮件工具真实发送。
5. 中高风险内容生成脱敏预览和摘要草稿，但状态进入 `pending_approval`。
6. 审批通过后，系统只使用脱敏摘要真实发送。
7. 审批驳回后，流程终止，并写入审计日志。

### 收件后处理

1. `IMAP_ENABLED=true` 时，系统可只读同步企业邮箱 IMAP 邮件。
2. 默认早报窗口为“昨日 00:00 到当前时间”，用于回答“今天收了多少邮件”“昨天到现在有什么重要邮件”。
3. 同步结果只保存必要元数据、正文片段、摘要、风险提示和 `message_id`，不把完整邮件原文写入 Chroma。
4. 新邮件只生成 `notification_outbox` 事件，当前 Streamlit 只提供调试查看，不做强提醒弹窗。
5. 用户要求总结邮件或起草回复时，只生成摘要/草稿；用户明确要求发送时，复用外发 DLP 审批链路。

## 验收样例

- 低风险：`请总结这段公开会议纪要并发给邮箱。`
- 高风险：`User 张三, Phone 13800000000, API_KEY sk-123456, 数据库连接超时，请总结并发给邮箱。`
- 文件上传：上传包含手机号和 API Key 的 `.log` 文件，应进入 `pending_approval`。

## 当前边界

- 首版只支持文本类文件，不做 PDF、Word、Excel 解析。
- 真实外发依赖 `.env` 中的 SMTP 发件邮箱和授权码。
- 原始敏感文本只保存在本地 SQLite 演示库，不写入 Chroma。
- 后续可扩展为知识库防投毒审批、批量删除二次确认、多租户隔离和异步任务队列。

## EnterpriseRAG-Bench 专项 RAG

当前企业 RAG 新主线只聚焦 `EnterpriseRAG-Bench`，不再把 LeetCode/problem_id 检索作为企业知识问答基础。新增子系统位于 `app/enterprise_rag/`：

- `core/`：复合任务规划、检索编排、证据包、回答生成、memory 策略。
- `ingestion/`：EnterpriseRAG-Bench 本地/HuggingFace 加载、归一化、chunk、索引、manifest。
- `libs/`：文本清洗、source_type 解析、metadata 标准化、轻量 rerank、citation 格式化。
- `eval/`：casebook 与半自动 benchmark，记录 retrieved doc recall 和 answer_facts 覆盖。

新增接口：

- `POST /enterprise-rag/ingest`
- `POST /enterprise-rag/query`
- `GET /enterprise-rag/casebook`
- `GET /enterprise-rag/benchmark`

v1 使用现有 Chroma 和 embedding 配置，企业文档通过 `domain=enterprise_knowledge` 与历史语料隔离。没有足够证据时，回答必须明确说明“当前企业知识库中没有检索到足够证据”，不能编造结论。

### EnterpriseRAG Docker-first 回归

EnterpriseRAG 当前主链路固定为：

- dense retrieval：`Chroma` + `BAAI/bge-m3`
- sparse retrieval：`SQLite FTS5 + BM25`
- rerank：`BAAI/bge-reranker-v2-m3`

当前检索预算已经从“固定扩大 top_k”升级为 adaptive budget：

- `small`：事实查找、明确字段或错误文案查询，降低 dense/sparse/rerank 开销。
- `medium`：普通语义问答，保持适中召回。
- `large`：recommendation、constrained、conflicting 等高召回问题，保留足够 rerank/evidence 预算。
- `expanded`：只有第一轮证据不足时才触发二阶段扩展。

`retrieval_stage_debug` 会记录 `budget_profile / expansion_triggered / expansion_reason / first_pass_counts / second_pass_counts`，用于判断系统是真缺证据，还是只是召回预算不足。

推荐在容器内准备模型路径；如果本地目录不存在，代码会自动回退到 HuggingFace model name：

```env
ENTERPRISE_EMBEDDING_LOCAL_DIR=/app/external-models/bge-m3
ENTERPRISE_RERANKER_LOCAL_DIR=/app/external-models/bge-reranker-v2-m3
ENTERPRISE_SPARSE_DB_PATH=/app/data/enterprise_sparse.db
```

完整回归以 Docker 内执行为准：

```powershell
docker compose up --build -d
docker compose exec api python scripts/enterprise_rag_regression.py --reset --limit 20
```

这个回归脚本会在容器内依次执行：

1. `POST /enterprise-rag/ingest`
2. `POST /enterprise-rag/query`
3. `GET /enterprise-rag/benchmark`

并输出：

- ingest 是否成功写入企业 Chroma collection 与 `enterprise_sparse.db`
- 指定 query 的 `answer / supporting_facts / retrieval_stage_debug / rerank_debug`
- benchmark 的 `average_doc_recall / average_evidence_fact_coverage / average_answer_fact_coverage`
- 若干 bad cases 方便排查答案器或 reranker 问题

# 历史基线说明（已非当前主线）

这是一个面向面试准备和 Agent/RAG 学习的工程化 PoC。项目现在已经从“多个独立 Labs 演示页”升级为“一个统一 Agent”，用户通过同一个聊天入口提问，后端用 `LangGraph` 负责意图路由、工具调用、统一 RAG 检索、答案生成和可观测性记录。

## 当前能力

### 2026-05-07 当前主线状态

当前主线已经从早期的“DLP 外发演示 + 若干独立实验页”收口为一个更接近企业落地形态的统一 Agent。  
当前正式可用并且已经进入 Docker 回归范围的能力主要有：

1. 企业知识问答
   - `EnterpriseRAG-Bench` 新主链已接入 `/agent/chat`
   - 检索主链为：
     - dense retrieval：`Chroma + BAAI/bge-m3`
     - sparse retrieval：`SQLite FTS5 + BM25`
     - rerank：`BAAI/bge-reranker-v2-m3`
   - 已补齐：
     - sentence-level evidence
     - canonical facts
     - answer intent / question focus
     - recommendation slot assembly
     - structured recommendation rendering：`core_facts / answer_slots / answer_plan -> LLM answer`
     - debug / benchmark fields

2. 统一 `/agent/chat`
   - 当前主路径已经切成：
     - 安全硬分支
     - 轻量 Router
     - Fast Path / Slow Path(ReAct)
     - observation-first renderer
   - Fast Path 已覆盖：
     - enterprise fact
     - contextual memory
     - upload analysis
     - mailbox / task status
     - persona / capability
   - 目标是不再让所有请求都先进重型 think。

3. 邮件协作与 DLP 外发
   - 用户可直接在聊天里生成外发计划，不再依赖手动创建外发任务入口
   - 已支持真实附件持久化与二进制附件发送
   - 已支持 DLP 风险判定、审批流、任务台与 SMTP 真实发送
   - 已支持 pending draft follow-up patch：
     - 第二轮、第三轮追问会修改上一版待确认草稿，而不是重建新计划
   - 已补齐正文结构约束：
     - greeting
     - sender identity / “我们是谁”
     - send date / send time
     - reason / tone / do_not_include
   - 已加入 `attachment_source / reference_source / body_source` 分离，默认禁止附件全文直接进入收件人正文
   - 邮件 observations 已稳定暴露：
     - `draft_state`
     - `patch_kind`
     - `source_policy`
     - `confirmation_required`
   - 规则层负责邮件状态、约束和来源边界，用户可见 draft / clarification / patch 说明优先由 LLM authoring renderer 基于 observation 生成

4. 上传文档分析
   - 已支持：
     - summarize
     - qa
     - critique
     - rewrite
     - extract_action_items
   - 文档评价类请求可以直接走 Fast Path，不再强制先进重型 ReAct。

5. Hermes-style Memory（第一版）
   - 已落地四层 memory：
     - `session_transcript`
     - `turn_summary`
     - `workspace_memory`
     - `user_model`
   - 已支持：
     - `conversation_recent`
     - `conversation_summary`
     - `workspace_memory`
     - `user_model`
     作为主链中的 first-class read target
   - 已补上 transcript compaction 与 controlled reflection 的基本策略
   - Workspace memory 已从“每次都 hybrid 检索”升级为 policy-driven retrieval：
     - README / todolist / 总体要求 / 明确 `.md` 文件优先 exact path + FTS
     - prompt / policy / config / memory note 优先 metadata + FTS
     - 语义架构类问题才进入 vector 或 hybrid
   - Follow-up memory 已从“命中关键词就放大 top_k”升级为 `MemoryRetrievalPlan`：
     - `FOLLOW_UP_HINTS` 只作为轻量 signal
     - recent turns 和 merged summaries 分层读取
     - 文件名、任务号、README.md、Docker 等会抽取为 `entity_anchors`
     - 只有历史命中不足时才扩展 summary 检索
   - Memory observation 明确带有边界：
     - `memory_boundary = context_only`
     - `enterprise_citation_required = true`
   - 这表示 memory 只能补上下文、偏好和项目约定；企业事实仍必须由 EnterpriseRAG citations 支撑

6. 当前明确边界
   - Docker 是唯一有效验收环境；不以本机 Python 作为完成标准
   - 邮件的 reply / forward 目标绑定仍可继续增强
   - 日历 / 腾讯会议能力尚未接入当前主链
   - DLP review content 出于审计目的仍可能包含附件文本，但收件人可见正文已与之分离

7. 推荐回归方式
   - 启动：
     - `docker compose up --build -d`
   - EnterpriseRAG 回归：
     - `docker compose exec api python scripts/enterprise_rag_regression.py --reset --limit 20`
   - UI 回归：
     - Web：`http://localhost:8511`
     - API docs：`http://localhost:8010/docs`

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
cd E:\数据\leetcode-rag-agent-enterprise
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
- `POST /mail/inbound/sync`
- `GET /mail/inbound/summary?since=&until=`
- `GET /mail/inbound/messages`
- `POST /mail/inbound/{message_id}/draft-reply`
- `GET /notifications/outbox`

Prometheus metrics:

- `agent_workflow_created_total`
- `agent_workflow_pending_approvals_total`
- `agent_workflow_approved_total`
- `agent_workflow_rejected_total`
- `agent_workflow_risk_total`

## Inbound Mail And Notification Outbox

The first inbound-mail version is intentionally narrow:

- 163 IMAP only, read-only, no delete/move/mark-read operations.
- Disabled by default through `IMAP_ENABLED=false`.
- `POST /mail/inbound/sync` performs manual sync and writes message metadata/snippets.
- `POST /mail/inbound/digest` creates a `daily_mail_digest` notification event.
- `new_mail_received` events are written to `notification_outbox` for future Feishu/WeCom/DingTalk integration.
- Chat queries such as `今天收了多少邮件` use the inbound summary instead of creating outbound DLP tasks.
- Draft replies are not sent automatically; sending still requires an explicit user request and the DLP approval state machine.

## Public Office Datasets For Manual Evaluation

Do not import these datasets into a new lab yet. Copy representative snippets manually into the chat or upload area:

- [EnterpriseRAG-Bench](https://huggingface.co/datasets/onyx-dot-app/EnterpriseRAG-Bench): enterprise-like Slack, Gmail, Drive, Confluence and issue data. Best for office Q&A, email-thread understanding, and external-summary prompts.
- [Enron Email Dataset](https://www.loc.gov/item/2018487913/) / [WAC Enron corpus](https://wacclearinghouse.org/jwa/corpora/enron/): realistic enterprise email threads. Best for inbound-mail summaries, reply drafting, and accidental external-disclosure checks.
- [QMSum](https://github.com/Yale-LILY/QMSum): query-focused meeting summaries. Best for meeting-note externalization tests.
- [MeetingBank](https://meetingbank.github.io/): long public meeting transcripts and minutes. Best for long-document compression and external-facing summaries.
- [Schema-Guided Dialogue](https://www.tensorflow.org/datasets/catalog/schema_guided_dialogue): task-oriented office-like dialogues. Best for clarification and missing-recipient/missing-content behavior.
- [SMCalFlow](https://microsoft.github.io/task_oriented_dialogue_as_dataflow_synthesis): calendar/person/location workflows. Best for future email-to-task or meeting follow-up expansion.
- [docx-corpus](https://docxcorp.us/): public document samples. Best for manually copied policy/report/contract-like text.

## DLP Scenario Pack And Fault Injection Lab

The enterprise copy now includes a first-pass DLP scenario replay lab on top of the asynchronous
task pipeline.

- Scenario catalog API: `GET /labs/dlp/scenarios`
- Replay a scenario into the real task queue: `POST /labs/dlp/scenarios/{scenario_id}/replay`
- Scenario runs reuse the same `/tasks`, Celery worker, WebSocket stream, and approval console
- Task responses now include:
  - `lab_run`
  - `scenario_id`
  - `scenario_name`
  - `fault_injection`
  - `expected_outcome`
  - `status_path`
  - `scenario_evaluation`

Current built-in injected controls:

- `force_smtp_fail`
- `force_model_timeout`
- `force_retrieval_empty`
- `force_rule_only_mode`
- `force_queue_delay_seconds`
