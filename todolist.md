# Enterprise Agent ToDo

更新时间：2026-05-31
当前有效运行目录：`E:\leetcode-rag-agent-enterprise`
验收基线：只以 Docker 容器内结果为准，不以本机 Python 作为完成标准。

## 状态标记
- `[x]` 已完成并完成 Docker 回归
- `[-]` 已落地核心能力，但仍需扩展样本或真实场景验证
- `[ ]` 未完成

## 0. 总体约束
- `[x]` 遵循 `总体要求.md`：代码只负责状态、约束、来源边界、权限、工具调用和 observation。
- `[x]` 用户可见 `answer / clarification / draft` 优先由 LLM renderer 基于 observations 生成。
- `[x]` side-effectful 工具必须经过权限、confirmation、幂等或异步任务边界。
- `[x]` 8511 Web 入口保持不变：`http://localhost:8511/`。

## 1. 当前项目等级
- `[-]` 当前处于 **L3++ 多人试用版 / 多 Agent 跨域闭环初级可用版**。
- `[x]` 已具备多用户/租户上下文、权限边界、队列治理、Prometheus/Admin 可观测基础。
- `[x]` 已具备中心化 Supervisor + Domain Agents + DAG executor + typed observation 主架构。
- `[-]` 尚未达到 L4 生产雏形：真实 calendar provider、更大样本回归、真实外部 provider 压测和运维恢复手册仍需加强。

## 2. Enterprise Landing Foundation
- `[x]` `ActorContext`：`tenant_id / user_id / workspace_id / roles / session_id / conversation_id`。
- `[x]` `/agent/chat`、EnterpriseRAG、DLP task、mail、memory/workspace retrieval 已接入 actor context。
- `[x]` EnterpriseRAG 与 memory/task 查询已具备 tenant/workspace/user 过滤边界。
- `[x]` side-effectful mail send、DLP approval、RAG ingest/benchmark、meeting write 已接入 role permission check。
- `[x]` Redis-backed rate limit、Celery queue health、Admin API、Prometheus 指标已落地。
- `[x]` Docker 并发 smoke 已覆盖 `/agent/chat`、EnterpriseRAG query、mail draft/confirm、queue health、Prometheus snapshot。
- `[x]` worker/queue 入队失败已返回结构化 recovery observation，不再假装 queued 成功。
- `[ ]` 扩大并发/限流/worker failure recovery 样本，形成更接近内测压测的回归。

## 3. EnterpriseRAG 主链
- `[x]` Docker-first ingest / query / benchmark 主链跑通。
- `[x]` dense = Chroma + `bge-m3`，sparse = SQLite FTS5，rerank = `bge-reranker-v2-m3`。
- `[x]` source-aware chunking、sentence-level facts、canonical facts、answer slots、LLM structured renderer 已落地。
- `[x]` retrieval adaptive budget 与 workspace retrieval policy 已落地。
- `[-]` 当前 GCP onboarding 主案例已明显改善，但仍需扩大 benchmark 样本和追踪 `answer_fact_coverage`。
- `[ ]` Docker 内扩大 EnterpriseRAG benchmark，重点覆盖同主题不同问法、secondary focus、错误文档混入。

## 4. Mail / DLP 主链
- `[x]` Mail draft / patch / confirm / approval / send Docker 回归已跑通。
- `[x]` 附件正文隔离、body source policy、DLP 审批和 SMTP worker 状态机已落地。
- `[x]` policy hot update 已支持 active policy version 与 fallback static rules。
- `[x]` SMTP 失败、DLP 高风险阻断、worker 入队失败已纳入 recovery observation 回归。
- `[x]` 已删除旧 V1 Agent、旧 sensitive workflow 和 8511 legacy 治理面板，主链继续保持 observation-first。
- `[ ]` 增加更多等价新问法回归，避免邮件草稿和确认路径依赖固定表达。

