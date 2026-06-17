# Problem Todolist

Status: active
Date: 2026-06-01

This file is a problem-driven tracker. It is intentionally separate from
`todolist.md`: the roadmap file tracks milestones, while this file tracks
observed failures, current status, and repair tasks.

Verification rule:
The current local Docker Compose environment is the only acceptance baseline.
Local Python remains out of bounds. Historical remote-Docker findings stay in
this tracker as evidence, but they are no longer the active verification target
while the remote server is offline.

Status legend:

- `[x]` done and verified
- `[-]` partially implemented or in progress
- `[ ]` open

## Plan Alignment Check

Status: `[x]` aligned

Conclusion:
The continuation state foundation and main Mail Agent recovery path are now
stable enough to move the primary focus to Phase 3. The highest priority is the
EnterpriseRAG runtime contract: active-index visibility, progressive observation
disclosure, full-stage latency/token observability, and typed degradation.
Proactive Agent capabilities follow after this baseline is measurable.

Planned execution order:

1. `[-]` Phase 3 RAG runtime contract: finish active-index diagnostics, progressive observation disclosure, and stage-level latency/token tracing.
2. `[-]` UI/debug split: keep user progress on the workspace and expose raw trace, cost, token, and recovery detail through `8080/governance`.
3. `[ ]` Triggered Agent work: route scheduled/event work through Supervisor and DAG execution.
4. `[ ]` Reply Channel separation: keep governed business email inside Mail Agent and move system notifications into channel adapters.
5. `[ ]` OpenAPI tool factory: pilot read-only Workspace/Document tool generation through the existing registry.

## Full Repair Iteration Plan

目标：
把当前问题从“按症状补规则”收口为一条稳定主链：
`pending object registry -> continuation resolver -> typed observation -> renderer -> trace/evaluation`。
代码只负责状态、来源边界、权限、确认、工具调用和 debug；用户可见回答、澄清、草稿继续由 renderer 基于 observations 生成。

### Phase 1. Continuation State Foundation

Status: `[x]` done and remote-Docker/live-regressed

Scope:

1. `[x]` 建立 `PendingObject` 抽象，并覆盖 mail/domain confirmation。
2. `[-]` 扩展为持久化 `pending_object_registry`，覆盖 `answer_artifact / source_clarification / mail_draft / meeting_confirmation / dlp_task / domain_task / upload_artifact`。当前已落地 mail/domain confirmation 与 source / recipient / body clarification。
3. `[-]` 让 source clarification 成为可恢复对象：用户回答 “GCP / MedThink / 第一个 / 上一个方案” 时，绑定到上一条澄清而不是重新路由。
4. `[-]` 支持 `confirm / cancel / patch / choose_source / provide_missing_field / new_task` 等 continuation decision。当前已覆盖 `confirm / cancel / choose_source / provide_missing_field`，通用 patch 仍待收口。
5. `[x]` 将 consumed confirmation 显式写入状态；邮件重复确认继续由 draft idempotency key 安全重放，会议确认消费后落为 `consumed`。

Acceptance:

1. `[x]` `create meeting -> 创建/确认` 能先消费最新 pending meeting confirmation，再进入 worker。
2. `[x]` `帮我发送文段为“测试”` 缺收件人时不使用默认邮箱。
3. `[x]` `MedThink answer -> ask send -> system asks which source -> user says GCP` 能正确选择 GCP source。
4. `[-]` 页面刷新后 pending mail draft / mail confirmation 可继续处理，domain confirmation 在丢失 turn debug 后仍可恢复；DLP task 与完整前端刷新恢复仍需继续回归。

### Phase 2. Mail Source And Authoring Contract

Status: `[x]` backend main source/body/review contract implemented and remote-Docker-regressed; live UI preview acceptance is tracked separately in Issue 16

Scope:

1. `[-]` 将 current-turn inline body 解析为 `content_candidates`，避免正文被误判为附件或引用源。
2. `[x]` 将 assistant prior answers、uploaded artifacts、mail threads、meeting results 都统一成 source candidates。
3. `[-]` Mail Agent 主链不直接拼正文；只输出 `source_selection / authoring_instruction / constraints / missing_fields / confirmation_required`。Legacy async outbound 已迁移，通用安全 fallback 仍需继续审计。
4. `[-]` Renderer 根据 selected source 生成 recipient-ready body，并移除“根据当前可确认的信息”等内部检索口吻；DLP review content 已与 source artifact 分层。在线 authoring 在 LLM provider 限流时仍需更准确的 recovery observation。
5. `[x]` Source clarification、recipient clarification、body clarification 都持久化为 pending object。
6. `[x]` Source resolver 在 LLM 超时时先尝试通用 entity/topic anchor fallback；只有唯一候选明显领先才自动选择，否则继续澄清。
7. `[x]` `meeting_result` 作为 source candidate 接入 source resolver 与 outbound delivery，进入 `recipient_ready_summary` authoring，不直接把 JSON 塞进正文或 DLP preview。

Acceptance:

1. `[-]` 用户说“把该信息发送给...”时，系统能定位最近相关 answer artifact；ambiguous source now clarifies and can bind a later “GCP” answer, but broader wording coverage remains open.
2. `[ ]` 用户说“注意不要包含无关内容”时，邮件正文只包含被选中 answer artifact 的业务结论，不包含系统说明、引用证据标题或调试语。
3. `[x]` 没有收件人、没有内容、source ambiguous 时只产生 typed clarification，不生成可发送任务；source/recipient/body clarification、mail thread 与 meeting artifact 合同路径均已覆盖 Docker 回归。
4. `[x]` 脱敏预览和 DLP review content 只审查最终 `resolved_body`，source artifact 作为 provenance/debug 保留，不再拼入正文审查内容造成重复。
5. `[x]` LLM source resolver timeout 时，MedThink/GCP 这类有明确主题锚点的 prior answer 可安全选择；无唯一锚点时仍然澄清。
6. `[x]` Meeting result source regression: `meeting_result -> recipient_ready_summary -> provenance/debug only source_artifacts -> empty pre-render review_content` 通过远端 Docker 回归。

Latest Phase 2 source-candidate coverage, 2026-06-02:

- `[x]` Assistant prior answers, uploaded artifacts, meeting results, and inbound mail threads are now represented as unified source candidates.
- `[x]` Remote Docker regression covers `assistant_last_answer -> recipient_ready_summary`.
- `[x]` Remote Docker regression covers `meeting_result -> recipient_ready_summary -> provenance/debug-only source_artifacts -> empty pre-render review_content`.
- `[x]` Remote Docker regression covers `mail_thread -> recipient_ready_summary -> provenance/debug-only source_artifacts -> empty pre-render review_content`.
- `[x]` Meeting invitation draft now follows the same renderer contract: pre-render `resolved_body` and `review_content` are empty, `compose_mode=recipient_ready_summary`, and full meeting details live in `reference_sources` plus provenance-only `source_artifacts`.
- `[x]` Remote Docker regression covers meeting invitation authoring contract and full cross-domain workflow: customer mail context -> meeting creation -> invitation mail -> DLP/SMTP.
- `[x]` Polish/rewrite draft actions now follow the same authoring contract: rule code only declares `reference_sources / body_sources / constraints`, while `resolved_body` and `review_content` remain empty until the renderer writes the draft.
- `[x]` Remote Docker regression covers `polish_body` from a mail-thread source as `recipient_ready_summary` with provenance-only `source_artifacts`.
- `[x]` Legacy async outbound fallback now routes through `_build_mail_action_plan -> mail renderer -> pending_confirmation`; it no longer creates a DLP task directly or bypasses recipient-ready body authoring.
- `[x]` Remote Docker regression covers the legacy async outbound entry: no immediate task is created, `pending_confirmation` is returned, and `resolved_body/review_content` come from the renderer.
- `[x]` Removed the unreachable legacy direct-DLP block from `_handle_async_outbound_agent_request(...)`; the old path can no longer silently reappear after the new renderer return flow.
- `[x]` Remote Docker compile and deterministic contract regressions still pass after dead-code removal: legacy async outbound, polish/rewrite, mail-thread source, and meeting invitation source contracts.
- `[x]` Online `assistant_last_answer -> recipient_ready_summary` strict regression passed after upstream recovery: internal retrieval framing is removed, `review_content == resolved_body`, and source artifacts remain provenance-only.
- `[x]` Deterministic remote Docker regression covers upstream authoring failure: the chain returns typed `dependency_failure`, keeps an `authoring_failed` draft, and creates no confirmation or task.
- `[x]` Phase 2 backend cleanup audit: no reachable legacy direct-DLP mail bypass remains. The remaining deterministic mail-renderer fallback and generic orchestration clarification are safety fallbacks, not business-path authoring shortcuts.
- `[x]` Issue 16 visible-layer acceptance complete: remote Streamlit `AppTest` renders one
  desensitized preview, and the public `8080/governance` gateway is healthy with the
  workspace-scoped pending task visible through the public API.

### Phase 3. RAG Observation Contract And Latency Recovery

