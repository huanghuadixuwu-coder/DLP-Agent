# Enterprise Memory 架构说明

更新时间：2026-05-06

## 1. 这份文档的目的

这份文档专门讲当前项目里的 memory 是如何构造的、它在整个 EnterpriseRAG 体系中扮演什么角色，以及它目前的能力边界在哪里。

需要先明确一点：

当前 memory 不是一个独立替代 RAG 的系统，也不是主检索层本身。  
它更准确的定位是：

**一个围绕 runtime context bundle 构建的多源上下文增强系统。**

也就是说：

- EnterpriseRAG 主检索负责“从企业知识库里找证据”
- Memory 负责“把当前会话、工作区长期知识、历史摘要和用户画像补进来”

因此，理解 memory 的关键，不是把它看成“另一个向量库”，而是把它看成“主问答链的辅助上下文层”。

---

## 2. 总体结构

当前 memory 的总入口位于 [hermes_memory.py](/E:/leetcode-rag-agent-enterprise/app/hermes_memory.py)：

- `build_runtime_context_bundle(...)`

它会把多种来源统一收敛成一份 runtime bundle，然后交给 EnterpriseRAG 的答案层使用。

目前 bundle 里整合的来源主要有 4 类：

1. `workspace memory`
2. `session transcript`
3. `legacy conversation memory`
4. `user model provider`

最终产物包括：

- `context_text`
- `context_sources`
- `workspace_memory_hits`
- `transcript_hits`
- `user_model_used`
- `memory_retrieval_hits`
- `workspace_memory`
- `transcript_entries`
- `legacy_memory`

这意味着 memory 现在不是黑盒，而是：

- 有原始来源
- 有命中数
- 有文本化上下文
- 有结构化调试信息

---

## 3. Memory 在主问答链中的位置

在 [service.py](/E:/leetcode-rag-agent-enterprise/app/enterprise_rag/core/service.py) 中，一次企业问答的顺序是：

1. `build_retrieval_plan(...)`
2. `build_runtime_context_bundle(...)`
3. `retrieve_evidence(...)`
4. `compose_enterprise_answer(...)`

也就是说，memory 是在 retrieval 和 answer 之间组装出来的，但它的主要作用点是在答案层。

具体传入 `compose_enterprise_answer(...)` 的 memory 相关字段包括：

- `context_text`
- `context_sources`
- `workspace_memory_hits`
- `transcript_hits`
- `user_model_used`

所以当前 memory 的工作方式更接近：

`question -> evidence retrieval -> answer with memory context`

而不是：

`question -> memory-aware retrieval ranking`

这也是当前 memory 的一个重要边界：  
它是 **answer-time augmentation**，还不是 **retrieval-time fusion**。

---

## 4. Workspace Memory

这是当前 memory 体系里最像“长期工作区知识”的一块。

### 4.1 存储来源

Workspace memory 来自配置中的 `workspace_memory_root` 目录。  
[hermes_memory.py](/E:/leetcode-rag-agent-enterprise/app/hermes_memory.py) 中的 `_workspace_root()` 会自动确保这些目录存在：

- `bootstrap/`
- `notes/`
- `skills/`

系统会扫描这个根目录下的所有 Markdown 文件：

- `_iter_memory_files()`

也就是说，当前 workspace memory 的原始数据来源是：

- 项目笔记
- bootstrap 说明
- 技能文档
- 团队维护的工作区级 Markdown 知识

### 4.2 Section 切分方式

Workspace memory 不是整文件入库，而是先按 Markdown heading 做 section 切分。

函数是：

- `_split_markdown_sections(text)`

规则比较直接：

- 碰到 `#` heading 就开一个新 section
- 每个 section 形成一个 memory chunk

所以一个 Markdown 文件可能会变成多个 chunk，每个 chunk 对应一个 heading 下的内容。

### 4.3 索引结构

Workspace memory 同样采用双索引：

1. SQLite FTS
2. Vectorstore

SQLite 部分包括：

- `workspace_memory_sections`
- `workspace_memory_sections_fts`

Vectorstore 部分通过：

