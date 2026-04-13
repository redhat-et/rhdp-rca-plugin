---
name: rca-annotator
description: Structured annotation tool that walks users through reviewing and labeling root-cause-analysis outputs, with evidence traceability, difficulty calibration, and alternative diagnosis capture. Use this skill whenever the user wants to annotate, review, label, or evaluate a root cause analysis - whether they say "annotate the RCA for job X", "review the diagnosis", "label this analysis", or "mark what's correct/incorrect". Also use when they want to create ground-truth data, build evaluation datasets, or validate RCA agent performance.
allowed-tools:
  - Read
  - Write
  - Bash
  - AskUserQuestion
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
  - `step5_analysis_summary.json` — Agent's final diagnosis (primary input)
  - `step1_job_context.json` — Job metadata, failed tasks
  - `step3_correlation.json` — Timeline with AAP + Splunk events
  - `step4_github_fetch_history.json` — Configuration and code context

If missing, run `root-cause-analysis` skill first.

## Before You Start

Read [`references/example_annotations.md`](references/example_annotations.md) to see what good annotations look like. This shows real examples with proper evidence traceability, difficulty scoring, and alternative diagnoses.

## Workflow

1. Download from jumpbox (if `JUMPBOX_URI` set) or verify local files
2. Read `step5_analysis_summary.json` — present the agent's diagnosis to the user with pre-populated answers
3. Walk through annotation questions interactively — the user confirms or corrects each section
4. Write `annotation.json` with the user's labels
5. Validate annotation with `scripts/validate.py`
6. Upload to jumpbox (if `JUMPBOX_URI` set)

---

## Step 1: Download Analysis Files

First, determine where the analysis files are:

1. Check if `step5_analysis_summary.json` exists in the current directory
2. If not, check `.analysis/<job_id>/`
3. If neither exists and `JUMPBOX_URI` is set, download files:


```bash
cd skills/rca-annotator
python scripts/cli.py download --job-id <job_id>
```

Downloads from jumpbox `/usr/local/mlflow/<job_id>/` to local `.analysis/<job_id>/`. If `JUMPBOX_URI` unset, validates local files only. Errors on missing remote directory, missing required files, or connection failure.

**For eval/headless mode**: Files are in the current directory (workspace root)
**For interactive mode**: Files are in `.analysis/<job_id>/` (relative to `skills/rca-annotator/`)

Once located, record the base path for reading step files in subsequent steps.

---

## Step 2: Read Agent Diagnosis and Pre-populate Answers

Read `step5_analysis_summary.json` and present the agent's diagnosis clearly to the user. This is the starting point for annotation.

**Present with pre-populated answers** to make annotation faster. Instead of asking blank questions, show what the agent produced and let the user confirm or correct:

- Root cause category and summary
- Confidence level
- Key evidence cited
- Difficulty score (if present)
- Recommendations
- Alternative diagnoses (if any)

This is the starting point for annotation. The user is reviewing the agent's work.

---

## Step 3: Interactive Annotation

Walk through each question below with the user. **Present pre-populated answers based on `step5_analysis_summary.json`** before asking for confirmation. This makes annotation much faster - users just confirm or correct rather than answering from scratch.

Wait for the user's response before continuing to the next question.

### 1. Root Cause Category

Present the agent's category and summary with a suggested answer:

> **The agent identified this as: `configuration`**
> 
> **Is this correct?** *(confirm with ✓, or provide the correct category)*

Valid categories: `configuration` | `infrastructure` | `application_bug` | `dependency` | `network` | `resource` | `cloud_api` | `credential` | `secrets` | `unknown`

If user confirms: record `category_correct: true`
If user corrects: record `category_correct: false` and the corrected category

### 2. Summary Accuracy

Present the agent's summary sentence:

> **Agent's summary:**
> "Missing Kubernetes cluster credentials due to intentionally empty configuration..."
> 
> **Is this accurate and specific?** *(Does it clearly describe what failed and why? Confirm with ✓ or suggest improvements)*

If user has suggestions, capture them in `summary_comment`.

### 3. Evidence

Present the evidence items the agent cited in a numbered list:

