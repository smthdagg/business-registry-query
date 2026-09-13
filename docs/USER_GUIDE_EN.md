# Business Registry Query System · Complete User Guide (English)

> Applies to: v1.x · UI as served at `http://<host>:8000`
> Companion docs: [中文完整教程](USER_GUIDE_ZH.md) · [README](../README.md)

---

## 1. Login & roles

If `ADMIN_PASSWORD` or `MEMBER_PASSWORD` was set at deploy time, a login mask appears first:

| Role | Default account | Scope |
|---|---|---|
| Admin | `admin` / `ADMIN_PASSWORD` from `.env` | Everything + settings + user management + audit log |
| Member | `member` / `MEMBER_PASSWORD` from `.env` | Search / profiles / graph / batch / watchlist / history |

- 5 consecutive failed logins lock the account for 60 seconds.
- Admins can change their password under **Settings → Change password** (≥ 6 chars) and manage accounts under **Settings → User management**.
- Personal deployments without any password: every visitor enters as admin (local use only).

## 2. Quick search (home)

### 2.1 Running a search

1. The search box accepts: **company name / legal-rep name / unified social credit code / registration number / phone**.
2. Pick an angle:

| Angle | Sources used | Notes |
|---|---|---|
| Comprehensive | all | Fuzzy match across fields; includes same-name person groups |
| Company name | all | Company-name lookup |
| Legal rep | Riskbird + Tianyancha | Person entries (👤) + legal-rep matched companies |
| Shareholder | Riskbird | Shareholder-name search |
| Credit code | Riskbird + 88cha + Tianyancha | Exact lookup |
| Phone | Tianyancha + Riskbird | Reverse phone lookup |

3. Hit **Search** (aggregate mode by default) or use the quick chips (Company / Legal rep / …) to switch angles instantly.

### 2.2 Reading results

- The chip row shows per source: `count` on success, `latency` on hover, `skipped` with the reason on hover (no cookie / quota / risk control).
- **Company rows**: click the name for the profile; row actions **Profile** / **Relation** open the detail and graph pages.
- **👤 person rows**: show `N companies`, the largest company and region; **Companies** opens the person page. Person rows come from Riskbird person records — same-name individuals stay **separate entries**.
- Row-level provenance: in aggregate mode each row lists which sources contributed it.

### 2.3 Filter bar

The result page offers cascading filters over the returned set:
`Province → City`, `Industry section → category`, `Registration status`; the counter shows `filtered N / total M`. A person row's province is the dominant province of their companies.

### 2.4 History chips

"Recent searches" at the bottom: click to re-run a query with its angle, ✕ removes one, **Clear** wipes the list.

## 3. Boss search (people by name)

Entry: the **Boss Search** nav item, or switch the search angle to **Legal rep**.

1. Enter a name + province (optional) → search.
2. Results are **same-name person groups** — one real person per group:
   - index, company count, dominant region, largest-registered-capital company;
   - data comes from Riskbird person records, **precisely separated by personId**.
3. Click a group → **all companies** of that person:

| Column | Content |
|---|---|
| Company | click for the profile |
| Role | legal rep / shareholder (with stake %) / manager / supervisor, merged |
| Status | active / revoked etc. |
| Region | registration place |
| Source | Riskbird (precise) or Tianyancha (fallback) |

4. Province filtering is **server-side** (Riskbird regionId, e.g. 340000 = Anhui) — selecting Anhui returns only people whose companies are in Anhui.
5. A single group auto-redirects to that person.

> **Unlike "search a legal rep, get a pile of companies"**: this returns **people** first — pick the person, then their companies. Same-name individuals are never conflated.

## 4. Company profile

Click any company name; sections mirror Tianyancha:

| Section | Content | Main source |
|---|---|---|
| Basic info | credit code, legal rep, capital, establishment date, status, address, scope, phone, email, website, former names, 20+ fields | all sources |
| Shareholders | name, **stake %**, subscribed capital, type (natural person / entity); natural persons link to their companies | Riskbird + Aiqicha |
| Key personnel | executives and positions | Riskbird |
| Outward investment | invested companies (best via SVIP) | Riskbird |
| Branches | branch list | Riskbird |

