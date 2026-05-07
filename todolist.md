# 项目 ToDo 总表

更新时间：2026-05-07  
项目目录：`E:\leetcode-rag-agent-enterprise`

状态说明：
- `[x]` 已完成
- `[-]` 进行中 / 已部分落地
- `[ ]` 未完成

## 0. 开发自查约束
- `[x]` 默认只以 Docker 作为运行与验收环境，不以本机 Python 作为有效基线
- `[-]` 每次设计或改动前，必须先按 [总体要求.md](E:/leetcode-rag-agent-enterprise/总体要求.md) 自查
- `[ ]` 每次 agent 相关改动前，额外对照 `总体要求.md` 第 2/3/6/7/8 条，确认“规则只管状态与边界，不管用户可见语言”
- `[ ]` 每次架构调整前，额外查阅 `enterprise_rag_problems_and_solutions.md`，避免重复引入模板化、玩具化或错误分层

## 1. 产品主线
- `[x]` 主线已切到“安全外发与邮件协作 Agent”
- `[x]` 统一入口覆盖：企业知识问答、上传分析、外发草稿、DLP 审批、SMTP 发送
- `[ ]` 继续清理仓库中的历史命名和旧文案

## 2. Docker-first 运行约束
- `[x]` 后续开发、回归、验收默认都在 Docker 内完成
- `[x]` `web` 入口保持为 `http://localhost:8511/`
- `[x]` EnterpriseRAG 本地模型目录已接入容器
  - `/app/external-models/bge-m3`
  - `/app/external-models/bge-reranker-v2-m3`
- `[x]` 已补齐 Docker 内 `ingest / query / benchmark` 回归路径

## 3. DLP / 邮件协作主链路
- `[x]` 聊天区支持外发请求
- `[x]` 自动提取收件邮箱
- `[x]` 高风险进入 `pending_approval`
- `[x]` 审批通过后恢复执行并真实发信
- `[x]` 发送失败进入 `send_failed / delivery_deferred`
- `[x]` 支持入站邮件同步、摘要、读信、回信草稿
- `[ ]` 继续降低外发摘要和回信草稿的模板味

## 4. 统一编排层
- `[x]` `/agent/chat` 已走 `guardrail -> planner -> executor -> aggregator`
- `[x]` 企业知识问答默认进入 `enterprise_rag_query`
- `[x]` 已修复 planner 误传非法 `source_types` 导致 `/agent/chat` 检索被过滤为空的问题
- `[x]` 企业问答可见引用已按文档去重，避免同一文档重复展示
- `[-]` `/agent/chat` 正在从静态 `planner -> executor -> aggregator` 升级为标准 ReAct 主循环
- `[x]` 已新增 `think -> act -> observe -> remember -> terminate` controller 骨架
- `[x]` capability 已开始从“用户语义入口”降级为“底层受控工具集合”
- `[x]` 已为工具补充 `read_only / side_effectful / requires_confirmation / returns_observation_type` 元信息
- `[x]` 新主链路已支持 `react_trace / termination_reason / pending_confirmation / memory_reads`
- `[x]` 已新增 `/agent/chat` 轻量 `L0 router`，先分流到 `Fast Path` 或 `Slow Path`
- `[x]` Fast Path 已覆盖 `persona / contextual_memory / upload_analysis / enterprise_fact / mail_status`
- `[x]` `uploaded_content_analyze` 已扩展到 `summarize / qa / critique / rewrite / extract_action_items`
- `[x]` `react_trace` 仅在 Slow Path 返回；Fast Path 直接返回空 trace
- `[x]` ReAct `think` 超时或 orchestration 失败时，已优先降级到安全直答或澄清，而不是直接 500
- `[-]` legacy orchestration fallback 默认关闭，仅在显式开关启用时保留为排障退路
- `[ ]` 继续把更多旧的静态路由判断迁入 ReAct 循环
- `[ ]` 继续完善副作用动作的确认节点与恢复执行衔接

## 5. EnterpriseRAG 答案质量修复
- `[-]` `EnterpriseRAG answer quality repair` 持续进行中

### 5.1 Source-aware chunking
- `[x]` `fireflies` 改为按 utterance / time block 切分
- `[x]` `gmail` 改为按邮件边界 / quoted reply / 段落块切分
- `[x]` `slack` 改为按消息边界聚合切分
- `[x]` `confluence / google_drive` 改为 heading / 段落优先的结构化切分
- `[x]` fixed-window fallback 保留
- `[x]` chunk metadata 已补齐 `chunk_strategy`
- `[ ]` 继续观察不同 source 的 chunk 粒度是否还需要微调

### 5.2 Sentence-level supporting facts
- `[x]` supporting facts 已升级为句子级证据抽取
- `[x]` 每条 fact 保留 `doc_id / chunk_id / sentence_text / score`
- `[x]` retrieval debug 已输出 supporting fact details
- `[x]` 已补上 `canonical_facts` 与 `excluded_facts`
- `[x]` recommendation 类问题已新增 `core_facts / secondary_facts` 划分
- `[ ]` 将 recommendation 类 canonical facts 继续压成 slot-ready core facts
- `[ ]` 新增 question_focus 提升逻辑，防止被明确追问的 secondary 点被错压
- `[ ]` 继续压低背景句、action item、跨文档干扰句的占比

