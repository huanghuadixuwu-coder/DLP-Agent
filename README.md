# 安全外发与邮件协作 Agent

这是一个 Docker-first 的企业邮件协作与知识问答 Agent。当前主线已经从早期的 LeetCode RAG / Labs 演示，收口为面向企业落地的多用户试用版：

- 员工通过统一聊天入口处理企业知识问答、邮件草稿、DLP 外发审批、收件摘要和上传内容分析。
- 系统通过 `ActorContext` 区分 `tenant_id / user_id / workspace_id / roles / session_id / conversation_id`，支持基础多用户、租户隔离和权限边界。
- 企业事实问答默认使用 EnterpriseRAG，memory 只能补上下文和项目约定，不能替代企业事实 citation。
- 所有验收以 Docker 容器内结果为准，不以本机 Python 作为有效基线。

## 入口

- Web: `http://localhost:8511`
- API docs: `http://localhost:8010/docs`
- Chroma: `http://localhost:8011`
- Prometheus: `http://localhost:9091`

## 快速启动

```powershell
cd E:\codex-home-restored\worktrees\ae94\leetcode-rag-agent-enterprise
docker compose up --build -d
```

常用 Docker 回归命令：

```powershell
docker compose exec -T api python -m compileall -q app scripts
docker compose exec -T api python scripts/agent_runtime_regression.py
docker compose exec -T api python scripts/concurrency_regression.py
docker compose exec -T api python scripts/enterprise_rag_regression.py --reset --limit 20
```

如果当前运行中的同名 compose 项目挂载了其他副本，可用 one-off 容器验证当前目录：

```powershell
docker compose run --rm --no-deps api python -m compileall -q app scripts
docker compose run --rm --no-deps api python scripts/agent_runtime_regression.py
```

## 环境变量

SMTP / IMAP 凭据只允许放在 `.env` 或容器环境变量中，不写入源码、文档、SQLite、Chroma 或截图。

```env
EMAIL_SEND_ENABLED=true
SMTP_HOST=smtp.exmail.qq.com
SMTP_PORT=465
SMTP_USERNAME=your-name@your-company.com
SMTP_PASSWORD=your-smtp-auth-code
SMTP_FROM=your-name@your-company.com

IMAP_ENABLED=true
IMAP_HOST=imap.exmail.qq.com
IMAP_PORT=993
IMAP_USERNAME=your-name@your-company.com
IMAP_PASSWORD=your-imap-auth-code
IMAP_MAILBOX=INBOX

ENTERPRISE_EMBEDDING_LOCAL_DIR=/app/external-models/bge-m3
ENTERPRISE_RERANKER_LOCAL_DIR=/app/external-models/bge-reranker-v2-m3
ENTERPRISE_SPARSE_DB_PATH=/app/data/enterprise_sparse.db
```

当前默认 embedding / reranker：

- dense: `BAAI/bge-m3`
- sparse: `SQLite FTS5 + BM25`
- rerank: `BAAI/bge-reranker-v2-m3`

## 当前能力

### 1. 多用户与企业邮箱绑定

当前 v1 采用轻量企业版身份方案：

- Web 端支持绑定腾讯企业邮箱。
- 请求侧通过 header 或 payload 注入身份：`X-Tenant-Id / X-User-Id / X-Workspace-Id / X-Roles`。
- 无身份时使用 `local-dev / local-user / default`，保证 Docker 本地演示不断裂。
- conversation、task、memory、RAG debug 和 admin 状态会携带 actor context。

权限边界：

- 只读用户可以问答和查看自身上下文。
- 发信、审批、RAG ingest / benchmark、policy 修改等 side-effectful 动作需要对应 role。
- 权限失败返回结构化 observation，最终自然语言说明由 renderer 基于 observation 生成。

### 2. 邮件协作与 DLP 外发

邮件链路不是泛化邮箱客户端，而是“安全外发与邮件协作”：

1. 用户上传或输入内容，并要求生成邮件或发送给外部对象。
2. 系统抽取收件人、主题、正文意图、附件策略和来源约束。
3. 草稿进入 `pending_draft / patch / confirm` 三段式状态。
4. 用户明确确认发送后，进入 DLP scan。
5. 低风险可进入发送队列；中高风险进入 `pending_approval`。
6. 审批通过后才执行真实 SMTP 发送。
7. SMTP 失败不会伪装成功，会写入 `send_failed / delivery_deferred` 和 failure observation。

邮件 authoring 的结构化字段包括：

- `draft_state`
- `patch_kind`
- `source_policy`
- `confirmation_required`
- `body_constraints`
- `body_sources`
- `attachment_source / reference_source / body_source`

附件内容默认不能进入收件人正文；只有用户明确要求引用、总结或摘录附件时，才允许作为 body source。

### 3. 收件与邮件早报

IMAP 链路是只读同步：

- 不删除、移动或标记邮件。
- 同步结果保存必要元数据、正文片段、摘要、风险提示和 `message_id`。
- 不把完整邮件原文写入 Chroma。
- 支持每日早报、收件箱摘要、邮件搜索和回信草稿。
- 真正发送回复时仍复用 DLP 外发审批链路。

相关接口：

- `POST /mail/inbound/sync`
- `POST /mail/inbound/digest`
- `GET /mail/inbound/summary`
- `GET /mail/inbound/messages`
- `POST /mail/inbound/{message_id}/draft-reply`
- `GET /notifications/outbox`

### 4. EnterpriseRAG 企业知识问答