## 5. Memory / ReAct
- `[x]` conversation recent / summary / workspace memory / user model 已作为 ReAct memory substrate。
- `[x]` structured turn memory、pending reflection candidate、memory safety boundary 已落地。
- `[x]` memory 只能补上下文、偏好、项目约定；企业事实仍必须由 EnterpriseRAG citations 支撑。
- `[x]` pending memory review 后端最小能力已落地。
- `[x]` conversation memory 已拆分到 `conversation_memory_bge_m3_v1`，旧 512 维 runtime memory 已按 content hash 幂等迁移到 `bge-m3` 1024 维索引。
- `[ ]` 补 UI/Admin 层的 pending memory approve/reject/expire 审计展示。

## 6. Dynamic Tool Discovery & Dispatch
- `[x]` `@register_tool` + decorator-driven discovery 已落地。
- `[x]` orchestration tools 与 MCP-style tools 已迁移到动态 registry。
- `[x]` unified `dispatch_tool_call(...)` 已支持 unknown action、参数错误、handler 异常、side-effectful 默认拒绝。
- `[x]` MCP JSON-lines bridge 已支持 manifest list 与 read-only 调用；发送类工具默认 confirmation-required。

## 7. Centralized Supervisor & Domain Agents
- `[x]` Domain Agent Contract 已落地：Mail / Calendar / Meeting / DLP / EnterpriseRAG / Memory。
- `[x]` `AgentTaskPlan` 已扩展：`agent / action / dependencies / risk / confirmation_required / idempotency_key / resource_scope`。
- `[x]` DAG executor 已支持 schema 校验、循环依赖拒绝、read-only 并发、side-effectful confirmation。
- `[x]` `/agent/chat` 已接入高置信 meeting/calendar DAG 路径，普通请求仍保留 ReAct 主链。
- `[x]` Agent Trace Evaluator 已覆盖 dependency blocked、side-effect blocked、confirmation boundary、memory boundary 等检查。

## 8. Calendar Agent
- `[x]` 已注册 `calendar_list_events / calendar_find_available_slots / calendar_create_event / calendar_update_event / calendar_cancel_event`。
- `[x]` 未配置真实 provider 时返回 `provider_not_configured` observation，不伪造成可用日程。
- `[x]` Calendar 写操作已带权限、confirmation、幂等键和 typed observation 边界。
- `[ ]` 接入真实 calendar provider。当前现实路径优先考虑 CalDAV/Exchange/企业邮箱日历；腾讯会议同步日历更像外部客户端能力，不应当作为后端日历 truth source。

## 9. Tencent Meeting Agent
- `[x]` 已注册 `meeting_list_user_meetings / meeting_get_details / meeting_create_tencent_meeting / meeting_cancel_tencent_meeting / meeting_attach_to_calendar_event`。
- `[x]` 已接入个人账号腾讯会议 Skill/MCP provider：`TENCENT_MEETING_TOKEN + tools/list + tools/call`。
- `[x]` Docker 内真实只读回归已通过：tools/list 与 get_user_meetings。
- `[x]` Meeting confirmation 已切到 governed async task：确认后创建 `task_type=domain_meeting`，进入 `meeting_queue`。
- `[x]` Meeting worker 已支持异步消费 `domain_meeting`，通过 unified dispatch 执行 `meeting_*` action。
- `[x]` 会议参数抽取/规范化已修正：`Topic: ... tomorrow afternoon` -> `topic / natural_time / start_time / end_time / duration_minutes`。
- `[x]` 已真实创建一次腾讯会议 E2E：`task_f2c56ccafa63` completed，provider 返回真实 meeting id / code / join url / start/end。
- `[x]` 腾讯会议 MCP 嵌套 JSON 返回已规范化为 `meeting_id / meeting_code / meeting_url / start_time / end_time`。
- `[ ]` 如需演示真实取消会议，再走一次明确 confirmation 的 cancel E2E；不要自动清理真实会议。
- `[ ]` 保留企业账号 REST Open API provider 作为后续增强，不替换当前个人账号 Skill/MCP 路径。

