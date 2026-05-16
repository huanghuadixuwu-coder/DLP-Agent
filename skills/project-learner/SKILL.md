---
name: project-learner
description: "Interactive project learning coach via interview-style Q&A. Reads codebase and docs, dynamically generates interview questions per knowledge domain and sub-topic, conducts up to 4 follow-up rounds, scores answers, provides learning guidance with code/doc references, and persists progress. 10 domains × 3-5 sub-topics = 45 knowledge points for comprehensive interview coverage. Use when user says '学习项目', '了解项目', '检验项目', '项目学习', '面试准备', 'learn project', 'study project', 'review project', 'interview prep', 'knowledge check', or wants to understand/master the project through guided Q&A."
---

# Project Learner

Interactive interview-coach that helps users master this project through guided Q&A.

All user-facing interaction in **中文**. Internal instructions in English.

## Pipeline Overview

```
Discovery → Check History → User Intent → Select Domain → Select Sub-topic
→ Generate Question → Interactive Q&A (≤4 follow-ups) → Evaluate
→ Learning Guide → Persist Progress → Continue or End
```

---

## Phase 1: Project Discovery

Autonomously build project understanding. Do NOT ask user anything yet.

1. Read `README.md` — project goals, architecture, tech stack, module design
2. Read `技术文档.md` and `enterprise_rag_architecture.md` — configuration system and architecture
3. List `app/` directory tree — module structure (enterprise_rag/, orchestration/, inbound_mail/, outbound_delivery/, etc.)
4. Read key entry points: `app/main.py`, `app/enterprise_rag/core/service.py`, `app/orchestration/planner.py`
5. Read `myproject_for learn/` folder — project evolution, problem-solving, and design rationale

Build an internal mental model covering these **10 Knowledge Domains**, each containing **3-5 Sub-topics** (知识点), totaling **45 interview knowledge points**:

### Domain & Sub-topic Map

