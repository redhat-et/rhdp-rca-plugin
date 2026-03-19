---
name: rca-annotator
description: Two-pass annotation of root-cause-analysis outputs. Pass 1 acts as an independent judge LLM assessing steps 1-4 to produce unbiased ground-truth labels. Pass 2 compares the independent annotation against step 5 to evaluate model diagnosis quality.
allowed-tools:
  - Read
  - Write
  - Bash
---

# RCA Annotator

Create ground-truth annotations for root-cause-analysis outputs to enable model evaluation and improvement. This skill uses a two-pass approach: first acting as an independent judge LLM analyzing steps 1-4 outputs to produce unbiased diagnostic labels, then comparing the independent annotation against step 5 to evaluate model diagnosis quality.

## When to Use This Skill

Use this skill when:
- You have completed a root-cause-analysis for a job (steps 1-4 outputs exist)
- You want to create labeled training/evaluation data
- You need to assess the difficulty of diagnosing specific failures
- You want to identify plausible alternative diagnoses (red herrings)
- You want to evaluate step 5 diagnosis quality against independent ground truth (pass 2)

**DO NOT** use this skill to:
- Perform the initial root cause analysis (use `root-cause-analysis` skill instead)

## Prerequisites

Before running this skill, verify that root-cause-analysis has been completed:

```bash
# Check that analysis directory exists
ls -la .analysis/<job_id>/

# Required files for Pass 1 (independent annotation)
# step1_job_context.json       ← Job metadata, failed tasks
# step3_correlation.json        ← Timeline with AAP + Splunk events
# step4_github_fetch_history.json ← Configuration and code context

# Optional file for Pass 2 (step 5 comparison)
# step5_analysis_summary.json  ← Model diagnosis output (read ONLY in Step 3)
```

If required files are missing, run `root-cause-analysis` skill first.
If `step5_analysis_summary.json` is missing, Pass 2 (Step 3) will be skipped.

## How It Works

**Two-Pass Approach**: Pass 1 acts as an independent judge reading only steps 1-4 (NOT step 5) to produce unbiased ground-truth labels. Pass 2 then compares the independent annotation against step 5 to evaluate model diagnosis quality. The independent annotation is finalized and written to disk before step 5 is ever read.

**Workflow**:
1. Validate `.analysis/<job_id>/` directory exists with required files
2. Read step1, step3, step4 outputs (DO NOT read step5 yet)
3. Perform independent analysis following structured patterns
4. Generate annotation with root cause, evidence, difficulty, recommendations, and alternatives
5. **Write** `annotation_draft.json` to disk — independent annotation is now final
6. **If `step5_analysis_summary.json` exists**: Read step 5 and compare against independent annotation
7. Append `step5_comparison` block to `annotation_draft.json` and save

**CRITICAL**: Complete and write all independent annotation fields (steps 1-5) to disk BEFORE reading step 5. Do NOT revise any independent fields after reading step 5. The comparison is additive, not corrective.

---

## Step 1: Validate and Read Outputs

### File Verification

```bash
# List analysis files
ls -la .analysis/<job_id>/

# Verify required files exist
test -f .analysis/<job_id>/step1_job_context.json && \
test -f .analysis/<job_id>/step3_correlation.json && \
test -f .analysis/<job_id>/step4_github_fetch_history.json && \
echo "All required files present" || echo "ERROR: Missing required files"
```

**Error Handling**:
- If directory doesn't exist: "Run root-cause-analysis skill first for job <job_id>"
- If files missing: List which files are missing and provide guidance

### Reading Order (Pass 1 Only)

**CRITICAL**: Read files in this specific order. Do NOT read step5 until Step 3 (Pass 2):

1. **Read step1_job_context.json** - Understand job metadata, failed tasks, error messages
2. **Read step3_correlation.json** - Get correlated timeline (this includes relevant Splunk context)
3. **Read step4_github_fetch_history.json** - Review configuration hierarchy and code

