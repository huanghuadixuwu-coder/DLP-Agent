
# 邮件 Agent 路线参考

本文件只记录路线判断。正式规格入口是 `spec/MAIL_AGENT_V2_SPEC.md`，细节按渐进式披露拆在 `spec/mail-agent/` 目录。

## 已锁定边界

Mail Agent 负责：

- 邮件搜索、读取、线程视图、摘要。
- 新邮件、回复、转发、会议邀请等草稿。
- HTML/纯文本双轨处理。
- label、mailbox、thread、attachment、provider metadata 归一化。
- DLP、审批、确认、发送、失败恢复。
- Mail Harness：回归、失败注入、并发、状态回放。

Docs / Drive / Sheets / Slides / Forms 不进入 Mail Agent core。它们作为 Workspace/Document domain agents，由 Supervisor 通过 DAG 调用；Mail Agent 只消费它们产生的 observation 或 attachment/document reference。

## 第一阶段默认方案

- Provider：当前 IMAP/SMTP + Fake Provider Harness。
- UI：遵循 `spec/AGENT_UI_SPEC.md` 与 `spec/mail-agent/UI_CONTRACT.md`。
- 并发与可靠性：参考 `spec/Orchestration Patterns for Concurrent .md`，优先 idempotency、checkpoint、DLQ。
- 隐私与记忆：参考 `spec/mail-agent/PRIVACY_MEMORY.md`，原始邮件正文和高风险内容默认不进 active memory。

## 后续方向

- Workspace/Document Agent：Docs、Drive、Sheets、Slides、Forms。
- Calendar Provider：真实日历增删改查和 attendees 管理。
- Provider Expansion：腾讯企业邮箱、Google Workspace、Exchange。