Status: `[-]` observation, evidence-boundary, and typed timeout recovery complete; progressive async retrieval remains

Scope:

1. `[x]` EnterpriseRAG 输出 `enterprise_answer_observation`：`canonical_facts / answer_state / citations_brief / confidence / missing_aspects / source_doc_ids`。
2. `[x]` 将 `retrieval_stage_debug / rerank_debug / full snippets / raw citations` 移到 debug-only，不进入 final renderer 主输入。
3. `[x]` 给 RAG 各阶段加 latency：`dense / sparse / neighbor / merge / rerank / evidence_pack / answer_compose / renderer / total`。
4. `[x]` 重调 expansion gate：只有 answerability 不足或一侧检索失败时才二次扩展，不因候选数量阈值无脑扩大。
5. `[x]` RAG timeout 返回 `dependency_failure` observation，包含 `service / operation / retryable / fallback_strategy`，而不是把 HTTP timeout 原文给用户。

Acceptance:

1. `[-]` GCP onboarding、MedThink 已有 stage latency；multipart upload、perf-canary 仍待补齐。
2. `[x]` `/agent/chat` RAG renderer input token 明显下降，compact observation payload regression 已通过。
3. `[-]` GCP onboarding、MedThink 代表性回归答案不以 `...` 或 `…` 结尾；仍需扩大样本覆盖。
4. `[x]` RAG timeout 产生可恢复 `dependency_failure` observation，前端不再直接暴露裸 HTTP timeout。

### Phase 4. UI And Governance Split

Status: `[-]` substantially improved; CPU reranker and progressive async retrieval remain

Scope:

1. `[ ]` 8511 用户界面只展示用户侧对话、任务进度、必要确认和恢复入口。
2. `[ ]` Debug/Governance surface 展示 route、tool latency、renderer latency、token、cost、queue、DLP、trace。
3. `[ ]` Route UI 拆成 `routing_source / route_mode / router_intent / recommended_tool / degraded_from`，避免 `fast_router` 被误解为“快速完成”。
4. `[ ]` 邮件高风险审批、驳回、审计仍在治理台；普通用户只看到等待/失败/恢复状态。
5. `[x]` 远程部署中治理台不再依赖未开放的 `8081/8512`；公网 `8080/governance` 通过 gateway 代理到治理台。

Acceptance:

1. `[ ]` 8511 不暴露冗长 raw debug。
2. `[ ]` 治理台可查看 RAG timeout、worker failure、SMTP failure、DLP high-risk 等 recovery observations。
3. `[ ]` Latency 和 token 不再显示 0，且按 router/tool/renderer 分段。
4. `[x]` 公网环境下用户可以从 8080 工作台稳定打开治理入口，且链接不指向未开放端口。

### Phase 5. Legacy Rule/Text Cleanup

Status: `[x]` done and remote-Docker-regressed

Scope:

1. `[ ]` 扫描 `/agent/chat` 中所有用户可见 answer / clarification / draft 生成点。
2. `[ ]` 迁移 legacy business wording 到 typed observation -> final renderer。
3. `[ ]` 保留规则只用于安全边界、状态机、权限、来源选择、工具调用和 schema 校验。
4. `[ ]` Agent trace evaluator 检查：错误工具选择、漏确认、跨租户、误用 memory、无 citation 企业事实、失败无 recovery observation。

Acceptance:

1. `[ ]` 同一场景换问法不需要继续追加业务 `if/else`。
2. `[ ]` 副作用工具不会绕过 confirmation / DLP / permission。
3. `[ ]` 失败路径不假装成功，并可在 trace 中审计。

## 1. Continuation State Layer Missing

Status: `[-]` current-turn body path fixed; attachment boundary remains open

问题：
用户在多轮对话中经常使用“该信息”“上述解决方案”“确认”“创建”“GCP”等短表达来继续操作已有对象。当前系统缺少统一的 continuation state layer，导致邮件、会议、DLP、source clarification 和 task recovery 各自用局部规则判断，容易重新开新任务、丢失上一轮对象，或触发错误 fallback。

Current evidence:

- `[x]` Mail drafts are persisted in PostgreSQL through `mail_drafts`.
- `[x]` Recent assistant answers can be exposed as stable candidate IDs for source resolution.
- `[x]` Source resolver can semantically choose MedThink/GCP-like prior answer candidates.
- `[x]` Mail and domain confirmations are persisted in PostgreSQL `pending_objects`.
- `[x]` Pending confirmation resolution no longer competes with the DAG planner for confirmation continuations.
- `[x]` Mail source clarification is persisted in PostgreSQL `pending_objects` as a durable `source_clarification` object.
- `[x]` Missing-recipient and missing-body clarifications are persisted in PostgreSQL `pending_objects`.
- `[x]` Remote Docker regression verifies source clarification lifecycle: `needs_clarification -> choose_source -> consumed`.
- `[x]` Remote Docker regression verifies recipient/body clarification lifecycle: `needs_clarification -> provide_missing_field -> consumed`.
- `[x]` Remote Docker regression verifies confirmation lifecycle: mail confirmation persists as `pending_confirmation`; meeting confirmation becomes `consumed` after async task creation.
- `[x]` Remote Docker regression verifies domain confirmation recovery after process-local turn debug is cleared.
- `[x]` Remote Docker regression verifies explicit cancel becomes `cancelled`, while mail constraints such as “不要包含无关内容” are not misclassified as cancellation.

解决该问题的待办：

1. `[-]` Add `pending_object_registry` for `answer_artifact / source_clarification / mail_draft / meeting_confirmation / dlp_task / domain_task / upload_artifact`. Current durable registry covers mail/domain confirmation and source / recipient / body clarification.
2. `[-]` Store `object_id / object_type / conversation_id / actor_context / status / expires_at / salience / allowed_continuations / source_observation_ids`. Implemented for mail/domain confirmation and source / recipient / body clarification in PostgreSQL `pending_objects`.
3. `[-]` Add `continuation_resolver` that outputs `mode / continuation_type / object_id / confidence / missing_fields / reason_observation`. Current resolver covers confirm, cancel, choose_source, and provide_missing_field.
4. `[x]` Resolve pending confirmation before starting a new planner.
5. `[x]` Persist Mail source clarification as a pending object, so later answers such as “GCP” bind to the original clarification.
6. `[x]` Remote Docker regression: `MedThink answer -> ask send -> clarify GCP/MedThink -> user says GCP -> correct source selected`.
7. `[x]` Remote Docker regression: `create meeting -> confirm variants -> latest pending confirmation is consumed before planner`.
8. `[x]` Remote Docker regression: missing recipient/body clarification can be resumed and consumed from durable pending objects.
9. `[x]` Remote Docker regression: persistent mail draft survives process-local debug loss; patch and duplicate confirmation remain idempotent.
10. `[x]` Remote Docker regression: domain confirmation survives process-local debug loss and explicit cancel records `cancelled`.

## 2. Mail Default Recipient Fallback Is Unsafe

Status: `[x]` done for real mail chain; keep monitoring harness/lab-only defaults

问题：
真实邮件链路中仍存在默认收件人 fallback。缺邮箱时系统可能自动使用默认邮箱，造成错误外发风险。

Current evidence:

- `[x]` `_extract_destination_email(...)` no longer falls back to `DEFAULT_DLP_EMAIL`.
- `[x]` Missing recipient returns a mail clarification path with `missing_fields=["recipient"]`.
- `[x]` `DEFAULT_DLP_EMAIL` remains only in DLP scenario/lab fallback code, not in the real mail action resolver.

解决该问题的待办：

1. `[x]` Delete default recipient fallback from real mail send paths.
2. `[x]` Missing recipient returns typed observation with `missing_fields=["recipient"]`.
3. `[x]` Renderer generates the natural clarification from that observation.
4. `[-]` Any default email kept for tests/labs must remain outside the real mail action resolver.
5. `[x]` Docker regression: no-recipient send request does not create a sendable task.

## 3. Inline Body Parsing Still Relies On Fragile Patterns

Status: `[-]` partial

问题：
当前 inline body 提取仍依赖少量正则。表达稍有变化时会漏判，随后 Mail Agent 可能把正文误判为附件、引用源或缺失字段。

Current evidence:

- `[x]` Existing regression covers `文段是：“测试”`.
- `[x]` `文段为“测试”` is covered by `scripts/continuation_state_regression.py`.
- `[x]` `main.py` no longer uses fixed inline-body regex fallback for current-turn outbound candidates; current-turn body extraction goes through `ContentCandidate`.

解决该问题的待办：

1. `[x]` Add `message_content_parser` that outputs structured `content_candidates` with `source_type / content / provenance_span / confidence / allowed_uses`.
2. `[x]` Detect quoted current-turn inline text as `source_type=user_inline_text`; rules only validate boundaries and allowed uses.
3. `[ ]` Attachments enter candidates only from upload artifacts or explicit attachment-use selection.
4. `[x]` Mail Agent consumes `content_candidates`; current-turn outbound candidate collection no longer falls back to legacy raw-text scanning.
5. `[x]` Docker regression: `文段为` and existing `文段是` variants enter the structured path.