## 10. Cross-Domain Workflow
- `[x]` 跨域 DAG 已支持 `mail_context + availability -> invitation_dlp -> create_meeting -> invitation_draft`。
- `[x]` `mail_invitation_draft` 已落地：只生成结构化 invitation draft state，不发送邮件。
- `[x]` Meeting worker fake 回归已验证：会议创建成功后可继续生成 invitation draft，并保留 meeting id/link/time。
- `[x]` `/agent/chat` 已支持 meeting worker completed 后展示 `meeting_result` typed observation。
- `[x]` 已支持从 `post_confirm_results.mail_invitation_draft` / meeting task 合成 pending meeting invitation mail plan。
- `[x]` 已支持用户补收件人后进入 `send_mail_plan` confirmation，再确认后创建 DLP task。
- `[x]` 已补 `cross_domain_workflow_regression.py`：客户邮件摘要 DAG -> 会议确认 -> worker 创建会议 -> invitation draft -> 补收件人 -> DLP/审批 -> SMTP worker sent。
- `[x]` 最新 mail confirmation 优先于旧 meeting confirmation，避免跨域链路二次 `confirm` 被误路由到会议任务。
- `[-]` 当前跨域闭环已在 Docker 回归中打通；真实线上演示仍需使用当前 `.env` SMTP/腾讯会议凭据跑一遍人工确认。

## 11. Docker 回归命令
- `[x]` `docker compose run --rm --no-deps api python -m compileall -q app scripts`
- `[x]` `docker compose run --rm --no-deps api python scripts/multi_agent_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/agent_chat_dag_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/agent_chat_endpoint_dag_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/meeting_confirmation_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/meeting_worker_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/tencent_meeting_mcp_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/meeting_result_continuation_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/cross_domain_workflow_regression.py`
- `[x]` `docker compose run --rm --no-deps api python scripts/failure_recovery_regression.py`
- `[x]` `docker compose exec -T api python scripts/mail_harness_regression.py`
- `[x]` `docker compose exec -T api python scripts/mail_provider_failure_regression.py`
- `[x]` `docker compose exec -T api python scripts/mail_concurrency_regression.py`
- `[x]` `docker compose exec -T api python scripts/mail_persistent_draft_regression.py`
- `[x]` `docker compose exec -T api python scripts/mail_m6_ui_governance_regression.py`
- `[x]` `docker compose exec -T api python scripts/migrate_conversation_memory_collection.py`
- `[x]` `docker compose exec -T api python scripts/conversation_memory_migration_regression.py`

## 12. 下一步优先级
1. `[-]` 完成 Phase 3 RAG runtime contract：active index diagnostics、渐进式 observation disclosure、阶段级 latency/token tracing。
2. `[-]` 完成全阶段可观测性：复用现有 Prometheus/Admin，覆盖 Supervisor、DAG、Domain Agent、LLM renderer 与异步 worker。
3. `[ ]` 增加 Trigger 主动工作能力：定时/事件任务统一进入 Supervisor -> DAG，不直接调用副作用工具。
4. `[ ]` 增加 Reply Channel：将正式业务邮件与系统通知分离，先支持 workspace 与 governance console。
5. `[ ]` 增加 OpenAPI 工具工厂：先为 Workspace/Document Agent 生成只读工具，并复用现有 dynamic registry。
6. `[ ]` 用当前真实 `.env` 凭据人工演练一次跨域闭环：创建真实腾讯会议 -> 真实 SMTP 发送邀请邮件；必要时先用测试收件箱。
7. `[ ]` 接入或明确选择真实 calendar provider；没有 provider 时继续保持 provider boundary，不伪造空闲时间。
8. `[ ]` 增加真实 provider retry / circuit breaker / manual handover 的更大样本回归。