| ID | 知识域 / 知识点 | Key Code Areas |
|----|----------------|---------------|
| **D1** | **企业邮件协作系统架构** | |
| D1.1 | 端到端邮件处理流程：从IMAP同步到SMTP发送的完整链路 | `app/inbound_mail.py`, `app/outbound_delivery.py`, `app/email_sender.py` |
| D1.2 | 邮件协作三层架构：inbound/outbound/dlp 各层职责与数据流 | `app/inbound_mail.py`, `app/outbound_delivery.py`, `app/dlp_entry.py` |
| D1.3 | 任务编排机制：TaskQueue、TaskWorker、TaskStore 的异步执行模型 | `app/task_queue.py`, `app/task_worker.py`, `app/task_store.py` |
| D1.4 | 核心数据类型：MailMessage、DeliveryStatus、DLPResult 等类型系统 | `app/models.py`, `app/enterprise_rag/core/types.py` |
| D1.5 | 配置驱动架构：环境变量、邮箱配置、SMTP/IMAP 参数管理 | `app/config.py`, `.env.example`, `技术文档.md` |
| **D2** | **DLP 安全检测与审批工作流** | |
| D2.1 | DLP 检测引擎：敏感信息识别、风险等级评估、脱敏处理机制 | `app/dlp_entry.py`, `app/dlp_runtime.py`, `app/privacy_lab.py` |
| D2.2 | 审批状态机：任务流转、人工审批、驳回处理的状态机设计 | `app/dlp_scenarios.py`, `app/workflow_store.py`, `app/sensitive_workflow.py` |
| D2.3 | 敏感信息类型：手机号、身份证、API Key、客户名单等检测规则 | `app/disambiguation_lab.py`, `app/dlp_entry.py` |
| D2.4 | 治理化任务管理：任务创建、状态更新、审计日志记录 | `app/task_events.py`, `app/session_store.py`, `app/upload_analysis.py` |
| D2.5 | 邮件事件通知：notification_outbox、daily_mail_digest、new_mail_received 事件机制 | `app/inbound_mail_store.py`, `app/reminder_store.py` |
| **D3** | **EnterpriseRAG 核心架构** | |
| D3.1 | EnterpriseRAG 服务层：query_planner、retrieval_orchestrator、answer_composer 协同 | `app/enterprise_rag/core/service.py`, `app/enterprise_rag/core/query_planner.py` |
| D3.2 | 检索规划引擎：问题类型推断、源类型识别、检索策略生成 | `app/enterprise_rag/core/query_planner.py`, `app/enterprise_rag/core/types.py` |
| D3.3 | 混合检索编排：Dense检索、Sparse检索、候选去重与融合机制 | `app/enterprise_rag/core/retrieval_orchestrator.py`, `app/enterprise_rag/libs/sparse_index.py` |
| D3.4 | 证据打包机制：EvidencePack构建、citation生成、missing_evidence 分析 | `app/enterprise_rag/core/evidence_pack.py`, `app/enterprise_rag/core/types.py` |
| D3.5 | 答案生成引擎：compose_enterprise_answer 支持的事实覆盖、引用生成 | `app/enterprise_rag/core/answer_composer.py` |
| **D4** | **混合检索与重排序系统** | |
| D4.1 | Dense检索实现：Chroma向量库、BGE-M3嵌入模型、语义相似度计算 | `app/vectorstore.py`, `app/enterprise_rag/libs/embedding/` |
| D4.2 | Sparse检索实现：SQLite FTS5、BM25、关键词匹配与索引构建 | `app/enterprise_rag/libs/sparse_index.py`, `app/enterprise_rag/libs/text_cleaning.py` |
| D4.3 | 检索融合策略：候选合并、heuristic评分、diversification 策略 | `app/enterprise_rag/core/retrieval_orchestrator.py`, `app/enterprise_rag/libs/scoring.py` |
| D4.4 | 轻量级重排序：lexical rerank、source_type优先级、候选质量评估 | `app/enterprise_rag/libs/reranker/`, `app/enterprise_rag/libs/scoring.py` |
| **D5** | **Memory 系统与上下文管理** | |
| D5.1 | Hermes记忆系统：workspace_memory、transcript、user_memory 的三级存储 | `app/hermes_memory.py`, `app/hermes_dynamic_memory.py` |
| D5.2 | 上下文构建：build_runtime_context_bundle 会话记忆与工作空间记忆检索 | `app/hermes_memory.py`, `app/enterprise_rag/core/service.py` |
| D5.3 | 记忆压缩策略：compact_text、summary merge、记忆结构化 | `app/conversation_memory.py`, `app/raw_vs_langgraph.py` |
| D5.4 | 会话管理：ConversationStore、SessionStore 的状态持久化 | `app/conversation_store.py`, `app/session_store.py` |
| D5.5 | 动态记忆策略：memory_policy、缓存失效、热更新机制 | `app/enterprise_rag/core/memory_policy.py` |
| **D6** | **编排层与任务规划** | |
| D6.1 | 统一编排入口：planner.py 的复合任务分解与能力路由 | `app/orchestration/planner.py`, `app/orchestration/fast_router.py` |
| D6.2 | 能力注册表：Agent能力发现、动态加载、API映射机制 | `app/orchestration/registry.py`, `app/unified_agent.py` |
| D6.3 | 任务执行器：executor.py 的子任务调度、结果聚合、错误处理 | `app/orchestration/executor.py`, `app/orchestration/aggregator.py` |
| D6.4 | 渲染层设计：final_renderer 的结果格式化、对话上下文管理 | `app/orchestration/final_renderer.py`, `app/graph.py` |
| D6.5 | Agent路由策略：hybrid_router 的能力选择、负载均衡、故障转移 | `app/hybrid_router.py`, `app/orchestration/policies.py` |
| **D7** | **企业知识库 Ingestion** | |
| D7.1 | EnterpriseRAG 数据接入：loader、normalizer、chunker、indexer 流程 | `app/enterprise_rag/ingestion/`, `app/enterprise_rag/ingestion/indexer.py` |
| D7.2 | 元数据标准化：normalize_source_type、business_domain、timestamp 标准化 | `app/enterprise_rag/libs/metadata.py`, `app/enterprise_rag/libs/text_cleaning.py` |
| D7.3 | 企业文档隔离：domain=enterprise_knowledge 与历史语料隔离机制 | `app/enterprise_rag/core/types.py`, `app/vectorstore.py` |
| D7.4 | Benchmark 评测体系：casebook构建、benchmark_runner、指标计算 | `app/enterprise_rag/eval/casebook.py`, `app/enterprise_rag/eval/benchmark_runner.py` |
| D7.5 | 证据覆盖分析：doc recall、answer facts coverage、missing evidence 评估 | `app/enterprise_rag/eval/`, `app/enterprise_rag/eval/metrics.py` |
| **D8** | **MCP 工具集成与 API 设计** | |
| D8.1 | MCP 客户端架构：mcp_client.py 的工具发现、调用、结果处理 | `app/mcp_client.py`, `app/mcp_tools.py` |
| D8.2 | 邮件工具实现：send_email_163 的 SMTP 集成、163 邮箱配置 | `app/mcp/interview-agent-tools/`, `app/email_sender.py` |
| D8.3 | API 接口设计：RESTful API、WebSocket 实时通信、异步处理 | `app/main.py`, `技术文档.md` |
| D8.4 | 前端界面：Streamlit App 的聊天界面、文件上传、任务监控 | `web/streamlit_app.py`, `app/observability/dashboard/` |
| D8.5 | 工具注册表：unified_tools 的能力注册、参数校验、执行封装 | `app/unified_tools.py`, `app/agent_cli.py` |
| **D9** | **监控、观测与工程化** | |
| D9.1 | 可观测性系统：metrics、tracing、logging 的三维度监控 | `app/metrics.py`, `app/observability/`, `app/orchestration/tracing.py` |
| D9.2 | 业务指标监控：邮件处理量、DLP 检测率、RAG 检索成功率 | `app/metrics.py`, `app/observability/dashboard/` |
| D9.3 | 性能追踪系统：TraceContext、request_id、性能瓶颈分析 | `app/orchestration/tracing.py`, `app/enterprise_rag/core/service.py` |
| D9.4 | 容器化部署：Docker、docker-compose、环境变量管理 | `Dockerfile`, `docker-compose.yml`, `.env.example` |
| D9.5 | 日志与审计：操作日志、安全审计、错误追踪 | `app/observability/logger.py`, `app/task_events.py` |
| **D10** | **集成测试与生产运维** | |
| D10.1 | 回归测试策略：EnterpriseRAG-Bench、DLP 场景、邮件协作集成测试 | `scripts/`, `app/enterprise_rag/eval/` |
| D10.2 | 数据集验证：公开办公数据集、Enron邮件、QMSum会议数据 | `技术文档.md`, `enterprise_rag_bench_documents_sample.csv` |
| D10.3 | 配置管理：环境隔离、动态配置、热更新机制 | `app/config.py`, `app/observability/` |
| D10.4 | 容错与降级：服务降级、熔断机制、优雅关闭 | `app/main.py`, `app/orchestration/policies.py` |
| D10.5 | 生产部署架构：多进程、异步队列、健康检查 | `docker-compose.yml`, `app/task_queue.py`, `app/task_worker.py` |

