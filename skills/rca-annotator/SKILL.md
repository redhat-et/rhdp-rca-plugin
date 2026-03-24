---
name: rca-annotator
description: LLM-as-judge skill that independently evaluates root-cause-analysis outputs to produce ground-truth annotations with multi-pass consistency checks, difficulty calibration, and complete evidence traceability.
allowed-tools:
  - Read
  - Write
  - Bash
---

# RCA Annotator (LLM-as-Judge)

An **LLM-as-judge** skill that independently re-analyzes raw RCA evidence (step1, step3, step4 outputs) to produce ground-truth annotations. The judge does not read or evaluate the agent's diagnosis directly — it forms its own conclusion from the same source evidence the agent had access to, then the resulting annotation serves as a labeled reference for scoring agent accuracy.

## Role: Judge vs Agent

| | `root-cause-analysis` (Agent) | `rca-annotator` (Judge) |
|---|---|---|
| **Purpose** | Diagnose failures for users | Produce ground-truth labels for evaluation |
| **Reads** | Logs, Splunk, GitHub (live) | Step 1/3/4 output files (offline) |
| **Output** | Human-readable diagnosis | Structured `annotation_draft.json` |
| **Used by** | RHDP operators | Eval harness, benchmarking, dataset curation |

The judge annotation can then be compared against the agent's diagnosis to measure precision, recall, and category accuracy.

## Key Capabilities

1. **Multi-Pass Consistency Checks** - Optional validation across multiple independent annotation passes
2. **Difficulty Calibration Rubric** - Objective 0-10 scoring system for benchmarking
3. **Evidence Traceability Matrix** - Exact JSON paths, line numbers, and quotes back to source files

## When to Use This Skill

Use this skill when:
- You have completed a root-cause-analysis for a job (steps 1-4 outputs exist)
- You want to create labeled ground-truth data for evaluating agent accuracy
- You need to assess the difficulty of diagnosing specific failures for benchmarking
- You want to identify plausible alternative diagnoses (red herrings) that could mislead an agent

**DO NOT** use this skill to:
- Perform the initial root cause analysis (use `root-cause-analysis` skill instead)
- Directly evaluate or score an agent's output (use the annotation as a reference key, then compare externally)

## Prerequisites

Before running this skill, verify that root-cause-analysis has been completed.

**Environment Variables** (optional):
- `JUMPBOX_URI` - SSH connection string for jumpbox access
  - Format: `"user@host -p port"` or `"user@host"` (default port 22)
  - Example: `"deploy@jumpbox.example.com -p 2222"`
  - If not set, uses local `.analysis/` directory only

**SSH Configuration** (if using jumpbox):
- SSH keys configured in `~/.ssh/config` for passwordless access
- System dependencies: `ssh`, `rsync` installed

**Required Files**:
- `step1_job_context.json` - Job metadata, failed tasks
- `step3_correlation.json` - Timeline with AAP + Splunk events
- `step4_github_fetch_history.json` - Configuration and code context

If required files are missing, run `root-cause-analysis` skill first.

## How It Works

The judge forms an independent diagnosis from the same raw evidence the agent used, without seeing the agent's conclusions. This avoids anchoring bias and produces an unbiased reference label.

