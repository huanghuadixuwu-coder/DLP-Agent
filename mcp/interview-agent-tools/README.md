# interview-agent-tools MCP Design

This folder is a local MCP-style tool server for the enterprise Agent prototype. It intentionally uses a tiny JSON-lines server so the tool ideas can run without adding another dependency.

## Tools

- `create_reminder`: create a local reminder from natural language.
- `list_reminders`: list local reminders.
- `privacy_scan`: redact PII and classify sensitive-message risk.
- `budget_context`: demonstrate long-document context budgeting.
- `disambiguate_entity`: resolve Apple company/fruit ambiguity.
- `send_email_163`: send a text email through 163 SMTP using environment-provided credentials.

## Email Safety

`send_email_163` reads credentials from environment variables:

- `EMAIL_SEND_ENABLED=true`
- `SMTP_HOST=smtp.163.com`
- `SMTP_PORT=465`
- `SMTP_USERNAME=<sender email>`
- `SMTP_PASSWORD=<163 authorization code>`
- `SMTP_FROM=<sender email>`

Do not write a mailbox authorization code into source code, documentation screenshots, SQLite, Chroma, or prompt logs.

## Local Demo

```powershell
python mcp/interview-agent-tools/server.py
```

Then send one JSON object per line:

```json
{"tool":"list_tools","args":{}}
{"tool":"privacy_scan","args":{"message":"手机号13812345678和api_key=abcdef1234567890需要外发"}}
{"tool":"send_email_163","args":{"to_email":"17388861183@163.com","subject":"DLP Agent test","body":"This is a redacted test email."}}
```

## Interview Talking Point

The key point is not this tiny server itself. The point is the separation of concerns: the Agent should not embed every business capability in its prompt. It should call tools through a stable protocol, keep tool inputs/outputs structured, and make capabilities reusable across Agent workflows.