> **Total: 10 domains × 3-5 sub-topics = 45 knowledge points**
> Each sub-topic can be studied multiple times with different questions, providing 100+ possible interview questions.

---

## Phase 2: Check Learning History

1. Try reading `.github/skills/project-learner/references/LEARNING_PROGRESS.md`
2. **File missing** → first-time learner, proceed to Phase 3
3. **File exists** → parse BOTH tables:
   - **Domain Summary**: which domains are ⬜/🔴/🔶/✅
   - **Sub-topic Progress**: which sub-topics are ⬜ (unlearned), 🔴 (weak ≤3), 🔶 (learning 4-6), ✅ (mastered ≥7)
   - Count: total sub-topics mastered / 45
   - Identify lowest-scoring sub-topics for review recommendation

---

## Phase 3: User Intent

Use `ask_questions` (中文) to determine what the user wants:

**Question 1 — 学习模式** (single-select):

| Option | Description |
|--------|------------|
| 🆕 学习新知识点 | Pick from unlearned/weak sub-topics |
| 📖 复习已学内容 | Review previously learned low-score sub-topics |
| 📋 查看学习进度 | Display progress table, then end |
| 🎯 Agent 推荐 | Auto-pick the best next sub-topic to study |

If user picks 📋 → display the full progress table from `LEARNING_PROGRESS.md` and stop.

If user picks 🎯 → Agent auto-selects the optimal sub-topic (prioritize: ⬜ unlearned in weakest domain → 🔴 weak → 🔶 lowest score). Skip Question 2 & 3, go directly to Phase 4.

**Question 2 — 知识域选择** (single-select, only for 🆕 or 📖):

List all 10 domains with current status + completion rate. Example format:
- `D1 RAG Pipeline 整体架构 [2/5 ✅] 🔶`
- `D2 Ingestion Pipeline [0/5 ✅] ⬜`

