# EnterpriseRAG 遇到的问题、原因与解决方案

更新时间：2026-05-06

## 1. 这份文档的目的

这份文档专门回答三个问题：

1. 我们在 EnterpriseRAG 上到底遇到了哪些问题？
2. 这些问题分别出在什么层？
3. 我们是怎么逐步解决的？

它和 [enterprise_rag_architecture.md](/E:/leetcode-rag-agent-enterprise/enterprise_rag_architecture.md) 的关系是：

- 架构文档讲“系统现在怎么设计”
- 本文讲“系统为什么会设计成这样”

也就是说，本文重点是问题演化、踩坑路径和修复思路。

---

## 2. 最早的误判：以为是检索问题，其实主问题在答案层

一开始我们最容易产生的直觉是：

- 回答不好
- 那应该是没检索到

但 benchmark 很早就给出了一个非常关键的信号：

- `average_doc_recall = 1.0`
- `average_evidence_fact_coverage = 0.0`
- `average_answer_fact_coverage = 0.0`

这说明：

1. 文档其实已经被召回了
2. 但证据事实没有正确落下来
3. 最终答案也没有把 gold facts 表达出来

也就是说，最早最大的认知修正是：

**主问题不是“找不到文档”，而是“找到了文档，却没有把正确证据组织成正确答案”。**

---

## 3. 检索层的问题

虽然主问题不完全在检索层，但检索层确实存在多种质量问题。

### 3.1 只靠 dense 会带来语义漂移

如果只做向量召回，会出现：

- 语义相近但不关键的 chunk 被带进来
- 会议主题相似的其他文档被混入
- 推荐问题中，“概述句”被排到“具体建议句”前面

在企业语料中，这种问题尤其常见，因为很多会议文档都围绕相近主题，比如：

- onboarding
- subscription
- entitlement
- support

只靠 dense 很容易召回“主题相关”但不“问题直答”的内容。

### 3.2 只靠 sparse 又不够

如果只做 sparse / BM25，会出现另一类问题：

- 同义表达召不回来
- 语义接近但措辞不同的建议句容易漏掉
- 会议里的自然口语表达不一定和用户问法直接对齐

因此我们最终保留 hybrid retrieval，而不是切回单检索。

### 3.3 Dense + Sparse 合并后仍有 candidate 失真

最早的 hybrid 不是没问题，而是：

- dense 候选可能很多
- sparse 命中的关键词 chunk 反而被冲掉
- 同一文档的多个 chunk 重复出现
- 一个 doc 的弱 chunk 抢掉别的 doc 的强 chunk

这会导致：

- rerank 输入质量下降
- 后续 evidence 太集中在某些“表面相关 chunk”

### 3.4 解决方式

我们对检索层做的修复包括：

1. 继续保留 `dense + sparse + reranker`
2. 在 merge 时以 `chunk_id` 去重
3. 记录 `dense / sparse / hybrid` 来源
4. 增加 heuristic candidate score
5. 增加 diversification，优先保留 `hybrid` 和 `sparse`
6. 限制单文档入选 chunk 数量
7. rerank 异常时做 heuristic fallback

也就是说，解决方式不是“换检索模型”，而是把候选编排得更像一个真正的 retrieval pipeline。

---

## 4. Chunking 的问题

### 4.1 最早的 chunk 过于朴素

最开始 chunk 更接近固定长度滑窗：

- 按字符窗口切
- 少量 overlap
- 不够关注 source 结构

这在一般文本里还能工作，但在企业数据里有明显缺点，尤其是会议纪要：

- 推荐句可能落在 chunk 边界
- 关键建议和背景总结混在一起
- action items、自我介绍、流程句会污染答案候选

### 4.2 为什么 meeting / mail 特别吃亏

像 `fireflies` 这类会议转录，其实有明显结构：

- 时间戳
- 发言人
- turn
- 行动项
- 推荐句

如果不按结构切，RAG 系统看到的就只是“一大段混合文本”，而不是“几句明确建议”。