## 4. Planner Runs Before Existing Pending Confirmation

Status: `[-]` confirmation and cancel slice implemented

问题：
当前 `/agent/chat` 中 DAG planner 先于 pending confirmation resolution 执行。用户的确认类短句可能被当成新请求，造成会议或邮件状态接不上。

Current evidence:

- `[x]` Pending confirmation resolution now runs before `plan_multi_agent_dag_request(...)` in `/agent/chat`.
- `[-]` Confirmation, cancellation, source clarification, and missing-field continuation now go through `continuation_resolver`; generic edit/patch handling is not complete.

解决该问题的待办：

1. `[x]` Establish entry order: actor context -> pending object resolution -> continuation execution -> new task planner.
2. `[-]` Confirmation consumption validates actor scope through existing draft/task stores and idempotency keys.
3. `[x]` Consumed confirmations are protected by idempotency and explicitly recorded in `pending_objects`.
4. `[ ]` Trace evaluator checks that pending confirmation is not bypassed by a new side-effectful plan.

## 5. RAG Query Latency Is Too High

Status: `[x]` post-confirm renderer contract fixed; broader source selection remains in Mail Source Resolution Hardening

问题：
EnterpriseRAG 查询在远端 Docker 中耗时偏高。直接 `/enterprise-rag/query` 的 GCP 问题约 23-29 秒，经过 `/agent/chat` 后可到 80 秒以上。

Current evidence:

- `[x]` Remote raw API diagnostic captured direct RAG at about 23-29 seconds.
- `[x]` Remote raw `/agent/chat` MedThink diagnostic captured about 83 seconds and around 18k input tokens.
- `[x]` Stage-level RAG latency now records planning, context bundle, dense, sparse, neighbor expansion, merge, rerank, evidence pack, answer compose, renderer, and total timing.
- `[x]` Agent-path GCP latency dropped from about 50 seconds to about 18.5 seconds after bounded rerank, compact observations, observation-only composition, router precheck, and async memory-vector persistence.
- `[x]` Agent-path MedThink latency dropped from about 46.7 seconds to about 21.2 seconds after anchored fact routing avoided the unnecessary LLM-router and ReAct detour.
- `[x]` Latest online GCP and MedThink requests both use `routing_source=fast_router_heuristic`; the remaining dominant cost is CPU cross-encoder reranking at roughly 7-10 seconds.

解决该问题的待办：

1. `[x]` Add stage latency: `dense_ms / sparse_ms / neighbor_ms / merge_ms / rerank_ms / evidence_pack_ms / answer_compose_ms / final_renderer_ms / total_ms`.
2. `[x]` Separate RAG debug payload from renderer observation payload.
3. `[x]` Avoid double user-visible answer generation between RAG answer composer and final renderer.
4. `[x]` Add reranker soft budget telemetry and typed `dependency_failure`; reranker exceptions safely degrade to heuristic reranking.
5. `[ ]` Add async task/progressive status for heavy RAG queries.
6. `[-]` Docker benchmark: GCP and MedThink representative questions record stage latency; multipart upload and perf-canary remain.

## 6. RAG Expansion Gate Too Easy To Trigger

Status: `[x]` done and remote-Docker-regressed

问题：
当前 GCP query direct RAG 已触发 expanded retrieval，`dense_hits=80`、`sparse_hits=60`、`rerank_backend=cross_encoder`。在 CPU-only Docker 环境中，这会显著拉长响应时间。

Current evidence:

- `[x]` Remote diagnostic captured `expansion_reason=merged_candidates_below_rerank_budget`.
 - `[x]` Expansion still produced only 8 merged/reranked evidence rows, suggesting limited benefit.
- `[x]` Bounded rerank candidate budgets and citation-aware expansion gates prevent the GCP case from expanding without evidence benefit.

解决该问题的待办：

1. `[x]` Recalibrate expansion gate around answerability/fact coverage instead of only candidate count.
2. `[x]` Use bounded rerank candidate budgets for small / medium / large / expanded profiles.
3. `[x]` Suppress expansion when first-pass citations are sufficient.
4. `[ ]` Output `expansion_cost_estimate` and `expansion_benefit_signal`.
5. `[ ]` Regression: GCP case reduces unnecessary expansion without answer regression.

## 7. RAG Answer May Surface Truncated Or Compressed Text

Status: `[x]` done and remote-Docker-regressed

问题：
用户看到过答案末尾出现省略号。远端原始 API 复现中 direct response 没有省略号，说明省略号可能来自 LLM 截断、frontend/history display、或 compacted observation 被二次当作答案。

Current evidence:

- `[x]` Raw API diagnostic did not end with ellipsis in the latest run.
- `[x]` Codebase contains many `compact_text(...)` preview paths that append `...`.
- `[ ]` No regression currently compares raw API answer with displayed Streamlit answer.

解决该问题的待办：

1. `[ ]` Separate `answer_text`, `observation_summary`, and `debug_preview`.
2. `[ ]` Final renderer uses full facts and concise citations, not compacted answer summaries.
3. `[ ]` Add postcheck: visible answer must not end with `...` or `…` unless evidence contains that ending.
4. `[ ]` API/UI comparison regression for representative RAG answers.

## 8. Fast Route Display Is Misleading

Status: `[-]` typed recovery complete; progressive task status remains open

问题：
8511 右侧 Route 显示 `fast_router`，用户容易理解为低耗时路径。实际上它只是 routing source，不说明业务路径、degraded 状态、或是否走 heavy RAG pipeline。

Current evidence:

- `[x]` UI displays `Route fast_router`.
- `[x]` Same route can still take over 30 seconds.

解决该问题的待办：

1. `[ ]` UI displays `route_mode / router_intent / routing_source / degraded_from / recommended_tool` separately.
2. `[ ]` EnterpriseRAG fast path displays structured `fast router + heavy RAG` state.
3. `[ ]` Split latency into router/tool/renderer latency.
4. `[ ]` Keep cost, token, and raw trace in governance/debug surfaces.

## 9. RAG Observation Payload Too Heavy

Status: `[x]` resolved on remote Docker

问题：
EnterpriseRAG payload contains debug, rerank rows, full content, and memory context. When `/agent/chat` sends this to the final renderer, token input becomes too large.

Current evidence:

- `[x]` Remote `/agent/chat` MedThink query reached about 18k input tokens.
- `[x]` Direct RAG is much faster than the `/agent/chat` wrapped path.
- `[x]` Compact renderer observation removed full debug rows and duplicated fact payloads from final-renderer input.

解决该问题的待办：

1. `[x]` Define compact `enterprise_answer_observation` contract with `canonical_facts / answer_state / citations_brief / source_doc_ids / context_sources`.
2. `[x]` Move `retrieval_stage_debug / rerank_debug / full_content / raw citations` to debug-only fields.
3. `[x]` Final renderer reads only compact observation and concise citations.
4. `[x]` Agent trace records renderer token usage and cost.

## 10. EnterpriseRAG Answer Ownership Is Split

Status: `[-]` Agent path fixed; direct endpoint compatibility remains

问题：
EnterpriseRAG 当前既在 `answer_composer` 中生成答案，又在 `/agent/chat` final renderer 中再次生成最终答复。用户可见答案归属不清晰。

Current evidence:

- `[x]` Direct `/enterprise-rag/query` returns an answer.
- `[x]` `/agent/chat` fast enterprise path turns RAG result into observation and calls final renderer.

解决该问题的待办：

1. `[x]` RAG tool outputs evidence/facts/answer_state in Agent mode; final renderer outputs the user-visible answer.
2. `[ ]` `/enterprise-rag/query` uses the same renderer contract internally where possible.
3. `[ ]` Remove or downgrade user-visible hard template output to structured answer state.
4. `[ ]` Regression: same facts through `/enterprise-rag/query` and `/agent/chat` produce semantically consistent answer.

## 11. RAG Timeout Handling Is Not User-Friendly

Status: `[ ]` open

问题：
当 RAG 查询超时时，用户看到 `API request timed out`，缺少 recovery observation、阶段状态和可重试路径。

Current evidence:

- `[x]` UI showed `API request timed out. Check whether the api service is healthy.`
- `[x]` Injected remote Docker regression verifies retrieval timeout returns a retryable `dependency_failure` observation.
- `[x]` Injected remote Docker regression verifies reranker timeout degrades to heuristic reranking without pretending success.
- `[ ]` Heavy RAG query does not yet return progressive task status.

解决该问题的待办：

1. `[-]` Add request budget and stage timeout for RAG query. Reranker soft-budget telemetry is implemented; async hard cancellation remains future work.
2. `[x]` Timeout returns typed failure observation with `service=enterprise_rag / operation / fallback_strategy / retryable=true`.
3. `[ ]` Heavy query can enter async task with UI progress.
4. `[ ]` Governance console records timeout stage and trace.

## 12. Legacy Rule/Text Paths Remain In Main Agent

Status: `[ ]` open