**DO NOT READ YET**:
- `step5_analysis_summary.json` - Deferred to Step 3 (Pass 2), after independent annotation is written to disk
- `step2_splunk_logs.json` - Step3 already includes relevant correlated events

---

## Step 2: Act as Judge LLM

Perform independent analysis following these patterns:

### A. Root Cause Category Assessment

Analyze the evidence to determine the primary failure category:

**From Step1 (Job Context)**:
- Review `failed_tasks[]` - Look at task names, actions, error messages, durations
- Task action patterns indicate likely categories:
  - `kubernetes.core.k8s_info` → infrastructure/resource/rbac
  - `ansible.builtin.uri` → network/cloud_api/secrets
  - `command/shell` → configuration/dependency
- Error messages often directly state the issue (e.g., "is undefined" → configuration)

**From Step3 (Correlation Timeline)**:
- Examine `timeline_events[]` in chronological order
- Look for `event_type: task_failed` and `event_type: pod_error`
- Time gaps between events reveal causation vs coincidence
- Pod errors before task failures suggest infrastructure issues
- Task failures before pod errors suggest configuration/code issues

**From Step4 (GitHub Context)**:
- Review `github_fetches[]` for configuration hierarchy
- Check `fetched_configs` - look for missing variables, conflicts, malformed YAML
- Check `fetched_workload` - review code at failed task locations
- Note any `status: 404` or `error: all_paths_failed` - indicates missing/incorrect paths

**Category Definitions**:
- `configuration` - Missing variables, incorrect values, YAML syntax, env overrides
- `infrastructure` - Pod failures, node issues, cluster problems (before job starts)
- `application_bug` - Logic errors in AgnosticD workload code
- `dependency` - Missing packages, wrong versions, unavailable services
- `network` - Connection failures, timeouts, DNS issues
- `resource` - OOM, CPU limits, quota exceeded, disk full
- `cloud_api` - AWS/Azure/GCP API errors, rate limits, permissions
- `secrets` - Missing credentials, incorrect keys, vault access issues
- `unknown` - Insufficient evidence to determine root cause

### B. Evidence Construction

Build an evidence list that supports the root cause diagnosis. Mark the item that identifies the root cause with `is_root_cause: true`.

**Step-by-Step Process**:

1. **Start with Step3 Timeline** - Events are already in chronological order
2. **Filter to Critical Events** - Focus on `task_failed` and `pod_error` types
3. **Identify Root Cause Evidence** - The item that pinpoints the underlying issue (`is_root_cause: true`)
4. **Link to GitHub Context** - Include `github_path` for config/code sources from step4
5. **Include Supporting Evidence** - Additional items that corroborate or show propagation

**GitHub Path Extraction**:

For `agnosticv_config` sources:
```
From step4.github_fetches[0].fetched_configs.env_overrides:
  path: "openshift/cnv/staging.yaml"
  owner: "rhpds"
  repo: "agnosticv"
→ github_path: "rhpds/agnosticv:openshift/cnv/staging.yaml"
```

For `agnosticd_code` sources:
```
From step4.github_fetches[0].location.parsed:
  owner: "redhat-cop"
  repo: "agnosticd"
  file_path: "roles/example-role/tasks/main.yml"
  line_number: 42
→ github_path: "redhat-cop/agnosticd:roles/example-role/tasks/main.yml:42"
```

**Evidence Example** (Configuration Error):
```json
[
  {
    "source": "agnosticv_config",
    "message": "Variable 'database_password' undefined in env overrides",
    "github_path": "rhpds/agnosticv:openshift/cnv/staging.yaml",
    "is_root_cause": true
  },
  {
    "source": "aap_job",
    "message": "Task 'Deploy Database' failed: 'database_password' is undefined"
  },
  {
    "source": "splunk_ocp",
    "message": "Pod 'db-pod-123' CrashLoopBackOff: authentication failed"
  }
]
```

### C. Difficulty Rating

Assess how hard this issue would be for a human expert to diagnose. Use one of three levels:

- **easy**: Diagnosable from `step1_job_context.json` alone. Error message directly names the issue, single failed task with clear error (e.g., "'aws_key' is undefined", RBAC permission denied).
- **medium**: Requires correlating AAP failure with Splunk pod errors or GitHub config. Multiple sources needed to identify root cause (e.g., AAP + Splunk timeline, or AAP + GitHub config hierarchy).
- **hard**: Requires understanding operator lifecycle, config override precedence, or cross-role interactions. Subtle timing dependencies, multiple interacting failures, or deep domain expertise needed.

### D. Alternative Diagnoses (Red Herrings)

Identify plausible-but-incorrect hypotheses that could mislead diagnosis:

**Common Red Herrings**:
- **Pod restarts after failure**: These are effects, not causes (pod crashed because task failed, not vice versa)
- **Generic "Task failed"**: Symptom without identifying the underlying cause
- **Timing coincidences**: Events close in time but not causally related
- **Infrastructure events unrelated to job**: Pre-existing pod errors in same namespace
- **Correct configuration misidentified**: Assuming config is wrong when code has the bug

**Example**:
```json
{
  "category": "infrastructure",
  "summary": "Pod infrastructure failure caused job to fail",
  "why_wrong": "Pod crashed AFTER task failed due to authentication error from missing secret variable. The pod failure is a symptom, not the root cause."
}
```

---

## Step 3: Compare with Step 5 (Pass 2)

**CRITICAL**: Only proceed to this step AFTER `annotation_draft.json` has been written to disk with all independent fields finalized. Do NOT revise any independent fields based on what you read here.

If `step5_analysis_summary.json` does not exist, skip this step entirely.

### Read Step 5

```bash
# Only read step5 AFTER annotation_draft.json is saved
test -f .analysis/<job_id>/step5_analysis_summary.json && echo "Step 5 found — proceeding with comparison" || echo "Step 5 not found — skipping Pass 2"
```

Read `.analysis/<job_id>/step5_analysis_summary.json` and compare it against your independent annotation.

### Comparison Process

Evaluate step 5 against the independent annotation across these dimensions:

**A. Root Cause Agreement**

Compare the judge's `root_cause` against step 5's `root_cause`:

- `category_match` — Do both identify the same root cause category?
- `summary_alignment` — Do the summaries describe the same underlying issue, even if worded differently?
- `confidence_match` — Do both assign the same confidence level?
- `root_cause_agreement` — Overall: `full` (category + summary align), `partial` (same category, different nuance), or `none` (fundamentally different diagnosis)

**B. Evidence Coverage**

Compare evidence items between judge and step 5:

- Which evidence did step 5 include that the judge also found?
- Which evidence did step 5 find that the judge missed? (`missed_by_judge`)
- Which evidence did the judge find that step 5 missed? (`missed_by_step5`)
- For each missed item, briefly explain its significance

**C. Discrepancies**

For each field where judge and step 5 disagree, document:

- The field path (e.g., `root_cause.confidence`, `difficulty`)
- The judge's value vs step 5's value
- Reasoning for why they differ (based on the raw evidence from steps 1-4)

**D. Quality Score**

Rate step 5's diagnosis quality using the independent annotation as ground truth:

- `diagnosis_accuracy` — `correct` (root cause matches), `partial` (right category, wrong specifics), `incorrect` (wrong root cause)
- `evidence_completeness` — `complete` (all key evidence found), `partial` (missed some), `incomplete` (missed critical evidence)
- `recommendation_quality` — `good` (actionable, correct files), `adequate` (generally right direction), `poor` (wrong or vague)
- `overall` — Letter grade: `A` (correct diagnosis, complete evidence, good recommendations), `B` (mostly correct with minor gaps), `C` (partially correct or significant evidence gaps), `D` (incorrect diagnosis or major evidence omissions), `F` (fundamentally wrong)

---

## Output Format