同理，邮件线程也有：

- 主邮件
- quoted reply
- forward
- follow-up

如果这些结构被打平，系统就很难知道哪一段才是当前问题真正依赖的证据。

### 4.3 解决方式

我们后来把 chunking 升级成 `source-aware chunking`：

- `fireflies`：按 turn / time block 切
- `gmail`：按 reply / quote / block 切
- `slack`：按 message block 切
- `confluence / google_drive`：按 heading / section 切
- 其他源：保留 fixed-window fallback

这一步并没有神奇地“直接提升最终答案质量”，但它显著提高了下游 evidence extraction 的输入质量。

---

## 5. Rerank 与 Evidence Selection 的问题

### 5.1 不是 rerank 没有，而是 rerank 目标不够“answer-aware”

最早我们虽然已经有 reranker，但仍然会出现：

- 语义概述句排到前面
- 真正的 recommendation 句没有进入 top evidence
- 会议背景句比 UI / wording / retry 句更靠前

这说明 rerank 并不自动等于“最终答案更好”。

### 5.2 原因

原因在于：

- rerank 看的是 query-passages 相关性
- 但 recommendation 类问题真正要的，不只是“相关”
- 而是“最能直接回答怎么做”

换句话说：

`相关性` 不等于 `可回答性`

### 5.3 解决方式

我们增加了 answerability 方向的辅助排序思路，例如：

- 优先 recommendation-rich chunk
- 优先包含 `pending / retry / refresh / support / not entitled` 的 chunk
- 降低 summary / intro / action items 的优先级

同时在 evidence selection 阶段继续限制单文档刷屏，尽量保证证据不只是“重复地取同一类 chunk”。

---

## 6. 证据层的问题：把 chunk 当证据，太粗了

### 6.1 最早的 supporting facts 太粗

在早期实现里，系统虽然有 citations，但 supporting facts 很大程度上还是 chunk 级的，或者只是在 snippet 上做轻量拼接。

这会带来几个问题：

- 一整段都相关，但只有其中一句真正回答问题
- 一段里既有正确建议，也有会议闲聊
- 最终答案器拿到的是“半干净、半噪声”的大块文本

### 6.2 解决方式：sentence-level evidence extraction

我们后来把证据层推进了一层：

1. 对 chunk 做句子切分
2. 对 sentence candidates 打分
3. 选出 `supporting_fact_details`
4. 再构建 `canonical_facts`

这使得系统从“找对 chunk”进一步进化为“找对句子”。

这是一个关键转折点，因为 recommendation 类问题往往不是需要一整个 chunk，而只需要其中 1 到 3 句高价值建议。

---

## 7. Canonical Facts 的问题：句子仍然不等于答案级事实

### 7.1 句子级 evidence 还是不够

即使 sentence extraction 已经比 chunk 好很多，仍然会有问题：

- 原句带 speaker / timestamp / 会议口吻
- 原句里仍有很多 transcript 风格噪声
- 原句是“建议句”，但还不是“可直接回答用户的问题的归纳事实”

举例说，系统可能抽到：

- `don't show a scary error that says you are not entitled`
- `we're still syncing your subscription, retry in a few minutes`

这两句已经很接近答案，但如果系统只是把它们并排贴出来，仍然更像证据，而不是最终答案。

### 7.2 解决方式：canonical facts

我们增加了归一化步骤，把 sentence-level facts 转成：

- 去时间戳
- 去 speaker
- 去 transcript 噪声
- 做轻量改写
- 标记 `fact_type`
- 标记 `priority`

这样系统至少有了“答案原材料”的中间层，而不是直接面对会议原句。

但这一步也有局限：

- 它是轻量 canonicalization，不是强抽象
- 很多 canonical facts 仍然保留英文术语
- 仍然可能偏“清洗后的原句”，而不是高度抽象的答案骨架

所以 canonical facts 是必要升级，但不是最终解决点。

---