## 13. Mail Agent V2
- `[-]` 已完成规格清理与边界锁定，正式入口为 `spec/MAIL_AGENT_V2_SPEC.md`。
- `[x]` 旧长规格已归档到 `spec/archive/MAIL_AGENT_V2_SPEC_DRAFT.md`。
- `[x]` UI 契约已正式化为 `spec/AGENT_UI_SPEC.md`。
- `[x]` Mail Agent core 边界已锁定：读写、线程、草稿、附件来源、DLP、审批、发送。
- `[x]` Docs / Drive / Forms 已定位为后续 Workspace/Document domain agents。
- `[x]` Mail Harness 已定位为第一阶段核心能力。

### 13.1 Spec Package
- `[x]` `spec/mail-agent/BOUNDARIES.md`
- `[x]` `spec/mail-agent/DEFAULT_DECISIONS.md`
- `[x]` `spec/mail-agent/DOMAIN_MODEL.md`
- `[x]` `spec/mail-agent/TOOL_CONTRACTS.md`
- `[x]` `spec/mail-agent/STATE_MACHINES.md`
- `[x]` `spec/mail-agent/PROVIDER_STRATEGY.md`
- `[x]` `spec/mail-agent/HARNESS.md`
- `[x]` `spec/mail-agent/UI_CONTRACT.md`
- `[x]` `spec/mail-agent/PRIVACY_MEMORY.md`
- `[x]` `spec/mail-agent/RELIABILITY.md`
- `[x]` `spec/mail-agent/IMPLEMENTATION_SEQUENCE.md`

### 13.2 First Implementation Slice
- `[x]` 新增 Mail domain schema module。
- `[x]` 新增 fake provider contract 与 fixture data。
- `[x]` 新增 `mail_harness_regression.py`，覆盖 read / draft / governance happy path。
- `[x]` 新增 `mail_provider_failure_regression.py`，覆盖 SMTP timeout、auth expired、duplicate uncertainty。
- `[x]` 新增 `mail_concurrency_regression.py`，覆盖多用户、多租户、同 draft 并发 patch。
- `[x]` Docker 内验证当前 IMAP/SMTP 路径不退化：IMAP sync、inline draft/confirm、mock SMTP send、forced SMTP failure recovery 均通过；真实 SMTP 多邮件演练保留为显式人工动作。

### 13.3 Persistent Draft State
- `[x]` 新增 PostgreSQL `mail_drafts` store，草稿不再只依赖 conversation debug 恢复。
- `[x]` draft / clarification / confirmation / patch 路径统一持久化 `draft_id`、版本与 actor scope。
- `[x]` confirmation 绑定稳定 `idempotency_key`，重复确认复用同一个 DLP task。
- `[x]` 新增 `mail_persistent_draft_regression.py`，覆盖页面刷新后 patch 与重复确认去重。

### 13.4 UI Surface Separation
- `[x]` UI 边界已锁定：`8511` 为普通用户工作台，`8512` 为治理与运维台。
- `[x]` Grafana 暂缓；Prometheus 继续作为指标采集后端。
- `[x]` 风险分层已锁定：`low` 自动发送、`medium` 发件人二次确认、`high` 治理升级、`critical` 默认阻断。
- `[x]` 从 `8511` 移除 high-risk 例外审批/驳回按钮、raw tool calls、trace JSON、token/cost、队列和故障注入视图。
- `[x]` `8511` 保留 inbox/thread、draft review、显式确认、发件人外发安全确认、简化任务进度和 renderer 生成的失败提示。
- `[x]` 新增 `8512` 治理台：high-risk 例外审批/驳回、审计时间线、provider/queue health、recovery observation、DLQ replay、Harness 结果、trace 和成本诊断。
- `[x]` Docker 内验证：`medium` 可在 `8511` 二次确认，`high` 只能在 `8512` 例外审批，`critical` 默认阻断。
### 13.4.1 M6 UI Integration Completion
- `[x]` M6 completed: `8511` removed high-risk exception approve/reject controls from the active user task panel and moved raw tool calls / trace / token-cost diagnostics out of the user-facing panel.
- `[x]` `8511` keeps inbox/thread status, draft review, explicit confirmation, simplified task progress, and renderer/user-facing recovery guidance.
- `[x]` Added independent `8512` governance console with high-risk approval/rejection, task timeline, provider health, queue health, recovery observation, DLQ replay, and harness catalog panels.
- `[x]` Added governance APIs: `/admin/task-stats`, `/admin/mail-provider-health`, `/admin/mail-dlq`, `/admin/mail-dlq/{dlq_id}/replay`, `/admin/mail-harness-summary`.
- `[x]` Docker regression passed: `docker compose exec -T api python scripts/mail_m6_ui_governance_regression.py`.

