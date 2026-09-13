# Security Policy / 安全策略

## Supported versions / 支持版本

| Version | Supported |
|---|---|
| main branch | ✅ |
| older tags | ❌ — please upgrade / 请升级 |

## Reporting a vulnerability / 报告漏洞

**English**: Report vulnerabilities privately via [GitHub Security Advisories](https://github.com/smthdagg/business-registry-query/security/advisories/new) rather than public issues. Never paste cookies, server IPs, passwords, or real query data (company/person records) into public content.

**中文**：请通过 [GitHub Security Advisories](https://github.com/smthdagg/business-registry-query/security/advisories/new) 私密报告漏洞，不要开公开 issue。任何 Cookie、服务器 IP、口令、真实查询数据（企业/人员记录）都不得出现在公开内容中。

## Hardening baseline / 安全基线

- Passwords: PBKDF2-SHA256, 200,000 iterations / 口令 PBKDF2-SHA256 20 万次迭代
- Sessions: HMAC-SHA256 signed cookies (`gs_session`), HttpOnly + SameSite=Lax / HMAC 签名会话
- Login throttle: 5 failures → 60s lockout / 登录限速锁定
- Role separation: settings, user management and audit log are admin-only / 设置与日志仅管理员
- Export hardening: CSV/XLSX formula-injection neutralization / 导出防公式注入
- Secrets live only in `.env` and `data/` — both are git-ignored and must never be committed / 敏感凭据只存 `.env` 与 `data/`，禁止入库