## 8. 最大的问题：答案层把“相关句拼接”当成“答案组织”

这是整个项目里最核心、最本质的问题。

### 8.1 问题表现

系统最早的回答虽然常常“事实没完全错”，但风格会很差：

- 像证据句列表
- 像会议摘录
- 混入英文原句
- 混入 action items
- 缺少总括句

例如 recommendation 类问题，本来应该回答：

- 团队建议怎么处理
- 用户应该看到什么
- 如果没恢复应该怎么办

但系统常常输出成：

- 一句原文
- 又一句原文
- 再一句 support 细节

这不是答案组织，而是高相关句堆叠。

### 8.2 原因

根因有三个：

1. 缺少 `answer intent` 分流  
   不同问题类型都被丢给同一个生成器。

2. 缺少结构规划  
   没有先决定“答案应该怎么组织”，而是直接从 evidence 跳 final answer。

3. fallback 过于保守  
   一旦系统判断生成不稳，就直接回退到“事实句列表”。

### 8.3 解决方式

我们把 answer composer 升级成了分层结构：

1. `answer_intent` 分类
2. `question_focus`
3. `core_facts / secondary_facts`
4. recommendation 的 `answer_slots`
5. `answer_plan`
6. template-first answer assembly
7. optional polish
8. post-check
9. fallback

这意味着答案层不再是：

`evidence -> end-to-end generation`

而是：

`evidence -> structured representation -> answer assembly`

这一步是整个项目里最大的架构提升。

---

## 9. Recommendation 类问题为什么特别难

### 9.1 这类问题不是普通 fact lookup

Recommendation 问题常常问的是：

- 应该怎么做
- 团队建议是什么
- 用户流程应该如何处理
- 界面该显示什么
- 出错后怎么兜底

这类问题天生需要：

- 抽象
- 结构规划
- 动作优先
- 用户视角重写

如果仍用普通 fact-style pipeline，就很容易答成：

- 几句证据罗列
- 没有总括
- 没有动作导向

### 9.2 解决方式：template-first

我们后来明确让 recommendation 类问题走模板骨架路径：

1. 先分类为 `recommendation`
2. 再抽 `answer_slots`
3. 再用中文模板组装主答案
4. 最后只做可选润色

这一步的意义在于：

- 主结构由系统控制
- LLM 只做语言细化
- 避免模型自由发挥把答案重新拉回 transcript 风格

---

## 10. 问题分类与问题焦点的问题

### 10.1 如果不分类，就会“全都长得差不多”

不同问题其实需要完全不同的回答策略：

- `recommendation`
- `explanation`
- `fact_lookup`
- `mixed`

如果不分流，就会出现：

- 事实点查也答得很长
- 推荐问题答得像摘句
- 原因问题答得像建议

### 10.2 如果不识别焦点，secondary 细节会被错压

有些信息在 recommendation 主问题下只是 secondary，例如：

- support SLA
- support channel
- current error state wording

但如果用户明确追问这些点，它们就必须被提升成主答对象。

### 10.3 解决方式

我们在 answer 层增加了：

- `answer_intent`
- `question_focus`
- `question_focus override`

这让系统能区分：

- 这是“整体建议问题”
- 还是“某个 wording / error text / support detail 的追问”

从而避免 secondary 信息被错误压低。

---

## 11. 润色层的问题：润色可能重新带回 transcript 风格

### 11.1 问题

哪怕模板骨架已经比较像答案，LLM 在 polish 时仍然可能：

- 带回英文原句
- 带回发言人风格
- 带回会议口吻

也就是说，润色本身会成为一个新的退化源。

### 11.2 解决方式

我们对 recommendation 的 polish 增加了 post-check：

- 是否出现 transcript-style token
- 是否出现 speaker
- 是否出现时间戳
- 是否出现大量英文原句

如果失败，直接退回模板答案。

这条规则本质上是在说：

**润色可以优化表达，但不能破坏答案边界。**

---

## 12. `/agent/chat` 路由层的问题