### 13.5 Provider Abstraction
- `[x]` M3 Provider Abstraction completed: current IMAP/SMTP path is wrapped by `CurrentImapSmtpMailProvider`.
- `[x]` Provider responses can emit typed health / failure observations through `MailProviderResponse.to_observation(...)`.
- `[x]` Fake provider and current provider pass the same Docker contract regression: `docker compose exec -T api python scripts/mail_provider_contract_regression.py`.
- `[x]` Contract regression keeps external current-provider sync/send disabled by default and verifies `provider_not_configured` / `confirmation_required` observations instead of pretending success.

### 13.6 HTML / Thread / Label / Attachment Normalization
- `[x]` M4 completed: inbound mail store persists `body_text / body_html_sanitized / body_preview / thread_id / provider_thread_id / labels / attachments / headers_json`.
- `[x]` IMAP parser extracts safe visible text from HTML, stores sanitized HTML, and strips script/event/javascript URL patterns.
- `[x]` Attachment handling stores metadata only: filename, content type, size, provider attachment id, and source policy; attachment body does not enter mail body.
- `[x]` Thread fallback derives stable local `thread_id`, while `provider_thread_id` is preserved from References/In-Reply-To when available.
- `[x]` Current provider exposes normalized fields and returns explicit `provider_write_supported=false` observation for unsupported label writes.
- `[x]` Docker regression passed: `docker compose exec -T api python scripts/mail_m4_normalization_regression.py`.

### 13.7 Reliability and DLQ
- `[x]` M5 completed: added `mail_dead_letter_queue` storage for failed mail operations.
- `[x]` DLQ entries record task id, operation, payload digest, last error, attempt count, safe replay flag, recovery hint, actor context, and payload snapshot.
- `[x]` Replay-safe checkpoint can move safe DLQ entries back to `queued_for_send` without changing the original idempotency key.
- `[x]` SMTP uncertain result enters DLQ with `safe_replay_allowed=false` and blocks automatic replay until manual provider outbox verification.
- `[x]` Retry exhaustion can mark mail tasks as `dead_letter` and create a DLQ entry.
- `[x]` Prometheus exposes `agent_mail_dlq_created_total` and `agent_mail_dlq_replay_total`.
- `[x]` Docker regression passed: `docker compose exec -T api python scripts/mail_m5_reliability_regression.py`.

### 13.8 Legacy Cleanup
- `[x]` 删除旧 `unified_agent.py / hybrid_router.py / unified_tools.py` LangGraph V1 栈。
- `[x]` 删除旧 `sensitive_workflow.py / workflow_store.py` SQLite workflow 栈和 `/workflows/sensitive-outbound*` 接口。
- `[x]` `app/graph.py` 收敛为共享 `get_llm()` client 工厂。
- `[x]` Docker 内重新验证 M1-M6、动态工具发现和跨域 DAG 闭环。
- `[x]` Task 7 retired the quarantined legacy orchestration fallback: ReAct exceptions now return typed recovery observations with renderer-owned output; active legacy tool/source/memory dependencies remain intentionally preserved.

## 14. EnterpriseRAG Dataset Governance
- `[x]` Replace historical mixed indexes with a reproducible canonical dataset -> deterministic slice -> shadow build -> gated publish workflow.