- Header actions: **Watch** and **View in graph**.
- Every field is annotated with its source; cross-source fields are completed automatically.

## 5. Relation graph

1. Entry: the **Relation** button on profiles/results, or the Relation Graph nav item.
2. Expansion layers:

```
root → ① same-legal-rep companies → ② natural-person shareholders → their companies
     → ③ executives → their companies → ④ investments → ⑤ branches
```

3. Budgets: **depth ≤ 3 hops, nodes ≤ 60**; every node records its data source.
4. Canvas: **drag** nodes, **wheel** to zoom, drag the background to pan; click a node for entry points.
5. Node colors encode type (company / person / root); edge colors encode the relation.

## 6. Batch queries & task center

### 6.1 Submitting

One keyword per line, up to **500 lines**, with optional angle prefixes:

```
法人:张三            → legal-rep search
股东:李四            → shareholder search
苏胜                 → comprehensive
91340000MA2XXXXXX    → credit code (auto-detected)
```

### 6.2 Execution & inspection

- Tasks run in a serial background queue (source pacing respected); closing the page is safe.
- **Task Center**: live progress, cancel button, full results on completion.
- Individual failures don't abort the batch; reasons are listed per line.

### 6.3 Export

| Format | Notes |
|---|---|
| CSV | BOM included — opens correctly in Excel |
| XLSX | multiple sheets (results + metadata) |
| JSON | full structured data |

> All exports neutralize spreadsheet formula injection (cells starting with `= + - @` get a `'` prefix).

## 7. Watchlist

1. **Watch** on a company page (with a note), or add manually.
2. One-click **re-check** refetches the current status and diffs against the last check.
3. Tracks last-checked time, latest status and a change counter.

## 8. History & audit log

- **Query history**: see §2.4.
- **Audit log** (admins, in Settings): logins (success/failure/lockouts), searches, profile views, batch tasks, setting changes, cookie updates, watchlist actions — with actor and IP. Clearable.

## 9. Settings (admins)

### 9.1 Sources & cookies

- **Default source**: `all` or a specific one; the search page follows it.
- **Update cookies**: paste a cookie string per source — effective immediately (stored in `data/cookies.json`).
- **Import cookie files**: four auto-detected formats —
  1. Netscape `cookies.txt`
  2. JSON array (EditThisCookie etc.)
  3. Header string `k1=v1; k2=v2`
  4. cURL command (DevTools → Copy as cURL)
- **Connectivity probe**: one minimal request per source, reporting `OK / reason / sample / latency`.
- **Keep-alive**: a background loop pings configured sources every 30 minutes (configurable).

### 9.2 Accounts

- **Change password**: verify old, set new (≥ 6 chars).
- **User management**: create admin/member, reset passwords, change roles, delete accounts.

## 10. Troubleshooting quick reference

| Symptom | Cause | Fix |
|---|---|---|
| A source chip shows "skipped" | no cookie / quota / risk control | hover for the reason; update that cookie in Settings |
| Tianyancha always returns 5 | hard per-page limit | expected; aggregation compensates |
| Aiqicha 0 results but chip green | site change, logged-in search sometimes empty | expected; wait for recovery |
| Riskbird "quota exhausted" | guest/daily quota used | use an SVIP cookie; resets next day |
| 88cha returns non-JSON | anonymous slider risk control | open 88cha in a browser, search once, refresh the cookie |
| Logged in, then logged out again | browser blocks cookies | allow site cookies; avoid private mode |
| "Incorrect username or password" | unknown account or wrong password | the admin password comes from `ADMIN_PASSWORD`; another admin can reset it |

## 11. Quotas & usage advice

- All sources are third-party public interfaces with built-in pacing — **do not** hammer them with scripts.
- Prefer aggregate mode for daily work (one request, four sources); run batch jobs off-peak.
- Cookies are sensitive credentials: manage them only in Settings / `data/cookies.json`; never post them in screenshots or public documents.