EnterpriseRAG 底层做好以后，产品层又暴露了一批新问题。

### 12.1 非法 `source_types` 导致 0 hits

曾经出现过 LLM planner 产出的 source types 并不是 EnterpriseRAG 真正支持的值，比如：

- `meeting_notes`
- `technical_documents`
- `best_practices`

这些值一旦进 filter，检索直接变成空结果。

### 12.2 短 hint 误命中

另一个很隐蔽的问题是：

- `pr` 被当作 `github PR` source hint
- 但它其实可能只是 `product` 的子串

这会污染 source_types，让系统去错误来源检索。

### 12.3 解决方式

我们在编排与 planning 层做了修复：

1. 合法 source type 白名单清洗
2. 短 ASCII hint 只允许 whole-word 匹配
3. `/agent/chat` 在 finalize 时再次归一化 source_types

这一步解决的是“系统上游把检索路由错了”的问题。

---

## 13. 引用展示层的问题

### 13.1 问题

早期用户在 8511 页面里经常看到：

- 同一个文档被重复引用多次
- 不同 chunk 作为多条 citation 展示
- 整体观感很像“检索结果”，不像“回答”

这会直接拉低产品观感，即使底层证据本身是对的。

### 13.2 解决方式

我们在展示层做了 dedupe：

- 按 `doc_id` 去重
- doc_id 缺失时按 `title` 去重
- 可见 citations 最多展示 3 个
- raw citations 仍保留在 debug 中

这一步对 answer correctness 影响不大，但对“像不像成品”影响很大。

---

## 14. Docker / 运行环境的问题

### 14.1 问题一：代码改了，但容器没 reload

多次出现的一个实际问题是：

- 文件已经改了
- 8511 页面却还是旧回答

根因不是算法没生效，而是：

- Docker 容器没 reload
- 服务进程还在跑旧代码

### 14.2 问题二：复制项目目录却没有形成新环境

因为 Compose 默认按项目名生成资源，所以即使你复制了目录，只要项目名没变，Docker 还是会继续复用：

- 容器名
- 卷名
- 网络
- 镜像标签

结果就是看起来是“新项目路径”，实际上是“旧 Docker 环境继续跑”。

### 14.3 问题三：模型与构建缓存吃空间

这轮开发过程中还遇到了明显的磁盘占用问题：

- external models 很大
- Docker build cache 很大
- 多轮 build 产生很多 dangling image
- WSL 虚拟盘不会自动缩回宿主盘空间

### 14.4 解决方式

我们做过的环境侧修复包括：

- 明确 Docker-first 验收
- 本地模型目录挂载标准化
- `.dockerignore` 防止大模型进入 build context
- 清理停止容器、build cache、旧镜像
- 压缩 Docker 的 `docker_data.vhdx`

这部分不是 RAG 算法本身，但对项目可持续开发非常关键。

---

## 15. Benchmark 暴露问题，也帮助我们修问题

如果没有 benchmark 和 debug trace，这个项目会非常难调。

### 15.1 Benchmark 给我们的最大帮助

它帮助我们从“感觉答案不好”具体定位到：

- `doc_recall`
- `evidence_fact_coverage`
- `answer_fact_coverage`

于是我们能分辨：

- 是文档没找到
- 是句子没抽对
- 是答案没组织好

### 15.2 新增的 debug 字段解决了什么

通过这些字段：

- `supporting_fact_details`
- `canonical_facts`
- `answer_plan`
- `core_facts`
- `secondary_facts`
- `answer_slots`
- `slot_coverage`
- `fallback_reason`
- `final_answer_source`

我们终于能把失败 case 细分成：

- retrieval failure
- evidence failure
- representation failure
- generation failure
- polish regression
- display regression

这大大降低了“靠猜调系统”的成分。

---

## 16. 目前还没有彻底解决的问题

虽然系统已经从“半成品”走到了“可工作的工程原型”，但还有几类问题没有完全解决。

