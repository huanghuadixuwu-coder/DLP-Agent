┌─────────────────────────────────────────────┐
│              Your Application               │
├─────────────────────────────────────────────┤
│  [Cost Router] → Pick the right model       │
│  [Budget Allocator] → Fair token sharing     │
├─────────────────────────────────────────────┤
│  [Supervisor + Backpressure]                 │
│  [Shared State + Conflict Resolution]        │
├─────────────────────────────────────────────┤
│  [Agent Memory with Decay]                   │
│  [Checkpoint + Recovery]                     │
├─────────────────────────────────────────────┤
│  [Dead Letter Queue]                         │
│  Observability + Alerting                    │
└─────────────────────────────────────────────┘

每一层解决不同的失效模式：
1.成本路由器：防止预算井喷
Not all LLM calls are equal. A simple classification task doesn't need GPT-4o. A complex reasoning task shouldn't go to a cheap model. Route tasks based on estimated cost and complexity.
This pattern alone can cut your LLM costs by 60-80%. Most agent tasks are TRIVIAL or SIMPLE — don't send them to your expensive model.
2.预算分配器：防止资源耗尽
When you have a team of agents working on a shared task, one chatty agent can consume the entire token budget. A budget allocator ensures fair distribution.The token-stealing mechanism is what makes this work in practice. Idle agents shouldn't hoard tokens while active agents starve.
3.主管：防止过载
The classic supervisor pattern — one agent delegates to workers — breaks down under load. Worker 3 is slow, but the supervisor keeps sending it tasks. Queue grows. Memory grows. Everything dies.
Backpressure means: when a worker is overwhelmed, the system slows down instead of crashing.
4.共享状态：防止数据损坏
Multiple agents reading and writing shared state creates race conditions. Agent A reads user preferences, Agent B updates them, Agent A writes stale data back.
For most agent systems, strategy works best — agents contribute different fields to the same state object without overwriting each other."merge"
5.内存：防止上下文污染
Long-running agents accumulate context. But not all context ages equally. A user preference from 5 minutes ago matters more than a search result from 2 hours ago.The decay rates per type make all the difference. Instructions should persist almost forever. Search results should fade quickly.
6.检查点：防止进度丢失
Long-running agent workflows crash. Network blip, OOM, timeout. Without checkpointing, you lose all progress and start over.The resume behavior is automatic. Crash at step 3? Next run loads the checkpoint and starts at step 3 with all previous state intact.
7.死信队列：防止静默故障
When an agent task fails after all retries, it shouldn't just disappear. A dead letter queue captures failed tasks for analysis, replay, or manual resolution.The replay mechanism is the killer feature. Found the bug? Fix it and replay the dead letter — no need to recreate the original request.

原文档在：https://dev.to/dohkoai/7-ai-agent-orchestration-patterns-for-scaling-concurrent-systems-with-production-code-1onc