### 5.3 Structured answer organization
- `[x]` 主路径已从“相关句拼接”升级为“提取 -> 规划 -> 生成”
- `[x]` 已新增 `canonical facts` 归一化层
- `[x]` 已新增 `answer_plan`
- `[x]` 已新增 `rewrite layer`
- `[x]` fallback 已改为“保守归纳版答案”，不再直接回退原句列表
- `[x]` 已新增 answer intent 分类层（窄规则 + LLM 兜底）
- `[x]` recommendation 类已切到 generic-slot-first 模板路径
- `[x]` 领域槽位作为增强层接入，不再作为主模板前提
- `[x]` recommendation 润色层已补上 transcript-style 后检查
- `[x]` fallback 已改为槽位弱答案，不再用英文 lexical coverage 兜头打回
- `[ ]` 继续提升中文表达自然度，减少英文事实直接外露
- `[ ]` 继续提高同主题不同问法下的 recommendation 一致性

### 5.4 Hybrid retrieval tuning
- `[x]` 检索主链路保持 `dense + sparse + reranker`
  - dense：Chroma + `bge-m3`
  - sparse：SQLite FTS5 + BM25
  - rerank：`bge-reranker-v2-m3`
- `[x]` 已加入 answerability boost
- `[x]` `constrained / semantic` 问题已提高 `dense_top_k / sparse_top_k / rerank_top_k`
- `[ ]` 继续减少错误文档混入 evidence 的概率

### 5.5 Layered benchmark diagnostics
- `[x]` benchmark 已保留 `average_doc_recall / average_evidence_fact_coverage / average_answer_fact_coverage`
- `[x]` benchmark 已补充 `canonical_facts / answer_plan / draft_answer / rewritten_answer / excluded_facts / rewrite_applied`
- `[x]` 已新增 `answer_intent / classifier_source / classifier_confidence / classifier_reason`
- `[x]` 已新增 `question_focus / core_facts / secondary_facts / answer_slots / slot_coverage`
- `[x]` 已新增 `polish_rejected / polish_rejected_reason / duplicate_visible_citations`
- `[ ]` 继续扩大样本回归，确认新答案链路在更多题目上稳定

### 5.6 当前基线
- `[x]` 已完成 Docker 内 sample ingest 重建索引
- `[x]` 已确认 query / benchmark 可在 Docker 内跑通
- `[x]` 当前小样本基线（`limit=4`）：
  - `average_doc_recall = 1.0`
  - `average_evidence_fact_coverage = 0.0833`
  - `average_answer_fact_coverage = 0.2917`
- `[-]` 当前 recommendation 类问题已能命中核心证据，但仍存在 fallback 误触发、英文证据句直出和引用重复，尚未达合格线

## 6. Hermes 风格 Memory
- `[x]` 已完成 memory / transcript / provider 分层方案
- `[-]` workspace memory hybrid search 已落地，但还不是主 agent 的一等动作
- `[x]` recent turns / conversation summary 已接入 ReAct 主循环的 memory read 动作
- `[x]` contextual memory 问题不再只能依赖 persona 或企业检索路径
- `[ ]` 继续提升 `conversation_recent -> conversation_summary -> workspace_memory` 的自主决策稳定性
- `[ ]` 把新的 context assembler 完整接入所有主推理链路，统一成 ReAct 的 memory substrate

## 7. 下一阶段优先级
1. `[ ]` 把邮件链路里仍写死的 `answer / clarification / draft` 文案全部迁出规则层，改成 `state/observation -> LLM renderer`
2. `[-]` 保留并继续完善 mail authoring state model：pending draft / patch / confirm 三段式
3. `[-]` 保留并继续完善 body_constraints + attachment/body/reference source separation，但只输出结构，不直接输出用户可见文案
4. `[ ]` 在 Docker 内回归真实 UI bad case，重点验证“新问法不靠补模板也能泛化”
5. `[ ]` 再继续收口 `/agent/chat` 余下 legacy 预判逻辑与 memory/context 稳定性

## 7.1 ?????????
- `[-]` legacy `planner -> executor -> aggregator` ?????????????????????????????memory/context ?????????????????
- `[x]` ??? contextual memory ?????????????????????????????? LLM ??????????????????
- `[x]` `/agent/chat` ????????orchestration ?????????`run_unified_agent`???????????legacy orchestration fallback

