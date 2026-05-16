# EnterpriseRAG 架构说明

更新时间：2026-05-06

## 1. 架构目标

当前 EnterpriseRAG 的目标不是做一个“只会向量检索然后让 LLM 总结”的轻量 Demo，而是做一条可回归、可调试、可在 Docker 内稳定运行的企业知识问答主链路。

它承担的核心任务有三类：

1. 面向企业知识库做问答。
2. 在 `/agent/chat` 中作为默认企业知识问答能力被调度。
3. 为后续 benchmark、Memory、邮件协作和企业 Agent 编排提供稳定底座。

因此，这套系统设计时强调的不是单点模型能力，而是完整链路工程化：

- 数据进入方式可控
- chunk 粒度可解释
- 检索过程可观测
- 证据提取可调试
- 答案生成可分层
- Docker 内可稳定复现

---

## 2. 总体链路

EnterpriseRAG 当前主链路位于 [service.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/service.py)。

一次问答的调用顺序可以概括为：

1. `build_retrieval_plan(...)`
2. `build_runtime_context_bundle(...)`
3. `retrieve_evidence(...)`
4. `compose_enterprise_answer(...)`

也就是说，一次问题处理不是直接“问题 -> 检索 -> 回答”，而是：

`问题 -> 检索规划 -> 运行时上下文 -> 混合检索 -> 证据打包 -> 答案组织 -> 输出`

这条链既可以通过 `/enterprise-rag/query` 直接调用，也可以在 `/agent/chat` 中被编排层调度。

---

## 3. 外层编排入口

### 3.1 `/enterprise-rag/query`

这是直接的企业问答接口，核心逻辑是：

- 接收问题
- 生成 RetrievalPlan
- 组装运行时上下文
- 检索并构造 EvidencePack
- 输出最终答案与 debug 信息

其优点是链路最短，最适合 benchmark 和问题定位。

### 3.2 `/agent/chat`

这是产品级统一入口，先经过 [planner.py](/E:/leetcode-rag-agent-enterprise/app/orchestration/planner.py) 做能力规划，再决定是否走 `enterprise_rag_query`。

这一层不仅能调企业问答，也能调：

- `persona_or_chitchat`
- `outbound_mail_summary`
- `inbound_mail_summary`
- `inbound_reply_draft`
- 上传内容分析等其他能力

因此 `/agent/chat` 不是 EnterpriseRAG 本身的一部分，但它决定了 EnterpriseRAG 在真实产品中的接入方式。

---

## 4. 数据与索引层

### 4.1 双索引：Dense + Sparse

当前不是单索引架构，而是混合检索：

1. Dense 检索
   - 底层：Chroma
   - embedding：`bge-m3`

2. Sparse 检索
   - 底层：SQLite + FTS5
   - 排序：BM25

对应实现分别位于：

- Dense： [retrieval_orchestrator.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/retrieval_orchestrator.py)
- Sparse： [sparse_index.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/libs/sparse_index.py)

### 4.2 为什么需要双索引

Dense 检索擅长：

- 语义相似
- 同义表达
- 近义概念召回

Sparse 检索擅长：

- 精确术语
- 专有名词
- 文案片段
- 按词匹配的可控性

企业知识里同时存在：

- 会议纪要
- 邮件线程
- issue / ticket
- 文档
- 用户可见文案

如果只做 dense，容易把“语义相近但不关键”的 chunk 召回来。
如果只做 sparse，又很容易错过语义相近但措辞不同的句子。

因此我们用 hybrid retrieval 来兼顾召回广度和术语精度。

---

## 5. Ingestion 与 Chunking

### 5.1 原则

数据进入系统时，先被规范化，再切成 chunk，然后同时写入：

- 向量索引
- sparse FTS 索引
- manifest / 元数据记录

### 5.2 当前的切分方式

chunk 逻辑位于 [chunker.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/ingestion/chunker.py)。

它现在采用 `source-aware chunking`，而不是统一滑窗。

#### `fireflies`

按说话轮次 / 时间块切分：

- 优先按 `[timestamp] speaker: body` 结构提取 turn
- 聚合 2 到 4 个 turn 为一个 chunk
- 保留 `speaker / turn_start / turn_end`

#### `gmail`

按邮件线程结构切分：

- reply block
- quoted reply
- 邮件正文块

#### `slack`

按消息边界聚合：

- 以消息为基本单元
- 聚合若干相邻消息

#### `confluence / google_drive`

按结构切分：

- heading
- section
- paragraph block

#### 其他源

回退到 fixed-window：

- 按 source_type 配置 chunk size
- 保留 overlap

### 5.3 为什么 chunking 很关键

因为后面无论是 dense recall、sparse recall、rerank 还是 sentence extraction，输入单位都是 chunk。

如果 chunk 切得太粗，会出现：