For 📖 mode: only show domains with previous scores. For 🆕 mode: prioritize domains with most ⬜ sub-topics.

**Question 3 — 知识点选择** (single-select, only after Question 2):

List all sub-topics under the selected domain with their status:
- `D2.1 Pipeline 整体流程 ⬜ 未学习`
- `D2.2 Chunking 策略 🔶 6/10`
- `D2.3 Transform 链 ✅ 8/10`

Include option:
- 🎯 Agent 推荐 — auto-pick the weakest/unlearned sub-topic in this domain

---

## Phase 4: Generate Interview Question

Based on the selected **sub-topic** (not just domain):

1. **Deep-read** the sub-topic's specific source code — read actual class definitions, key functions, config sections listed in the Sub-topic Map
2. **Dynamically generate** ONE main interview question (中文) grounded in this sub-topic's real code
3. **Internally prepare** up to 4 progressive follow-up questions (do NOT show these yet)
4. **Avoid repeating** questions from previous sessions — check Detailed History for this sub-topic and generate a different angle

### Question Design Principles

- Questions MUST reference real code/architecture from THIS project, never generic
- Questions should be specific to the sub-topic, not the whole domain
- Focus on enterprise-specific challenges: DLP security, mail collaboration, EnterpriseRAG benchmark
- Difficulty progression for follow-ups:
  - Follow-up 1: "为什么这样设计？" (design rationale for enterprise scenarios)
  - Follow-up 2: "和替代方案对比有什么优劣？" (trade-offs in enterprise context)
  - Follow-up 3: "边界条件/异常情况怎么处理？" (edge cases in production)
  - Follow-up 4: "如果让你重新设计，会怎么做？" (redesign for scale)
- Adjust follow-ups dynamically based on what the user actually answers

### Question Angle Variety

Each sub-topic can be asked from multiple angles. When a sub-topic is revisited, pick a DIFFERENT angle:
- **What**: 描述这个企业级模块/机制的功能和价值
- **How**: 具体实现细节，代码层面怎么做的（关注enterprise场景的特殊性）
- **Why**: 为什么选择这种设计方案（企业安全、治理、合规考量）
- **Compare**: 和传统方案的对比，企业级优势
- **Debug**: 如果出了问题怎么排查（生产环境调试技巧）
- **Extend**: 如何扩展到更大规模或更多场景

### Question Format

Present to user:

```
## 🎯 面试问题

**知识域**: [Domain Name] > **知识点**: [Sub-topic Name]

**面试官问**: [Question text — specific to this sub-topic, referencing project components]

请回答：
```

---

## Phase 5: Interactive Q&A (≤4 Follow-up Rounds)

```
Round 0: Main question → User answers
Round 1-4: Brief feedback on previous answer + follow-up question → User answers
Early exit: User says "结束"/"pass"/"跳过" OR answer is sufficiently comprehensive
```

### Per-Round Behavior

1. **Acknowledge** what the user got right (1-2 sentences, 中文)
2. **Hint** at what was missed without giving away the answer (1 sentence)
3. **Ask follow-up** that digs deeper based on their answer direction

### Follow-up Output Format

```
### 第 N 轮追问

✅ **答得好**: [What they got right]
💡 **提示**: [What they could explore further]

**追问**: [Follow-up question]
```

If user's answer already covers the planned follow-up, skip to a harder one or end early.

---

## Phase 6: Evaluation

After Q&A ends, output a structured evaluation report (中文):

```markdown
## 📊 评价报告

**知识域**: [Domain] > **知识点**: [Sub-topic ID & Name] — [Question summary]
**追问轮数**: N/4

### ✅ 回答亮点
- [Strength 1 — specific to what they said]
- [Strength 2]

### ⚠️ 需要加强
- [Gap 1 — what was missed or inaccurate]
- [Gap 2]

### 📈 评分明细

| 维度 | 分数 | 说明 |
|------|------|------|
| 准确性 | X/10 | [Factual correctness of answers] |
| 深度 | X/10 | [How deep they went beyond surface] |
| 代码关联 | X/10 | [Did they reference actual code/config] |
| 设计思维 | X/10 | [Trade-off analysis, architecture reasoning] |

### 🏆 综合评分: X/10

### 📊 学习进度: [mastered count]/45 知识点已掌握
```

