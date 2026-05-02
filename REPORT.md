# 项目与岗位匹配分析报告

生成日期：2026-04-28

---

## 一、项目概述

- 项目名：LeetCode RAG Agent（Python）
- 核心功能：文档采集与向量化、RAG 检索、智能代理（ReAct / Plan-and-Execute / Reflection）、会话记忆写回、Prometheus 指标埋点、FastAPI 后端 + Streamlit 演示前端。
- 关键文件证据：
  - [app/main.py](app/main.py)
  - [app/graph.py](app/graph.py)
  - [app/retrieval.py](app/retrieval.py)
  - [app/vectorstore.py](app/vectorstore.py)
  - [app/memory.py](app/memory.py)
  - [app/prompts.py](app/prompts.py)
  - [app/observability.py](app/observability.py)
  - [web/streamlit_app.py](web/streamlit_app.py)

## 二、逐关键词匹配（按岗位要求逐项说明与代码证据）

- **大模型应用架构设计与开发 / 全流程开发**
  - 说明：后端以 FastAPI 提供完整流程接口（ingest → plan → execute/chat → metrics），前端提供交互式演示。
  - 证据：[app/main.py](app/main.py) 中实现 `/ingest`、`/plan`、`/execute`、`/chat`、`/metrics`；演示在 [web/streamlit_app.py](web/streamlit_app.py)。

- **智能代理系统 / 任务规划 / 多范式（ReAct、Plan-and-Execute、Reflection）**
  - 说明：通过 StateGraph（langgraph）定义代理节点与流程，支持 ReAct、Plan-and-Execute、Reflection 三种范式与自动选择逻辑。
  - 证据：[app/graph.py](app/graph.py)（`get_graph()`、`run_agent()`、`preview_plan()`、`execute_confirmed_plan()`、`select_mode()`、`react_reason()`、`plan_task()`、`execute_plan()`）。

- **工具调用（Tool Calling）**
  - 说明：为 LLM 暴露检索上下文工具（`context_browser`），ReAct agent 可在生成中调用工具以读取检索到的证据。
  - 证据：[app/graph.py](app/graph.py) 中 `_build_react_agent()` 定义了 `@tool` 的 `context_browser` 并注入 agent。 

- **记忆管理（会话记忆写回与检索）**
  - 说明：在会话结束或合适时将对话摘要写回向量库作为 runtime memory，并在后续检索中优先/辅助使用；本地也保留短期会话 turns。
  - 证据：[app/memory.py](app/memory.py) 的 `build_conversation_summary_document()`、[app/session_store.py](app/session_store.py) 的 `append_turn()` / `get_recent_turns()`、写回通过 [app/vectorstore.py](app/vectorstore.py) 的 `upsert_documents()`。

- **RAG（检索增强生成）与 Prompt Engineering**
  - 说明：有检索过滤策略、上下文构建与合并逻辑，并用模板化 prompt（planner、executor、reflection 等）驱动不同范式。
  - 证据：[app/retrieval.py](app/retrieval.py) 的 `retrieve_documents()`、`build_context_block()`、`merge_context_sources()`；[app/prompts.py](app/prompts.py) 的 prompt 模板。 

- **向量数据库与 Embedding 集成**
  - 说明：封装 Chroma（chromadb）客户端与 embedding 接口，便于替换模型或后端服务。
  - 证据：[app/vectorstore.py](app/vectorstore.py)（`get_chroma_client()`、`get_embeddings()`、`get_vectorstore()`）。

- **将大模型能力封装为 API 服务 / 前后端对接**
  - 说明：FastAPI 提供对外接口，Streamlit 演示前端；项目具备容器化文件（`Dockerfile`、`docker-compose.yml`），利于 PoC 与服务化部署。
  - 证据：[app/main.py](app/main.py)、[web/streamlit_app.py](web/streamlit_app.py)、`Dockerfile`、`docker-compose.yml`。

- **Observability / 数据追踪（Latency / Tokens / Cost）**
  - 说明：使用 OpenTelemetry 作节点级 tracing，Prometheus 指标记录 token、延迟、估算成本、retrieval hits 等，支持接入 LangSmith 的环境变量。
  - 证据：[app/observability.py](app/observability.py)、[app/metrics.py](app/metrics.py)、[app/config.py](app/config.py) 中 LangSmith 配置项。 

- **AI 框架与生态（LangChain 风格、langgraph）**
  - 说明：项目使用 `langchain_core` / `langchain_chroma` / `langchain_openai` 等包，并用 `langgraph` 构建状态图 agent，符合在业务场景中落地 LangChain 思路。
  - 证据：[app/vectorstore.py](app/vectorstore.py)、[app/graph.py](app/graph.py)。

- **AI 编程与代码解释（部分）**
  - 说明：项目把解题代码拆 chunk 并纳入检索，利于对代码段进行证据级引用与解释（对“代码解释器”或 sandbox 的集成可进一步扩展）。
  - 证据：[app/corpus.py](app/corpus.py) 中 `_code_chunks()` 与文档构建逻辑。 

## 三、总体结论（匹配度与建议）

- 强匹配：代理架构（ReAct/Plan-and-Execute/Reflection）、RAG 检索、记忆写回、向量库集成、Prometheus 指标、API 封装与前端演示。
- 部分匹配：多智能体协作可扩展但当前为单 agent 节点化实现；模型微调/自定义微调流程未见（需补充 fine-tune 代码或训练流水线）；AI 编程工具（Copilot/Cursor 等）未直接集成。
- 建议补充项（面试可主动提及改进计划）：
  - 引入 LangSmith / Weights & Biases 打点并产出周报 pipeline；
  - 增加 LLM 池与异步队列（worker）来支持高并发与流量削峰；
  - 可选替换/扩展为 Milvus/Weaviate 等可扩展向量 DB；
  - 增加 Function Calling / 执行沙箱以支持更强的工具链（Code Interpreter 风格）。

