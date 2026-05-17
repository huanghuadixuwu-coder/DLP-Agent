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

##5/17 更新
不是单人 Demo 了，也还不是生产级 SaaS。更准确地说，是“可多人化、可治理、可观测”的基础层已经搭起来，但还没通过足够多的隔离、并发、邮件和 RAG 回归来宣称可稳定给多人真实使用。
已经可以比较硬地讲：
项目不再只是单用户 Streamlit demo，已经有 ActorContext，包含 tenant_id / user_id / workspace_id / roles / session_id / conversation_id。
/agent/chat、EnterpriseRAG、DLP task、memory、workspace retrieval 已开始传递 actor context。
RAG dense/sparse 检索已经支持 tenant/workspace filter。
conversation/task list 已经有 tenant/user/workspace 过滤边界。
side-effectful 能力，比如 mail send、DLP approve、RAG ingest/benchmark，已经有 role permission check。
已经有 Redis-backed rate limit、Celery async ingest/benchmark、queue health、Prometheus 指标基础。
Admin API v1 已能查看 queue health、policy version、memory candidates、RAG manifest。
Docker 内 compileall、权限失败 observation、queue health、enterprise queue listener 已验证。
但现在还不能说“生产可用”，主要缺这几块：

多用户隔离还没有完整 Docker 回归，比如 user A 的 memory/task 是否绝不被 user B 看到。
租户级 RAG 隔离还没有端到端验证，比如 tenant A 不能检索 tenant B 的文档。
权限失败目前能返回结构化 detail，但还没完全走 renderer 生成自然语言说明。
mail draft / patch / confirm / approval / send 还缺真实 Docker 全链路回归。
EnterpriseRAG 还需要扩大 benchmark，尤其继续看 answer_fact_coverage 和错误文档混入。
Memory pending review 还没最小落地，高风险 memory candidate 现在有方向，但缺审核闭环。
并发只是有 backpressure/queue 基础，还没做压测和降级策略验证。
下一步优先级
我建议下一轮不要急着加 PDF、GraphRAG、日历、腾讯会议。现在最值钱的是把 L2 打实，推进到 L3：可控多人内测版。

先做“隔离回归套件”
目标：证明多用户、多租户不是字段摆设。
要测：两个 user_id 的 conversation/memory/task 不互相可见；两个 tenant_id 的 EnterpriseRAG query 不能跨租户命中文档；无权限用户不能 send/approve/ingest。

再做“异步任务闭环”
目标：ingest/benchmark 不只是能入队，还能查状态、看失败原因、在 Admin API 里可追踪。
要补：task_id 状态查询、queue health 更细、worker failure observation、Prometheus 指标确认。

然后做“产品主链真实回归”
目标：把面试和演示里最容易被追问的链路跑扎实。
要跑：mail draft/patch/confirm/approval/send；GCP onboarding RAG case 与同主题不同问法；contextual recall/workspace memory/user_model read；policy hot update；doc-level invalidation。

最后补“Memory pending review”
目标：让高风险偏好、长期记忆、策略类记忆不会自动污染行为。
要补：pending candidates 查询、approve/reject 后端接口、ReAct read 展示审核状态。

一句话判断：我们现在已经从“能演示”进入“有企业系统骨架”的阶段；下一步不是继续扩功能面，而是把隔离、权限、任务状态、邮件/RAG/memory 回归做成可信证据。这样面试时就能从“我做了 RAG Agent”升级成“我做了一个具备多租户、权限、队列、审计和回归基线的企业 Agent 后端”。

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