### 14.1 Canonical Dataset
- `[x]` Add canonical EnterpriseRAG-Bench parquet fetcher with SHA256 manifest.
- `[x]` Download and retain immutable `documents.parquet / questions.parquet` from the official dataset revision.
- `[x]` Pin upstream revision `69916e31c68aa5963c00248fd7f0bc12d04fd235` and record file sizes, checksums, and row counts: `511962` documents / `500` questions.

### 14.2 Deterministic Benchmark Slice
- `[x]` Add deterministic slice builder with stratified questions, required regression cases, and hash-ranked distractors.
- `[x]` Generate `bench-slice-v1` from canonical parquet files.
- `[x]` Store slice manifest: selected questions, expected docs, source revision, seed, policy, and checksums.

### 14.3 Shadow Index and Publish Gate
- `[x]` Add isolated shadow-index builder.
- `[x]` Add dense / sparse consistency validator.
- `[x]` Build `enterprise_rag_bench_slice_v1` with a dedicated sparse DB.
- `[x]` Reject publish when expected docs are missing, dense/sparse chunk IDs drift, metadata is incomplete, or `l3_*` regression docs leak into the index.
- `[x]` Run Docker benchmark smoke and switch live configuration only after validation passes.
- `[x]` Publish baseline: `2940` dense chunks / `2940` sparse chunks / `283` documents; retain legacy `enterprise_rag_bench_v2` for rollback.
- `[-]` Current smoke baseline: `average_doc_recall=1.0`, `average_evidence_fact_coverage=0.25`, `average_answer_fact_coverage=0.0`; next iteration must suppress cross-document fact noise and replace lexical-only answer coverage for Chinese summaries.

### 14.4 Regression Isolation
- `[x]` Replace mutable L3 RAG seed documents with a read-only fixture from the deterministic EnterpriseRAG slice.
- `[x]` Ensure concurrency regression never appends test documents to the live EnterpriseRAG index.

## 15. Mail Source Resolution Hardening
- `[x]` Explicit references to a prior assistant answer default to `recipient_ready_summary` instead of copying the conversational answer into the outbound body.
- `[x]` Preserve explicit verbatim forwarding through `verbatim_copy` only when the user requests exact text.
- `[x]` Fail safely when recipient-ready authoring returns an empty body; do not create an empty pending-confirmation draft.
- `[x]` Add Docker regression coverage for reference resolution, recipient-ready authoring, direct inline body, persistent draft idempotency, and cross-user isolation.
- `[x]` Replace the boolean-only recent-answer reference path with structured LLM source resolution: `selected_candidate_ids / source_mode / compose_mode / confidence / reason`.
- `[x]` Keep recent reusable assistant answers as stable `candidate_id / source_turn_id` candidates and allow semantic selection across the recent conversation window.
- `[x]` When semantic resolution is unavailable, use only narrow high-confidence safe fallback signals; otherwise ask for clarification instead of guessing.
- `[x]` Allow L0 Router `intent=mail_action` to enter Mail Agent when the legacy keyword gate misses; classify the requested capability before Mail Agent resolves or clarifies the body source.
- `[x]` Keep degraded routing safe: if the semantic Router is unavailable, use only a recipient-address plus high-confidence referential-source signal before entering Mail Agent.
- `[x]` Make the governance-console URL deployment-aware: local development keeps `8512`, while the remote deployment exposes the independent governance surface through the single public gateway path `8080/governance`.
- `[x]` Bind the remote governance console to its allowed tenant/workspace; `admin / approver` can review cross-user tasks inside that workspace while cross-workspace access remains blocked.
- `[-]` Add conversation-scoped `ContentArtifact` records and semantic `mail_resolve_body_sources(...)` observations for non-adjacent, multi-turn, and ambiguous references. Current slice now reuses actor-scoped durable `answer_artifact` records as outbound source candidates and preserves the selected artifact through post-confirm renderer/task creation.
- `[ ]` Add explicit artifact selection before any cross-conversation source retrieval.