Save annotation to `.analysis/<job_id>/annotation_draft.json` following this schema:

```json
{
  "job_id": "1234567",
  "annotator": "annotator_id",
  "annotated_at": "2026-03-08T12:00:00Z",
  "difficulty": "easy | medium | hard",

  "root_cause": {
    "category": "configuration | infrastructure | application_bug | dependency | network | resource | cloud_api | secrets | unknown",
    "summary": "One sentence describing what failed and why.",
    "confidence": "high | medium | low"
  },

  "evidence": [
    {
      "source": "aap_job | splunk_ocp | agnosticv_config | agnosticd_code",
      "message": "The relevant log line or config snippet.",
      "github_path": "owner/repo:path/to/file.yml:line",
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
      "summary": "A plausible but wrong diagnosis an agent might produce.",
      "why_wrong": "Why the evidence does not support this."
    }
  ],

  "step5_comparison": {
    "compared_at": "2026-03-08T12:01:00Z",
    "category_match": true,
    "confidence_match": false,
    "root_cause_agreement": "full | partial | none",
    "discrepancies": [
      {
        "field": "root_cause.confidence",
        "judge_value": "medium",
        "step5_value": "high",
        "reasoning": "Why the values differ based on raw evidence from steps 1-4."
      }
    ],
    "missed_by_judge": [
      "Evidence or insight that step 5 found but the judge did not."
    ],
    "missed_by_step5": [
      "Evidence or insight that the judge found but step 5 did not."
    ],
    "quality_score": {
      "diagnosis_accuracy": "correct | partial | incorrect",
      "evidence_completeness": "complete | partial | incomplete",
      "recommendation_quality": "good | adequate | poor",
      "overall": "A | B | C | D | F"
    }
  }
}
```

**Fields (Pass 1 — Independent Annotation)**:
- `job_id` - Job being annotated
- `annotated_at` - Current timestamp (ISO 8601)
- `annotator` - Set to "claude_judge"
- `difficulty` - One of: `easy`, `medium`, `hard`
- `root_cause` - Category, summary, confidence
- `evidence` - Evidence items with source, message, optional `github_path` and `is_root_cause` flag
- `recommendations` - Actionable fixes with priority, action, and file path
- `contributing_factors` - Factors that made the failure more likely or harder to diagnose
- `alternative_diagnoses` - At least 1-2 plausible-but-incorrect hypotheses

**Fields (Pass 2 — Step 5 Comparison, optional)**:
- `step5_comparison` - Present only when `step5_analysis_summary.json` exists. Added AFTER all independent fields are finalized
- `step5_comparison.compared_at` - Timestamp when comparison was performed
- `step5_comparison.category_match` - Whether root cause categories agree
- `step5_comparison.confidence_match` - Whether confidence levels agree
- `step5_comparison.root_cause_agreement` - `full`, `partial`, or `none`
- `step5_comparison.discrepancies[]` - Field-level differences with reasoning
- `step5_comparison.missed_by_judge[]` - Evidence step 5 found that the judge missed
- `step5_comparison.missed_by_step5[]` - Evidence the judge found that step 5 missed
- `step5_comparison.quality_score` - Accuracy, completeness, recommendation quality, overall grade

---

## Labeling Guidelines

### Category Selection

Choose the **primary** root cause, even if multiple factors contributed:
- If a missing variable caused a task to fail → `configuration` (not `application_bug`)
- If incorrect RBAC prevented pod creation → `infrastructure` (not `configuration`)
- If a code bug only manifests with certain config → `application_bug` (config is trigger, not cause)

### Evidence Extraction

