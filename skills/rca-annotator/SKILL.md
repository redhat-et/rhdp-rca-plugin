---
name: rca-annotator
description: Two-pass annotation with multi-pass consistency checks, difficulty calibration rubric, and complete evidence traceability for accurate ground-truth labels of root-cause-analysis outputs.
allowed-tools:
  - Read
  - Write
  - Bash
---

# RCA Annotator

Create high-accuracy ground-truth annotations for root-cause-analysis outputs with multi-pass consistency validation and complete traceability. This skill uses a two-pass approach with optional consistency checks to ensure reliable diagnostic labels for model evaluation.

## Enhancements

1. **Multi-Pass Consistency Checks** - Optional validation across multiple independent runs
2. **Difficulty Calibration Rubric** - Objective 0-10 scoring system
3. **Evidence Traceability Matrix** - Exact JSON paths, line numbers, and quotes

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

**Two-Pass Approach with Optional Consistency Validation**: Pass 1 acts as an independent judge reading only steps 1-4 (NOT step 5) to produce unbiased ground-truth labels. Optionally run 2-3 independent passes to validate consistency. Pass 2 then compares the independent annotation against step 5 to evaluate model diagnosis quality.

**Workflow**:
1. Validate `.analysis/<job_id>/` directory exists with required files
2. Read step1, step3, step4 outputs (DO NOT read step5 yet)
3. Perform independent analysis with enhanced evidence scoring
4. **OPTIONAL**: Run multi-pass consistency check
5. Generate annotation with root cause, evidence (with strength/confidence), difficulty score, recommendations, and alternatives
6. **Write** `annotation_draft.json` to disk — independent annotation is now final
7. **If `step5_analysis_summary.json` exists**: Read step 5 and compare against independent annotation
8. Append `step5_comparison` block to `annotation_draft.json` and save

**CRITICAL**: Complete and write all independent annotation fields to disk BEFORE reading step 5. Do NOT revise any independent fields after reading step 5. The comparison is additive, not corrective.

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

**CRITICAL**: Read files in this specific order. Do NOT read step5 until Step 4 (Pass 2):

1. **Read step1_job_context.json** - Understand job metadata, failed tasks, error messages
2. **Read step3_correlation.json** - Get correlated timeline (this includes relevant Splunk context)
3. **Read step4_github_fetch_history.json** - Review configuration hierarchy and code

**DO NOT READ YET**:
- `step5_analysis_summary.json` - Deferred to Step 4 (Pass 2), after independent annotation is written to disk
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

## Step 3: Finalize and Write Independent Annotation

**Before writing**, verify:
- [ ] All evidence has traceability fields (source_file, json_path, exact_value/quote)
- [ ] All evidence has strength (direct/inferential/circumstantial)
- [ ] All evidence has confidence score (0.0-1.0)
- [ ] Exactly one evidence item has `is_root_cause: true`
- [ ] Difficulty score calculated with justification
- [ ] Alternative diagnoses have plausibility levels

Write `annotation_draft.json` to disk with complete schema (see Output Format below).

---

## Step 4: Compare with Step 5 (Pass 2)

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
  },

  "step5_comparison": {
    "compared_at": "2026-03-19T12:01:00Z",
    "category_match": true,
    "confidence_match": false,
    "root_cause_agreement": "full | partial | none",
    "discrepancies": [],
    "missed_by_judge": [],
    "missed_by_step5": [],
    "quality_score": {
      "diagnosis_accuracy": "correct | partial | incorrect",
      "evidence_completeness": "complete | partial | incomplete",
      "recommendation_quality": "good | adequate | poor",
      "overall": "A | B | C | D | F"
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

### Pass 1 (Independent Annotation)

Before writing `annotation_draft.json` to disk, verify:

- [ ] All required files were read (step1, step3, step4)
- [ ] Did NOT read step5_analysis_summary.json yet
- [ ] Root cause category matches evidence
- [ ] Exactly one evidence item has `is_root_cause: true`
- [ ] All evidence has traceability (source_file, json_path, exact_value/quote)
- [ ] All evidence has confidence level (high/medium/low)
- [ ] Difficulty score calculated with justification
- [ ] Alternative diagnoses have plausibility levels
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
