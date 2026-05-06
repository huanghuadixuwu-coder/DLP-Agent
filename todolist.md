# 项目 ToDo 总表

更新时间：2026-05-06  
项目目录：`E:\leetcode-rag-agent-enterprise`

状态说明：
- `[x]` 已完成
- `[-]` 进行中 / 已部分落地
- `[ ]` 未完成

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
- `[ ]` 继续完善依赖图、并行执行和部分失败聚合

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
- `[-]` workspace memory hybrid search 已落地
- `[ ]` 把新的 context assembler 完整接入主推理链路

## 7. 下一阶段优先级
1. `[ ]` 继续提升 recommendation 类问题的中文骨架答案质量
2. `[ ]` 继续提升同主题不同问法下的 answer intent 稳定性
3. `[ ]` 在 Docker 内重跑更大规模 benchmark，刷新稳定基线
4. `[ ]` 继续收口 Hermes memory 到主推理链路
5. `[ ]` 继续优化 DLP 外发摘要、邮件回复和异常治理
