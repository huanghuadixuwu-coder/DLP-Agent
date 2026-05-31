# Enterprise Agent ToDo

更新时间：2026-05-25
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
- `[-]` 仍需继续降低少数 fallback / legacy 文案路径，保持 observation-first。
- `[ ]` 增加更多等价新问法回归，避免邮件草稿和确认路径依赖固定表达。

## 5. Memory / ReAct
- `[x]` conversation recent / summary / workspace memory / user model 已作为 ReAct memory substrate。
- `[x]` structured turn memory、pending reflection candidate、memory safety boundary 已落地。
- `[x]` memory 只能补上下文、偏好、项目约定；企业事实仍必须由 EnterpriseRAG citations 支撑。
- `[x]` pending memory review 后端最小能力已落地。
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

## 12. 下一步优先级
1. `[-]` Mail Agent V2 规格清理与边界锁定：采用渐进式披露规格包，Mail Agent core 与 Workspace/Document Agent 边界已锁定。
2. `[x]` Mail Harness 第一阶段：fake provider、状态回放、失败注入、重复发送防护、并发隔离。
3. `[ ]` Persistent Mail Draft：将 pending draft 从 conversation debug 迁移到持久 draft state。
4. `[ ]` Mail provider abstraction：保留当前 IMAP/SMTP，新增 fake provider contract。
5. `[ ]` 用当前真实 `.env` 凭据人工演练一次跨域闭环：创建真实腾讯会议 -> 真实 SMTP 发送邀请邮件；必要时先用测试收件箱。
6. `[ ]` 接入或明确选择真实 calendar provider；没有 provider 时继续保持 provider boundary，不伪造空闲时间。
7. `[ ]` 扩大 EnterpriseRAG benchmark 与 Agent trace evaluation 样本。
8. `[ ]` 清理剩余 legacy/fallback 文案路径，继续收口 observation-first。
9. `[ ]` 增加真实 provider retry / circuit breaker / manual handover 的更大样本回归。

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

### 13.3 UI Surface Separation
- `[x]` UI 边界已锁定：`8511` 为普通用户工作台，`8512` 为治理与运维台。
- `[x]` Grafana 暂缓；Prometheus 继续作为指标采集后端。
- `[x]` 风险分层已锁定：`low` 自动发送、`medium` 发件人二次确认、`high` 治理升级、`critical` 默认阻断。
- `[ ]` 从 `8511` 移除 high-risk 例外审批/驳回按钮、raw tool calls、trace JSON、token/cost、队列和故障注入视图。
- `[ ]` `8511` 保留 inbox/thread、draft review、显式确认、发件人外发安全确认、简化任务进度和 renderer 生成的失败提示。
- `[ ]` 新增 `8512` 治理台：high-risk 例外审批/驳回、审计时间线、provider/queue health、recovery observation、DLQ replay、Harness 结果、trace 和成本诊断。
- `[ ]` Docker 内验证：`medium` 可在 `8511` 二次确认，`high` 只能在 `8512` 例外审批，`critical` 默认阻断。
