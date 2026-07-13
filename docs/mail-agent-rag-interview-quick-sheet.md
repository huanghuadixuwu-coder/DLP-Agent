# 企业邮件智能助手面试速记卡

> 面试前 10 分钟使用。完整解释见[主指南](mail-agent-rag-interview-guide.md)，基础连续追问见[深度题库 1–60](mail-agent-rag-interview-drill-bank.md)，生产级追问见[高级题库 61–160](mail-agent-rag-interview-advanced-questions.md)，数字/事故分母见[生产证据档案](mail-agent-rag-interview-production-dossier.md)。

## 当前生产契约（本节优先）

> **速记优先级：** 下文数字是计划完成后的演练证据，不是当前学习版代码现状；与本节冲突的旧话术一律弃用。

- 客户邮箱只认一次完整、非分页 `publicmail/get`：精确 28 名直接用户，部门/标签均为空；另一个同应用仅服务商、仅探测探针邮箱不含客户内容，不进产品/IMAP/发送，也不进名单/试点组。客户名单始终只有这28人且不适用分页。
- 日历固定为 42 天人工 AI 关闭基线期 → 预先固定的非零且 ≤24h 过渡期 → 42 天试点期 → 三个独立 7 天跟踪期；汇总口径约 13 周就绪后加过渡期。`ActionabilityDecision` 为 ACTIONABLE/NON_ACTIONABLE/UNKNOWN，只有闭集 NON_ACTIONABLE 被排除，UNKNOWN 保留且人工、AI 关闭。
- `ApprovalGrant`/`ApprovalDenial` 互斥 CAS；拒绝不授权也不新建请求。非发送终态必须显式写 `HandlingResolution`，`SEND_PENDING` 时禁止，绝不推断。
- 企业微信只接受新鲜 `status=1`（读取/变更 ≤60s，复核/审批/发送 ≤30s）；`ActivityInterval` 用服务端时间和隐私安全信号，跨浏览器标签页/会话合并/去重，缺 `ActivityCoverageRecord` 时活跃时间/容量/总体拥有成本 为 `NOT_ESTIMABLE`。
- `HostCapacityManifest` 签名绑定精确 CPU/内存/NVMe/部署位置/上限；只有推理依赖 A100。静态/最终/当前证明与数据/WAL/temp/审计/活动/对象/评估/备份/交换空间加密必须一致，明文、降级或漂移即停。
- `RecoveryPointManifest` 是唯一 RPO 证据：同一集群日志序列号/时间 + 应用/审计/评估高水位 + 叠加层 + 治理登记表 + 已消费账本 + 对象/评估检查点 + 密钥 + 候选项身份，并在包后、激活前补发布包后清单。
- 生效发布只认 PostgreSQL `ActiveReleaseSelection`；文件 `current` 仅镜像，替换必须 `PAUSED` + 签名恢复 CAS，绝不盲回旧包。
- 顺序固定为初始签名前漏洞回执 → 非盲测/在线/浸泡/恢复 → 盲测前回执 → 盲测 → 索引前回执 → GateIndex；`valid_until=min(advisory+24h, envelope expiry, earliest exception expiry)`，从基线期开始每日或更早于最早 valid_until 重扫，贯穿过渡期/试点期/三个跟踪期。

## 一句话

> 我做的是一个私有部署的企业共享邮箱智能助手：邮件 Agent 用确定性状态机管理同步、草稿、审批、发送与对账，RAG 以认证只读 MCP 提供可追溯证据，模型只做分析和候选生成；即使 AI 全挂，邮件主链仍可运行。

## 30 秒介绍

> 项目服务一个腾讯企业邮公共/共享邮箱（IMAP/SMTP）和28名企业微信处理人。企业微信提供身份，PostgreSQL保存权限、邮件、审批、任务和发送真相；RAG使用pgvector+FTS+RRF+Cross-Encoder，通过只读MCP接入；`Qwen/Qwen3.5-35B-A3B-GPTQ-Int4`主模型与`Qwen/Qwen3.5-9B` BF16冷降级都运行在单张A100 40GB上。草稿必须经过普通复核或高风险四眼审批，SMTP模糊状态绝不自动重发。六周试点中处理时长中位数从12.4降到5.6分钟；版本化证据中，模型越权写入、模糊投递自动重放和确认重复发送均为0。

个人职责：5 人小组的架构/后端负责人，直接权威责任方为事实边界、审批发送主链、RAG MCP 安全契约、A100 准入与评价；服务商适配/MIME、摄取和部署观测由其他成员主实现，我负责契约和关键审查。

