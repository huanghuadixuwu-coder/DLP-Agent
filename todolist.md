# 项目 ToDo 总表

更新时间：2026-05-17
项目目录：`E:\leetcode-rag-agent-enterprise`

状态说明：
- `[x]` 已完成
- `[-]` 进行中 / 已部分落地
- `[ ]` 未完成

## 0. 开发约束
- `[x]` 默认只以 Docker 作为运行、验证与验收环境
- `[-]` 每次设计或改动前，对照 [总体要求.md](E:/leetcode-rag-agent-enterprise/总体要求.md) 自查
- `[ ]` 每次 agent 相关改动前，额外确认“规则只负责状态、边界、约束、权限与工具调用，不负责用户可见文案”
- `[ ]` 每次较大架构调整前，额外查阅 `enterprise_rag_problems_and_solutions.md`，避免重复引入旧问题

## 1. 当前产品主线
- `[x]` 主线已经切换到“企业安全外发与邮件协作 Agent”
- `[x]` 统一入口覆盖：企业知识问答、上传分析、外发草稿、DLP 审批、SMTP 发送
- `[x]` `web` 入口保持为 `http://localhost:8511/`
- `[ ]` 继续清理仓库中的历史命名、旧文案与过渡代码

## 2. 编排与 Agent 主链
- `[-]` `/agent/chat` 正在从 legacy `planner -> executor -> aggregator` 收口到标准 ReAct 主循环
- `[x]` 已有 `think -> act -> observe -> remember -> terminate` controller 骨架
- `[x]` Fast Path 已覆盖 `persona / contextual_memory / upload_analysis / enterprise_fact / mail_status`
- `[x]` Slow Path 已支持 `react_trace / termination_reason / pending_confirmation / memory_reads`
- `[x]` capability 已开始从“用户意图入口”下沉为“受控工具集合”
- `[x]` 工具元信息已补齐：`read_only / side_effectful / requires_confirmation / returns_observation_type`
- `[x]` Dynamic Tool Discovery & Dispatch 已落地基础版，并保持 ReAct / legacy planner / MCP JSON-lines 兼容
- `[ ]` 继续把残余 legacy 预判逻辑迁入统一 ReAct 循环
- `[ ]` 继续完善副作用动作的确认节点、恢复执行与失败恢复衔接

## 3. Observation-First 渲染
- `[-]` `/agent/chat` 正在持续去模板化，主路径改为 observation-first
- `[x]` Fast Path 与 Slow Path 已接入共享 final renderer
- `[x]` memory observation 已暴露 `memory_boundary=context_only` 与 `enterprise_citation_required=True`
- `[x]` Mail authoring 已开始使用结构化 `draft_state / body_constraints / body_sources / source_policy`
- `[x]` recommendation 类 EnterpriseRAG 主答案已切为“结构化中间态 -> LLM 自然语言组织”
- `[x]` `/agent/chat` task status / rule clarification / ReAct confirmation / abort guardrail 已改为 observation -> renderer
- `[x]` legacy planner fallback 已改为基于 tool observations 调用 final renderer，不再优先使用 aggregator 拼接文案
- `[x]` 上传分析 / persona / unsupported capability 工具 payload 已补 `observation_summary / constraints / status`，降低工具内部固定 answer 对最终表达的影响
- `[-]` 清理剩余 hard-coded fallback / helper 文案；当前仍保留 LLM 失败兜底与少量工具内部 summary
- `[-]` 继续确认权限失败、限流失败、澄清场景都通过 observation 交给 LLM 组织最终表达；mail clarification 已验证 `mail_clarification_renderer`

## 4. 邮件 / DLP 主链
- `[x]` 聊天区支持外发请求
- `[x]` 自动提取收件邮箱
- `[x]` 高风险进入 `pending_approval`
- `[x]` 审批通过后可恢复执行并真实发信
- `[x]` 发送失败进入 `send_failed / delivery_deferred`
- `[x]` 支持入站邮件同步、摘要、读信、回信草稿
- `[x]` 已落地 `pending draft / patch / confirm` 三段式 mail state model
- `[x]` follow-up edit 可 patch 现有 pending draft，而不是重建新计划
- `[x]` 已加入 `body_source_provenance`，默认阻止附件内容泄漏进正文
- `[-]` 规则层正在迁出用户可见 `answer / clarification / draft` 文案，只保留结构化状态
- `[x]` Mail draft / patch / confirm / approval / send 全链路 Docker 回归已通过：`scripts/mail_dlp_full_regression.py`
- `[x]` DLP policy evidence / 通用 workspace embedding 已统一到 `/app/external-models/bge-m3`，并切到 `leetcode_rag_bge_m3_v1` 避免旧 512 维 collection 冲突
- `[x]` DLP model risk scorer 已加入升级 gate：仅“无法独立确认 / 外部收件人”等保守理由不能把规则 low 提升到 medium
- `[ ]` 继续降低外发摘要和回信草稿的模板味，验证等价新问法不靠补分支也能成立