Scoring rules:
- Average of 4 dimensions, rounded to nearest 0.5
- 9-10: Expert level, can explain design decisions and trade-offs
- 7-8: Solid understanding, knows how and why
- 4-6: Basic understanding, knows what but not deep why
- 1-3: Surface level, needs significant study

---

## Phase 7: Learning Guide

Immediately after evaluation, provide targeted study resources (中文):

```markdown
## 📚 学习指南

### 📂 相关代码
- [file_path](file_path#LX-LY) — 说明这段代码的作用和关键逻辑

### 📄 相关文档
- [README.md](README.md) — 项目整体介绍和功能定位
- [技术文档.md](技术文档.md) — 技术实现细节和配置说明
- [enterprise_rag_architecture.md](enterprise_rag_architecture.md) — EnterpriseRAG架构设计原理
- [enterprise_rag_problems_and_solutions.md](enterprise_rag_problems_and_solutions.md) — 问题解决思路

### 🔗 参考资料
- [External concept name] — 1-sentence explanation of relevance

### 💡 建议学习路径
1. 先阅读 [file] 理解 [what]
2. 再看 [file] 掌握 [implementation detail]
3. 运行 `[command] 实际体验效果
4. 尝试修改 [config/code] 观察变化
5. 使用公开数据集进行验证和测试
```

Guidelines:
- Code references MUST use actual file paths with line numbers where relevant
- Only recommend reading 3-5 key files, not entire codebase
- Include at least one hands-on command the user can run
- External references only for concepts not explained in the codebase (e.g., RRF algorithm, BM25)

---

## Phase 8: Persist Progress

Update `.github/skills/project-learner/references/LEARNING_PROGRESS.md`.

If file doesn't exist, create it from the template in [references/LEARNING_PROGRESS.md](references/LEARNING_PROGRESS.md). If it exists, update it.

### Update Rules

1. **Append** one row to the `Detailed History` table (include Sub-topic ID)
2. **Update** the `Sub-topic Progress` table for the affected sub-topic:
   - 已学 = count of sessions for that sub-topic
   - 最高分 = max score across all sessions for this sub-topic
   - 最近分 = score from this session
   - Status: ≥7 → ✅ 掌握, 4-6 → 🔶 学习中, ≤3 → 🔴 薄弱, 0 sessions → ⬜ 未学习
3. **Recalculate** the `Domain Summary` table:
   - 已掌握 = count of ✅ sub-topics in that domain / total sub-topics in domain
   - 已学习 = count of non-⬜ sub-topics / total sub-topics
   - 平均分 = average score of all studied sub-topics in domain
   - Domain status: all sub-topics ✅ → ✅ 掌握, any studied → 🔶 学习中 or 🔴 薄弱 (based on avg), none → ⬜ 未学习
4. **Update** the `Last updated` timestamp
5. **Update** the session counter `#` (auto-increment)
6. **Update** the overall progress line: `总进度: X/45 知识点已掌握`

---

## Phase 9: Continue or End

After persisting, ask the user (中文):

| Option | Action |
|--------|--------|
| 🔄 继续学习下一个知识点 | Loop back to Phase 3 |
| 🎯 Agent 推荐下一个 | Auto-pick optimal next sub-topic, go to Phase 4 |
| 📋 查看当前学习进度 | Display full progress table |
| 🏁 结束本次学习 | Show session summary, stop |

### Session Summary (on 🏁 end)

```markdown
## 📝 本次学习总结

- 完成知识点: N 个
- 平均得分: X/10
- 最强知识点: [sub-topic] (X/10)
- 需加强知识点: [sub-topic] (X/10)
- 总进度: X/45 知识点已掌握 (XX%)

继续加油！下次建议学习: [recommended sub-topic name]
```

---

## Key Paths

| File | Purpose |
|------|---------|
| `skills/project-learner/references/LEARNING_PROGRESS.md` | Persistent learning state (45 sub-topics) |
| `README.md` | Project overview & product positioning |
| `技术文档.md` | Technical documentation & configuration |
| `enterprise_rag_architecture.md` | EnterpriseRAG architecture design |
| `myproject_for learn/` | Project evolution & problem-solving history |
| `app/` | All source code modules (enterprise_rag/, orchestration/, etc.) |
| `web/` | Frontend interface (Streamlit app) |
| `scripts/` | CLI entry points & evaluation scripts |
| `data/` | EnterpriseRAG-Bench datasets and manifests |