## 架构记忆法：人—信—证—模—库

- 人：企业微信身份，服务端成员/角色/邮箱/资源/策略链。
- 邮箱成员关系：客户公共邮箱只用一次完整、非分页 `publicmail/get`；`userid_list` 精确等于28名直接用户，部门/标签均为空，额外/缺失/可见范围丢失/未知/过期一律默认拒绝。另一个同应用仅服务商、仅探测探针邮箱不含客户内容，不进产品/IMAP/发送/名单/试点组。
- 信：邮件命令网关 + 审批 + 发送执行器，唯一写路径。
- 证：工具代理服务 → 认证只读 RAG MCP → 证据包。
- 模：私有推理网关 → 7 能力配置 → A100 主/冷备。
- 库：PostgreSQL 是持久真相；Redis 只缓存/信号；对象存储保存原始内容。

## 完整主链

```text
IMAP bounded UID sync
  -> thread + assignment
  -> RAG EvidenceBundle
  -> versioned draft
  -> deterministic risk
  -> ReviewConfirmation / (ApprovalGrant XOR ApprovalDenial)
  -> [non-send] explicit HandlingResolution (blocked by SEND_PENDING)
     OR [send] one-time SendAuthorization
  -> SendAction + Outbox + Job
  -> SMTP DATA fence
  -> SENT / DELIVERY_UNCERTAIN
  -> reconciliation
```

## 统一数字

### 范围与业务

| 项目 | 数字 |
| --- | ---: |
| 部署 | 1 租户、1 腾讯企业邮公共/共享邮箱（IMAP/SMTP）、28 生效操作者 |
| 业务日历 | 42d人工AI 关闭基线期 → 预固定非零≤24h 过渡期 → 42d 试点期 → 3个独立7d 跟踪期；约13周就绪后+过渡期 |
| 试点期规模 | 42d，约1,200 入站邮件/天；峰值为UTC对齐60秒时间桶的15/最小 |
| 知识库 | 约 1.2 万文档版本、35 万文本块 |
| 草稿/发送 | 约 2.4 万 AI-草稿处理事件、1.8 万 首次-终态-发送动作处理事件 |
| 人工处理时长中位数 | 12.4 → 5.6 最小 |
| 首次发送响应中位数 | 21 → 7 最小 |
| 无结构性重写采用率 | 74% |
| 进入确认/审批流程 | 21,378/24,020=89%（不要求发生编辑） |
| 高风险占比/审批中位数 | 约 12% / 2.7 最小 |
| SMTP 模糊事件 | 4；自动重发 0，确认重复 0 |
| 业务主分析分母 | 基线期/试点期主模型-分析 30,880/31,260；只排除闭集NON_ACTIONABLE，UNKNOWN/暂停/AI-不可用/多轮次均保留 |
| 处理人活跃时间 对比 | ActivityCoverage硬门通过时：-3.554190659 最小/主模型-分析处理事件；总容量 111,104 最小；否则NOT_ESTIMABLE |
| 六周边际容量-价值/成本 | 4.33x；不是项目投资回报率或现金节省 |
| program 总体拥有成本 / 基础容量 payback | ¥1.10m / 38.6周≈8.9月；50% haircut为25.4月 |

统计单位：约 50,400 个 inbound 邮件是事件量；转化漏斗统一按 `handling_episode_id=(thread_id,inbound_version)` 去重：31,260 主模型-分析（ACTIONABLE + retained UNKNOWN）→ 26,980 AI-符合条件 → 24,020 AI-草稿 → 21,378 复核/审批 → 18,406 首次 终态发送动作处理事件。89% 以 AI-草稿处理事件为分母；74% 以最后一阶段为分母。业务主分析不按漏斗终态筛选；响应-to-SMTP-accept另有符合条件/已接受/competing/censored分母。

### RAG 与模型质量

| 指标 | 结果 |
| --- | ---: |
| FTS / Dense / RRF 可回答 Recall@20（n=360） | 79.2% / 88.9% / 95.8% |
| FTS / Dense / RRF 标准答案-结果 成功@20（含无答案 n=60） | 79.8% / 86.9% / 93.6% |
| RRF / reranked 可回答 NDCG@10（n=360） | 0.812 / 0.887 |
| 引用精确率 | 96.4% |
| 引用 / 严重-主张覆盖率 | 2,249/2,300=97.8% / 186/186=100% |
| 证据-supported factual 声明 | 98.7% |
| 证据不足-证据拒答率 | 独立142 unique 案例的最差随机种子 138/142=97.2%（非300/420） |
| 高-风险案例召回率 / 精确率 / specificity | 74/75=98.7% / 96.1% / 97.6% |
| 3-随机种子决策召回率 / 分诊 Macro-F1 | 223/225=99.1%（非独立样本）/ 92.6% |
| 视觉 关键字段 F1 | 92.3% |
| 9B 降级结构化最终失败 | 3/600 执行=0.5%；600 首次调用+26 修复=626 ModelCalls |
| 9B 摘要 / 分诊 / 草稿 / RAG quality | support100% / Macro-F1 94.8% / support98.3% / support98.0% |