## 四、面试易问问题与参考回答（Q&A）

1. Q: 请描述项目的总体架构与一条请求的完整流程。  
   A: 请求从前端（Streamlit）或客户端到 FastAPI（`/chat` 或 `/plan`）→ 服务端调用 `run_agent()`（[app/graph.py](app/graph.py)）构建状态机：分类 query → 检索知识（[app/retrieval.py](app/retrieval.py)）→ 检索会话记忆（[app/session_store.py](app/session_store.py)）→ 合并上下文 → 选择模式（react/plan_execute/reflection）→ 调用 LLM（`get_llm()`）生成初稿 → reflection/修订（若开启）→ 返回结果并记录指标（[app/metrics.py](app/metrics.py)），可能写回会话摘要（[app/memory.py](app/memory.py)）。

2. Q: 如何实现 Plan-and-Execute 与 ReAct，二者如何切换？  
   A: Plan-and-Execute 由 `plan_task()` 先生成 `plan_steps`（PLANNER_PROMPT），用户在 `/plan` 预览并确认后用 `execute_plan()`（EXECUTOR_PROMPT）执行；ReAct 使用 `_build_react_agent()` 注入 `context_browser` 工具让 LLM 在生成中直接检索与调用。切换逻辑在 `select_mode()`（[app/graph.py](app/graph.py)）。

3. Q: 会话记忆如何管理与写回？如何避免写入低价值内容？  
   A: 近期 turn 存在内存（`append_turn()` / `get_recent_turns()`），最终通过 `build_conversation_summary_document()` 构造摘要并 `upsert_documents()` 写入 Chroma；写回前用 `_should_write_summary()`（[app/main.py](app/main.py)）过滤空或“证据不足”的答案。

4. Q: 系统如何采集并上报模型性能/成本/延迟？  
   A: 使用 OpenTelemetry（`timed_node()`）做节点级 tracing，Prometheus 指标在 [app/metrics.py](app/metrics.py) 中定义（tokens、latency、estimated_cost、retrieval_hits 等），并通过 `/metrics` 暴露；配置支持 LangSmith 环境变量以便接入 LangSmith。 

5. Q: 针对 RAG 的性能与成本有哪些优化策略？  
   A: 精细化 metadata filter（见 `retrieval_filter()`）减少不相关 chunk；调整 top_k 与 chunk 大小；使用小模型做初筛，再在必要时用大模型（模型分层）；缓存热点问题；监控 token 使用并自动回退。 

6. Q: 如何减少 hallucination？  
   A: 强制基于检索证据回答（prompt 明确要求依据上下文），在证据不足时返回“证据不足”，并收集失败样本用打标签训练或调整检索策略与 prompt。 

7. Q: 哪里实现了工具调用？如何支持 Function Calling？  
   A: `context_browser` 在 `_build_react_agent()`（[app/graph.py](app/graph.py)）以 `@tool` 暴露。要支持 Function Calling，可在 LLM 层传入函数签名并解析模型的结构化输出，然后在系统中调用实际函数并将结果回传给模型。 

8. Q: 系统如何支持不同模型/服务商（OpenAI/HuggingFace/Azure）？  
   A: 通过 [app/config.py](app/config.py) 的 `llm_base_url`、`embedding_base_url`、`llm_api_key` 等配置抽象化后端，`get_embeddings()` 与 `get_llm()` 封装了具体 client，便于切换 provider。 

9. Q: 生产化部署如何做水平扩展与高可用？  
   A: 将 LLM 请求隔离到可扩展的 worker 层（队列/异步任务）、使用托管/集群向量库、在 API 层做限流与熔断、并把指标接入告警。仓库已有容器化示例（`Dockerfile`、`docker-compose.yml`）与 Prometheus 指标，便于落地。 

10. Q: 请简要白话解释 MoE、Function Calling、向量检索。  
   A: MoE：按输入动态选择少数“专家”子模型计算以提高效率；Function Calling：让模型输出结构化调用信息由系统执行函数再回传结果；向量检索：把文本编码为向量并通过相似度检索语义相关内容，常用于给模型提供证据。 

（面试时可从 repo 的演示进行 walk-through，展示不同模式、检索片段与指标。）

## 五、可演示 Demo 建议

- 在本地快速启动 API 与 Streamlit：

```bash
pip install -r requirements.txt
# 运行后端
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# 另开终端运行前端
streamlit run web/streamlit_app.py
```

- 或使用 `docker-compose up --build`（若项目的 `docker-compose.yml` 已配置服务）。

## 六、建议在简历/面试中突出表述（可直接复用）

- 片段示例："设计并实现基于 StateGraph 的智能代理流，支持 ReAct、Plan-and-Execute 与 Reflection，实现 RAG 检索、会话记忆写回与 Prometheus 全链路指标埋点，封装为可演示的 FastAPI 服务与 Streamlit 前端（详见 repo）。"

## 七、下一步（可选）

- 我已将完整报告写入仓库： [REPORT.md](REPORT.md)。
- 若需要我可以：
  - 提交并推送 Git commit；
  - 生成一页面试卡片（便于背题）；
  - 把报告内容精简为 1 页 PPT 提纲。

---

报告由代码静态分析与仓库关键文件摘取证据生成。如需我把报告内容再精简为一页要点，请告诉我偏好（长度或重点）。
