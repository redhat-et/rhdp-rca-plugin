---
name: rca-annotator
description: Structured annotation tool that walks users through reviewing and labeling root-cause-analysis outputs, with evidence traceability, difficulty calibration, and alternative diagnosis capture.
allowed-tools:
  - Read
  - Write
  - Bash
---

# RCA Annotator

A structured annotation tool that presents the `root-cause-analysis` agent's diagnosis to the user and guides them through labeling it — capturing whether the diagnosis is correct, evidence quality, difficulty, and alternative hypotheses.

| | `root-cause-analysis` (Agent) | `rca-annotator` (Annotation tool) |
|---|---|---|
| **Purpose** | Diagnose failures for users | Capture human-labeled ground-truth data |
| **Reads** | Logs, Splunk, GitHub (live) | Step 1/3/4/5 output files (offline) |
| **Output** | Human-readable diagnosis | Structured `annotation.json` |

**Use when**: a `root-cause-analysis` run is complete and you want to annotate its output as correct, incorrect, or partially correct — for evaluation, benchmarking, or dataset building.

**Do NOT use** to perform initial RCA (use `root-cause-analysis`).

## Prerequisites

Verify root-cause-analysis has been completed.

- `JUMPBOX_URI` (optional) — SSH connection string (e.g. `"user@host -p 2222"`). If unset, uses local `.analysis/` only.
- SSH keys configured in `~/.ssh/config` if using jumpbox; `ssh` and `rsync` installed.
- **Required files** in `.analysis/<job_id>/`:
  - `step5_summary.json` — Agent's final diagnosis (primary input)
  - `step1_job_context.json` — Job metadata, failed tasks
  - `step3_correlation.json` — Timeline with AAP + Splunk events
  - `step4_github_fetch_history.json` — Configuration and code context

If missing, run `root-cause-analysis` skill first.

## Workflow

0. Download from jumpbox (if `JUMPBOX_URI` set) or verify local files
1. Read `step5_summary.json` — present the agent's diagnosis to the user
2. Walk through annotation questions interactively — the user labels each section
3. Write `annotation.json` with the user's labels
4. Upload to jumpbox (if `JUMPBOX_URI` set)

---

## Step 0: Download Analysis Files

```bash
cd skills/rca-annotator
python scripts/cli.py download --job-id <job_id>
```

Downloads from jumpbox `/usr/local/mlflow/<job_id>/` to local `.analysis/<job_id>/`. If `JUMPBOX_URI` unset, validates local files only. Errors on missing remote directory, missing required files, or connection failure.

---

## Step 1: Read Agent Diagnosis

Read `step5_summary.json` and present the agent's diagnosis clearly to the user:

- Root cause category and summary
- Confidence level
- Key evidence cited
- Difficulty score (if present)
- Recommendations
- Alternative diagnoses (if any)

This is the starting point for annotation. The user is reviewing the agent's work.

---

## Step 2: Interactive Annotation

Walk through each question below with the user. Present the relevant section from `step5_summary.json` before asking each question. Wait for the user's response before continuing.

### 1. Root Cause Category

Present the agent's category and summary. Ask:

> **Is the root cause category correct?** *(e.g. `configuration`, `infrastructure`, `credential` — or should it be something else?)*

Valid categories: `configuration` | `infrastructure` | `application_bug` | `dependency` | `network` | `resource` | `cloud_api` | `credential` | `secrets` | `unknown`

### 2. Summary Accuracy

Present the agent's summary sentence. Ask:

> **Is the summary accurate and specific?** *(Does it clearly describe what failed and why?)*

### 3. Evidence

Present the evidence items the agent cited. Ask:

> **Is any evidence missing or wrong?** *(Any key log lines, config values, or Splunk events that were overlooked or incorrectly cited?)*

If the user wants to cross-check, read step1/step3/step4 and compare against what the agent cited. This is reference material for validation — not a re-analysis.

**Evidence traceability format** (for any new or corrected evidence items the user provides):

```json
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
```

### 4. Difficulty Rating

Present the agent's difficulty score (or estimate one from the evidence). Present the calibration rubric to help the user score:

| Criterion | Points |
|---|---|
| Requires cross-source correlation (AAP + Splunk + GitHub) | +3 |
| Requires understanding code behavior | +2 |
| Error message is generic or misleading | +2 |
| Requires variable precedence/override knowledge | +1 |
| Requires domain knowledge (K8s, Ansible, cloud APIs) | +1 |
| Multiple plausible alternatives exist | +1 |
| Timing dependencies are critical | +1 |

Mapping: 0–3 = easy, 4–6 = medium, 7–10 = hard.

Ask:

> **Is the difficulty rating appropriate?** *(Score of X / 10 — too easy, too hard, or about right? Use the rubric above if helpful.)*

### 5. Alternative Diagnoses

Present any alternative diagnoses the agent identified. Ask:

> **Any alternative diagnoses to add or correct?** *(Other plausible-but-incorrect hypotheses worth capturing?)*

Alternative diagnosis format:

```json
{
  "category": "infrastructure",
  "summary": "A plausible but wrong diagnosis.",
  "why_wrong": "Why the evidence does not support this.",
  "plausibility": "high | medium | low",
  "supporting_evidence": ["long timeout", "destroy action"],
  "contradicting_evidence": ["test environment", "auth retry pattern"]
}
```

`plausibility`: `high` = shares many characteristics | `medium` = some evidence | `low` = superficial similarity

---

## Step 3: Write Annotation

After all questions are answered, verify before writing:

- Root cause category confirmed or corrected
- Exactly one evidence item has `is_root_cause: true`
- All evidence has traceability (source_file, json_path, exact_value/quote)
- Difficulty score calculated with justification
- Alternative diagnoses have plausibility levels

Write `annotation.json` to `.analysis/<job_id>/`.

---

## Step 4: Upload Annotation

```bash
cd skills/rca-annotator
python scripts/cli.py upload --job-id <job_id>
```

Uploads `.analysis/<job_id>/annotation.json` to jumpbox if `JUMPBOX_URI` set. Local copy always preserved. If `JUMPBOX_URI` unset, file remains local only.

---

## Output Format

Save to `.analysis/<job_id>/annotation.json`:

```json
{
  "job_id": "1234567",
  "annotated_at": "2026-03-19T12:05:00Z",

  "category_correct": true,
  "category_comment": "Confirmed — matches the auth retry pattern.",

  "root_cause": {
    "category": "configuration | infrastructure | application_bug | dependency | network | resource | cloud_api | credential | secrets | unknown",
    "summary": "One sentence describing what failed and why.",
    "confidence": "high | medium | low"
  },

  "summary_accurate": true,
  "summary_comment": "Clear and specific.",

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

  "evidence_feedback": "Missing the kubeconfig 404 from step4 github_fetches.",

  "difficulty": "easy | medium | hard",
  "difficulty_score": 5,
  "difficulty_justification": "Requires correlating task code (+2) with missing configs and interpreting generic MODULE FAILURE (+2). Total: 5.",
  "difficulty_appropriate": false,
  "difficulty_comment": "Should be hard (8/10) — requires deep variable precedence knowledge.",

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
  ]
}
```
