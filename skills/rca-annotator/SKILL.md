---
name: rca-annotator
description: LLM-as-judge skill that independently evaluates root-cause-analysis outputs to produce ground-truth annotations with multi-pass consistency checks, difficulty calibration, and complete evidence traceability.
allowed-tools:
  - Read
  - Write
  - Bash
---

# RCA Annotator (LLM-as-Judge)

An LLM-as-judge skill that independently re-analyzes raw RCA evidence (step1, step3, step4 outputs) to produce ground-truth annotations. The judge forms its own conclusion from the same source evidence the agent had — without reading the agent's diagnosis — producing an unbiased reference label for scoring agent accuracy.

| | `root-cause-analysis` (Agent) | `rca-annotator` (Judge) |
|---|---|---|
| **Purpose** | Diagnose failures for users | Produce ground-truth labels for evaluation |
| **Reads** | Logs, Splunk, GitHub (live) | Step 1/3/4 output files (offline) |
| **Output** | Human-readable diagnosis | Structured `annotation_draft.json` |

**Use when**: steps 1-4 outputs exist and you need labeled ground-truth data, difficulty benchmarking, or alternative diagnosis identification.

**Do NOT use** to perform initial RCA (use `root-cause-analysis`) or directly score agent output (compare annotation externally).

## Prerequisites

Verify root-cause-analysis has been completed.

- `JUMPBOX_URI` (optional) — SSH connection string (e.g. `"user@host -p 2222"`). If unset, uses local `.analysis/` only.
- SSH keys configured in `~/.ssh/config` if using jumpbox; `ssh` and `rsync` installed.
- **Required files** in `.analysis/<job_id>/`:
  - `step1_job_context.json` — Job metadata, failed tasks
  - `step3_correlation.json` — Timeline with AAP + Splunk events
  - `step4_github_fetch_history.json` — Configuration and code context

If missing, run `root-cause-analysis` skill first.

## Workflow