EnterpriseRAG 是企业事实问答的唯一主路径，不再使用 LeetCode/problem_id 语料作为企业知识基础。

核心链路：

```text
documents/questions
-> source-aware chunking
-> Chroma dense index + SQLite FTS5 sparse index
-> dense/sparse merge
-> bge-reranker-v2-m3 rerank
-> sentence-level evidence
-> canonical_facts / core_facts / answer_slots
-> LLM structured answer renderer
```

已支持：

- source-aware chunking: fireflies / gmail / slack / confluence / google_drive
- doc-level invalidation: 同一 `doc_id` 重 ingest 前先删除旧 dense/sparse chunks
- adaptive retrieval budget: `small / medium / large / expanded`
- answerability boost 和二阶段 expanded retrieval
- sentence-level supporting facts
- answer intent / question focus
- recommendation generic-slot-first structured answer
- visible citations 按 `doc_id` 去重

相关接口：

- `POST /enterprise-rag/ingest`
- `POST /enterprise-rag/query`
- `GET /enterprise-rag/casebook`
- `GET /enterprise-rag/benchmark`

核心指标：

- `average_doc_recall`
- `average_evidence_fact_coverage`
- `average_answer_fact_coverage`

### 5. Hermes-style Memory

当前 memory 是多层 substrate，不是单纯聊天摘要：

- `session_transcript`: 原始会话事件窗口
- `turn_summary`: 结构化 turn memory
- `workspace_memory`: 项目文档、规则、todolist、技术笔记
- `user_model`: 用户偏好和长期记忆候选

Memory 边界：

- memory 可以补上下文、偏好、项目约定和任务连续性。
- 企业事实必须使用 EnterpriseRAG citation。
- 高风险偏好或策略类记忆进入 pending candidate，不自动改变工具、审批或发信策略。

Workspace memory 已从“所有文件都 hybrid search”升级为 policy-driven retrieval：

- README / todolist / 总体要求 / 明确文件名优先 exact path + FTS
- prompt / policy / config / memory note 优先 metadata + FTS
- 语义架构问题才进入 vector 或 hybrid
- hash/mtime 增量检测，未变化文件跳过重新切块和 embedding

### 6. Agent Runtime

`/agent/chat` 当前运行时结构：

```text
request
-> ActorContext
-> permission / rate limit
-> L0 fast router
-> Fast Path 或 ReAct Slow Path
-> tool call / memory read
-> TypedObservation
-> final renderer
-> Agent Verifier
-> Agent Trace Evaluator
-> response + metrics
```

Fast Path 覆盖：

- persona / capability
- contextual memory
- upload analysis
- enterprise fact
- mail / task status

Slow Path 使用 ReAct controller，工具由 dynamic registry 注册和发现。side-effectful 工具必须经过 confirmation guardrail。

### 7. Typed Observation 与 Guardrails

工具、memory、retrieval、mail、failure 都统一输出 typed observation：

- `observation_type`
- `status`
- `provenance`
- `confidence`
- `missing_fields`
- `constraints`
- `side_effects`
- `citations`
- `actor_context`

final renderer 只基于 observations 和 working memory 生成用户可见回答，不直接读取业务模板。

Agent Verifier 会检查：

- 是否越权或把 `permission_denied` 说成成功
- 企业事实是否缺 citation
- 是否把 memory 当企业事实来源
- 是否绕过 confirmation 声称外部动作已完成
- 是否泄漏 attachment-only 内容

Agent Trace Evaluator 会评估行为本身：

- 工具选择是否正确
- 是否遗漏 confirmation
- 是否错误使用 memory
- 是否跨 tenant / workspace
- 是否重复调用重工具
- 失败时是否存在 recovery / fallback observation

### 8. Failure Recovery 与可观测性

关键依赖失败会输出 `dependency_failure` observation，并进入 Prometheus：

- LLM renderer
- reranker
- SMTP
- Celery enqueue
- Redis rate limit / queue health
- Chroma dense recall
- SQLite FTS recall

Prometheus 指标：

- `agent_dependency_failures_total{service,operation,fallback_strategy}`
- `agent_llm_errors_total`
- `agent_renderer_fallback_total`
- `agent_queue_backlog`
- `agent_rate_limited_total`
- `agent_retrieval_expansion_total`

失败处理原则：

- 不乱说成功。
- 不吞掉错误。
- 降级路径写入 observation。
- task / trace / metrics 均可审计。

## 主要接口

统一 Agent：

- `POST /agent/chat`

EnterpriseRAG：

- `POST /enterprise-rag/ingest`
- `POST /enterprise-rag/query`
- `GET /enterprise-rag/casebook`
- `GET /enterprise-rag/benchmark`

任务与治理：

- `GET /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/{task_id}/approve`
- `POST /tasks/{task_id}/reject`
- `POST /tasks/{task_id}/retry-send`

Admin：

- `GET /admin/queue-health`
- `GET /admin/task-stats`
- `GET /admin/privacy-policy`
- `GET /admin/memory-candidates`
- `GET /admin/rag-manifest`

Metrics：

- `GET /metrics`

## 当前边界

- 本轮不提供完整 SSO/OIDC；v1 使用企业邮箱绑定 + request headers / payload 身份注入。
- Streamlit 是多人试用前端，不是完整生产前端。
- PDF、GraphRAG、日历、腾讯会议能力保留为后续计划，不纳入当前主线。
- 历史 LeetCode / Labs 接口可能仍保留用于兼容或调试，但不再是当前产品主线。