Every piece of evidence must:
- Come directly from step1/step3/step4 outputs (no inference)
- Quote actual error messages (don't paraphrase)
- Include `github_path` when source is `agnosticv_config` or `agnosticd_code`
- Set `is_root_cause: true` on exactly one item that identifies the underlying cause

### Confidence Levels

- **High**: Clear error messages, direct evidence, single failure mode
- **Medium**: Requires correlation, some ambiguity, multiple possible causes
- **Low**: Insufficient data, missing logs, unclear timeline

---

## Examples

### Example 1: Configuration Error (Missing Variable)

**Scenario**: Task fails due to undefined variable in AgnosticV config. Step 5 exists and correctly identified the root cause but missed a contributing factor.

**Annotation Output**:
```json
{
  "job_id": "1234567",
  "annotated_at": "2025-01-15T10:45:00Z",
  "annotator": "claude_judge",
  "difficulty": "easy",
  "root_cause": {
    "category": "configuration",
    "summary": "Missing 'aws_access_key_id' variable in staging environment config",
    "confidence": "high"
  },
  "evidence": [
    {
      "source": "agnosticv_config",
      "message": "Variable 'aws_access_key_id' not defined in openshift/cnv/staging.yaml",
      "github_path": "rhpds/agnosticv:openshift/cnv/staging.yaml",
      "is_root_cause": true
    },
    {
      "source": "aap_job",
      "message": "TASK [setup-aws : Configure AWS credentials] failed: 'aws_access_key_id' is undefined"
    },
    {
      "source": "splunk_ocp",
      "message": "Pod aws-setup-pod CrashLoopBackOff: container exited with code 1"
    }
  ],
  "recommendations": [
    {
      "priority": "high",
      "action": "Add aws_access_key_id variable to staging environment config",
      "file": "openshift/cnv/staging.yaml"
    }
  ],
  "contributing_factors": [
    "Variable not defined at any level of the AgnosticV config hierarchy"
  ],
  "alternative_diagnoses": [
    {
      "category": "secrets",
      "summary": "AWS credentials are invalid or expired",
      "why_wrong": "Error message states variable is 'undefined', not invalid. The variable doesn't exist in config, not that credentials are wrong."
    }
  ],
  "step5_comparison": {
    "compared_at": "2025-01-15T10:46:00Z",
    "category_match": true,
    "confidence_match": true,
    "root_cause_agreement": "full",
    "discrepancies": [],
    "missed_by_judge": [],
    "missed_by_step5": [
      "Step 5 did not note that the variable is missing at every level of the AgnosticV config hierarchy, not just the staging override."
    ],
    "quality_score": {
      "diagnosis_accuracy": "correct",
      "evidence_completeness": "partial",
      "recommendation_quality": "good",
      "overall": "A"
    }
  }
}
```

### Example 2: Infrastructure Failure (Pod Eviction)

**Scenario**: Node pressure causes pod eviction before job completes. Step 5 exists but misdiagnosed the root cause as a network issue.

**Annotation Output**:
```json
{
  "job_id": "7654321",
  "annotated_at": "2025-01-15T11:00:00Z",
  "annotator": "claude_judge",
  "difficulty": "medium",
  "root_cause": {
    "category": "infrastructure",
    "summary": "Node memory pressure triggered pod eviction during job execution",
    "confidence": "high"
  },
  "evidence": [
    {
      "source": "splunk_ocp",
      "message": "Node ip-10-0-1-100 MemoryPressure=True",
      "is_root_cause": true
    },
    {
      "source": "splunk_ocp",
      "message": "Pod ansible-runner-pod-abc Evicted: Node has insufficient memory"
    },
    {
      "source": "aap_job",
      "message": "TASK [deploy-app : Wait for deployment] failed: Connection lost to executor pod"
    }
  ],
  "recommendations": [
    {
      "priority": "high",
      "action": "Investigate node memory usage and increase resource limits or add nodes",
      "file": "cluster/node-config.yaml"
    }
  ],
  "contributing_factors": [
    "Node memory pressure pre-existed before job execution",
    "No pod disruption budget configured for runner pods"
  ],
  "alternative_diagnoses": [
    {
      "category": "network",
      "summary": "Network connectivity issue between AAP and OCP",
      "why_wrong": "Splunk timeline shows pod was evicted due to memory pressure before connection loss. Network was fine; pod was terminated by kubelet."
    },
    {
      "category": "application_bug",
      "summary": "Task deployment step caused pod to crash",
      "why_wrong": "Pod eviction event shows 'insufficient memory' reason, not application crash. Node pressure existed before task execution."
    }
  ],
  "step5_comparison": {
    "compared_at": "2025-01-15T11:01:00Z",
    "category_match": false,
    "confidence_match": false,
    "root_cause_agreement": "none",
    "discrepancies": [
      {
        "field": "root_cause.category",
        "judge_value": "infrastructure",
        "step5_value": "network",
        "reasoning": "Step 5 focused on the 'Connection lost to executor pod' error message and diagnosed a network issue. However, the Splunk timeline shows MemoryPressure=True and pod eviction events occurred before the connection loss, making infrastructure the root cause."
      },
      {
        "field": "root_cause.confidence",
        "judge_value": "high",
        "step5_value": "medium",
        "reasoning": "Step 5 rated medium confidence due to ambiguity between network and infrastructure. The Splunk pod eviction events provide clear causal evidence that the judge rated as high confidence."
      }
    ],
    "missed_by_judge": [],
    "missed_by_step5": [
      "Step 5 did not identify the MemoryPressure=True node condition as the root cause event.",
      "Step 5 missed that the pod eviction preceded the connection loss in the timeline."
    ],
    "quality_score": {
      "diagnosis_accuracy": "incorrect",
      "evidence_completeness": "partial",
      "recommendation_quality": "poor",
      "overall": "D"
    }
  }
}
```

---

## Troubleshooting

**Error: Directory not found**
```
Run root-cause-analysis skill first:
  root-cause-analysis skill for job <job_id>
```

**Error: Missing required files**
```
Check which files exist:
  ls .analysis/<job_id>/

Required: step1_job_context.json, step3_correlation.json, step4_github_fetch_history.json
```

**Warning: step4 has GitHub fetch errors**
```
If step4 shows "status": "404" or "error": "all_paths_failed":
- Adjust confidence to "medium" or "low"
- Note the missing data in contributing_factors
```

**Question: When should I read step5?**
```
Only AFTER annotation_draft.json is written to disk with all independent fields.
Step 5 is read in Step 3 (Pass 2) for comparison only. Never read it during Steps 1-2.
```

**Question: What if step5 is missing?**
```
Skip Step 3 (Pass 2) entirely. The annotation is still valid without step5_comparison.
The independent annotation (Pass 1) is the primary output.
```

---

## Quality Checklist

### Pass 1 (Independent Annotation)

Before writing `annotation_draft.json` to disk, verify:

- [ ] All required files were read (step1, step3, step4)
- [ ] Did NOT read step5_analysis_summary.json yet
- [ ] Root cause category matches evidence
- [ ] Exactly one evidence item has `is_root_cause: true`
- [ ] `github_path` included for all `agnosticv_config` / `agnosticd_code` evidence
- [ ] Difficulty is one of: `easy`, `medium`, `hard`
- [ ] At least 1-2 alternative diagnoses provided with `category`, `summary`, `why_wrong`
- [ ] Alternative hypotheses are actually plausible (not strawmen)
- [ ] All evidence comes directly from source files (no fabrication)
- [ ] Recommendations include actionable fixes with file paths
- [ ] Confidence level matches evidence quality
- [ ] `annotation_draft.json` is written to disk before proceeding to Pass 2

### Pass 2 (Step 5 Comparison)

After reading step5 and generating comparison, verify:

- [ ] No independent annotation fields were modified after reading step 5
- [ ] `root_cause_agreement` accurately reflects the degree of alignment
- [ ] Each discrepancy includes reasoning grounded in steps 1-4 evidence
- [ ] `missed_by_judge` and `missed_by_step5` are honest (not empty by default)
- [ ] `quality_score.overall` grade is consistent with the individual dimension scores
- [ ] `compared_at` timestamp is after `annotated_at`

---