### 16.1 中文表达仍然不够自然

Recommendation 类答案虽然结构已经比以前好很多，但还存在：

- 英文术语暴露
- 部分句子仍偏模板腔
- 缺少更自然的中文压缩表达

### 16.2 同主题不同问法的一致性仍需继续打磨

我们已经加了 `answer_intent` 分类和 recommendation 槽位模板，但：

- 不同问法下的稳定性还需要更多回归验证
- question focus 的覆盖还可以继续扩展

### 16.3 错误文档混入还没有完全归零

系统现在已经明显比之前好，但仍可能：

- 把相邻主题文档当辅助证据带进来
- 在 recommendation 问题里引入一些边缘事实

### 16.4 recommendation 的 canonicalization 还可以更强

现在 canonical facts 还是“轻归一化”，不是“强语义抽象”。

后面如果继续提升答案质量，最可能的着力点依然是：

- 更强的 fact normalization
- 更稳定的 slot assembly
- 更自然的中文模板

---

## 17. 这一路修复的本质

如果用一句话总结我们遇到的问题和解决路径，那就是：

**最早系统把“相关性”误当成了“答案能力”；后来我们逐步把它改造成了一个分层、可调试、面向最终答案组织的 RAG 系统。**

更具体地说，我们经历的是下面这条演化路径：

1. 先发现“回答不好”并不等于“检索不到”
2. 再承认 chunk、候选编排、sentence evidence 都会影响答案
3. 再承认 recommendation 问题需要单独处理
4. 再把 answer composer 从自由生成改成结构化生成
5. 最后补齐编排层、展示层和 Docker 运行层的问题

所以这个项目的核心经验不是某个模型名字，而是：

**企业级 RAG 的问题通常不是单点模型问题，而是多层表示和工程链路的问题。**

只有把：

- chunk
- retrieval
- rerank
- evidence
- canonicalization
- answer planning
- post-check
- orchestration
- runtime environment

一起工程化，系统才会真正稳定下来。

---

## 18. 2026-05-07 补充：这轮真正暴露出来的不是单点 Retrieval 问题，而是 Agent 与 Mail Authoring 问题

这一版继续往前做之后，我遇到的最关键结论是：
**EnterpriseRAG 本身已经不是当前系统最主要的短板，真正影响落地的是外层 Agent 编排、邮件正文建模、草稿状态管理，以及“规则是否越权替大模型写话”。**

### 18.1 一开始把所有请求都先送进重型 ReAct think，是错的

系统此前存在一个明显问题：

- 简单寒暄也先 think
- 上传文档后的直接评价也先 think
- “我刚才问了什么”这类上下文问题也先 think
- 企业事实题先空想，再决定是否检索

这导致：

- 响应慢
- OpenAI/GLM 上游超时更容易暴露
- 用户看到“当前没有生成可展示结果”或 500

这一轮的解决方式不是继续调 prompt，而是把主链路改成：

- 安全硬分支
- 轻量 Router
- Fast Path / Slow Path(ReAct)
- timeout degradation

也就是说，**该直接行动的请求不再先进重型 think。**

### 18.2 另一个大坑：系统会不自觉退化成“半状态机 + 半模板机”

前面我已经在答案层吃过一次亏：为了快速修 bad case，很容易把用户可见答案写死成模板。

这次在 Agent / 邮件主链路里，我再次遇到了同样的问题：

- 规则层写死 clarification
- 规则层写死正文句子
- 规则层写死 unsupported 提示
- 遇到新问法就继续补字符串

这与当前项目的总体要求冲突，因为我们要做的是：

- 代码负责状态、约束、权限、来源边界
- tool/function calling 负责提供 observation
- LLM 基于 observation 自然语言编排用户可见输出

所以这一轮的修正方向很明确：

- 保留 structured state
- 保留 source policy
- 保留 pending draft state
- 把正常 clarification / draft / patch explanation 逐步交回 renderer

也就是说，**真正稳定可泛化的不是“继续补模板”，而是 observation-first。**