**Workflow**:
0. **Download** analysis files from jumpbox (if `JUMPBOX_URI` set) or verify local files exist
1. Validate `.analysis/<job_id>/` directory exists with required files
2. Read step1, step3, step4 outputs (raw evidence only — never the agent's diagnosis)
3. Independently determine root cause with evidence scoring and traceability
4. **OPTIONAL**: Run multi-pass consistency check to validate judge reliability
5. Generate annotation with root cause, evidence (with confidence), difficulty score, recommendations, and alternatives
6. **Write** `annotation_draft.json` to disk as the ground-truth reference
7. **Upload** `annotation_draft.json` to jumpbox (if `JUMPBOX_URI` set)

---

## Step 0: Download Analysis Files from Jumpbox

Before reading analysis files, download them from jumpbox if configured.

**Download Command**:
```bash
cd skills/rca-annotator
python scripts/cli.py download --job-id <job_id>
```

**What this does**:
1. Checks if `JUMPBOX_URI` environment variable is set
2. If set: Downloads `/tmp/analysis/<job_id>/` or `/tmp/<job_id>` from jumpbox to local `.analysis/<job_id>/`
3. If not set: Verifies local `.analysis/<job_id>/` exists and has required files
4. Validates that required files exist (step1, step3, step4 JSON files)

**Error Handling**:
- If `JUMPBOX_URI` not set: Uses local files only (backward compatible)
- If remote directory missing: "Error: Remote directory does not exist or connection failed"
- If required files missing: Lists specific missing files
- If connection fails: "Failed to download files from jumpbox (timeout or connection error)"

**Success Output**:
```
Downloading analysis files for job 1234567...
  Downloading analysis files from jumpbox...
    Remote: user@jumpbox:/tmp/analysis/1234567/
    Local:  .analysis/1234567/
   Analysis files downloaded successfully
    Location: .analysis/1234567/

 Analysis files ready at .analysis/1234567/
```

Once files are downloaded and verified, proceed to Step 1.

---

## Step 1: Validate and Read Outputs

### File Verification

Files have already been verified in Step 0. You can proceed directly to reading them.

If you skipped Step 0 (local mode only), verify files exist:
```bash
ls -la .analysis/<job_id>/
```

### Reading Order

1. **Read step1_job_context.json** - Understand job metadata, failed tasks, error messages
2. **Read step3_correlation.json** - Get correlated timeline (this includes relevant Splunk context)
3. **Read step4_github_fetch_history.json** - Review configuration hierarchy and code

**Note**: `step2_splunk_logs.json` is not needed — step3 already includes relevant correlated events.

---

## Step 2: Analyze and Annotate

Perform analysis following these patterns:

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
- `credential` - Missing credentials, undefined variables, invalid authentication
- `secrets` - Missing credentials, incorrect keys, vault access issues
- `unknown` - Insufficient evidence to determine root cause

### B. Evidence Construction with Traceability

Build an evidence list with complete traceability to source files.

**Evidence Traceability Requirements**:
1. **Source file reference** - Which analysis file (step1/step3/step4)
2. **JSON path** - Exact path to the evidence in the source file
3. **Exact value/quote** - The literal value or error message
4. **Confidence level** - high/medium/low assessment of evidence quality

**Traceability Matrix Format**:

```json
{
  "source": "step1",
  "source_file": ".analysis/2035512/step1_job_context.json",
  "json_path": "failed_tasks[0].duration",
  "exact_value": 917.565567,
  "message": "Task duration of 917.565567 seconds indicates timeout, not immediate failure",
  "confidence": "medium",
  "is_root_cause": false
}
```

**For agnosticd_code sources**, also include:
```json
{
  "source": "agnosticd_code",
  "source_file": ".analysis/2035512/step4_github_fetch_history.json",
  "json_path": "github_fetches[0].fetched_workload.failed_task_code.content",
  "line_number": 5,
  "exact_quote": "kubeconfig: \"{{ _bookbag_kubeconfig | default(omit) }}\"",
  "github_path": "redhat-cop/agnosticd:ansible/roles/bookbag/tasks/remove_workload.yaml:5",
  "message": "Conditional kubeconfig usage - when undefined, parameter omitted",
  "confidence": "high",
  "is_root_cause": true
}
```

**For agnosticv_config sources**:
```json
{
  "source": "agnosticv_config",
  "source_file": ".analysis/2035512/step4_github_fetch_history.json",
  "json_path": "fetched_configs.env_overrides.paths_tried[0]",
  "exact_value": {"path": "tests/babylon-empty-config/prod.yaml", "status": "404"},
  "github_path": "rhpds/agnosticv:tests/babylon-empty-config/prod.yaml",
  "message": "Configuration file returned 404 - variable cannot be defined",
  "confidence": "high",
  "is_root_cause": false
}
```

**Step-by-Step Process**:
1. **Start with Step3 Timeline** - Events are already in chronological order
2. **Filter to Critical Events** - Focus on `task_failed` and `pod_error` types
3. **Identify Root Cause Evidence** - The item that pinpoints the underlying issue (`is_root_cause: true`)
4. **Extract Traceability Data** - JSON path, exact values, line numbers
5. **Assign Confidence Level** - high/medium/low based on evidence quality
6. **Include Supporting Evidence** - Additional items that corroborate

**Mark exactly ONE evidence item with `is_root_cause: true`** - this should be the most direct evidence pointing to the underlying cause.

### C. Difficulty Calibration with Scoring Rubric

Calculate an objective difficulty score (0-10) based on analysis complexity:

**Scoring Criteria**:
- **+3**: Requires correlation across multiple sources (AAP + Splunk + GitHub)
- **+2**: Requires understanding code behavior (not just config values)
- **+2**: Error message is generic/misleading (not directly diagnostic)
- **+1**: Requires understanding variable precedence or override hierarchy
- **+1**: Requires domain knowledge (Kubernetes, Ansible, cloud APIs)
- **+1**: Multiple plausible alternative diagnoses exist
- **+1**: Timing dependencies or causal ordering critical to diagnosis

**Difficulty Mapping**:
- **0-3**: easy
- **4-6**: medium
- **7-10**: hard

**Include both score and justification**:
```json
{
  "difficulty": "medium",
  "difficulty_score": 5,
  "difficulty_justification": "Requires correlating task code (+2) with missing configs (+0) and interpreting generic MODULE FAILURE (+2) and understanding default(omit) behavior (+1)"
}
```

### D. Alternative Diagnoses (Red Herrings)

Identify plausible-but-incorrect hypotheses with plausibility scoring:

**Enhanced Format**:
```json
{
  "category": "infrastructure",
  "summary": "OpenShift cluster is unavailable or unreachable, causing k8s_info to fail",
  "why_wrong": "The long timeout duration (917s) and MODULE FAILURE error are consistent with authentication retry/timeout, not network unavailability which would fail faster.",
  "plausibility": "medium",
  "supporting_evidence": ["long timeout", "destroy action", "no namespace"],
  "contradicting_evidence": ["test environment", "no cluster infrastructure", "auth retry pattern"]
}
```

**Plausibility Level**:
- **high**: Very plausible - shares multiple characteristics with correct diagnosis
- **medium**: Moderately plausible - some evidence supports it
- **low**: Weakly plausible - superficial similarity or clearly contradicted by evidence

---

## Step 2b: Multi-Pass Consistency Check (Optional)

**When to use**: For high-stakes annotations or when initial confidence is medium/low.

Run 2-3 independent annotation passes and check consistency:

```bash
# Run annotation 3 times (pseudocode - manual process)
# Pass 1: Already completed in Step 2
# Pass 2: Repeat Step 2 with fresh perspective
# Pass 3: Repeat Step 2 with fresh perspective
```

**Consistency Validation**:

```json
{
  "consistency_check": {
    "num_passes": 3,
    "agreement": {
      "category_agreement": "full",
      "category_votes": {"credential": 3, "infrastructure": 0, "configuration": 0},
      "evidence_overlap": 0.85,
      "confidence_distribution": {"high": 3, "medium": 0, "low": 0},
      "confidence_mode": "high"
    },
    "consensus_root_cause": {
      "category": "credential",
      "confidence": "high"
    },
    "discrepancies": []
  }
}
```

**Consistency Rules**:
- If all 3 agree on category → use consensus, confidence: high
- If 2/3 agree → use majority, confidence: medium, note discrepancy
- If all differ → confidence: low, flag for human review, include all alternatives

**When to skip**: If initial confidence is already high and difficulty is easy, consistency check may be unnecessary.

---

## Step 3: Finalize and Write Annotation

**Before writing**, verify:
- [ ] All evidence has traceability fields (source_file, json_path, exact_value/quote)
- [ ] All evidence has strength (direct/inferential/circumstantial)
- [ ] All evidence has confidence score (0.0-1.0)
- [ ] Exactly one evidence item has `is_root_cause: true`
- [ ] Difficulty score calculated with justification
- [ ] Alternative diagnoses have plausibility levels

Write `annotation_draft.json` to disk with complete schema (see Output Format below).

---

## Step 4: Upload Annotation to Jumpbox

After writing `annotation_draft.json` locally, upload it to jumpbox if configured.

**Upload Command**:
```bash
cd skills/rca-annotator
python scripts/cli.py upload --job-id <job_id>
```

**What this does**:
1. Verifies `annotation_draft.json` exists locally at `.analysis/<job_id>/`
2. If `JUMPBOX_URI` is set: Uploads to `/tmp/analysis/<job_id>/annotation_draft.json` or `/tmp/<job_id>/annotation_draft.json` on jumpbox (matches whichever remote path exists)
3. If `JUMPBOX_URI` not set: Skips upload, file remains local only
4. Always keeps local copy as backup

**Error Handling**:
- If local file missing: "Error: Local annotation file not found"
- If `JUMPBOX_URI` not set: "JUMPBOX_URI not set. Annotation saved locally only"
- If connection fails: "Error: Failed to upload annotation to jumpbox"
- Even if upload fails, local copy is preserved

**Success Output**:
```
Uploading annotation for job 1234567...
  Uploading annotation to jumpbox...
    Local:  .analysis/1234567/annotation_draft.json
    Remote: user@jumpbox:/tmp/analysis/1234567/annotation_draft.json
   Annotation uploaded successfully
    Remote location: user@jumpbox:/tmp/analysis/1234567/annotation_draft.json
    Local backup: .analysis/1234567/annotation_draft.json

 Annotation uploaded successfully
```

**Important**: The local copy at `.analysis/<job_id>/annotation_draft.json` is always preserved, even if upload fails. This ensures annotations are never lost.

---

## Output Format

Save annotation locally to `.analysis/<job_id>/annotation_draft.json` following this schema.

After writing locally (Step 3), the file will be automatically uploaded to the jumpbox at `/tmp/analysis/<job_id>/annotation_draft.json` or `/tmp/<job_id>/annotation_draft.json` (Step 4) if `JUMPBOX_URI` is configured.

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
      "source_file": ".analysis/2035512/step1_job_context.json",
      "json_path": "failed_tasks[0].duration",
      "exact_value": 917.565567,
      "exact_quote": "kubeconfig: \"{{ _bookbag_kubeconfig | default(omit) }}\"",
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
      "summary": "A plausible but wrong diagnosis an agent might produce.",
      "why_wrong": "Why the evidence does not support this.",
      "plausibility": "medium",
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

**New/Enhanced Fields**:
- `difficulty_score` - Numeric score 0-10
- `difficulty_justification` - Explanation of score calculation
- `root_cause.confidence` - high/medium/low assessment
- `evidence[].source_file` - Full path to source analysis file
- `evidence[].json_path` - Exact JSON path to evidence
- `evidence[].exact_value` - Literal value from source file
- `evidence[].exact_quote` - Literal text quote (for code/config)
- `evidence[].line_number` - Line number in source file
- `evidence[].confidence` - high/medium/low assessment
- `alternative_diagnoses[].plausibility` - high/medium/low assessment
- `alternative_diagnoses[].supporting_evidence` - List of supporting factors
- `alternative_diagnoses[].contradicting_evidence` - List of contradictions
- `consistency_check` - Multi-pass validation results (optional)

---

## Quality Checklist

Before writing `annotation_draft.json` to disk, verify:

- [ ] All required files were read (step1, step3, step4)
- [ ] Root cause category matches evidence
- [ ] Exactly one evidence item has `is_root_cause: true`
- [ ] All evidence has traceability (source_file, json_path, exact_value/quote)
- [ ] All evidence has confidence level (high/medium/low)
- [ ] Difficulty score calculated with justification
- [ ] Alternative diagnoses have plausibility levels

---