问题：
主链仍有局部规则、fallback 文案、legacy planner 预判逻辑。它们会让新问法泛化不稳定，也和 `总体要求.md` 的 observation-first 目标冲突。

Current evidence:

- `[x]` `_build_task_agent_response` and related paths still produce synthetic rule results.
- `[x]` Mail/body/context decisions are spread across `main.py`, `outbound_delivery.py`, and `source_resolver.py`.

解决该问题的待办：

1. `[ ]` Scan all user-visible answer/clarification/draft generation points in `/agent/chat`.
2. `[ ]` Migrate business wording output to observation -> renderer.
3. `[ ]` Keep rules only for state, permissions, constraints, source boundaries, tool calls, and safety checks.
4. `[ ]` Regression: same scenario with different wording does not require new business if/else.

## 13. LangSmith Tracing Configuration Produces Remote Noise

Status: `[x]` done

问题：
远端 Docker 回归中出现 LangSmith `401 Unauthorized`。当前业务请求仍能成功，但无效 tracing 上报会制造日志噪声和额外网络开销。

Current evidence:

- `[x]` Remote Docker regression logged `LangSmithAuthError: Authentication failed`.
- `[x]` Mail and DAG business regressions still completed successfully.

解决该问题的待办：

1. `[x]` 明确远端当前不需要 LangSmith tracing。
2. `[x]` 关闭远端 `LANGSMITH_TRACING`，并将默认值改为 false。
3. `[x]` 保持业务链路在 tracing 不可用时不受影响。

## 14. RAG Evidence Boundary Can Promote Questions Or Unrelated Tenant Facts

Status: `[x]` done and remote-Docker-regressed

问题：
MedThink 查询曾命中同一邮件线程中的澄清问题清单，以及无关 `FinServX`
runbook。旧 evidence pack 会把问句当作 canonical facts，并把通用 runbook 的
RTO/RPO 当作 MedThink 的已确认约束。索引数据实际包含 MedThink 回复，但 Gmail
内容是字符串化列表，导致 source-aware chunking 退化为 fixed-window。

Current evidence:

- `[x]` Remote sparse DB and deterministic parquet slice contain the authoritative MedThink reply.
- `[x]` The expected answer is present: `EU primary -> EU warm standby -> US emergency failover`, `RPO 15 minutes`, `RTO 30 minutes`, and a pre-approved US emergency window capped at `4 hours`.
- `[x]` Remote trace proved old citations promoted unresolved questionnaire lines and unrelated `FinServX` facts.
- `[x]` Remote deterministic slice reindex completed with `3047` dense chunks and `3047` sparse chunks across `283` documents, including `212` Gmail thread-block chunks.
- `[x]` Remote validator confirms dense/sparse parity, required metadata, and expected-document coverage with no forbidden test documents.
- `[x]` Online MedThink regression returns the authoritative hierarchy, RPO, RTO, and 4-hour cap without unresolved questions, contact-list noise, or `FinServX` contamination.

解决该问题的待办：

1. `[x]` Normalize serialized list-like source cells before chunking and decode escaped newlines for legacy indexed fragments.
2. `[x]` Reject unresolved interrogative sentences from canonical facts.
3. `[x]` Extract distinctive query entity anchors and demote unrelated-document facts when an aligned source exists.
4. `[x]` Add bounded adjacent-chunk recall for entity-aligned documents so a question hit can recover the reply block.
5. `[x]` Rebuild the remote deterministic slice and validate dense/sparse parity.
6. `[x]` Remote Docker regression: MedThink facts include hierarchy, RPO, RTO, and 4-hour cap; canonical facts exclude question lines and `FinServX`.

## 15. Active EnterpriseRAG Rebuild Is Not Zero-Downtime

Status: `[ ]` open

问题：
当前 `reset=True` 重建会先清空 active Chroma collection 和 sparse DB，再逐批写入新
索引。CPU-only bge-m3 重建期间，线上 RAG 会经历空索引或部分索引窗口。该行为适合
开发恢复，不适合企业环境中的在线重建。

Current evidence:

- `[x]` Remote rebuild progress can be observed through Celery state, Chroma `count()`, sparse row count, and worker logs.
- `[x]` Current rebuild temporarily exposes partial dense counts while sparse remains empty until dense upsert completes.

解决该问题的待办：

1. `[ ]` Rebuild into versioned shadow Chroma collection and shadow sparse DB.
2. `[ ]` Validate dense/sparse parity, expected doc coverage, and representative benchmark cases before activation.
3. `[ ]` Store active index pointer/version outside process memory.
4. `[ ]` Atomically switch query traffic to the validated shadow pair.
5. `[ ]` Keep the previous index version for rollback and garbage-collect only after a retention window.

## 16. Mail DLP Preview Duplicates Authored Body And Source Artifact

Status: `[x]` done and remote-Docker/UI-regressed

问题：
用户先让 RAG 回答 MedThink 故障转移问题，再说“将该信息发送到
1136732521@qq.com”。系统确认发送时，脱敏预览中出现两遍内容：第一遍是较干净的
邮件正文，第二遍是原始 assistant answer artifact。根因不是 RAG 检索错误，而是邮件
authoring 与 DLP review source 没有分层：`delivery_body` 已经是 renderer 生成的待发送
正文，`selected_candidate.content` 又被 `build_review_content(...)` 追加进审查内容。

Current evidence:

- `[x]` 用户可见确认阶段已能选中上一轮 MedThink answer artifact。
- `[x]` 脱敏预览显示“精简正文 + 原始答案”两份近似相同内容。
- `[x]` 代码中 `build_review_content(delivery_body, selected_candidate, attachment_candidate)` 会在 `selected_text != body` 时追加 `selected_candidate.content`。
- `[x]` DLP 审查输入明确区分 `body_for_sending`、`source_artifact`、`provenance/debug`。
- `[x]` Remote Docker regression confirms `review_content == resolved_body` for `RAG answer -> 将该信息发送到邮箱`.

解决该问题的待办：

1. `[x]` 将 mail plan 的可发送正文、审查正文、来源证据拆成独立字段：`resolved_body / review_content / source_artifacts / provenance_refs`。
2. `[x]` `build_review_content(...)` 默认只使用最终 `resolved_body` 和明确要求审查的附件内容；source artifact 只作为 provenance/debug。
3. `[x]` 当 `selected_candidate.kind=assistant_last_answer` 且 renderer 已生成 `body_for_sending` 时，不再把原始 assistant answer 拼入 DLP preview。
4. `[x]` 增加 Docker 回归：`RAG answer -> 将该信息发送到邮箱 -> 脱敏预览正文只出现一次`。
5. `[x]` 增加回归：`注意不要包含无关内容` 时，邮件正文不包含“根据查询结果/根据当前可确认的信息/引用证据/调试语”。

Latest live sample, 2026-06-02:

- `[x]` User asked the agent to send a MedThink RAG answer to `1136732521@qq.com`; the confirmation/desensitized preview displayed two versions of the same content: the clean authored mail body plus the original assistant answer artifact.
- `[x]` This is a mail authoring/review-boundary failure, not a retrieval failure: RAG selected the right MedThink facts, but the review surface mixed final body and source artifact.
- `[x]` Backend contract regression now requires `review_content == resolved_body`; `source_artifacts` and `provenance_refs` must remain debug/provenance-only.
- `[x]` Remote Docker end-to-end regression now runs
  `answer artifact -> send that information -> confirmation -> DLP worker -> pending_approval`
  and verifies `message_raw == resolved_body`, with the MedThink failover fact appearing once
  in both raw and redacted preview.
- `[x]` Remote Streamlit `AppTest` renders the governance console and verifies exactly one
  matching `脱敏预览` widget, one failover-fact occurrence, and `[PHONE]` redaction.
- `[x]` Public API and gateway check confirm the same pending task is visible through the
  configured governance workspace, `http://118.196.142.222:8080/governance/` returns `200`,
  and `http://118.196.142.222:8080/governance/_stcore/health` returns `ok`.

## 17. Governance Console Public Port Is Not Reachable

Status: `[x]` done and remote-public-regressed

问题：
远程云服务器当前公网使用 `8080` 访问 8511 用户工作台，但治理台配置仍指向
`8081/8512` 这一类未开放端口。用户侧看到“请在治理台查看”时，实际无法通过公网
进入治理台完成审批、驳回、审计或查看 recovery observation。

Current evidence:

- `[x]` `docker-compose.yml` 支持 `GOVERNANCE_WEB_HOST_PORT` 映射治理台。
- `[x]` README 目前建议公网示例为 `WEB_HOST_PORT=8080`、`GOVERNANCE_WEB_HOST_PORT=8081`。
- `[x]` 当前云平台没有开放 `8081`，用户只能稳定访问 `8080`。
- `[x]` 远程部署新增 nginx gateway：`8080/` -> user workspace，`8080/governance` -> governance console。
- `[x]` Public checks confirm `http://118.196.142.222:8080/` and `http://118.196.142.222:8080/governance/` return 200, with Streamlit health `ok`.