- `get_workspace_memory_vectorstore()`
- `upsert_workspace_memory_documents(...)`

也就是说，workspace memory 和 EnterpriseRAG 主知识库一样，也走 hybrid 检索思路。

### 4.4 Chunk 元数据

每个 workspace memory chunk 会保留：

- `chunk_id`
- `file_path`
- `heading`
- `content`
- `file_hash`
- `file_mtime`

写入 vectorstore 时还会带：

- `domain = workspace_memory`
- `source_type = workspace_memory`
- `title = heading`

### 4.5 检索方式

入口函数是：

- `search_workspace_memory(question, top_k=6)`

它会：

1. 先 `sync_workspace_memory_index()`
2. 用 FTS 做 sparse 命中
3. 用 vectorstore 做 dense 命中
4. 以 `chunk_id` 合并结果
5. 形成 hybrid score
6. 输出 top-k

输出的每条命中包括：

- `chunk_id`
- `file_path`
- `title`
- `snippet`
- `score`
- `source`

其中 `source` 可能是：

- `fts`
- `vector`
- `hybrid`

### 4.6 在 runtime bundle 中的表现

命中的 workspace memory 会被组织成：

```text
Workspace memory:
[1] file_path :: title
snippet
```

同时在 `context_sources` 中记录：

- `workspace_memory_md`

### 4.7 适用场景

Workspace memory 特别适合放：

- 项目长期背景
- 团队约定
- 开发说明
- 技术路线
- 手工维护的策略文档

它的价值在于：这些内容通常不适合和企业知识主库混成一锅，但又确实应该在回答时被看见。

---

## 5. Session Transcript Memory

这是当前 memory 体系里的短期会话记忆层。

实现文件在 [session_transcript.py](/E:/leetcode-rag-agent-enterprise/app/session_transcript.py)。

### 5.1 存储方式

每次 turn 会通过：

- `append_transcript_event(event)`

写入一个 JSONL 文件。

路径来自：

- `transcript_export_path`

这意味着 transcript memory 当前不是数据库结构化检索，而是一个 append-only 的事件日志。

### 5.2 读取方式

读取函数是：

- `load_recent_transcript_entries(session_id, conversation_id, limit=8)`

它会：

1. 读取 transcript JSONL
2. 只保留当前 `session_id + conversation_id`
3. 用 deque 保留最近若干条

### 5.3 在 runtime bundle 中的表现

命中的 transcript 条目会被压缩为：

```text
Recent transcript:
user: ...
assistant: ...
```

这里会对内容做 compact，防止 transcript 过长。

同时在 `context_sources` 中记录：

- `session_transcript_jsonl`

### 5.4 作用

这块 memory 解决的是：

- 同一个会话内最近几轮上下文延续
- 模型知道“刚刚聊到了什么”
- 即使这些信息没有进入企业知识库，也能在当前回答中保留下来

所以它的定位是：

**短期会话级上下文记忆。**

---

## 6. Legacy Conversation Memory

这部分主要在 [conversation_memory.py](/E:/leetcode-rag-agent-enterprise/app/conversation_memory.py)，底层存储在 [conversation_store.py](/E:/leetcode-rag-agent-enterprise/app/conversation_store.py)。

它是当前 memory 系统里最“兼容旧方案”的部分，但也非常重要。

### 6.1 Conversation Store 的基础结构

SQLite 中当前维护 3 张核心表：

- `conversations`
- `turns`
- `merges`

也就是说，系统不仅知道每条 turn，还知道：

- 它属于哪个 conversation
- conversation 是否被 merge 过
- merge 的来源 conversation 是哪些

### 6.2 Turn Memory

每次问答后，可以生成 turn summary document。

相关函数包括：

- `build_turn_summary_document(...)`
- `write_turn_summary(...)`

这个 summary document 通常会包含：

- `question`
- `answer_summary`
- `intent`
- `source_turn_ids`
- `citations`
- 可选上传内容摘要

然后作为 `Document` 写入 vectorstore，metadata 中标记：

- `domain = conversation`
- `source_type = turn_summary`

所以 turn memory 的本质是：