## 16. Continuation State Foundation Closeout
- `[x]` Add actor-scoped PostgreSQL `pending_objects` registry reads for active conversation state.
- `[x]` Register mail drafts, confirmations, source/recipient/body clarifications, DLP tasks, domain tasks, upload artifacts, and answer artifacts.
- `[x]` Resolve `confirm / cancel / choose_source / provide_missing_field / patch / new_task` before opening a new planner run.
- `[x]` Keep open-ended draft patch classification structured and bounded: classifier output is state only, never user-visible wording.
- `[x]` Reject ungrounded classifier fields: recipient, subject, and body overrides must be present in the current user message.
- `[x]` Fail safely when the semantic continuation classifier times out: do not mutate the old draft or enter the planner; emit typed ambiguity for renderer clarification.
- `[x]` Expose actor-scoped safe state snapshots through `GET /agent/pending-objects`.
- `[x]` Add trace guard `pending_confirmation_bypassed_by_new_side_effect`.
- `[x]` Remote Docker regression covers refresh recovery, semantic patch, new-task isolation, task registration, upload/answer artifact registration, cross-user isolation, and trace guard.
- `[-]` Keep richer conversation-scoped `ContentArtifact` storage and explicit cross-conversation artifact selection in the next Mail Source Resolution iteration.

## 17. Phase 3 RAG Recovery And Full-Stage Observability
- `[-]` Stabilize the existing EnterpriseRAG pipeline before expanding platform capabilities.

### 17.1 Active Index Contract
- `[x]` Expose active dataset root, dense collection, sparse DB path, latest manifest run, content hash, and parity counts in query/admin diagnostics.
- `[x]` Ensure benchmark, audit, and ops scripts read the configured sparse DB path instead of the stale default path.
- `[x]` Mark stale default indexes as non-authoritative and archive them only after diagnostics are stable.

### 17.2 Progressive RAG Observation Disclosure
- `[x]` Return compact `retrieval_summary / evidence_manifest / selected_evidence` observations to the renderer.
- `[x]` Keep raw chunks, full citations, rerank rows, and heavy debug payloads behind debug/admin detail reads.
- `[x]` Keep `/enterprise-rag/query` and `/agent/chat` semantically aligned through the same observation contract.

### 17.3 Full-Stage Observability
- `[ ]` Record `correlation_id / actor_context / route / agent / tool / provider / observation_type`.
- `[ ]` Record stage latency for `router / planner / context_bundle / dense / sparse / neighbor / merge / rerank / evidence_pack / answer_compose / final_renderer / total`.
- `[x]` Record EnterpriseRAG LLM token usage, timeout stage, fallback strategy, active index version, and correlation id.
- `[x]` Extend the existing Prometheus/Admin surfaces; do not add a second tracing stack.
- `[x]` Propagate one EnterpriseRAG correlation id through direct query, async worker result, `/agent/chat` fast path response, and typed-observation provenance.
- `[-]` Continue propagating the same correlation id and normalized stage names across non-RAG Supervisor/DAG domain agents and async workers.

### 17.4 Remote Docker Regression
- `[x]` Regress GCP onboarding, MedThink EU failover, perf-canary, and multipart upload limits on the remote Docker deployment.
- `[x]` Distinguish retrieval miss, dense/sparse drift, rerank/evidence loss, and answer-composition weakness.
- `[x]` Add opt-in progressive async status for heavy RAG requests through the existing `enterprise_rag_queue`.
- `[x]` Bind async RAG progress metadata to actor context and reject cross-tenant or ownership-unknown status reads.

## 18. Proactive Agent Platform Capabilities
- `[ ]` Expand the existing Supervisor platform with bounded proactive-work and integration capabilities after Phase 3 stabilizes.

### 18.1 Triggered Agent Work
- `[ ]` Add `TriggerDefinition`: `trigger_id / tenant_id / workspace_id / trigger_type / schedule_or_event / target_capability / input_payload / status / retry_policy`.
- `[ ]` Route triggers through Supervisor and DAG executor; never invoke side-effectful tools directly from a trigger.
- `[ ]` Reuse PostgreSQL task state and Celery workers for mail digest schedules, DLP pending reminders, meeting reminders, and RAG parity audits.

