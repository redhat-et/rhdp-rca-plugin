# JIRA Ticket Routing & Data Model

How `analysis_agent.py` routes tickets using `aap2_events` and `aap2_job_results`, and where each field lives.

## Where the data lives

```mermaid
flowchart LR
    subgraph events["aap2_events (source)"]
        E1[job_id]
        E2[job_finished — used for 4h rule]
        E3[job_started]
        E4[ai_processed]
        E5[error_message]
    end

    subgraph results["aap2_job_results (RCA output)"]
        R1[id / result_id]
        R2[job_id]
        R3[ticket_link]
        R4[ticket_resolve_datetime_gmt — when JIRA closed]
        R5[is_open — routing output]
        R6[root_cause_summary / confidence]
    end

    E1 ---|JOIN on job_id| R2
    E2 -.->|compared to| R4
    R3 -.->|presence check| R5
    R4 -.->|time check| R5

    JIRA[JIRA / external sync] -->|populates| R3
    JIRA -->|populates| R4
```

| Table | Key columns | Role |
|-------|-------------|------|
| `aap2_events` | `job_id`, `job_finished`, `job_started`, `error_message`, `ai_processed` | Source failures from AAP; `job_finished` anchors the 4-hour rule |
| `aap2_job_results` | `id`, `job_id`, `ticket_link`, `ticket_resolve_datetime_gmt`, `is_open`, RCA fields | RCA output + JIRA state; `is_open` is written by `analysis_agent.py` |

Env vars (from `.claude/settings.json` or shell): `SOURCE_DB_TABLE`, `SOURCE_DB_RESULT_TABLE`.

**Writers today**

| Field | Populated by |
|-------|----------------|
| `aap2_events.*` | AAP ETL pipeline |
| RCA fields on results | `store_report.py` after Claude batch |
| `ticket_link` | External JIRA integration or manual test data |
| `ticket_resolve_datetime_gmt` | External JIRA sync (UTC/GMT wall time) |
| `is_open` | `analysis_agent.py` (`compute_is_open`) |

`ticket_resolve_datetime_gmt` should be stored as naive UTC/GMT (e.g. `2026-08-17 12:00:00`), not local time with offset, unless the column is `timestamptz` and the instant is correct in UTC.

---

## JIRA routing gate (Agent 1)

`analysis_agent.py` joins both tables, compares ticket state to `job_finished`, and updates `is_open`. Agent 2 (planned) will read `is_open` to create, re-open, or skip JIRA tickets.

```mermaid
flowchart TD
    START[analysis_agent.py — --since or --lookback-hours] --> JOIN[JOIN aap2_events + aap2_job_results on job_id]
    JOIN --> FILTER[Filter: job_finished >= window start]
    FILTER --> LOOP[For each matched row]

    LOOP --> READ[Read from DB]
    READ --> E1[aap2_events.job_finished]
    READ --> R1[aap2_job_results.ticket_link]
    READ --> R2[aap2_job_results.ticket_resolve_datetime_gmt]

    E1 --> RULE[compute_is_open]
    R1 --> RULE
    R2 --> RULE

    RULE --> OUT[UPDATE aap2_job_results.is_open]
    OUT --> AGENT2[Agent 2 — planned: create / skip JIRA ticket]

    style START fill:#e8f4fc
    style RULE fill:#fff3cd
    style AGENT2 fill:#f8d7da,stroke-dasharray: 5 5
```

**Note:** This script is not wired into `batch_rca_headless.sh` yet; run it manually or add it as a cron step before ticket creation.

---

## Routing decision (`compute_is_open`)

Ansible jobs can run up to ~4 hours. If a ticket was resolved recently and the job still failed, the failure is likely **residual** (the job started before the fix landed).

```mermaid
flowchart TD
    A[Inputs: ticket_link, ticket_resolve_datetime_gmt, job_finished] --> B{ticket_link exists?}
    B -->|No| OPEN1["is_open = TRUE — UNMATCHED_NEW (no ticket)"]
    B -->|Yes| C{ticket_resolve_datetime_gmt set?}
    C -->|No| OPEN2["is_open = TRUE — MATCHED_ACTIVE (ticket open)"]
    C -->|Yes| D["delta = job_finished − resolved_at (UTC)"]
    D --> E{delta < 4 hours?}
    E -->|Yes| CLOSED["is_open = FALSE — MATCHED_ACTIVE (residual)"]
    E -->|No| OPEN3["is_open = TRUE — UNMATCHED_NEW (expired)"]

    style OPEN1 fill:#d4edda
    style OPEN2 fill:#d4edda
    style OPEN3 fill:#d4edda
    style CLOSED fill:#f8d7da
```

| Condition | `is_open` | Label | Ticket action (Agent 2) |
|-----------|-----------|-------|---------------------------|
| No `ticket_link` | `true` | UNMATCHED_NEW (no ticket) | Create new ticket |
| `ticket_link`, no resolve time | `true` | MATCHED_ACTIVE (ticket open) | Ticket still open |
| Resolved &lt; 4h before `job_finished` | `false` | MATCHED_ACTIVE (residual) | Skip — residual failure |
| Resolved ≥ 4h before `job_finished` | `true` | UNMATCHED_NEW (expired) | New incident — re-open or new ticket |

`ticket_link` is only checked for presence (any non-empty string). The URL is not fetched or validated.

---

## RCA routing vs JIRA routing

These are separate layers; do not conflate them in operations or demos.

```mermaid
flowchart TD
    subgraph rca["RCA routing (batch_rca_headless.sh — live today)"]
        P1[pre_filter_jobs.py] --> P2{Same catalog_item + similar error_message?}
        P2 -->|Match| P3[Skip Claude — link to prior result]
        P2 -->|No match| P4[Full RCA via Claude]
    end

    subgraph jira["JIRA routing (analysis_agent.py — separate today)"]
        J1[analysis_agent] --> J2{ticket_link + resolve time vs job_finished}
        J2 --> J3[Set is_open on result row]
        J3 --> J4[Agent 2: ticket create / skip]
    end

    P4 --> K[store_report.py inserts result]
    K --> J1

    style rca fill:#e8f4fc
    style jira fill:#fff3cd
```

| Layer | Script | Question it answers |
|-------|--------|-------------------|
| RCA pre-filter | `pre_filter_jobs.py` | Have we already analyzed this failure pattern? |
| JIRA gate | `analysis_agent.py` | Should we open a JIRA ticket for this result? |

---

## Run the routing gate

```bash
# Load env from repo settings, then run (from scripts/)
eval "$(python3 -c "
import json
with open('/path/to/.claude/settings.json') as f:
    for k, v in json.load(f).get('env', {}).items():
        print(f'export {k}={json.dumps(v)}')
")"

cd deploy/batch-rca-automation/scripts
python3 analysis_agent.py --lookback-hours 24
# or: python3 analysis_agent.py --since '2026-08-17T00:00:00'
```

Tests (no DB): `pytest deploy/batch-rca-automation/tests/test_analysis_agent.py`