解决该问题的待办：

1. `[x]` 优先采用单公网入口方案：`8080` 保留用户工作台，治理台通过同域路径 `/governance` 反向代理到 `governance-web`。
2. `[x]` 远程 `.env` 设置 `PUBLIC_GOVERNANCE_BASE_URL=http://118.196.142.222:8080/governance`，无需依赖 `8081` 公网开放。
3. `[x]` 更新远程 `.env`、部署文档和工作台治理台链接，避免继续显示不可达端口。
4. `[x]` Docker/公网回归：公网工作台与治理台路径已可打开；真实高风险任务可在
   治理台渲染脱敏预览，M6 回归覆盖审批、驳回、审计和 DLQ 控件边界。

## 18. Recipient-Ready Mail Authoring Degrades Poorly Under LLM Rate Limiting

Status: `[x]` done and remote-Docker/UI-regressed

问题：
Mail Agent 已经正确选中上一轮 assistant answer artifact，并进入
`recipient_ready_summary` authoring。但是智谱接口持续返回 `429 Too Many Requests`
时，renderer 无法生成面向收件人的正文。系统当前会安全地停止，不创建 DLP task，
也不发送邮件；但它把 provider failure 表达成“请补充这些信息：
recipient_ready_body”，容易让用户误以为自己漏填了正文。

Current evidence:

- `[x]` Remote Docker regression captured Zhipu API error code `1302`:
  `您的账户已达到速率限制，请您控制请求频率`.
- `[x]` Source resolution still safely identifies the single explicit prior answer through
  `classifier_source=safe_fallback`.
- `[x]` Renderer authoring failure leaves `resolved_body=""`, `review_content=""`,
  `authoring_status=failed`, and does not create a sendable task.
- `[x]` Remote Docker regression verifies authoring failure now returns
  `termination_reason=dependency_failure`, keeps the draft as `authoring_failed`,
  leaves `missing_fields=[]`, and creates no confirmation or task.
- `[x]` Public user workspace and governance entry remain healthy after API restart:
  `http://118.196.142.222:8080/` returns `200`, and
  `http://118.196.142.222:8080/governance/_stcore/health` returns `ok`.
- `[x]` After provider recovery, the strict remote Docker regression verifies
  `assistant_last_answer -> recipient_ready_summary -> needs_confirmation`,
  removes internal retrieval framing, and keeps `review_content == resolved_body`.

解决该问题的待办：

1. `[x]` 将 authoring provider failure 建模为 typed `dependency_failure` observation，
   保留 `service / operation / retryable / fallback_strategy / recovery_hint`。
2. `[x]` 不再把 renderer/provider failure 映射成用户漏填
   `recipient_ready_body`；由 renderer 基于 recovery observation 说明可重试状态。
3. `[x]` 给 recipient-ready authoring 增加 bounded retry 和 circuit-breaker
   指标，避免连续调用进一步放大 provider 限流。
4. `[x]` provider 恢复后已重跑远端 Docker
   `mail_reference_authoring_regression.py`；并通过远端 Streamlit `AppTest` 与公网
   `8080/governance` gateway 完成
   `RAG answer -> send that information -> confirmation -> DLP preview` 可见层验收。

## 19. Governance Console Cannot Review Tasks Across Users In Its Bound Workspace

Status: `[x]` done and remote-Docker/UI-regressed

问题：
独立治理台最初使用 `tenant_id=local-dev / user_id=governance-admin /
workspace_id=default`。同时 `/tasks` 列表对所有非 `local-user` actor 都附加
`user_id` 过滤。这会导致治理员只能看到自己创建的任务，看不到同一企业 workspace
中普通用户提交的待审批任务。若直接去掉全部过滤，又会造成跨租户数据泄漏。

Current evidence:

- `[x]` 腾讯企业邮箱登录会把江南大学邮箱映射到
  `tenant_id=mail-stu_jiangnan_edu_cn` 与
  `workspace_id=mail-stu_jiangnan_edu_cn-default`。
- `[x]` 修复前，远端 Streamlit `AppTest` 能访问治理 API，但默认
  `pending_approval` 列表为空；公网按 task id 查询仍能读到目标任务。
- `[x]` 根因是 `/tasks` 对治理台 admin 仍按 `user_id=governance-admin`
  过滤，而不是按绑定 workspace 执行治理读取。

解决该问题的待办：

1. `[x]` `/tasks` 对 `admin / approver` 采用 workspace-scoped read：保留
   `tenant_id + workspace_id`，移除 `user_id` 限制。
2. `[x]` 普通 `viewer / user` 继续按
   `tenant_id + workspace_id + user_id` 隔离。
3. `[x]` 远端治理台 `.env` 显式绑定
   `mail-stu_jiangnan_edu_cn / mail-stu_jiangnan_edu_cn-default`，不再依赖
   `local-dev/default`。
4. `[x]` Remote Docker regression verifies: admin sees two users in the same workspace,
   viewer sees only its own task, and admin cannot read another workspace.
5. `[x]` Remote Streamlit `AppTest` verifies the governance console renders the selected
   workspace user's pending task and its single redacted preview.

## 20. Continuation State Foundation Was Fragmented Across Local Paths

Status: `[x]` backend foundation closed and remote-Docker-regressed

Problem:
Mail drafts, confirmations, clarifications, DLP tasks, meeting/domain tasks,
uploads, and reusable answers were not exposed through one actor-scoped durable
state substrate. Local branches could compete with the planner, and a draft
patch shortcut could mistake a new outbound request containing an email address
for an edit to an old draft.

Tasks:

1. `[x]` Add actor-scoped active-object listing in the PostgreSQL
   `pending_objects` store.
2. `[x]` Register `mail_draft / mail_confirmation / domain_confirmation /
   source_clarification / recipient_clarification / body_clarification /
   dlp_task / domain_task / upload_artifact / answer_artifact`.
3. `[x]` Resolve durable continuation state before opening a new planner run.
4. `[x]` Support structured `confirm / cancel / choose_source /
   provide_missing_field / patch / new_task` decisions.
5. `[x]` Remove the unsafe shortcut that treated any explicit email or quoted
   content as proof that the active draft should be patched.
6. `[x]` Keep semantic draft edits bounded: a lightweight classifier returns
   state only; user-visible wording remains renderer-owned.
7. `[x]` Reject ungrounded classifier output fields. Recipient, subject, and
   body overrides are accepted only when present in the current user message.
8. `[x]` On classifier timeout, preserve the old draft and emit typed
   ambiguity for renderer clarification instead of mutating the draft or
   entering the planner.
9. `[x]` Expose safe actor-scoped snapshots through `GET /agent/pending-objects`.
10. `[x]` Add trace evaluation for
    `pending_confirmation_bypassed_by_new_side_effect`.

Remote Docker evidence:

- `[x]` `continuation_state_foundation_regression.py`: registry coverage,
  refresh-safe semantic patch, DLP/upload/answer artifacts, actor isolation,
  and trace guard.
- `[x]` `continuation_state_regression.py`: recipient/body/source
  clarifications, meeting confirmation recovery, explicit cancel, and source
  selection.
- `[x]` `mail_persistent_draft_regression.py`: persisted patch plus idempotent
  duplicate confirmation.
- `[x]` `continuation_state_live_probe.py`: healthy classifier distinction or
  explicit `degraded_safe_ambiguity` under upstream timeout.

Remaining boundary:

- `[-]` Rich conversation-scoped `ContentArtifact` storage and explicit
  cross-conversation artifact selection remain in the next Mail Source
  Resolution iteration. They are not reimplemented as keyword branches here.

## 21. Active Mail Draft Can Preempt Fresh RAG Or New Tasks

Status: `[x]` current-turn body artifact fixed; attachment boundary tracked separately

Problem:
After a mail draft remains active, a later fresh enterprise question such as
`GCP Marketplace onboarding 中，订阅 entitlement 延迟时应如何处理？` can be routed
into the continuation layer before the planner/RAG path runs. When the semantic
continuation classifier is unavailable or ambiguous, the resolver may ask
whether the user wants to continue the old draft. A follow-up like
`处理GCP Marketplace订阅延迟的问题` can then be pulled back toward the old mail
draft body (`测试`). The failure is a state-applicability bug, not a GCP-specific
intent-recognition bug and not a RAG retrieval failure.

Current evidence:

- `[x]` User observed a fresh GCP RAG question being answered as a continuation
  ambiguity because an old mail draft was active.
- `[x]` User observed the next clarification response being tied back to the old
  draft body `测试`.
- `[x]` Code path confirms `resolve_continuation(...)` runs before the new
  planner/RAG route.
- `[x]` Patchable `mail_draft` objects previously entered semantic resolution
  without an object-applicability gate.

Tasks:

1. `[x]` Add an object-applicability gate before semantic draft patch
   classification.
2. `[x]` Keep strong continuations first: confirmation, cancellation, source
   clarification, and missing-field clarification still resolve before planner.
3. `[x]` Do not let a generic active `mail_draft` preempt a fresh EnterpriseRAG
   question or a new outbound request.