**把一次完整问答压缩成一个可检索摘要块。**

### 6.3 Merged Memory

如果多个 conversation 被合并，系统还可以生成 merged summary。

相关函数：

- `build_merged_summary_document(...)`
- `write_merged_summary(...)`

它会记录：

- merged summary
- topics
- source conversations
- source turn ids

metadata 中标记：

- `source_type = merged_summary`

所以 merged memory 的本质是：

**跨 conversation 的压缩记忆。**

### 6.4 最近 turns 直接上下文

除了向量检索式 summary，legacy memory 还会直接读取当前 conversation 最近 turns：

- `recent_turns_context(conversation_id, limit=6)`

这意味着它不是纯摘要 memory，而是：

- 最近原始 turns
- summary 检索
- merge summary 检索

三者混合。

### 6.5 检索方式

`build_memory_context(...)` 会调用：

- `retrieve_turn_memory(...)`
- `retrieve_merged_memory(...)`

其中：

- turn memory 限定当前 `session_id + conversation_id`
- merged memory 通过 `get_related_merged_conversations(...)` 找相关合并会话

### 6.6 follow-up 感知

系统会通过：

- `is_memory_follow_up(question)`

判断当前问题是不是 follow-up。

如果是 follow-up，会放大 memory 检索量，比如：

- turn memory top_k 更大
- merged memory top_k 更大

这是一种很轻量但实用的 follow-up 优化。

### 6.7 输出结果

`build_memory_context(...)` 最终会返回：

- `memory_context`
- `memory_hits`
- `merged_memory_hits`
- `turn_memory`
- `merged_memory`

在 runtime bundle 中，这部分被整体挂在：

- `legacy_memory`

### 6.8 定位

这块 memory 的核心作用是：

- 让同一 conversation 内的问题可以“记住前情”
- 让历史 conversation 的摘要可以在 follow-up 中被唤起

所以它更像：

**会话级长期兼容记忆层。**

---

## 7. User Model Provider

这一层在 [user_model_provider.py](/E:/leetcode-rag-agent-enterprise/app/user_model_provider.py)。

### 7.1 结构

当前定义了一个统一的用户模型结构：

- `UserModelSummary`
  - `summary`
  - `preferences`
  - `traits`
  - `source`

同时定义了 provider 接口：

- `UserModelProvider`

以及两个实现：

- `NullUserModelProvider`
- `HonchoUserModelProvider`

### 7.2 当前真实状态

目前默认情况下：

- `get_user_model_provider()` 通常返回 `NullUserModelProvider`

而 `HonchoUserModelProvider` 也还只是结构占位，没有实际把 profile 拉回来。

所以当前大多数情况下：

- `profile = None`
- `user_model_used = False`

### 7.3 未来定位

虽然现在它基本没真正参与推理，但架构位置已经留好。

未来它适合承载：

- 用户风格偏好
- 长期行为特征
- 沟通偏好
- 项目角色

也就是说，这是：

**用户画像 memory 的预留层。**

---

## 8. Runtime Context Bundle 是如何拼出来的

这是当前 memory 系统最重要的“装配层”。

函数：

- `build_runtime_context_bundle(...)`

它的基本逻辑是：

1. 先拿 `legacy_memory`
2. 再拿 `workspace_hits`
3. 再拿 `transcript_entries`
4. 再拿 `profile`
5. 把这些内容拼到 `context_sections`

### 8.1 拼接顺序

当前顺序是：

1. Workspace memory
2. Recent transcript
3. User model
4. Legacy memory

最后：

- `context_text = "\n\n".join(context_sections)`

### 8.2 为什么顺序重要

这个顺序其实在表达一种优先级：

- 先给模型长期工作区知识
- 再给它当前会话最近上下文
- 再给它用户画像
- 最后再补旧版 conversation summary memory

虽然这些内容暂时还没有统一 rerank，但至少在展示给答案器时是有明确结构顺序的。

### 8.3 bundle 里的原始结构化字段

除了 `context_text`，系统还保留原始结构：

- `workspace_memory`
- `transcript_entries`
- `legacy_memory`