### 18.3 邮件功能暴露出的主问题不是“发不出去”，而是“不会按要求写正文”

这一轮真实 bad case 里，系统表面上已经能：

- 识别收件人
- 带附件
- 进入确认发送

但它仍然做不到稳定地满足：

- “讲明我们是谁”
- “说明发送原因”
- “告知发送时间、日期”
- “先生成草稿，再按追问继续修改”

这说明旧链路只能做：

- `send`
- `confirm_send`

却还不会做：

- `author_body_with_constraints`
- `patch_pending_draft`

因此解决方式不是再补一个默认 cover note，而是把邮件正文需求显式建模成：

- `body_constraints`
- `missing_fields`
- `mail_draft_state`
- `mail_patch_result`

### 18.4 比“不满足需求”更危险的问题：附件正文污染

这轮测试里还暴露了一个比“写得不好”更严重的问题：

用户只是追问“正文补充发送时间、日期”，系统却把附件文件全文直接打进了邮件正文。

这说明如果不做严格来源边界，系统会把这些来源混成一个正文候选池：

- 当前用户追问
- 上一轮 pending draft
- 上传附件全文
- 默认摘要候选

这类错误不是润色问题，而是 **source-of-truth 错位**。

因此新增的关键修正是：

- `body_source`
- `reference_source`
- `attachment_source`
- `source_policy`

并明确规定：

- 附件默认只作附件
- reference 默认只可提炼
- 用户没有明确要求时，附件全文不能进正文

这个原则和 EnterpriseRAG 里的“evidence provenance”本质相同：  
**不仅要回答对，还要搞清楚答案是从哪里来的，以及哪些来源不允许进入用户最终可见内容。**

### 18.5 邮件 follow-up 不是普通新请求，而是对 pending draft 的 patch

这一轮还验证了另一个重要问题：

用户第二句、第三句追问并不是“重新写一封新邮件”，而是在修改同一封待确认草稿。

如果系统不理解这一点，就会出现：

- 收件人丢失
- 附件丢失
- 主题被重建
- 旧状态串线
- 新请求被错误理解成全文重写

因此 follow-up mail request 必须被建模成：

- `edit_pending_draft`
- `append_to_pending_draft`
- `replace_pending_draft_body`
- `change_pending_subject`
- `change_pending_recipient`
- `remove_pending_attachment`

这一点和前面修 RAG 时的经验也一致：  
**只看当前 query 不够，必须理解它和当前工作状态的关系。**

### 18.6 “讲明我们是谁”这个 case 说明：字段抽取质量也会直接毁掉用户体验

我还遇到过一个具体 bug：

- 用户说“我们是 OpenAI 华东售前团队”
- 系统错误地把“谁”提成了 sender identity

从表面看只是 regex 小 bug，但本质上它说明：

- 即使进入了正确的 mail authoring 主链路
- 如果结构字段提错了
- 后面的 renderer 也只能围着错状态生成

所以这轮修复除了引入状态和来源边界，还补了一层：

- `sender_identity`
- `self_intro_subject`
- `send_date_value`
- `send_time_value`

也就是把“正文要求”从模糊文字，推进成可验证、可 patch 的显式结构。

### 18.7 这轮对整个项目最重要的教训

如果把这一轮总结成一句话，那就是：

**Enterprise 项目里最难落地的，往往不是 retrieval 本身，而是“多轮状态 + 来源边界 + 结构字段 + 用户可见表达职责分配”。**

更具体地说，当前版本真正需要记住的经验是：

1. 不要让所有请求都先进重型 think
2. 不要让规则层越权替 LLM 写用户可见答案
3. 不要把附件来源和正文来源混在一起
4. 不要把 follow-up 请求当成全新任务
5. 不要只看“能不能发出去”，要看“是不是按用户要求写出来”

这部分经验虽然发生在邮件链路，但实际上已经成为当前整个企业 Agent 主链路的通用约束。