## 7.2 Outbound resolution follow-up
- `[x]` Referential outbound requests now clarify the target instead of turning the raw send instruction into `message_raw`.
- `[x]` Explicit summary-send requests now preserve the original command in `request_message` and write the resolved assistant summary into `message_raw`.
- `[x]` DLP task creation now separates `request_message` from the real outbound body, so worker summaries and delivery use resolved content rather than the user command itself.
- `[x]` `send both / ???` now sends the summary in the email body and includes the original uploaded text as an attachment through the SMTP / DLP worker path.
- `[x]` Uploaded raw files now persist as upload blobs and can be sent as true binary attachments through the SMTP / DLP worker path.
- `[-]` Outbound email body / attachment semantics are being repaired so recipient-visible mail follows user intent instead of DLP summary boilerplate.
- `[x]` Delivery planning has been extracted into a dedicated strategy module instead of continuing to grow inline `if/else` branches in `main.py`.
- `[x]` Worker sending now uses stored `delivery_subject` / `delivery_body`; DLP summary stays on the review/audit path.
- `[x]` Attachment-style requests can now produce a short cover note in the body while sending the original upload as the actual attachment.
- `[-]` Add `mail_draft_state` so follow-up edits patch the pending draft instead of creating a new plan.
- `[-]` Add structured `body_constraints` for self-intro / date / time / reason / tone.
- `[-]` Add `body_source_provenance` guardrail to block attachment text from leaking into the email body.
- `[-]` Remove rule-authored user-visible wording from outbound/mail authoring; rules may only emit `missing_fields / constraints / source_policy / draft_state / patch_kind`
- `[x]` Replace hard-coded clarification and draft sentence assembly with LLM rendering over `mail_draft_result / mail_patch_result / mail_confirmation_result`
- `[ ]` Only allow attachment content into the body when the user explicitly requests quoting/summarizing it there.
- `[x]` Regression: “讲明我们是谁” must become body intent, not disappear into a default cover note.
- `[x]` Regression: “需要更改，正文内容告知发送的时间、日期” must patch the existing pending draft and preserve recipient/attachment.
- `[x]` When date/time is requested in a pending draft patch, structured `send_date_value / send_time_value` must be populated and rendered as concrete values instead of placeholders.
- `[ ]` Regression: equivalent new phrasings should work without adding a new wording template branch

## 8. Hermes Memory Dynamic Policy
- `[-]` Hermes-style memory substrate is now the current implementation priority.
- `[x]` Define memory write policy across `session_transcript / turn_summary / workspace_memory / user_model`.
- `[x]` Add structured dynamic turn memory at turn end with goal, outcome, files, recipients, task ids, failure reason, and provenance.
- `[x]` Extend ReAct memory reads to support `conversation_recent / conversation_summary / workspace_memory / user_model`.
- `[x]` Add transcript compaction records that keep the recent raw window and summarize older turns with preserved identifiers.
- `[x]` Add controlled `reflection_candidate` records; they do not mutate tools, prompts, approval policy, or delivery policy automatically.
- `[x]` Store high-risk user memory as pending candidates instead of active long-term behavior.
- `[ ]` Add UI or admin review flow for approving `pending_memory_update / reflection_candidate` records.
- `[ ]` Tune memory routing after Docker regression with real conversations.

## 8.1 Memory Safety Boundaries
- `[x]` Memory may provide context, preferences, and task continuity.
- `[x]` Enterprise factual answers must still use EnterpriseRAG evidence; memory cannot replace enterprise citations.
- `[x]` Side-effect policy, approval rules, and outbound delivery defaults cannot be changed by reflection automatically.
- `[ ]` Add explicit regression cases for memory-vs-EnterpriseRAG boundary.

## 8.2 Updated Next Priorities
1. `[x]` Complete the first Hermes-style memory substrate.
2. `[x]` Connect controlled reflection and transcript compaction strategy.
3. `[ ]` Fix outbound email body / attachment semantics so recipient-visible mail follows the user request instead of DLP summaries.
4. `[ ]` Add calendar and Tencent Meeting capabilities.

## 9. Observation-First Answer Rendering
- `[-]` `/agent/chat` is being de-templated so Router/Guardrail stay deterministic but most user-visible answers come from LLM rendering over observations.
- `[x]` Add a shared final answer renderer for Fast Path and Slow Path.
- `[x]` Fast Path upload/persona/enterprise/memory/mail-status now produce structured observations before answer rendering.
- `[x]` Slow Path final answer composition now uses the shared renderer instead of a separate local prompt path.
- `[x]` Tool observations in ReAct now preserve payload and citations for downstream answer rendering.
- `[x]` Router fallback now adds explicit English/contextual memory cues, reducing mixed-language recall questions from slipping into Slow Path.
- `[-]` Deterministic user-visible answers are now constrained to confirmation / clarification / unsupported capability / hard timeout fallback, but a few rule-path helpers still need follow-up cleanup.
- `[-]` `mail draft / task queue / mailbox summary / compound request` are being migrated onto the same observation-first renderer.
- `[-]` Mail draft rendering is being upgraded from plain `resolved_body` to structured draft state + body constraints + body sources.
- `[-]` Mail follow-up edits should emit `mail_patch_result` observations before final confirmation rendering.
- `[-]` Clarification text itself must also be generated by LLM from structured `missing_fields / constraints`, not hand-written in business rules
- `[x]` Mail body generation must move from clause/template assembly to `body_constraints + body_sources + draft_state -> LLM authoring renderer`
- `[ ]` Regress document critique, contextual recall, enterprise fact QA, and mailbox status flows in Docker after the renderer unification.
