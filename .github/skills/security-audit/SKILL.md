# Security Audit — Skill

## Purpose

This skill defines the security requirements and audit checklist specific to `trading-bot-agent` — a system that handles financial data, live trading credentials, and LLM-generated trade decisions.

---

## High-Risk Areas

| Area                        | Risk                                       | Priority |
| --------------------------- | ------------------------------------------ | -------- |
| API key handling            | Credential leakage via logs or errors      | CRITICAL |
| LLM output parsing          | Prompt injection → unauthorized trade      | CRITICAL |
| Trade execution (`lumibot`) | Malformed order → financial loss           | CRITICAL |
| Ticker symbol validation    | Path traversal in cache file paths         | HIGH     |
| Redis queue                 | Unauthenticated queue → order injection    | HIGH     |
| Telegram bot token          | Token leakage → unauthorized alerts        | HIGH     |
| Pinecone API key            | Key leakage → unauthorized memory access   | MEDIUM   |
| CLI input                   | Command injection via user-supplied values | MEDIUM   |
| Cache files                 | Sensitive data written to disk             | MEDIUM   |
| Docker image                | Exposed secrets in image layers            | MEDIUM   |

---

## Security Checklist

### Secrets Management

- [ ] No hardcoded API keys, tokens, or passwords in source code
- [ ] All secrets loaded exclusively from environment variables or `.env` files
- [ ] `.env` files listed in `.gitignore`
- [ ] Docker secrets passed via env vars, not `ARG` build args
- [ ] No secrets in log output (check all `logger.*` calls in changed files)
- [ ] No secrets in error messages raised to users

### LLM Output Security

- [ ] LLM-generated content is NEVER passed to `eval()`, `exec()`, or `subprocess`
- [ ] Trade decisions parsed through validated Pydantic schema (`TraderProposal`)
- [ ] Fallback to safe default (HOLD) when LLM output cannot be parsed
- [ ] Signal processing rejects unexpected action values not in `{BUY, SELL, HOLD}`
- [ ] No raw LLM responses forwarded to external APIs without sanitization

### Input Validation

- [ ] All ticker symbols pass through `safe_ticker_component()` before file I/O
- [ ] Trade order quantity bounded by configurable limits
- [ ] Date strings validated before use in API calls (prevent injection)
- [ ] CLI inputs sanitized before passing to internal functions

### Trade Execution Safety

- [ ] Paper trading mode enforced by default; live mode requires explicit config flag
- [ ] Order size limits enforced in `trade_validator.py` before queue submission
- [ ] Duplicate order detection to prevent double-execution
- [ ] All trade actions logged with timestamp, ticker, action, and reasoning

### Network Security

- [ ] All external API calls use HTTPS (no HTTP fallback)
- [ ] SSL certificate verification enabled (no `verify=False`)
- [ ] Timeouts set on all `requests` / `httpx` calls
- [ ] Redis connection authenticated when in production mode

### Container Security

- [ ] Non-root user in Dockerfile
- [ ] No secrets in Dockerfile or docker-compose.yml (use env file reference)
- [ ] Base image pinned to specific digest, not `latest`
- [ ] Unnecessary packages not installed in final image layer

### Dependency Security

- [ ] All dependencies pinned in `requirements.txt`
- [ ] `pip audit` / `safety check` run in CI to detect known CVEs
- [ ] No transitive dependency conflicts that could be exploited

---

## OWASP Top 10 Mapping (for this project)

| OWASP Category                       | Project-specific risk                      |
| ------------------------------------ | ------------------------------------------ |
| A01 Broken Access Control            | Redis queue unauthenticated; Pinecone open |
| A02 Cryptographic Failures           | Weak secrets; HTTP in legacy API calls     |
| A03 Injection                        | Prompt injection; ticker path traversal    |
| A04 Insecure Design                  | Live trading on by default                 |
| A05 Security Misconfiguration        | Debug mode in prod; wide CORS              |
| A06 Vulnerable Components            | Unpinned dependencies with known CVEs      |
| A07 Auth/Identity Failures           | Shared API keys without rotation policy    |
| A08 Software/Data Integrity Failures | LLM output used without schema validation  |
| A09 Logging/Monitoring Failures      | Missing trade audit log; secrets in logs   |
| A10 SSRF                             | User-supplied URLs in data fetch calls     |

---

## Reporting Format

Each security finding must include:

```
ID: SEC-001
Severity: CRITICAL | HIGH | MEDIUM | LOW | INFO
File: tradingagents/integrations/lumibot_executor.py
Line: 87
Title: Live trading enabled by default without explicit opt-in
Description: The config default sets `live_trading=True` which could lead to
  real financial loss if a user runs the system without reviewing config.
Recommendation: Change default to `live_trading=False` and require explicit
  `TRADING_LIVE_MODE=true` environment variable.
CWE: CWE-276 (Incorrect Default Permissions)
```