## 5. EnterpriseRAG 主链
- `[-]` EnterpriseRAG 已是企业知识问答默认主路径，当前重点仍是答案质量与工程稳态

### 5.1 检索与索引
- `[x]` 主检索链为 `Chroma dense + SQLite FTS5 sparse + bge-reranker-v2-m3`
- `[x]` dense 使用 `bge-m3`
- `[x]` 已接入 adaptive retrieval budget，避免只靠固定放大 top_k
- `[x]` Workspace retrieval policy engine 已落地，避免所有 workspace 内容都走重型 hybrid
- `[x]` EnterpriseRAG dense / sparse 检索支持 tenant/workspace filter
- `[x]` EnterpriseRAG doc-level invalidation 已支持 `replace_existing=True` 先删旧再写新
- `[x]` 同 `doc_id` 的 scoped delete 已按 tenant/workspace 限定，避免跨租户误删
- `[ ]` 扩大租户隔离、重 ingest、benchmark 的 Docker 回归样本

### 5.2 Chunking 与证据
- `[x]` `fireflies` 改为按 utterance / time block 切分
- `[x]` `gmail` 改为按邮件边界 / quoted reply / 段落块切分
- `[x]` `slack` 改为按消息边界聚合
- `[x]` `confluence / google_drive` 改为 heading / 段落优先的结构化切分
- `[x]` fixed-window fallback 保留
- `[x]` supporting facts 已升级为句子级证据抽取
- `[x]` supporting facts / canonical facts / excluded facts / core facts 已形成分层中间态
- `[x]` `question_focus` 已输出结构化 focus signal 与 source fact provenance
- `[ ]` 继续降低错误文档、背景句、action item、会议噪声句进入核心证据的概率

### 5.3 答案组织
- `[x]` recommendation 类主路径已升级为“提取 -> 规划 -> 生成 -> 重写”
- `[x]` 已加入 `answer_intent` 分类、generic-slot-first recommendation 组织、rewrite 后检查
- `[x]` fallback 已改为保守归纳式答案，不再直接回退原句列表
- `[x]` 可见 citations 已按文档去重
- `[ ]` 继续提升中文自然度，减少英文事实直接外露
- `[ ]` 继续提高同主题不同问法下 recommendation 的一致性

### 5.4 Benchmark 与诊断
- `[x]` benchmark 保留 `average_doc_recall / average_evidence_fact_coverage / average_answer_fact_coverage`
- `[x]` debug 已包含 `canonical_facts / answer_plan / draft_answer / rewritten_answer / answer_intent / question_focus / slot_coverage`
- `[x]` Docker 内 sample ingest / query / benchmark 已可跑通
- `[x]` 当前小样本 smoke 已跑通，但 `answer_fact_coverage` 仍偏低
- `[ ]` 扩大样本回归，持续跟踪 `answer_fact_coverage`

## 6. Memory 主链
- `[-]` Hermes-style memory substrate 已是当前主实现，而不是旁路实验
- `[x]` 已形成 `conversation_recent / conversation_summary / workspace_memory / user_model` 四层读取面
- `[x]` 已加入 transcript compaction 与 controlled reflection candidate
- `[x]` 高风险 long-term memory 默认进入 pending，不自动改变工具、审批或发信策略
- `[x]` turn summary schema 已扩展为 `summary / intent / entities / files_uploaded / risk_level / provenance`
- `[x]` Hermes turn summary / user memory / reflection / compaction 已写入 actor context，并按 actor 读取过滤
- `[x]` Admin memory review 最小后端能力已落地：pending candidates list + review API
- `[x]` pending memory candidate 已从行为上下文与 user model 中隔离，只有 approve 后才进入可用记忆
- `[x]` Memory review 已补 approve / reject / pending / expired 状态、reviewer / reason / reviewed_at 与 audit trail
- `[x]` Docker 内 memory review 回归已通过：`scripts/memory_review_regression.py`
- `[ ]` 后续如需要再补 memory review UI；当前后端治理闭环已可用
- `[ ]` 继续提升 `conversation_recent -> conversation_summary -> workspace_memory -> user_model` 的决策稳定性
- `[ ]` 继续加强 memory 与 EnterpriseRAG 的边界回归