- recommendation 句被背景内容淹没
- 关键句和会议 summary 混在一起
- reranker 更偏爱“概述型 chunk”

如果 chunk 切得太碎，会出现：

- 语义不完整
- 证据割裂
- sparse / dense 分值不稳定

所以 chunking 是整个 RAG 系统中最底层、但影响后续所有环节质量的关键层。

---

## 6. Query Planning

检索前不会直接把问题扔进向量库，而是先在 [query_planner.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/query_planner.py) 中构造 RetrievalPlan。

### 6.1 规划内容

主要包含：

- `source_types`
- `question_type`
- `budget_profile`
- `dense_top_k`
- `sparse_top_k`
- `rerank_top_k`
- `evidence_top_k`
- `expansion_enabled`

### 6.2 `source_types`

通过 hint 推断来源，例如：

- `meeting` -> `fireflies`
- `github` / `pr` -> `github`
- `slack` -> `slack`
- `email` -> `gmail`

### 6.3 `question_type`

当前主要识别：

- `basic`
- `semantic`
- `constrained`
- `conflicting`
- `info_not_found`

不同题型会影响召回范围：

- `basic / fact_lookup` 类问题通常使用 `small` 预算
- `semantic` 类问题通常使用 `medium` 预算
- `constrained / conflicting / recommendation` 类问题通常使用 `large` 预算
- `expanded` 只作为 evidence 不足时的二阶段扩展预算

### 6.4 Adaptive Budget

这一版开始，EnterpriseRAG 不再把“提高质量”简单等同于“固定扩大 top_k”。`RetrievalPlan` 会显式记录 `budget_profile`：

- `small`：dense/sparse/rerank 都较小，服务明确事实查找。
- `medium`：服务普通语义问答。
- `large`：服务 recommendation、constrained、conflicting 等高召回问题。
- `expanded`：只在第一轮候选或证据不足时触发。

当前 GCP onboarding 这类 recommendation case 默认保持 `large`，保留旧主路径的高召回能力；但如果 dense 已经有足够候选，只是 sparse 为空，系统不会再盲目扩展到 `expanded`。

### 6.5 作用

这一层的职责不是回答问题，而是决定：

- 该去哪些来源找
- 候选集要开多大
- 后续证据应该走什么强度的收口策略
- 是否允许 evidence 不足时进入二阶段扩展

---

## 7. Retrieval Orchestrator

核心在 [retrieval_orchestrator.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/retrieval_orchestrator.py)。

完整流程如下：

1. `_retrieve_evidence_once(plan)`
2. `_should_expand_retrieval(plan, evidence)`
3. 必要时 `expand_retrieval_plan(plan)`
4. `_dense_recall(plan)`
5. `_sparse_recall(plan)`
6. `_merge_candidates(plan, dense_docs, sparse_docs)`
7. `_rerank_candidates(plan, docs, retrieval_sources)`
8. `_select_evidence_docs(...)`
9. 构造 citations
10. 调用 `build_evidence_pack(...)`

### 7.1 Dense Recall

通过 Chroma 做向量召回：

- 支持 source_type filter
- 限定 domain 为企业知识域

### 7.2 Sparse Recall

通过 SQLite FTS5 做词法召回：

- 建表：`enterprise_chunks`
- FTS5：`enterprise_chunks_fts`
- 排序：`bm25(...)`

### 7.3 Merge Candidates

不是简单地把 dense 和 sparse 结果拼起来，而是：

- 以 `chunk_id` 去重
- 记录 retrieval source：`dense` / `sparse` / `hybrid`
- 再用 heuristic score 做一轮初排

### 7.4 Candidate Diversification

这一步非常重要：

- 优先保留 `hybrid` 和 `sparse`
- 控制同一 doc 的 chunk 数量
- 避免一个文档刷屏

### 7.5 Rerank

Rerank 默认使用：

- `bge-reranker-v2-m3`

失败时会回退到 heuristic fallback，而不是整条 query 直接崩掉。

### 7.6 Evidence Selection

最终进入 evidence 的 chunk 还会再被限流：

- 同一文档最多保留一定数量
- 保证证据多样性

### 7.7 Expansion Gate

二阶段扩展不是默认行为。系统只有在这些信号出现时才会从当前预算扩展到 `expanded`：

- dense 和 sparse 同时为空
- merged candidates 少于 rerank 预算
- rerank 命中明显不足
- selected evidence 少于最低证据数量
- evidence confidence 过低

单侧召回失败会被谨慎处理：例如 sparse 为空但 dense 已经提供足够候选和 evidence 时，不触发扩展。这避免了“只要 sparse 没命中就把 dense_top_k 继续放大”的浪费。

debug 字段会记录：

- `budget_profile`
- `expansion_triggered`
- `expansion_reason`
- `first_pass_counts`
- `second_pass_counts`

---