4. `[x]` If the semantic classifier is unavailable, clarify only when the
   message is anchored to editing the active draft/request; otherwise let the
   planner/RAG path run.
5. `[x]` Add regression coverage for active draft + fresh GCP question, active
   draft + GCP follow-up, active draft + new outbound request, draft-edit
   classifier outage, pending confirmation, and source clarification.
6. `[x]` Remote Docker regression and API restart.
7. `[x]` Live 8080 smoke: fresh RAG question after an active draft must not ask
   whether to continue the old draft.

## 22. Current-Turn Mail Body Is Not A Stable Content Artifact

Status: `[x]` current-turn body artifact fixed; attachment boundary tracked separately

Problem:
When the user provides current-turn body text such as `文段为“测试”`, the mail
chain can still ask for `content_or_attachment`. This is not a continuation
problem. It means the current-turn body was not consistently converted into a
structured `user_inline_text` content candidate before Mail Agent source
selection.

Current evidence:

- `[x]` Live 8080 smoke after Issue 21 showed `文段为“测试”` could still produce
  `请补充这些信息：content_or_attachment`.
- `[x]` Existing parser had a `ContentCandidate` abstraction, but quote and
  inline-label parsing was partly corrupted by mojibake and therefore unreliable
  for normal Chinese punctuation.

Tasks:

1. `[x]` Replace the current-turn parser with a boundary-only
   `ContentCandidate` parser for quoted text and `文段/正文/内容/text/content`
   inline bodies.
2. `[x]` Ensure Mail Agent consumes `user_inline_text` before asking for a
   body/source clarification.
3. `[x]` Remote Docker regression: `文段为“测试”` creates a confirmation-ready
   mail plan with `selected_candidate.kind=user_inline_text`.
4. `[x]` Live 8080 smoke: current-turn quoted body no longer asks for
   `content_or_attachment`.
5. `[x]` Remove `main.py` legacy inline regex fallback; current-turn outbound
   body candidates now enter through `ContentCandidate` only.
6. `[ ]` Keep attachments as upload artifacts or explicit attachment choices;
   current-turn quoted text must not be treated as an attachment.

## 23. Mail Confirmation Final Answer Loses The Selected Content Artifact

Status: `[ ]` open

Problem:
After the system resolves an ambiguous prior-answer source and creates a
confirmation-ready mail plan, the user's final `确认` can still produce a
user-visible answer about the queued DLP task instead of the confirmed mail
operation and selected content artifact. In the observed GCP case, the system
successfully reached a mail confirmation, but after confirmation it answered
that it had no evidence about GCP Marketplace and only saw a queued DLP task.
That is an observation ownership failure: the final renderer is treating the
task queue observation as the main user-facing answer and losing the mail
confirmation/source-artifact context.

Current evidence:

- `[x]` User asked a GCP Marketplace entitlement-delay question and received a
  useful answer.
- `[x]` User asked to send the prior handling方案 to `1136732521@qq.com`.
- `[x]` The first source clarification was noisy and offered unrelated inbound
  mail/thread candidates before the desired `GCP Marketplace订阅延迟处理建议`.
- `[x]` After the user selected the GCP handling方案, the system reached
  `确认将上一轮对话的处理方案发送到 1136732521@qq.com`.
- `[x]` After `确认`, the user-visible answer discussed only a queued DLP task
  and claimed there was no GCP evidence, despite the mail plan having already
  selected the GCP content artifact.

Tasks:

1. `[x]` Preserve `selected_content_artifact / selected_candidate / resolved_body`
   in the post-confirmation observation returned to the final renderer.
2. `[x]` Make the final renderer's primary observation for mail confirmation be
   `mail_confirmation_result` or `governed_mail_task_created`, not a generic DLP
   queue observation.
3. `[x]` Keep DLP task status as progress/provenance/debug context unless the
   user explicitly asks about DLP/task state.
4. `[x]` Add remote Docker regression:
   `RAG answer -> send previous solution -> source clarification -> choose GCP -> confirm`
   must produce a user-facing confirmation/progress answer grounded in the
   selected GCP mail body, not an unrelated RAG/DLP explanation.
5. `[x]` Add trace evaluation guard for `mail_confirmation_renderer_lost_source_artifact`.

Latest regression evidence:

- `[x]` Remote Docker regression `mail_confirmation_task_renderer_regression.py`
  now verifies post-confirm observations begin with
  `governed_mail_task_created -> task_status_result`, preserve the selected GCP
  body, and produce `final_answer_source=mail_task_created_renderer`.
- `[x]` The same regression now verifies
  `mail_confirmation_renderer_lost_source_artifact` is absent from the attached
  trace evaluation.
- `[x]` Live remote smoke no longer collapses the user-visible confirmation
  answer into “I only see a queued DLP task”; it now explains that a governed
  mail task was created for the selected GCP handling方案 and is entering DLP
  review.
## 24. EnterpriseRAG Active Index Contract Is Obscured By Stale Default Paths

Status: `[x]` resolved on remote Docker

Problem:
Remote EnterpriseRAG is no longer using the old default sparse path, but the
deployment still contains stale default-path artifacts that make audits and
debugging easy to misread. During investigation, `/app/data/enterprise_sparse.db`
looked like the active sparse index and contained only `l3_concurrency_*` /
`l3_iso_*` regression data, while the real runtime sparse index was already
pointing to the versioned canonical slice DB. This is not a retrieval failure
by itself, but it is an operational problem because it obscures the true online
state and can lead future debugging, admin views, or maintenance scripts to
inspect the wrong index.

Current evidence:

- `[x]` Remote runtime dense collection count is `3047`.
- `[x]` Remote runtime collection name is `enterprise_rag_bench_slice_v1`.
- `[x]` Remote runtime sparse path is
  `/app/data/enterprise-indexes/bench-slice-v1/enterprise_sparse.db`.
- `[x]` The configured sparse DB also contains `3047` chunks and `283`
  distinct `doc_id`s, matching the dense slice build.
- `[x]` The old default path `/app/data/enterprise_sparse.db` contained only
  `10` regression rows from `l3_concurrency_* / l3_iso_*` docs and has now
  been archived under `data/non-authoritative-archive/`.
- `[x]` `/app/documents.parquet` and `/app/questions.parquet` do not exist on
  the remote deployment, so any audit or script still assuming those paths is
  stale.

Tasks:

1. `[x]` Expose the active EnterpriseRAG contract in debug/admin surfaces:
   active dataset root, active dense collection, active sparse DB path, latest
   manifest run, and content hash.
2. `[x]` Ensure all benchmark/audit/ops scripts read the configured sparse path
   instead of falling back to `/app/data/enterprise_sparse.db`.
3. `[x]` Make stale default-path indexes explicitly non-authoritative in code
   and diagnostics, so future audits cannot mistake them for the live index.
4. `[x]` Add a remote-Docker parity check that compares active dense count,
   active sparse count, distinct doc count, and latest manifest metadata.
5. `[x]` Archive the stale default sparse DB after
   the active-contract diagnostics are in place.

## 25. EnterpriseRAG Canonical Slice Is Aligned, But Answer Quality And Latency Remain Runtime Issues

Status: `[-]` runtime contract complete; continue bounded latency optimization

Problem:
The remote deployment is now aligned to a canonical deterministic slice
(`bench-slice-v1`) rather than an empty or mixed live index, so the remaining
RAG failures are no longer explained by "missing ingest" alone. Representative
queries still show long latency, occasional timeout, conservative or weak
answers, and user-visible degradation such as ellipsis-ending responses. That
means the next bottleneck is the runtime retrieval/observation/recovery layer:
how the system expands retrieval, times out, degrades, and hands evidence to
the answer/renderer path.

Current evidence:

- `[x]` The latest remote manifest run points to
  `/datasets/enterprise-rag-bench/slices/bench-slice-v1/documents.parquet` and
  `/datasets/enterprise-rag-bench/slices/bench-slice-v1/questions.parquet`.
- `[x]` The latest remote manifest run records `283` documents, `50` questions,
  and `3047` indexed chunks.
- `[x]` Remote GCP onboarding queries can succeed, but answer strength is still
  uneven and latency remains high.
- `[x]` Remote MedThink queries have shown both long latency and timeout.
- `[x]` Some successful RAG answers still end weakly or unnaturally, including
  renderer-visible truncation patterns such as trailing ellipsis.
- `[x]` Therefore the remaining problem is not just dataset absence; it is a
  Phase 3 runtime contract problem spanning retrieval budget, timeout recovery,
  evidence packaging, and final answer ownership.

Tasks:

1. `[x]` Promote `bench-slice-v1` to the explicit remote canonical baseline for
   all online audit and benchmark work until a new slice/version is published.
2. `[x]` Add active-index and manifest diagnostics to `/enterprise-rag/query`
   and admin/debug views so every slow or weak answer is tied to a concrete
   slice/index version.
3. `[x]` Continue Phase 3 observation-contract work: stage latency, expansion
   reason, timeout/degradation observation, and evidence-boundary ownership.