## 7. 企业级工程化基础层
- `[-]` 项目已从“单用户本地入口”推进到“更稳定的多人试用版”

### 7.1 Multi-User / Tenant Context
- `[x]` `ActorContext` 已统一定义：`tenant_id / user_id / workspace_id / roles / session_id / conversation_id`
- `[x]` `/agent/chat`、EnterpriseRAG、DLP task、memory、workspace retrieval 主链开始传递 actor context
- `[x]` 无身份时使用 `local-dev / local-user / default` 默认上下文，保证本地 Docker 可用
- `[x]` conversation、task direct read、Hermes memory Docker 隔离 smoke 已通过
- `[x]` L3 多租户隔离回归已通过：两个 tenant/user/workspace 的 conversation / task / memory 互不可见
- `[x]` 新增腾讯企业邮箱验证码登录：邮箱所有权验证后签发服务端 auth session
- `[x]` `8511` 前端已通过 `X-Auth-Session` 传递认证会话，后端由 session 构造 `ActorContext`
- `[x]` 角色映射改为服务端配置：`AUTH_*_EMAILS` 控制 admin / mail_sender / approver / memory_admin / ingest_admin
- `[x]` Docker 内 `scripts/exmail_auth_regression.py` 已验证登录、`/auth/me`、conversation actor 回填与 logout

### 7.2 Permission / Isolation
- `[x]` side-effectful mail send、DLP approval、RAG ingest / benchmark 已加 role permission check
- `[x]` task list / direct read 已加 tenant-user-workspace 过滤边界
- `[x]` RAG scoped delete 与 retrieval filter 已形成租户级隔离基础
- `[x]` EnterpriseRAG tenant/workspace 检索隔离已通过 Docker 回归：Tenant B 不能检索 Tenant A seeded doc
- `[x]` readonly 用户 approve / ingest / benchmark 均被拒绝
- `[-]` 权限失败已输出结构化 observation / HTTP detail；后续继续收口为 renderer 自然语言说明

### 7.3 Concurrency / Backpressure
- `[x]` 已加入 Redis-backed rate limit decision：`agent_chat / rag_query / ingest / benchmark / mail_send`
- `[x]` EnterpriseRAG ingest / benchmark 已支持 Celery async task enqueue
- `[x]` 已有 Admin async task status 查询
- `[x]` 已暴露 queue health 与基础 Prometheus 指标
- `[x]` 6 并发 queue-health Docker smoke 已通过
- `[x]` 真实 `/agent/chat`、EnterpriseRAG query、mail draft / confirm、queue health 并发 smoke 已通过：`scripts/concurrency_regression.py`
- `[x]` 并发回归输出已纳入 Admin queue health 与 Prometheus metrics snapshot
- `[x]` mail confirm 任务已验证 worker 可消费到 `sent / send_failed / pending_approval` 等治理状态
- `[x]` 权限失败已验证返回结构化 `permission_denied` observation
- `[ ]` 增加更大样本压测、限流降级与 worker failure recovery 回归

## 8. 下一步优先级
1. `[x]` 校准 DLP model risk scorer，区分“真实敏感内容”与“保守无法确认”导致的审批
2. `[x]` 增加 L3 多租户 / 多用户隔离回归脚本：`scripts/multi_tenant_isolation_regression.py`
3. `[x]` 增加真实并发 smoke：`scripts/concurrency_regression.py`
4. `[x]` 完成一轮 `/agent/chat` legacy / hard-coded 文案扫描与高风险出口迁移
5. `[x]` 完成 Memory review 后端闭环：pending 隔离、approve / reject / expire / audit、Docker 回归
6. `[x]` 腾讯企业邮箱登录已接入主链，并完成 Docker auth 回归
7. `[-]` 继续收口剩余 legacy 预判逻辑、工具内部 answer 字段和 LLM 失败 fallback
8. `[-]` 扩大 EnterpriseRAG benchmark 样本，重点提升 `answer_fact_coverage`
9. `[ ]` 增加更大样本压测、限流降级与 worker failure recovery 回归

## 9. 后续候选项
- `[ ]` PDF 入库与复杂版面解析方案
- `[ ]` GraphRAG 是否作为 sidecar 能力接入，而不是替换现有主检索架构
- `[ ]` Calendar / 腾讯会议等新能力扩展
