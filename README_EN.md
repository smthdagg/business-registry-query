# Business Registry Query System

> **工商企业聚合信息查询系统** — A personal aggregated China business-registry lookup platform powered by 88cha · Tianyancha · Riskbird · Aiqicha
>
> English (this page) | [中文文档](README.md)

One search queries four sources in parallel, merges and deduplicates the results automatically, and annotates every field with its origin. The UI and data structures are modeled after Tianyancha: clickable search results, sectioned company profiles, boss search that precisely disambiguates people with the same name, and a relation graph covering legal representatives, shareholders, executives, investments and branches.

![Home — aggregated search](docs/screenshot-home.png)

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Cookie Acquisition & Import](#cookie-acquisition--import)
- [User Guide](#user-guide)
- [HTTP API Reference](#http-api-reference)
- [Running Tests](#running-tests)
- [FAQ](#faq)
- [Security & Compliance](#security--compliance)

---

## Features

### Search capabilities

| Capability | Description |
|---|---|
| **Six search angles** | Comprehensive / company name / legal representative / shareholder / credit code / phone |
| **Four-source aggregation** | 88cha, Tianyancha, Riskbird and Aiqicha queried in parallel; merged by priority Riskbird > 88cha > Tianyancha > Aiqicha |
| **Merge & dedupe** | Records normalized by unified social credit code (company name as fallback); duplicate companies get field-level completion plus a `sources` list |
| **Boss search** | Person-name search returns *person entries*, not company noise: Riskbird groups same-name people into precise person records (independent personId), filterable by province of their companies, click through to all their companies (with roles) |
| **Company profile** | Tianyancha-style sections: basic info / shareholders (with stake %) / key personnel / outward investment / branches, cross-source field completion |
| **Relation graph** | Canvas force-directed graph: legal rep, shareholder, executive, investment and branch relations, multi-hop expansion (depth ≤ 3, nodes ≤ 60), per-node source tracking |
| **Two-level filters** | Province → city and industry section → category cascading filters on results, plus registration-status filter |
| **Province filter** | Boss search accepts a province code (regionId) applied server-side, e.g. 340000 = Anhui |

### Platform capabilities

| Capability | Description |
|---|---|
| **Batch queries** | Up to 500 lines per task, angle prefixes supported (e.g. `法人:张三`), background queue with progress and cancel |
| **Multi-format export** | CSV / XLSX / JSON with built-in spreadsheet formula-injection protection |
| **Watchlist** | Track companies of interest, one-click re-check, change counter |
| **History & logs** | Query history (deletable) plus an audit log (login/search/profile/task/settings; admins only) |
| **Role separation** | Admin / member accounts; settings and logs are admin-only |
| **Account security** | PBKDF2-SHA256 password hashing (200k iterations), HMAC-SHA256 signed sessions, lockout after 5 failed logins |
| **Cookie management** | Managed live from the web UI, accepts Netscape / JSON / header-string / cURL file imports; background keep-alive |
| **Browser fallback** | Optional Camoufox fingerprint-browser channel when cookies hit risk control (off by default) |
| **Bilingual docs** | Chinese and English documentation |

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│  Browser SPA (vanilla JS, hash routing, no build step)│
│  search / boss / company / person / graph / batch      │
└──────────────────────┬───────────────────────────────┘
                       │ fetch /api/*
┌──────────────────────▼───────────────────────────────┐
│  FastAPI (app/main.py)                                │
│  ├─ auth.py       login / session / throttle / roles  │
│  ├─ routes.py     search / profile / person / watch / │
│  │                batch / admin routes                │
│  └─ services/                                          │
│      ├─ aggregate.py  aggregation: parallel + merge    │
│      ├─ search.py     per-source run: cache/history    │
│      ├─ relation.py   relation-graph engine            │
│      ├─ profile.py    company profile builder          │
│      ├─ worker.py     batch task queue                 │
│      └─ exporter.py   CSV/XLSX/JSON export             │
├──────────────────────────────────────────────────────┤
│  sources/ adapters                                    │
│  ├─ cha88.py       88cha MTOP (token sign + retry)     │
│  ├─ tianyancha.py  Tianyancha mobile endpoints        │
│  ├─ riskbird.py    Riskbird (search/persons/profiles)  │
│  ├─ aiqicha.py     Aiqicha                             │
│  ├─ mock.py        demo source (full UX, no cookies)   │
│  └─ cookies.py     cookie store (data/cookies.json)    │
├──────────────────────────────────────────────────────┤
│  SQLite (WAL): config / cache / history / tasks /      │
│  watchlist / logs / users                              │
└──────────────────────────────────────────────────────┘
```

**Stack**: Python 3.12 · FastAPI · Uvicorn · SQLite (WAL) · vanilla-JS SPA · Canvas force graph · openpyxl · Docker

**Design notes**

- **Uniform source adapter interface**: each source declares supported angles, strategies and a minimum request interval; the aggregation layer schedules them in parallel and a single source failure never breaks the whole response
- **Cache key**: `source|angle|keyword|p:person|r:province|l:limit` avoids burning quotas twice
- **Person filtering**: legal-rep/shareholder results pass through `record_person_hit` (legal-rep / natural-person shareholder / matchType check), removing "company name contains the person's name" noise

---

## Quick Start

### Option 1 — Run locally

```bash
# 1. Install dependencies (uv recommended; pip works too)
uv venv && uv pip install -r requirements.txt

# 2. Prepare configuration
cp .env.example .env        # fill in cookies / password as needed

# 3. Start
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. Open http://127.0.0.1:8000
```

Without any cookie configured the system runs on a built-in **mock demo source**, so the full feature set can be tried offline (data is fictional).

### Option 2 — Docker

```bash
cp .env.example .env        # fill in configuration
docker compose up -d --build
# open http://127.0.0.1:8000
```

Data (database / cookies / session key) is persisted in the `./data` volume and survives container rebuilds.

### Option 3 — VPS deployment

```bash
# The server needs Docker + the compose plugin
# For mainland-China VPS, configure registry mirrors first
# (/etc/docker/daemon.json → registry-mirrors)

rsync -av --exclude .venv --exclude data/app.db* --exclude .git \
      --exclude __pycache__ --exclude tests \
      app scripts requirements.txt Dockerfile docker-compose.yml .env.example \
      user@your-vps:~/business-query/

ssh user@your-vps
cd ~/business-query
cp .env.example .env && nano .env     # at minimum set ADMIN_PASSWORD
sudo docker compose up -d --build
```

Open the port in your cloud security group; for production put Nginx/Caddy in front and enable HTTPS.

---

## Configuration

Everything lives in `.env` (see `.env.example`); sources and cookies can also be managed live in the web UI (stored in `data/cookies.json`).

| Variable | Description | Default |
|---|---|---|
| `ADMIN_PASSWORD` | Initial admin password (seeded as the `admin` account on first start) | empty (login disabled) |
| `MEMBER_PASSWORD` | Initial member password (seeded as `member`) | empty |
| `DATA_SOURCE` | Default source: `all` / `rb` / `c88` / `tyc` / `aqc` | `all` |
| `TYC_COOKIE` | Tianyancha cookie | empty |
| `AQC_COOKIE` | Aiqicha cookie | empty |
| `RB_COOKIE` | Riskbird cookie (SVIP works best) | empty |
| `CHA88_COOKIE` | 88cha cookie (works without an account, slider risk control) | empty |
| `TYC_DELAY` | Minimum Tianyancha request interval (seconds) | `2` |
| `COOKIE_KEEPALIVE_MIN` | Cookie keep-alive loop interval (minutes) | `30` |
| `BROWSER_FALLBACK` | Enable Camoufox browser fallback on risk control | `false` |
| `BROWSER_HEADLESS` | Headless browser mode | `true` |
| `SECRET_KEY` | Session signing key (auto-generated to `data/secret.key` when unset) | auto |
| `DATA_DIR` | Data directory | `data` |

> **Login switch**: login is enabled when either `ADMIN_PASSWORD` or `MEMBER_PASSWORD` is set. With neither set, every visitor gets admin access (local personal use only).

---

## Cookie Acquisition & Import

### Where to get each cookie

| Source | How | Notes |
|---|---|---|
| **Riskbird** | Log in at [riskbird.com](https://www.riskbird.com) → DevTools → Network → copy the Cookie header of any request | SVIP accounts expose the richest fields (person groups / shareholders / investments); guest quota is tiny |
| **Tianyancha** | Log in at [m.tianyancha.com](https://m.tianyancha.com) → same | 5 results per page is a hard limit of the free interface |
| **88cha** | Open [88cha.com/search](http://88cha.com/search), run any search, copy the cookie | No account needed; slider risk control, expires fastest |
| **Aiqicha** | Log in at [aiqicha.baidu.com](https://aiqicha.baidu.com) → copy the cookie | Guest quota very low; logged-in search sometimes returns empty after site changes |

### Web import (recommended)

Settings → Sources & Cookies → **import cookie file**. Four formats are auto-detected and normalized:

1. **Netscape** — `cookies.txt` exported by browser extensions
2. **JSON** — arrays exported by EditThisCookie-style extensions
3. **Header string** — paste `k1=v1; k2=v2; ...` directly
4. **cURL command** — "Copy as cURL" from DevTools

Imported cookies take effect immediately; no restart needed.

---

## User Guide

### 1. Search & filters

1. Type a keyword (company name / person name / credit code / phone), pick an angle, hit Search
2. The default **aggregate mode** queries all four sources in parallel; chips at the top show per-source status (count / latency / skip reason)
3. In the result table:
   - **Company rows** — click the name for the profile; the "Relation" button builds that company's graph
   - **👤 person rows** (legal-rep / comprehensive angles) — same-name person groups; "Companies" jumps to the person page
4. The filter bar cascades **province → city**, **industry section → category**, and registration status
5. History chips re-run a past query in one click

### 2. Boss search (search people by name)

1. Open "Boss Search" (or switch the search angle to legal-rep) → enter a name + province → search
2. Results are **same-name person groups**: each group is one real person (precisely separated by Riskbird personId) with company count, region distribution and largest company
3. Click a group → **all companies** of that person (legal-rep / shareholder / executive roles merged, with position and stake)
4. Re-filter by another province at will; a single group auto-redirects to that person

> Unlike platforms that return a pile of companies when you search a legal representative, this returns **people** — same-name individuals are never conflated.

### 3. Company profile

- Click any company name in results; sections mirror Tianyancha:
  - **Basic info**: credit code, legal rep, registered capital, establishment date, status, address, business scope, phone, etc.
  - **Shareholders**: name, **stake percentage**, subscribed capital (cross-source completion)
  - **Key personnel**: executives
  - **Outward investment / branches**: detailed lists (best via Riskbird SVIP)
- Every field is annotated with its source; the in-page button builds the relation graph

### 4. Relation graph

1. Click "Relation" on a company/row, or open the Relation Graph page and enter a company name
2. Expands **legal rep → shareholders → executives → investments → branches**, multi-hop (depth ≤ 3, nodes ≤ 60)
3. Nodes are color-coded by type and labeled by source; drag and zoom supported; click a node for its companies or to expand further

### 5. Batch queries & export

1. One keyword per line on the Batch page, up to 500 lines; angle prefixes supported:
   ```
   法人:张三
   苏胜
   91340000MA2XXXXXXX
   ```
2. Tasks run in a background queue — track / cancel / inspect in the Task Center
3. Export results as **CSV / XLSX / JSON** (formula-injection protected)

### 6. Watchlist

"Add to watchlist" on a company page (or add manually) → one-click re-check on the watchlist page → latest status and a change counter. Handy for tracking bidders or related parties.

### 7. History & logs

- **Query history**: recent queries beside the search box; delete one or clear all
- **Audit log** (admins): Settings → Audit log — logins (incl. failures and lockouts), searches, profile views, tasks, setting changes, watchlist actions

### 8. Settings & account management

- **Sources & cookies**: switch the default source, update/clear source cookies live, import cookie files, probe connectivity
- **Change password**: verify the old password, set a new one (≥ 6 chars)
- **User management** (admins): create admin/member accounts, reset passwords, change roles, delete accounts
- **Audit log** (admins): view and clear

---

## HTTP API Reference

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/health` | public | Health check |
| GET | `/api/config` | public | Source metadata / field config |
| POST | `/api/login` · `/api/logout` | public | Login / logout |
| POST | `/api/search` | login | Search (angle / limit / person / region_id) |
| POST | `/api/person-search` | login | Person search (Riskbird same-name groups, region_id) |
| POST | `/api/person-companies` | login | All companies of one person (with roles) |
| POST | `/api/profile` | login | Company profile (sections + cross-source completion) |
| GET/DELETE | `/api/history` | login | Query history |
| GET/POST/DELETE | `/api/watchlist*` | login | Watchlist CRUD + re-check |
| POST | `/api/batch` · `/api/tasks*` | login | Batch tasks |
| GET | `/api/export` | login | Export CSV / XLSX / JSON |
| GET/POST | `/api/admin/settings*` | admin | Source / cookie settings |
| GET/DELETE | `/api/admin/logs` | admin | Audit log |
| GET/POST | `/api/admin/users*` | admin | User management |
| GET | `/api/admin/source/probe` | admin | Source connectivity probe |

---

## Running Tests

```bash
.venv/bin/python -m pytest tests/ -q
# 63 passed
```

Coverage: aggregation merge & dedupe, relation-graph engine, batch task flow, source adapters (mocked), cookie import, accounts & profile services, utility helpers.

---

## FAQ

**Q: Why do legal-rep searches return both people and companies?**
Comprehensive / legal-rep angles merge "person entries" (👤) with "legal-rep matched companies". Person entries come from Riskbird's person-record grouping — the most precise boss-search semantics; company rows come from Tianyancha's matchType=legal-rep matches.

**Q: Why does Tianyancha only return 5 results?**
Five per page is a hard limit of the mobile interface; this project does not bypass it, it aggregates around it.

**Q: Aiqicha shows 0 results / skipped?**
Its logged-in search endpoint occasionally returns empty after site changes. Other sources are unaffected; the status chips say so honestly.

**Q: Riskbird says quota exhausted (guest / logged-in)?**
Guest quota is tiny — update the SVIP cookie in Settings. Daily quotas reset the next day.

**Q: How often do cookies expire?**
Each source differs (88cha fastest). A 30-minute keep-alive loop is built in; when a cookie dies the source chip shows the reason.

**Q: Forgot the admin password?**
Have another admin reset it in User Management; or stop the service and delete `data/app.db` (wipes history/logs/accounts; accounts are re-seeded from `.env` on restart).

---

## Security & Compliance

- This is a **personal-use** tool. All data comes from information the sources display publicly. Respect each platform's terms of service and robots policies; request pacing is built into every source
- Do not expose this system to the public internet without protection — always set `ADMIN_PASSWORD`, and prefer a reverse proxy with HTTPS and an IP allowlist
- Do not use it for commercial data resale or anything unlawful; you are responsible for your own usage
- Exports are formula-injection protected; passwords and sessions use industry-standard hashing/signing; logins are rate-limited

## 中文

完整中文文档见 **[README.md](README.md)**。