### 18.2 Reply Channel Separation
- `[ ]` Add `ReplyChannel`: `channel_type / destination / actor_context / correlation_id / payload / delivery_status`.
- `[ ]` Keep formal outbound business email inside Mail Agent with DLP, approval, confirmation, and SMTP governance.
- `[ ]` Use Reply Channels for progress, reminders, completion, recovery, and governance notifications.
- `[ ]` Start with `web_workspace` and `governance_console`; add webhook or IM adapters only after the contract is stable.

### 18.3 OpenAPI Tool Factory
- `[ ]` Generate read-only tool manifests from OpenAPI operations and register them through the existing dynamic tool registry.
- `[ ]` Require a governance overlay: `read_only / side_effectful / requires_confirmation / permissions / tenant_scope / timeout / retry_policy / observation_type`.
- `[ ]` Reject generated write operations unless an explicit governance overlay and confirmation boundary exist.
- `[ ]` Pilot with one Workspace/Document provider; keep existing Mail, DLP, and Tencent Meeting adapters unchanged unless a concrete need appears.

### 18.4 Scope Guardrails
- `[x]` Reuse existing Supervisor, DAG executor, registry, Prometheus/Admin, PostgreSQL task state, and Celery workers.
- `[x]` Avoid a second scheduler, a second tracing stack, or a parallel tool registry in the first implementation.
- `[x]` Keep new platform contracts small and provider-neutral; add adapters only for verified scenarios.

## 19. Communication Copilot Refactor Execution
- `[-]` Execute the approved Communication Copilot implementation plan from `docs/aegis/plans/2026-06-17-communication-copilot-implementation.md`.
- `[x]` Task 0A legacy carrier inventory report is complete in `problem_todolist.md`: carriers are classified as `dead_identity`, `safe_delete`, `compat_shim`, or `active_runtime_dependency`.
- `[x]` Task 0A stayed inventory-only: no runtime code changes and no front-end style changes.
- `[x]` Active Mail/DLP/RAG dependencies are protected by named regressions before any removal: mail authoring/reference/confirmation regressions, governed delivery/runtime regressions, and EnterpriseRAG/context regressions.
- `[x]` Task 0B safe-delete/quarantine complete with verification concern: `dead_identity` and `safe_delete` carriers were rewritten/pruned, `compat_shim` fallback surfaces are explicitly marked with replacement owner/removal trigger, and `active_runtime_dependency` carriers were left intact. Docker EnterpriseRAG regression could not run because `/app/questions.parquet` was missing in the API container.
- `[x]` Task 1 complete: top-level docs now frame the product as a Docker-first Communication Copilot with Mail Agent ownership for governed external communication; EnterpriseRAG, meeting, DLP, memory, uploads, and governance remain supporting capabilities.
- `[x]` Task 1 wording test: top-level docs must not make generic conversation or retrieval the product center, must keep local Docker Compose as the acceptance baseline, and must state that this docs-only slice has no runtime impact or compatibility-shim retirement.
- `[x]` Task 8 complete without staging/commit: generic thread-native closeout now uses `communication_brief` and no longer exposes raw assistant answer artifacts; explicit prior-answer selection remains as a named compatibility shim with `communication_brief` as the replacement owner.
- `[x]` Task 9 acceptance evidence recorded: `communication_copilot_regression.py --case acceptance_summary` now reports thread inbox, active thread, brief persistence, grounded reply, meeting escalation, governed send, recovery, and retirement coverage; Docker Communication Copilot, M6 governance, cross-domain workflow, EnterpriseRAG smoke, and 8511 browser smoke passed. Cross-domain workflow now verifies the M6 contract: medium risk returns `sender_review_required`, sender-safety-confirm queues send, and final delivery is `sent`; EnterpriseRAG sample quality remains a residual risk.