4. `[x]` Add representative remote regression cases for GCP onboarding,
   MedThink EU failover, perf-canary, and multipart upload limits.
5. `[x]` Distinguish clearly between retrieval miss, sparse/dense divergence,
   rerank/evidence loss, and answer-composition weakness in the returned debug
   contract.
6. `[x]` Add progressive RAG observation disclosure: return compact
   `retrieval_summary / evidence_manifest / selected_evidence` by default and
   expose raw chunks, full citations, and rerank rows only through debug/admin
   detail reads.
7. `[-]` Continue end-to-end stage observability for `router / planner /
   context_bundle / dense / sparse / neighbor / merge / rerank / evidence_pack /
   answer_compose / final_renderer / total`, including token usage, timeout
   stage, fallback strategy, index version, and correlation id.
8. `[x]` Keep implementation bounded: extend the existing EnterpriseRAG service
   and observation contract; do not add a second retrieval framework or a
   second tracing stack.

Latest remote regression evidence:

- `[x]` `enterprise_rag_active_contract_regression.py` and
  `validate_enterprise_rag_index.py` pass against `bench-slice-v1`: `3047`
  dense chunks, `3047` sparse chunks, `283` documents, no leaked `l3_*` docs.
- `[x]` GCP onboarding, MedThink EU failover, perf-canary, and multipart upload
  limit cases all answer successfully without trailing ellipsis.
- `[x]` Default `/enterprise-rag/query` now returns compact diagnostics; heavy
  rerank, fact-detail, and memory payloads require `include_debug_details=true`.
- `[x]` Prometheus exposes active-index parity, RAG stage latency, and RAG LLM
  token counters without adding a second tracing stack.
- `[x]` Heavy RAG callers can opt into the existing `enterprise_rag_queue`;
  `enterprise_rag_async_query_regression.py` observes
  `pending -> started -> progress -> success` and preserves the same
  correlation ID in the queued result.
- `[x]` Async RAG task metadata is actor-scoped and fail-closed: the owning
  tenant can poll progress, while a different tenant receives `403`; missing
  ownership metadata also blocks non-local reads.
- `[x]` Live `/agent/chat` fast EnterpriseRAG smoke preserves one correlation
  ID across the top-level response, typed-observation provenance, and compact
  observation payload.
- `[-]` Remaining latency is measurable rather than opaque: CPU reranking and
  answer generation are still variable. Generic high-confidence intent
  short-circuits removed avoidable classifier calls without adding
  domain-specific answer templates.

## 26. Communication Copilot Legacy Carriers Need Classified Retirement

Status: `[x]` Task 0B safe-delete/quarantine complete with verification concern; active runtime dependencies preserved

Problem:
The Communication Copilot refactor needs to retire historical product identity,
chat entrypoint ownership, and old fallback carriers without breaking current
Mail/DLP/RAG behavior. Some carriers are dead identity or documentation-only
history, but others still protect active runtime behavior such as source
selection, memory context, dynamic tool registration, and governed delivery
progress. Deleting them before replacement owners are regression-proven would
risk regressions in mail authoring, DLP task creation, and EnterpriseRAG
grounding.

Task 0A inventory rule:

- Categories are fixed as `dead_identity`, `safe_delete`, `compat_shim`, or
  `active_runtime_dependency`.
- Task 0B may delete only `dead_identity` and `safe_delete` items.
- `compat_shim` and `active_runtime_dependency` items need explicit replacement
  owners and removal triggers before runtime deletion.

Legacy carrier inventory:

| carrier | location | category | current behavior | replacement owner | required regression | removal trigger |
| --- | --- | --- | --- | --- | --- | --- |
| Historical package identity string | `app/__init__.py:1` | `dead_identity` | Task 0B replaced the old package docstring with `Communication Copilot package.` | Communication Copilot package identity. | Legacy identity search in Task 0B. | Complete; no runtime behavior depended on the string. |
| Historical README identity mentions | `README.md:3`, `README.md:148`, `README.md:196`, `README.md:345`, `README.md:346` | `safe_delete` | Task 0B rewrote documentation-only historical identity mentions so they no longer preserve active product identity. | Communication Copilot top-level product framing. | Legacy search plus `git diff --check -- README.md todolist.md problem_todolist.md` in Task 0B. | Complete; docs now describe current enterprise product framing. |
| Historical tracker identity entries | `todolist.md` / `13.8 Legacy Cleanup` | `safe_delete` | Task 0B pruned completed historical tracker lines that were not needed as active migration evidence. | Communication Copilot execution tracker. | Legacy search must show no active historical product identity after Task 0B. | Complete; tracker retains current execution evidence only. |
| Legacy orchestration feature flag | `app/config.py:86`, `app/orchestration/service.py:71`, `app/orchestration/service.py:73` | `compat_shim` | `enable_legacy_orchestration_fallback` defaults false and gates the call into the old orchestrator after ReAct failure. | Supervisor + ReAct controller + multi-agent DAG as the only runtime owners. | `scripts/agent_runtime_regression.py`; Mail/RAG smoke when the flag and fallback are removed. | Delete only after ReAct/DAG failure handling returns typed recovery observations without legacy fallback. |
| Legacy orchestration fallback function | `app/orchestration/service.py:210` | `compat_shim` | `_legacy_orchestrate_agent_request(...)` still plans, executes, aggregates, and returns old response metadata if the flag allows fallback. | Supervisor-owned orchestration service with final renderer ownership. | `scripts/agent_runtime_regression.py`; `scripts/cross_domain_workflow_regression.py`; failure-recovery regression for ReAct errors. | Delete after the fallback flag is gone and runtime regressions prove no Mail/DLP/RAG path relies on legacy aggregation. |
| Legacy response metadata values | `app/orchestration/service.py:252`, `app/orchestration/service.py:295` | `compat_shim` | Emits `mode_used=legacy_orchestration` and `final_answer_source=legacy_aggregator` from the fallback path. | Typed observation/final renderer metadata owned by Supervisor and domain agents. | Legacy search plus trace-evaluator checks that final answers are renderer/domain owned. | Remove with `_legacy_orchestrate_agent_request(...)`; no standalone deletion before fallback removal. |
| Legacy static tool registry merge | `app/orchestration/registry.py:391`, `app/orchestration/registry.py:566` | `active_runtime_dependency` | `_legacy_tool_registry()` is merged behind dynamic tools and still provides default read-only tools for catalog, context, mailbox status, inbound mail, uploaded content, persona, and EnterpriseRAG; exact tool coverage is listed below. | Decorator-driven dynamic registry plus Communication Copilot domain owners. | `scripts/agent_runtime_regression.py`; `scripts/mail_authoring_contract_regression.py`; inbound/mail status regressions; `scripts/enterprise_rag_regression.py --limit 4`. | Migrate each listed tool to a dynamic owner, prove the same tool names/observations in Docker, then delete the legacy registry helper. |
| Legacy static tool executor merge | `app/orchestration/registry.py:542`, `app/orchestration/registry.py:574`, `app/orchestration/tools/orchestration_tools.py:9`, `app/orchestration/tools/orchestration_tools.py:17-92` | `active_runtime_dependency` | `_legacy_tool_executor_map()` supplies handlers for static tools when no dynamic handler overrides them; `orchestration_tools.py` currently registers dynamic wrappers that still call `_legacy(...)`. Exact handler coverage is listed below. | Dynamic executor map registered by each domain package, without wrapper calls back into legacy registry handlers. | Same as static registry, plus DLP/governance task context checks. | Delete only after every listed legacy handler has a dynamic replacement and no missing tool appears in agent runtime regression. |
| Mail referential source fallback flag | `app/mail/source_resolver.py:76`, `app/mail/source_resolver.py:203`, `app/mail/source_resolver.py:234`, `app/main.py:1459`, `app/main.py:2108` | `active_runtime_dependency` | `legacy_referential_request` keeps safe deterministic prior-answer selection when LLM source resolution fails. | `communication_brief` source refs and semantic `mail_resolve_body_sources(...)` observations. | `scripts/mail_authoring_contract_regression.py`; `scripts/mail_reference_authoring_regression.py`; `scripts/mail_confirmation_task_renderer_regression.py`; continuation/source clarification regressions. | Remove after brief-driven source resolution covers prior answer, inline body, upload, meeting result, and mail thread references under LLM timeout. |
| Runtime legacy memory bundle | `app/hermes_memory.py:321`, `app/hermes_memory.py:361`, `app/hermes_memory.py:378` | `active_runtime_dependency` | `legacy_memory` folds existing conversation memory into the runtime context bundle and records `legacy_summary_memory` as a source. | Communication memory/context owner feeding `communication_thread` and `communication_brief`. | `scripts/enterprise_rag_regression.py --limit 4`; fast memory and context-bundle runtime regression; no loss of transcript/workspace memory counts. | Rename or remove only after the new context owner preserves summary memory behavior and diagnostics. |
| EnterpriseRAG context fallback payload | `app/enterprise_rag/core/service.py:192`, `app/enterprise_rag/core/service.py:215` | `active_runtime_dependency` | On context-bundle absence or failure, EnterpriseRAG still returns a stable payload containing empty `legacy_memory`. | Grounding bundle diagnostics owned by EnterpriseRAG and communication context service. | EnterpriseRAG regression including timeout/dependency-failure path and compact diagnostics. | Remove legacy payload key only after callers no longer read it and diagnostics expose the replacement field. |
| Generic `mode_used` response field | `app/models.py:118`, `app/main.py:828`, `app/main.py:1103-1210`, `app/main.py:2753-6642`, `app/orchestration/react_controller.py:95` | `active_runtime_dependency` | Runtime responses and UI/debug surfaces use `mode_used` for routing/progress metadata; the field is not legacy by itself. The broad `app/main.py` range is intentional because many response builders emit current non-legacy route modes. | Keep as response metadata unless replaced by a typed route/progress contract. | `scripts/agent_runtime_regression.py`; governance/workspace progress regressions. | Do not remove in Task 0B; only delete legacy values such as `legacy_orchestration` when their carrier is gone. |
| Generic `final_answer_source` response/debug field | `app/models.py:153`, `app/main.py:1105-1210`, `app/main.py:2489-6544`, `app/hermes_dynamic_memory.py:337`, `app/enterprise_rag/core/service.py:129`, `app/enterprise_rag/core/service.py:291`, `app/enterprise_rag/core/service.py:350`, `app/enterprise_rag/core/answer_composer.py:815-914`, `app/enterprise_rag/eval/benchmark_runner.py:46`, `app/orchestration/react_controller.py:129-972`, `app/orchestration/trace_evaluator.py:284-286` | `active_runtime_dependency` | Renderer, RAG, benchmark, memory, mail confirmation, and trace evaluator paths use this answer-source metadata to assert or preserve answer ownership. These users are not all legacy, but the metadata must not be removed in Task 0B. The broad `app/main.py` range is intentional because many active response builders emit non-legacy owner metadata. | Keep or replace with a typed answer ownership contract. | `scripts/mail_confirmation_task_renderer_regression.py`; `scripts/enterprise_rag_regression.py --limit 4`; trace evaluator checks. | Do not remove in Task 0B; only retire legacy values after replacement owner paths pass. |