这使得我们既能给模型文本化上下文，也能保留调试和后续更深层融合所需的数据。

---

## 9. 当前 memory 与 EnterpriseRAG 的关系

这一点必须讲清楚。

当前系统不是“memory 替代 EnterpriseRAG”，也不是“memory 和 enterprise docs 完全平级 rerank”。

当前的关系是：

### 9.1 EnterpriseRAG 负责主证据

主证据仍来自企业知识库索引：

- dense recall
- sparse recall
- rerank
- evidence selection

### 9.2 Memory 负责辅助上下文

Memory 主要负责：

- 当前会话上下文
- 工作区长期笔记
- conversation summary
- 用户画像

### 9.3 作用点主要在答案层

当前 memory 的主要使用位置是答案生成阶段，而不是主检索排序阶段。

因此，更准确地说：

**EnterpriseRAG 负责回答问题所需的外部证据，memory 负责回答问题时的上下文增强。**

---

## 10. 当前 memory 的优点

### 10.1 分层清楚

当前 memory 并没有把所有来源混成一个黑盒，而是明确分成：

- workspace
- transcript
- conversation summaries
- user model

这意味着后续每一层都可以单独优化。

### 10.2 兼容旧系统

通过 `legacy_memory`，当前系统可以在不完全推翻旧 conversation memory 的前提下继续演进。

### 10.3 Workspace memory 很适合项目型 Agent

很多真正重要的信息，比如：

- 项目目标
- 团队约定
- 长期背景

本来就不应该混进企业文档主检索，而更适合作为 workspace 记忆独立维护。

### 10.4 Runtime bundle 可观测

返回结果里可以看到：

- memory hits
- context sources
- transcript entries
- workspace snippets

这让 memory 不再是黑盒。

---

## 11. 当前 memory 的局限

### 11.1 还不是 retrieval-time first-class citizen

现在 memory 基本是在 retrieval 后组装，而不是在 retrieval plan、candidate merge、evidence selection 中深度参与。

所以它目前仍是：

- `answer-time augmentation`

而不是：

- `retrieval-time fusion`

### 11.2 Workspace memory 的切分还比较朴素

当前只按 Markdown heading 切 section：

- 适合说明文档
- 但还不够细粒度

### 11.3 Conversation memory 更像摘要，不像事实记忆

turn summary 和 merged summary 现在主要是 summary 风格文档，而不是像 EnterpriseRAG evidence 那样的 canonical fact 结构。

### 11.4 User model 基本还没真正启用

架构位置已经有，但当前默认 provider 仍是空实现。

### 11.5 多路 memory 尚未统一 rerank

当前不同来源的 memory 更像“分块拼接”，还没有形成跨来源统一 relevance ordering。

---

## 12. 如果继续演进，最值得做的方向

如果后续继续加强 memory，我认为最重要的方向有 4 个：

### 12.1 让 memory 更深度参与主检索

例如：

- retrieval plan 中感知 memory
- candidate merge 时引入 memory relevance
- evidence 选择时参考 memory signals

### 12.2 把 conversation memory 从 summary 升级成 facts

也就是从：

- `answer_summary`

向：

- `canonical conversation facts`

演进。

### 12.3 强化 workspace memory 的结构理解

例如：

- 更细粒度切分
- section importance 排序
- note / skill / bootstrap 分层

### 12.4 真正接入 user model provider

把用户偏好、角色和沟通习惯真正接进来，而不是只保留接口壳。

---

## 13. 一句话总结

如果用一句话概括当前 memory：

**它是一个围绕 runtime context bundle 组织起来的多源上下文增强系统，负责把工作区长期知识、当前会话内容、历史 conversation 摘要和未来的用户画像补充进 EnterpriseRAG 答案链。**

它当前的主要价值不是替代主检索，而是：

- 帮助系统记住当前会话
- 帮助系统引用工作区知识
- 帮助系统延续历史摘要
- 为未来更强的个性化和长期记忆预留结构

这也是它在当前项目中的真实位置。

---

## 14. 2026-05-07 补充：当前已经落地的 Hermes-style Dynamic Memory 策略