> **Agent cited 3 evidence items:**
> 1. [step1] Task 'List project namespaces' failed after 917s
> 2. [step4] Config files missing (404): prod.yaml, common.yaml
> 3. [step3] No Splunk correlation - test environment
> 
> **Is any evidence missing or wrong?** *(Confirm with ✓ if complete, or specify what's missing/incorrect)*

If the user wants to cross-check, read step1/step3/step4 and compare against what the agent cited. This is reference material for validation — not a re-analysis.

When user identifies missing evidence, help them add it with full traceability (see example_annotations.md for format).

**Evidence traceability format** (for any new or corrected evidence items the user provides):

```json
{
  "source": "step1 | step3 | step4",
  "source_file": ".analysis/<job_id>/step1_job_context.json",
  "json_path": "failed_tasks[0].duration",
  "exact_value": 920.0,
  "exact_quote": "optional — literal text for code/config",
  "line_number": 5,
  "github_path": "owner/repo:path/to/file.yml:line",
  "message": "The relevant log line or config snippet.",
  "confidence": "high | medium | low",
  "is_root_cause": true
}
```

### 4. Difficulty Rating

Present the calibration rubric and pre-calculate a suggested score based on the evidence:

**Official Rubric (0-10 points):**

1. **Evidence Availability (0-4 points)**: How easy is it to find the root cause?
   - 0 = Obvious from first error message, no investigation needed
   - 1 = Clear from AAP logs alone (single source)
   - 2 = Requires reading 2 sources (AAP + Splunk OR AAP + GitHub)
   - 3 = Requires correlating 3+ sources (AAP + Splunk + GitHub)
   - 4 = Evidence is hidden, requires deep log parsing or code tracing

2. **Confusion Factors (0-3 points)**: Are there misleading signals?
   - 0 = No distractions, single clear failure
   - 1 = Generic error message (e.g., "MODULE FAILURE") but context narrows it
   - 2 = Multiple concurrent failures or misleading error messages
   - 3 = Red herrings, timing dependencies, or transient issues that mask root cause

3. **Domain Expertise (0-3 points)**: How much specialized knowledge is needed?
   - 0 = No domain knowledge needed (simple configuration error)
   - 1 = Basic familiarity (knows what K8s RBAC is, what Ansible variables do)
   - 2 = Intermediate expertise (understands cloud API behavior, Ansible precedence rules)
   - 3 = Deep expertise (needs to understand cloud provider internals, complex code behavior)

**Mapping:** 0–3 = easy, 4–6 = medium, 7–10 = hard.

**Example calculation:**
> - **Evidence Availability:** Requires AAP + GitHub correlation (+2)
> - **Confusion Factors:** Generic error "MODULE FAILURE" (+1)
> - **Domain Expertise:** Needs basic K8s RBAC knowledge (+1)
> - **Total: 4 → medium**
> 
> **Does this seem right?** *(Confirm with ✓, or provide corrected score with justification)*

The pre-calculated score helps users calibrate quickly. They can accept or adjust based on their assessment of these three dimensions.

**IMPORTANT: Justification Format Requirement**

When the user provides or confirms the difficulty justification, it MUST use this exact structure with all three rubric dimensions explicitly labeled:

```
- Evidence Availability (0-4): <score> - <brief reason>
- Confusion Factors (0-3): <score> - <brief reason>
- Domain Expertise (0-3): <score> - <brief reason>
Total: <sum> → <difficulty level>
```

Before moving to the next question, verify that the justification:
- Lists all three rubric dimensions by name (Evidence Availability, Confusion Factors, Domain Expertise)
- Assigns a specific score (0-4, 0-3, 0-3) to each dimension
- Shows the calculation (sum → difficulty level)

Do NOT accept justifications using ad-hoc notation like "Generic error (+2), domain knowledge (+1), Total: 3" without explicit dimension labels. The judge requires seeing which points come from which rubric dimension to verify proper calibration.

### 5. Alternative Diagnoses

Present any alternative diagnoses the agent identified:

> **Agent identified 0 alternative diagnoses.**
> 
> **Any plausible alternatives worth capturing?** *(e.g., "Could this have been a credential expiration issue instead?" — see example_annotations.md for guidance)*
> 
> Focus on plausible alternatives that were ruled out, not random guesses. High plausibility means hard to distinguish from the real root cause.

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

## Step 4: Write Annotation

After all questions are answered, verify before writing:

- Root cause category confirmed or corrected
- Exactly one evidence item has `is_root_cause: true`
- All evidence has traceability (source_file, json_path, exact_value/quote)
- Difficulty score calculated with justification
- Alternative diagnoses have plausibility levels

Write `annotation.json` to `.analysis/<job_id>/`.

The output must conform to [`schemas/schema.json`](schemas/schema.json). See [`references/example_annotations.md`](references/example_annotations.md) for complete examples.

---

## Step 5: Validate Annotation

Before uploading, validate the annotation structure:

```bash
cd skills/rca-annotator
python scripts/validate.py --job-id <job_id>
```

This checks:
- Required fields present
- Valid category/confidence/difficulty values  
- Exactly one evidence item marked as root cause
- JSON structure matches schema

If validation fails, fix the errors before proceeding. If validation passes, continue to Step 5.

---

## Step 6: Upload Annotation

```bash
cd skills/rca-annotator
python scripts/cli.py upload --job-id <job_id>
```

Uploads `.analysis/<job_id>/annotation.json` to jumpbox if `JUMPBOX_URI` set. Local copy always preserved. If `JUMPBOX_URI` unset, file remains local only.

---

## Tips for Efficient Annotation

**Pre-population saves time**: By presenting the agent's answers first, correct diagnoses take ~2 minutes to annotate (just confirmations), while incorrect ones take ~10 minutes (corrections and additions).

**Evidence traceability matters**: Always include `source_file`, `json_path`, and `exact_value`/`exact_quote`. This lets others verify your annotation and makes the dataset useful for training.

**Difficulty calibration**: Use the rubric and show your work. This helps maintain consistency across annotators.

**Alternative diagnoses**: Focus on plausible alternatives that share characteristics with the real root cause. Low-plausibility alternatives (random guesses) add noise, not signal.

See [`references/example_annotations.md`](references/example_annotations.md) for complete examples showing all these patterns.