0. Download from jumpbox (if `JUMPBOX_URI` set) or verify local files
1. Read step1, step3, step4 (never the agent's diagnosis)
2. Independently determine root cause with evidence scoring and traceability
3. *(Optional)* Multi-pass consistency check
4. Write `annotation_draft.json`
5. Upload to jumpbox (if `JUMPBOX_URI` set)

---

## Step 0: Download Analysis Files

```bash
cd skills/rca-annotator
python scripts/cli.py download --job-id <job_id>
```

Downloads from jumpbox `/tmp/analysis/<job_id>/` (or `/tmp/<job_id>`) to local `.analysis/<job_id>/`. If `JUMPBOX_URI` unset, validates local files only. Errors on missing remote directory, missing required files, or connection failure.

---

## Step 1: Read Outputs

Read in order: **step1** (job metadata, failed tasks, errors) → **step3** (correlated timeline incl. Splunk) → **step4** (config hierarchy and code). Skip `step2_splunk_logs.json` — step3 already includes correlated events.

---

## Step 2: Analyze and Annotate

### A. Root Cause Category

**From Step1**: Review `failed_tasks[]` — task names, actions, errors, durations. Action patterns hint at categories (e.g. `k8s_info` → infrastructure/rbac, `uri` → network/secrets, `command/shell` → configuration/dependency).

**From Step3**: Examine `timeline_events[]` chronologically. Pod errors before task failures → infrastructure; task failures before pod errors → configuration/code.

**From Step4**: Review `github_fetches[]` for config hierarchy. Check `fetched_configs` for missing variables/conflicts and `fetched_workload` for code at failure points. Note any `status: 404` or `error: all_paths_failed`.

**Categories**: `configuration` | `infrastructure` | `application_bug` | `dependency` | `network` | `resource` | `cloud_api` | `credential` | `secrets` | `unknown`

### B. Evidence Construction with Traceability

Each evidence item requires:
- **source** — `step1` | `step3` | `step4`
- **source_file** — path to analysis file
- **json_path** — exact path in source JSON
- **exact_value** and/or **exact_quote** — literal value or text
- **confidence** — `high` | `medium` | `low`
- **is_root_cause** — exactly ONE item must be `true`

Additional fields for code/config sources: `line_number`, `github_path` (`owner/repo:path:line`).

```json
{
  "source": "step1",
  "source_file": ".analysis/<job_id>/step1_job_context.json",
  "json_path": "failed_tasks[0].duration",
  "exact_value": 917.565567,
  "message": "Task duration indicates timeout, not immediate failure",
  "confidence": "medium",
  "is_root_cause": false
}
```

For `agnosticd_code` or `agnosticv_config` sources, add `line_number`, `exact_quote`, and `github_path`.

**Process**: Start with Step3 timeline → filter to `task_failed`/`pod_error` → identify root cause evidence → extract traceability → assign confidence → include supporting evidence.

### C. Difficulty Calibration

Score 0-10 based on:
- **+3**: Requires cross-source correlation (AAP + Splunk + GitHub)
- **+2**: Requires understanding code behavior
- **+2**: Error message is generic/misleading
- **+1**: Requires variable precedence/override knowledge
- **+1**: Requires domain knowledge (K8s, Ansible, cloud APIs)
- **+1**: Multiple plausible alternatives exist
- **+1**: Timing dependencies critical

Mapping: 0-3 = easy, 4-6 = medium, 7-10 = hard. Include score and justification.

### D. Alternative Diagnoses

Identify plausible-but-incorrect hypotheses with:
- `category`, `summary`, `why_wrong`
- `plausibility`: `high` (shares many characteristics) | `medium` (some evidence) | `low` (superficial similarity)
- `supporting_evidence` and `contradicting_evidence` lists

---

## Step 2b: Multi-Pass Consistency Check (Optional)

For high-stakes annotations or medium/low initial confidence. Run 2-3 independent passes and compare:

- All agree on category → consensus, confidence: high
- 2/3 agree → majority, confidence: medium, note discrepancy
- All differ → confidence: low, flag for human review

Skip if initial confidence is high and difficulty is easy.

---

## Step 3: Write Annotation

Verify before writing:
- All required files read (step1, step3, step4)
- Root cause category matches evidence
- Exactly one evidence item has `is_root_cause: true`
- All evidence has traceability (source_file, json_path, exact_value/quote)
- All evidence has confidence level
- Difficulty score calculated with justification
- Alternative diagnoses have plausibility levels

Write `annotation_draft.json` to `.analysis/<job_id>/`.

---

## Step 4: Upload Annotation

```bash
cd skills/rca-annotator
python scripts/cli.py upload --job-id <job_id>
```

Uploads `.analysis/<job_id>/annotation_draft.json` to jumpbox if `JUMPBOX_URI` set. Local copy always preserved. If `JUMPBOX_URI` unset, file remains local only.

---

## Output Format

Save to `.analysis/<job_id>/annotation_draft.json`:

```json
{
  "job_id": "1234567",
  "annotator": "claude_judge",
  "annotated_at": "2026-03-19T12:00:00Z",
  "difficulty": "easy | medium | hard",
  "difficulty_score": 5,
  "difficulty_justification": "Requires correlating task code (+2) with missing configs...",

  "root_cause": {
    "category": "configuration | infrastructure | application_bug | dependency | network | resource | cloud_api | credential | secrets | unknown",
    "summary": "One sentence describing what failed and why.",
    "confidence": "high | medium | low"
  },

  "evidence": [
    {
      "source": "step1 | step3 | step4",
      "source_file": ".analysis/<job_id>/step1_job_context.json",
      "json_path": "failed_tasks[0].duration",
      "exact_value": 917.565567,
      "exact_quote": "optional — literal text for code/config",
      "line_number": 5,
      "github_path": "owner/repo:path/to/file.yml:line",
      "message": "The relevant log line or config snippet.",
      "confidence": "high | medium | low",
      "is_root_cause": true
    }
  ],

  "recommendations": [
    {
      "priority": "high | medium | low",
      "action": "What should be done to fix it.",
      "file": "path/to/file.yml"
    }
  ],

  "contributing_factors": [
    "Factor that made the failure more likely or harder to diagnose."
  ],

  "alternative_diagnoses": [
    {
      "category": "infrastructure",
      "summary": "A plausible but wrong diagnosis.",
      "why_wrong": "Why the evidence does not support this.",
      "plausibility": "high | medium | low",
      "supporting_evidence": ["long timeout", "destroy action"],
      "contradicting_evidence": ["test environment", "auth retry pattern"]
    }
  ],

  "consistency_check": {
    "num_passes": 3,
    "agreement": {
      "category_agreement": "full | partial | none",
      "category_votes": {"credential": 3},
      "evidence_overlap": 0.85,
      "confidence_distribution": {"high": 2, "medium": 1, "low": 0},
      "confidence_mode": "high"
    }
  }
}
```