Legacy registry/executor coverage detail:

- `tool_catalog_list` -> `_tool_catalog_list` (`registry.py:39`, wrapper `orchestration_tools.py:17`).
- `conversation_context_fetch` -> `_conversation_context_fetch` (`registry.py:43`, wrapper `orchestration_tools.py:22`).
- `governance_task_context_fetch` -> `_governance_task_context_fetch` (`registry.py:62`, wrapper `orchestration_tools.py:27`).
- `memory_search` -> `_memory_search` (`registry.py:67`, wrapper `orchestration_tools.py:32`).
- `outbound_mail_summary` -> `_outbound_mail_summary` (`registry.py:78`, wrapper `orchestration_tools.py:37`).
- `inbound_mail_summary` -> `_inbound_mail_summary` (`registry.py:86`, wrapper `orchestration_tools.py:42`).
- `inbound_message_search` -> `_inbound_message_search` (`registry.py:92`, wrapper `orchestration_tools.py:47`).
- `inbound_message_read` -> `_inbound_message_read` (`registry.py:114`, wrapper `orchestration_tools.py:52`).
- `inbound_reply_draft` -> `_inbound_reply_draft` (`registry.py:130`, wrapper `orchestration_tools.py:57`).
- `uploaded_content_analyze` -> `_uploaded_content_analyze` (`registry.py:263`, wrapper `orchestration_tools.py:62`).
- `persona_or_chitchat` -> `_persona_or_chitchat` (`registry.py:373`, wrapper `orchestration_tools.py:67`).
- `unsupported_capability` -> `_unsupported_capability` (`registry.py:367`, wrapper `orchestration_tools.py:72`).
- `enterprise_search` -> `_enterprise_search` (`registry.py:147`, wrapper `orchestration_tools.py:77`).
- `enterprise_evidence_pack` -> `_enterprise_evidence_pack` (`registry.py:159`, wrapper `orchestration_tools.py:82`).
- `enterprise_answer` -> `_enterprise_answer` (`registry.py:172`, wrapper `orchestration_tools.py:87`).
- `enterprise_rag_query` -> `_enterprise_rag_query` (`registry.py:217`, wrapper `orchestration_tools.py:92`).

Current protected regression map:

- Mail source and closeout dependencies are protected by
  `mail_authoring_contract_regression.py`,
  `mail_reference_authoring_regression.py`,
  `mail_confirmation_task_renderer_regression.py`, and continuation/source
  clarification regressions.
- DLP/governed delivery dependencies are protected by
  `agent_runtime_regression.py`, `cross_domain_workflow_regression.py`, and
  mail confirmation/task renderer checks that preserve governed task creation.
- EnterpriseRAG and memory dependencies are protected by
  `enterprise_rag_regression.py --limit 4`, active-index diagnostics, and
  context-bundle failure recovery checks.

Task 0A evidence:

- `[x]` Ran legacy identity search over app code and project trackers.
- `[x]` Ran runtime metadata search:
  `rg -n "final_answer_source|mode_used|enable_legacy_orchestration_fallback|legacy_" app`.
- `[x]` Classified every observed legacy carrier without editing runtime code.
- `[x]` Task 0B hard-deleted/replaced `dead_identity` and `safe_delete`
  carriers in `app/__init__.py`, `README.md`, and `todolist.md`.
- `[x]` Task 0B quarantined `enable_legacy_orchestration_fallback` and
  `_legacy_orchestrate_agent_request(...)` with explicit compatibility-shim
  comments/diagnostics naming Supervisor + ReAct + multi-agent DAG typed
  recovery as replacement owner and typed recovery regression pass as removal
  trigger.
- `[x]` Task 0B preserved all `active_runtime_dependency` rows: static
  registry/executor, mail referential fallback, runtime memory bundle,
  EnterpriseRAG fallback payload, and generic route/answer metadata fields.
- `[x]` Task 0B Docker checks passed for service startup, compileall,
  `scripts/agent_runtime_regression.py`, and
  `scripts/mail_authoring_contract_regression.py`.
- `[!]` Task 0B verification concern: `scripts/enterprise_rag_regression.py
  --limit 4` failed before exercising this change because the API container
  could not open `/app/questions.parquet`.

## Platform Capability Backlog (Not Failure Issues)

Status: `[ ]` planned after Phase 3 stabilization

Purpose:
These items are architecture and product-capability work, not newly observed
failures. They are tracked separately to avoid inflating the issue list or
mixing feature expansion with current RAG recovery.

### A. Full-Stage Agent Observability

1. `[ ]` Extend the existing Prometheus/Admin trace surfaces instead of adding
   another observability subsystem.
2. `[ ]` Record `correlation_id / actor_context / route / agent / tool /
   provider / duration_ms / token_usage / retry / fallback / observation_type`
   across Supervisor, DAG executor, domain agents, LLM renderer, and async
   workers.
3. `[ ]` Expose user-facing progress only on the 8511/8080 workspace; keep raw
   trace, cost, token, and failure detail in the governance surface.

### B. Triggered Agent Work

1. `[ ]` Add a small `TriggerDefinition` contract:
   `trigger_id / tenant_id / workspace_id / trigger_type / schedule_or_event /
   target_capability / input_payload / status / retry_policy`.
2. `[ ]` Route every trigger through the existing Supervisor and DAG executor;
   triggers must not call side-effectful tools directly.
3. `[ ]` First supported scenarios: mail digest schedule, DLP pending reminder,
   meeting reminder, and EnterpriseRAG parity audit.
4. `[ ]` Reuse PostgreSQL task state and Celery workers; do not introduce a
   second scheduler framework until the first scenarios prove it necessary.

### C. Reply Channel Separation

1. `[ ]` Add a `ReplyChannel` contract for system notifications:
   `channel_type / destination / actor_context / correlation_id / payload /
   delivery_status`.
2. `[ ]` Keep formal outbound business email inside Mail Agent with DLP,
   approval, confirmation, and SMTP governance.
3. `[ ]` Use Reply Channels only for task progress, reminders, completion,
   recovery, and governance notifications.
4. `[ ]` Start with `web_workspace` and `governance_console`; add webhook or IM
   adapters only after the contract is stable.

### D. OpenAPI Tool Factory

1. `[ ]` Generate read-only tool manifests from OpenAPI operations and register
   them through the existing dynamic tool registry.
2. `[ ]` Require an explicit governance overlay for every generated tool:
   `read_only / side_effectful / requires_confirmation / permissions /
   tenant_scope / timeout / retry_policy / observation_type`.
3. `[ ]` Reject generated write operations unless they have an explicit
   governance overlay and confirmation boundary.
4. `[ ]` Pilot the factory with a Workspace/Document provider; do not refactor
   existing Mail, DLP, or Tencent Meeting adapters merely for uniformity.