## 8. Evidence Pack

EvidencePack 是当前 EnterpriseRAG 和传统“chunk 检索式 RAG”的最大区别之一。

它不只是一组 chunk citation，而是进一步构建了：

- `supporting_doc_ids`
- `supporting_fact_details`
- `supporting_facts`
- `canonical_facts`
- `excluded_facts`

实现位于 [evidence_pack.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/evidence_pack.py)。

### 8.1 Sentence-level fact extraction

系统会从 citation 的全文中切句：

- 不是只用 snippet
- 而是读取 chunk full content

然后对句子候选进行打分。

### 8.2 打分信号

包括：

- lexical overlap
- phrase match
- answer fact hint
- retrieval source boost
- action hint
- question focus hint
- source-aware boost
- noise penalty

### 8.3 Supporting Fact Details

每条 fact 会保留：

- `doc_id`
- `chunk_id`
- `sentence_text`
- `score`
- `source_type`
- `title`
- `speaker`
- `timestamp`

这让我们能够对“系统为什么选了这句”做调试。

### 8.4 Canonical Facts

系统会进一步把句子归一化为 canonical facts：

- 去掉 transcript 噪声
- 去掉时间戳
- 去掉发言人
- 去掉部分会议口语前缀

同时为 fact 打上：

- `fact_type`
- `priority`
- `source_fact_ids`

并区分：

- `core`
- `peripheral / excluded`

---

## 9. Answer Composer

核心在 [answer_composer.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/answer_composer.py)。

这是系统目前最复杂的一层。

### 9.1 目标

这一层的目标不是“把 evidence 喂给 LLM”，而是把 evidence 组织成更接近最终答案的结构化中间表示。

### 9.2 当前流程

1. `answer_intent` 分类
2. `question_focus` 检测
3. `core_facts / secondary_facts` 划分
4. recommendation 类问题做 `answer_slots` 抽取
5. 构造 `answer_plan`
6. recommendation 由 LLM 基于 `core_facts / answer_slots / answer_plan` 渲染用户可见答案
7. 非 recommendation 走 intent-specific 生成
8. transcript-style post-check
9. fallback

### 9.3 `answer_intent`

当前支持：

- `recommendation`
- `explanation`
- `fact_lookup`
- `mixed`

### 9.4 `question_focus`

用于检测用户是否在追问细节，例如：

- error state
- message text
- wording / copy
- SLA
- support channel
- button / field

这一步能避免某些原本 secondary 的内容在被明确追问时仍被压低。

当前 `question_focus` 会稳定输出：

- `focus_type`
- `focused_entities`
- `focused_slots`
- `confidence`
- `source_fact_ids`

### 9.5 Recommendation 结构化渲染路径

Recommendation 类问题当前不是把检索到的句子直接拼接，也不是为某个 bad case 写固定答案，而是：

1. 提取 `answer_slots`
2. 计算 `slot_coverage`
3. 构造 `answer_plan`
4. LLM 只读取 `core_facts / secondary_facts / answer_slots / answer_plan`
5. 规则 post-check 检查是否泄漏 transcript 风格
6. slot 模板只作为 LLM 失败或格式泄漏时的安全兜底

槽位包括：

- `issue_context`
- `recommendation_summary`
- `recommended_action`
- `recommended_state`
- `user_facing_message`
- `recovery_step`
- `fallback_path`
- `error_avoidance`
- `last_checked_feedback`
- `support_path`

### 9.6 Post-check

LLM 渲染后的答案还会经过规则检查：

- 是否泄漏 transcript 风格
- 是否出现时间戳
- 是否出现发言人
- 是否出现大段英文原句
- 是否英文占比过高

如果失败，系统会退回保守结构化兜底答案，并在 debug 中记录 `fallback_reason`。

---

## 10. Runtime Context / Memory

EnterpriseRAG 不是完全孤立的，它还可以带上运行时上下文：

- session memory
- transcript memory
- workspace memory
- user model context

这些来自 `build_runtime_context_bundle(...)`，由 [service.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/service.py) 在检索前组装。

当前它的定位更像“附加上下文增强”，而不是主检索来源。

也就是说：

- 主证据仍来自 EnterpriseRAG 索引
- Memory 主要用于给答案增加会话相关上下文

---

## 11. Debug 与 Benchmark

这是当前系统能持续迭代的重要原因。

返回结果中已经包含很多调试字段，例如：

- `retrieval_stage_debug`
- `rerank_debug`
- `supporting_fact_details`
- `canonical_facts`
- `answer_debug`
- `retrieval_plan`

其中 `answer_debug` 又包含：

- `answer_intent`
- `classifier_source`
- `classifier_confidence`
- `question_focus`
- `core_facts`
- `secondary_facts`
- `answer_slots`
- `slot_coverage`
- `answer_plan`
- `draft_answer`
- `rewritten_answer`
- `fallback_reason`
- `final_answer_source`
- `deduped_citations`