这一轮除了 EnterpriseRAG 和邮件主链，我还正式补上了第一版 Hermes-style dynamic memory 策略。  
它不是“再加一个 memory 开关”，而是把 memory 变成统一 Agent 主链中的一等动作和上下文底座。

### 14.1 当前已经落地的四层写入策略

这轮明确了 memory 的写入分层：

1. `session_transcript`
   - 每轮用户消息、助手输出、tool observation 自动 append
   - 负责最短期的会话回忆
2. `turn_summary`
   - 每轮结束写结构化摘要
   - 记录用户目标、执行结果、关键文件、关键收件人、task_id、失败原因
3. `workspace_memory`
   - 只写稳定项目知识、已确认流程约定、用户明确要求长期记住的规则
4. `user_model`
   - 只写偏好候选和沟通习惯，例如：
     - Docker-first
     - 不希望默认邮件摘要
     - 更偏向 observation-first agent 设计

这样做的意义是：  
**memory 不再是一个黑盒 summary，而是分层维护不同半衰期的信息。**

### 14.2 当前已经落地的读取策略

Memory 现在不是旁路补丁，而是主链路里可主动读取的上下文来源。  
当前已明确分成四类 read target：

- `conversation_recent`
- `conversation_summary`
- `workspace_memory`
- `user_model`

使用原则也已经固定：

- 会话指代问题优先读 `conversation_recent`
- 跨轮总结问题再读 `conversation_summary`
- 项目约定、长期规则走 `workspace_memory`
- 个性化偏好、默认行为走 `user_model`

这意味着 memory 的职责已经比旧版清楚得多：  
**它主要负责上下文连续性和偏好，而不是替代企业知识证据库。**

### 14.3 压缩策略已经从“纯 transcript 堆叠”升级为 compaction

这一轮也落了第一版 transcript compaction 思路：

- 近期窗口保留原文
- 旧消息压成摘要
- 保留 task_id、文件名、邮箱、URL、时间等标识符

也就是说，压缩后的结构不是简单删历史，而是：

- `[压缩摘要]`
- `[最近原文窗口]`

这和前面 Hermes 文档里强调的一样，关键是保住：

- 当前进行中的任务
- 用户最近决策
- TODO / 阻塞点
- 外发/审批任务标识
- 文件与收件人引用

### 14.4 反思策略已经定成“受控反思”，而不是自由自进化

这一轮还有一个很重要的取舍：  
企业 Agent 不照搬 coding agent 的 full self-evolution。

当前已经明确：

- 可以生成 `reflection_candidate`
- 可以提出长期偏好候选
- 可以指出流程失败点和值得进入 todolist/debug 的事项

但不会自动：

- 改工具
- 改审批规则
- 改发信策略
- 改 prompt 主策略
- 把未经确认的反思直接写成长期事实

这就是当前 memory 的“受控反思”边界。  
**它允许系统总结经验，但不允许系统偷偷改变行为边界。**

### 14.5 当前 memory 与 EnterpriseRAG 的边界比以前更清楚

前面第 9 节已经讲过两者不是同一层。  
这轮之后，这个边界更加明确了：

- EnterpriseRAG
  - 负责企业事实证据
  - 负责检索、证据、citation、answer grounding
- Memory
  - 负责会话连续性
  - 负责项目约定
  - 负责用户偏好
  - 负责任务上下文增强

也就是说：

- “GCP onboarding 那题”的事实仍然应由 EnterpriseRAG 给出证据
- “我刚才问了什么”“我们之前决定 Docker 怎么验收”才是 memory 的职责

这避免了 memory 被错误用成“弱证据库”。

### 14.6 当前还没完全做完的部分

虽然这轮已经把 dynamic strategy 立住了，但还有几块尚未彻底完成：

- `pending_memory_update` 的用户确认/管理界面还没做完
- 多路 memory source 之间还没有统一 rerank
- `user_model provider` 仍需要更完整的真实接入
- 更多 Docker 内真实对话回归还需要继续积累

所以当前更准确的表述是：

**Memory 的动态策略已经成型，但离“完整可治理的长期记忆系统”还有一段距离。**