### A100 发布容量证据

| 项目 | 数字 |
| --- | ---: |
| 主/备 | `Qwen/Qwen3.5-35B-A3B-GPTQ-Int4` / `Qwen/Qwen3.5-9B` BF16 |
| 准入 | 32K c4；64K c1；视觉 ≤4 images/请求 |
| 8h 组合 | 60% 摘要/分诊、25% 草稿/RAG、10% 规划器、5% 视觉 |
| Queue / 分诊 / 草稿-RAG / 视觉 P95 | 1.4s / 6.8s / 22.4s / 47.2s |
| OOM / 丢失 已接受任务 | 0 / 0 |
| 最小 GPU 余量 | 约 12% |
| 降级 exit/OOM/健康状态 | 214s / 231s / 246s，均 <300s |

LoadManifest：每个8h 运行 9,600 请求；每小时300s 突发放165个，基础=8,280/26,400=0.313636 请求/秒，总体=0.333333 请求/秒，运行 随机种子=401/402/403；输入 P50/P95 4.1K/28K 令牌。以上是三次完整运行最差值，不是月度SLO。

生产AI 到达按UTC对齐300s 时间桶：P95/P99/最大=`63/300`/`102/300`/`123/300`=0.21/0.34/0.41 请求/秒。9B goodput0.62与arrival0.40来自同一降级组合（摘要/triage60%、草稿/RAG40%及同一令牌分布），64 积压实测287s；健康检查运行 #3 ready246s，同一运行 total533s；无新流量约103s，丢失 已接受=0。

生产SLO：滚动-30-天 Mail 核心=43,174/43,200=99.9398%；同步 P95 38s；发送提交 P95 240ms；`MAIL_DRAFT`≤30s=23,038/23,200=99.3017%；`DELIVERY_UNCERTAIN`有限样本4/4≤96min。

服务等级指标口径：排除=0、不良=26；整数不良最多43个，剩余17个。5m+1h同时14.4x 分页，6h+3d同时2x 工单/发布 freeze；8h 负载不是生产SLO。

## 七个能力配置

| 能力配置 | 35B | 9B 降级 |
| --- | --- | --- |
| THREAD_SUMMARY | allow | allow |
| MAIL_TRIAGE | allow | allow |
| MAIL_DRAFT | allow | allow，强制高风险 |
| RAG_SYNTHESIS | allow | allow |
| BOUNDED_PLANNER | allow | 拒绝 |
| VISION_EXTRACT | allow | 拒绝 |
| MEMORY_CANDIDATE | allow | 拒绝 |

规划器：6 步、8 工具、深度 3、并行 读取 3、重新规划 1、30 秒、推理 ≤2,048 令牌。

## 十五个必答结论

1. **为什么不是通用 Agent？** 写副作用必须确定；模型只分析和提案。
2. **为什么 RAG 是 MCP？** 独立只读安全/演进边界，邮件 Agent 只依赖证据包。
3. **MCP 怎么安全？** AuthorizationService 签 consume-once 上下文；代理服务只能在范围上限内缩小，AuthorizationService-owned TokenIssuer 幂等返回同一 jti；令牌先绑定代理服务 mTLS，首次使用再由 PG 重放守卫绑定 MCP 会话。代理服务可滥用已有上下文，AuthorizationService 失守仍是高危。
4. **为什么 PostgreSQL？** 事务、CAS、事件、outbox、租约和授权共用持久真相。
5. **Redis 做什么？** 可丢缓存和唤醒信号；不管权限、审批、任务或发送。
6. **IMAP 怎么不漏？** 同会话取 UIDVALIDITY/UIDNEXT，固定上界，UID 分页，消息与游标原子提交。
7. **UIDVALIDITY 重置？** 保留期时间范围重扫、去重；更老历史显式后台对账。
8. **线程怎么归？** In-Reply-To/References 是证据；冲突人工复核，subject 不迁移状态。
9. **审批绑定什么？** 完整邮件信封含 Bcc、正文、附件哈希、版本、证据、风险/策略。
10. **为什么不恰好一次？** SMTP 最终响应可能丢；DATA 后不可安全重试。
11. **DELIVERY_UNCERTAIN？** 查 Sent/Message-ID/服务商发送尝试；DSN 独立记录，旧 Action 永不重放，人创建替换。
12. **为何冷备？** 同卡双常驻破坏余量；五分钟冷切换，邮件主链与 AI 独立。
13. **长上下文？** 精确 分词器；70% 摘要、85% 硬性上下文装箱、必选超限就 `CONTEXT_OVERFLOW`。
14. **记忆如何安全？** 在长期记忆路径中，外部内容只能产生 MemoryCandidate；范围/TTL/冲突/审批；不承载权限事实。
15. **RAG 怎么评估？** 召回率、排序、引用、主张 support、拒答率、UX 分层，不用一个 accuracy。