这让系统不再是黑盒，我们能明确区分问题到底出在：

- 召回
- rerank
- sentence fact
- canonicalization
- template assembly
- polish
- fallback

---

## 12. Docker-first 运行约束

当前项目明确以 Docker 为唯一验收环境。

关键点包括：

- 本地模型挂载到 `/app/external-models/...`
- 所有 ingest / query / benchmark 都优先在容器内运行
- `web` 入口固定为 [http://localhost:8511](http://localhost:8511)

这样做的原因是：

- 避免本机 Python 环境与容器环境分裂
- 避免“本地能跑、容器里不行”的假象
- 固定 benchmark 和回归的真实运行条件

---

## 13. 当前架构的本质特点

如果用一句话概括，当前 EnterpriseRAG 已经从：

“向量召回 + 直接总结”

演进为：

“带 query planning、hybrid retrieval、sentence evidence、canonical facts、answer intent、template assembly、post-check 和 debug trace 的企业知识问答系统”

它的核心价值不只是召回文档，而是：

- 找到正确文档
- 找到文档中的关键句
- 把关键句压成可回答问题的事实
- 按问题类型组织成用户可读答案
- 在产品入口中稳定接入

这也是它和简单 RAG Demo 的根本区别。

---

## 14. 2026-05-07 补充：EnterpriseRAG 现在如何接入新的 Agent 主链路

这一轮之后，EnterpriseRAG 已经不再孤立存在，而是被更明确地接进统一 Agent 主链：

- 安全硬分支
- 轻量 Router
- Fast Path / Slow Path(ReAct)
- observation-first renderer

也就是说，EnterpriseRAG 当前不是一个“单独页面上的问答 demo”，而是 `/agent/chat` 里企业事实问答的正式事实来源。

### 14.1 企业事实题不再默认先进重型 think

这轮修正了一个很重要的外层问题：

- 过去很多请求都会先进入重型 ReAct think
- 企业事实题也会先空想，再决定是否检索

这带来的问题是：

- 响应慢
- timeout 更容易暴露
- 简单事实题也可能走错链路

现在的主思路是：

- Router 先判断是不是明确的企业事实问答
- 如果是，就优先走 Fast Path
- 直接调用 `enterprise_rag_query`
- 再由统一 renderer 基于 observation/evidence 输出答案

因此当前更准确的链路描述是：

`/agent/chat -> L0 router -> enterprise_fact fast path -> enterprise_rag_query -> final renderer`

### 14.2 EnterpriseRAG 与 observation-first Agent loop 已经对齐

前面这个项目最大的问题之一，是“规则替系统写答案”。  
这一轮之后，EnterpriseRAG 所在主链也开始遵守更清楚的职责分工：

- Router / controller
  - 负责判断要不要检索
  - 负责工具调用和 grounding 守卫
- EnterpriseRAG
  - 负责提供企业证据、supporting facts、citations、answer_debug
- Renderer
  - 负责基于 observation 做自然语言编排

也就是说，当前更接近真正的 agent 范式：

- rules 管状态和边界
- function calling 拿 observation
- LLM 基于 observation 组织输出

而不是再回到“RAG 工具给了结果，规则层顺手写死最终 answer”的老路。

### 14.3 这轮还证明了一件事：检索对了，不代表产品就能落地

这一轮的很多实际问题，已经不再是 EnterpriseRAG 检不到事实，而是：

- 邮件正文不会按要求写
- clarification 说法被规则写死
- pending draft 不会 patch
- 附件来源污染正文

这说明一个很重要的架构事实：

**EnterpriseRAG 只解决“事实来自哪里”，但产品是否可落地还取决于外层 Agent 的状态管理、来源边界和用户可见表达职责分配。**

因此当前系统的真实层次可以更明确地写成：

- EnterpriseRAG
  - 解决企业知识事实 grounding
- Memory
  - 解决上下文连续性
- Router / ReAct
  - 解决何时读 memory、何时调工具、何时检索
- Renderer
  - 解决如何把 observations 组织成用户可读回答
- Mail authoring / task state
  - 解决动作型场景里的状态演进和来源边界

### 14.4 当前仍然保留的边界与待继续收口部分

虽然主链已经清楚很多，但还要承认当前并非“全系统都完全收口”：

- 外层仍有少量 legacy 逻辑需要继续边缘化
- clarification / unsupported 的 deterministic fallback 还保留在安全边界中
- mixed request 的真实回归样本还需要继续增加
- 企业事实、memory、mail authoring 的跨域混合问法还需要更多验证

因此这轮更准确的表述是：

**EnterpriseRAG 的内部架构已经相对成型，而它与外层 Agent 主链的耦合关系，这一轮才真正被理顺。**