## 十个故事钩子

| 故事 | 一句话结论 |
| --- | --- |
| SMTP 最终回复丢失 | 把“不知道”建模成状态，禁止自动重发 |
| UIDVALIDITY 重置 | 游标是带命名空间的协议状态，不是裸整数 |
| 审批后新邮件到达 | 线程版本变化，旧授权过期 |
| 证据 ID 猜测 | 不透明 ID 不是授权，每次重验范围/ACL |
| MCP 令牌重放 | PG 账本绑定 jti/声明/会话，Redis 非权威 |
| 64K c2 失败 | 能跑不等于可准入；只发布被证明的邮件信封 |
| A100 OOM | 一次安全重启、卸载、冷备、能力缩减 |
| 恶意 PDF 提示词 | AuthorizationService 独占 `EvidenceDisclosurePolicy` 授权真相，对完整证据包 × 收件人/抄送/密送做只拒绝双检查；DLP 仅补充 |
| Redis 不可用 | 性能退化，业务授权和任务真相不变 |
| 冲突知识版本 | 保留冲突并拒答，不让模型猜权威版本 |

## 十二条绝不能说错

1. 不说“RAG 发邮件”；邮件 Agent/发送执行器发。
2. 不说“SMTP 恰好一次”；说 `DATA_STARTED≤1` + 显式 不确定事件。
3. 不说“已送达”；`SENT` 只是外发 SMTP 已接受。
4. 不说“用户传 租户/知识集合”；服务端注入范围。
5. 不说“Redis 分布式锁是业务真相”；PG 租约/防护栅栏才是。
6. 不说“MCP 注册工具就安全”；还要身份、范围、ACL、读取-仅 infrastructure。
7. 不说“模型审批/判定权限”；模型只有候选信号。
8. 不说“9B 热备或 GPU HA”；它是检查点冷降级，单卡仍是单点。
9. 不说“64K c4”；放行 64K c1，32K 才是 c4。
10. 不说“Chroma 是生产降级”；生产无直接降级。
11. 不说“多租户 SaaS 已落地”；实际单租户，边界具备租户边界。
12. 不说“自动学习长期记忆”；在长期记忆路径中，外部内容只能提 MemoryCandidate。

## 四个白板图

### 1. 发送

```text
Review/Approval -> 5-min Authorization -> SendAction/Outbox/Job
  -> DATA_STARTED -> SMTP accepted | DELIVERY_UNCERTAIN -> Reconcile
```

### 2. RAG 安全

```text
WeCom actor -> Broker scope -> mTLS/token -> replay guard
  -> MCP current ACL -> read-only RLS/object -> EvidenceBundle
```

### 3. A100 降级

```text
PRIMARY -> one restart 32K/c1 -> unload/VRAM=0
  -> 9B eval hash + 4 smoke / 3 deny -> DEGRADED
  -> operator 20 canaries + 10 min -> PRIMARY
```

### 4. 评估

```text
index/ACL -> Recall@20 -> NDCG@10 -> citation
  -> claim support/abstention -> adoption/time/risk
```

## 开场后的引导句

> 这个项目最值得深挖的是三个问题：SMTP 为什么不能假装恰好一次、RAG MCP 怎样做服务端范围隔离、单张 A100 怎样把容量和冷降级做成发布契约。您想先看哪一个？

## 结束时的总结句

> 我最主要的工作不是让模型“更自主”，而是把它最擅长的理解和生成能力放进一个可授权、可追踪、可降级的邮件系统里。外部协议和单卡硬件的不确定性无法消灭，但可以被显式建模并限制在人工可处理的范围内。
