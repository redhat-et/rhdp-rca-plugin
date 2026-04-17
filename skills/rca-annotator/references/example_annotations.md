# Example Annotations

This file contains example annotations of RCA outputs to help you understand what "good" looks like.

## Example 1: Configuration Error (Correct Diagnosis)

**Scenario**: Missing Kubernetes credentials in test environment

```json
{
  "job_id": "JOB-10042",
  "annotated_at": "2026-01-15T19:15:00Z",

  "category_correct": true,
  "category_comment": "Confirmed - this is a configuration issue, not infrastructure or credential problem.",

  "root_cause": {
    "category": "configuration",
    "summary": "Missing Kubernetes cluster credentials due to intentionally empty configuration. The 'acme-empty-config' test catalog has no configuration files defining kubeconfig/credentials required by the bookbag destroy workflow.",
    "confidence": "high"
  },

  "summary_accurate": true,
  "summary_comment": "Clear and specific - explains both what failed and why.",

  "evidence": [
    {
      "source": "step1",
      "source_file": ".analysis/JOB-10042/step1_job_context.json",
      "json_path": "failed_tasks[0].duration",
      "exact_value": 923.0,
      "message": "Task 'List project namespaces' failed with MODULE FAILURE after 917 seconds (15+ minutes)",
      "confidence": "high",
      "is_root_cause": false
    },
    {
      "source": "step4",
      "source_file": ".analysis/JOB-10042/step4_github_fetch_history.json",
      "json_path": "github_fetches[2].status",
      "exact_value": 404,
      "github_path": "acme-org/catalog-configs:tests/acme-empty-config/prod.yaml",
      "message": "All configuration files missing (404): tests/acme-empty-config/prod.yaml, tests/acme-empty-config/common.yaml, tests/account.yaml, common.yaml",
      "confidence": "high",
      "is_root_cause": true
    },
    {
      "source": "step3",
      "source_file": ".analysis/JOB-10042/step3_correlation.json",
      "json_path": "correlation.method",
      "exact_value": "no_correlation_possible",
      "message": "No Splunk correlation possible - namespace field is empty. This is a test environment without actual Kubernetes infrastructure.",
      "confidence": "medium",
      "is_root_cause": false
    }
  ],

  "evidence_feedback": "Complete - all key pieces are captured.",

  "difficulty": "medium",
  "difficulty_score": 5,
  "difficulty_justification": "Requires cross-source correlation (+3): AAP job shows generic MODULE FAILURE, GitHub shows missing configs, correlation notes confirm test environment. Requires understanding variable precedence (+1). Total: 4, rounded to medium.",
  "difficulty_appropriate": true,
  "difficulty_comment": "",

  "recommendations": [
    {
      "priority": "medium",
      "action": "Add conditional logic to bookbag destroy workflow",
      "file": "ansible/roles/bookbag/tasks/remove_workload.yaml"
    }
  ],

  "contributing_factors": [
    "Test catalog intentionally has no configuration",
    "Bookbag destroy workflow doesn't skip gracefully when kubeconfig undefined"
  ],

  "alternative_diagnoses": [
    {
      "category": "credential",
      "summary": "Kubernetes credentials expired or revoked",
      "why_wrong": "No credentials exist at all (404 on config files), not an expiration issue. The catalog name 'acme-empty-config' indicates this is intentional.",
      "plausibility": "low",
      "supporting_evidence": ["k8s_info task failed", "kubeconfig undefined"],
      "contradicting_evidence": ["404 on all config files", "test environment", "catalog named 'empty-config'"]
    }
  ]
}
```

**What makes this annotation good:**
- Category confirmed with clear reasoning
- Exactly one evidence item marked as root cause (the 404 configs)
- Evidence has full traceability (source_file, json_path, exact_value)
- Difficulty score matches rubric and is justified
- Alternative diagnosis explains why it's wrong, not just what it is
- Recommendations are actionable

---

## Example 2: Infrastructure Issue (Partially Correct)

**Scenario**: Network timeout during cloud API call, but agent misidentified as application bug

```json
{
  "job_id": "JOB-20187",
  "annotated_at": "2026-01-16T10:30:00Z",

  "category_correct": false,
  "category_comment": "Agent said 'application_bug' but this is actually 'network' - the timeout was due to intermittent connectivity to the cloud provider API, not a code defect.",

  "root_cause": {
    "category": "network",
    "summary": "Intermittent network connectivity to cloud EC2 API caused timeout during instance termination. Retry succeeded 2 minutes later.",
    "confidence": "medium"
  },

  "summary_accurate": false,
  "summary_comment": "Agent's summary focused on the code path but missed the network issue. Corrected to reflect transient cloud API connectivity problem.",

  "evidence": [
    {
      "source": "step3",
      "source_file": ".analysis/JOB-20187/step3_correlation.json",
      "json_path": "splunk_errors[0].message",
      "exact_quote": "botocore.exceptions.ReadTimeoutError: Read timeout on endpoint URL",
      "message": "Cloud SDK timeout after 60 seconds waiting for EC2 DescribeInstances response",
      "confidence": "high",
      "is_root_cause": true
    },
    {
      "source": "step1",
      "source_file": ".analysis/JOB-20187/step1_job_context.json",
      "json_path": "failed_tasks[0].task_action",
      "exact_value": "amazon.aws.ec2_instance_info",
      "message": "Task failed: 'ec2_instance_info' module",
      "confidence": "high",
      "is_root_cause": false
    },
    {
      "source": "step3",
      "source_file": ".analysis/JOB-20187/step3_correlation.json",
      "json_path": "timeline[12].aap_event",
      "exact_value": "retry successful",
      "message": "Automatic retry 120 seconds later succeeded without code changes",
      "confidence": "high",
      "is_root_cause": false
    }
  ],

  "evidence_feedback": "Agent missed the retry success evidence from step3. Added it manually from timeline.",

  "difficulty": "hard",
  "difficulty_score": 8,
  "difficulty_justification": "Requires cross-source correlation (+3): AAP shows task failure, Splunk shows timeout, timeline shows retry. Error message is generic timeout (+2). Multiple plausible alternatives exist (+1): could be cloud outage, code bug, or network issue. Timing dependencies critical (+1): retry pattern reveals transient nature. Requires cloud API knowledge (+1). Total: 8 = hard.",
  "difficulty_appropriate": true,
  "difficulty_comment": "",

  "recommendations": [
    {
      "priority": "high",
      "action": "Increase cloud SDK timeout from 60s to 180s for EC2 operations",
      "file": "ansible/roles/infra-ec2/defaults/main.yml"
    },
    {
      "priority": "medium",
      "action": "Add exponential backoff retry logic to ec2_instance_info tasks",
      "file": "ansible/roles/infra-ec2/tasks/cleanup.yml"
    }
  ],

  "contributing_factors": [
    "Cloud provider us-east-1 region experiencing elevated API latency during incident window",
    "No retry logic configured for ec2_instance_info module"
  ],

  "alternative_diagnoses": [
    {
      "category": "cloud_api",
      "summary": "EC2 API outage or rate limiting",
      "why_wrong": "Retry succeeded 2 minutes later without changing request rate, and the provider status page showed no outages - just elevated latency. Rate limiting would return 429, not timeout.",
      "plausibility": "medium",
      "supporting_evidence": ["timeout talking to cloud API", "regional API issue"],
      "contradicting_evidence": ["no provider outage", "retry succeeded", "timeout not 429"]
    },
    {
      "category": "application_bug",
      "summary": "Bug in ec2_instance_info module causing hang",
      "why_wrong": "This was the agent's diagnosis, but retry succeeded without code changes. A code bug would fail consistently.",
      "plausibility": "low",
      "supporting_evidence": ["timeout in ec2_instance_info"],
      "contradicting_evidence": ["retry succeeded", "no code changes", "timeout from SDK level"]
    }
  ]
}
```

**What makes this annotation good:**
- Clearly documents where agent was wrong and why
- Evidence includes the "smoking gun" (retry succeeded) that agent missed
- Difficulty justification walks through rubric step-by-step
- Alternative diagnoses include the agent's incorrect diagnosis with explanation
- Contributing factors cite external verification (provider status page)

---

## Example 3: Credential Issue (Correct but Low Confidence)

**Scenario**: ServiceAccount token expired, but evidence is indirect

```json
{
  "job_id": "JOB-30055",
  "annotated_at": "2026-01-17T14:00:00Z",

  "category_correct": true,
  "category_comment": "Correct category, but confidence should be medium not high - the evidence is circumstantial.",

  "root_cause": {
    "category": "credential",
    "summary": "Kubernetes ServiceAccount token expired, causing 401 Unauthorized errors during pod deployment verification.",
    "confidence": "medium"
  },

  "summary_accurate": true,
  "summary_comment": "Accurate but I'd add that the token was auto-rotated after 90 days per cluster policy.",

  "evidence": [
    {
      "source": "step3",
      "source_file": ".analysis/JOB-30055/step3_correlation.json",
      "json_path": "splunk_errors[0].message",
      "exact_quote": "Unauthorized: User \\\"system:serviceaccount:demo-ns:deployer\\\" cannot get resource \\\"pods\\\" in API group \\\"\\\" in namespace \\\"demo-ns\\\"",
      "message": "401 Unauthorized from Kubernetes API for ServiceAccount 'deployer'",
      "confidence": "high",
      "is_root_cause": true
    },
    {
      "source": "step3",
      "source_file": ".analysis/JOB-30055/step3_correlation.json",
      "json_path": "splunk_errors[2].metadata.timestamp",
      "exact_value": "2026-01-17T13:45:00Z",
      "message": "Error timestamp is exactly 90 days after job last succeeded (2025-10-19)",
      "confidence": "medium",
      "is_root_cause": false
    },
    {
      "source": "step1",
      "source_file": ".analysis/JOB-30055/step1_job_context.json",
      "json_path": "job_metadata.last_success",
      "exact_value": "2025-10-19T13:45:00Z",
      "message": "Job ran successfully 90 days ago, then started failing today",
      "confidence": "medium",
      "is_root_cause": false
    }
  ],

  "evidence_feedback": "Good correlation of timing. Would be better if we had direct evidence of token expiration from K8s API audit logs, but Splunk doesn't have that level of detail.",

  "difficulty": "easy",
  "difficulty_score": 2,
  "difficulty_justification": "Error message is clear and specific: 401 Unauthorized with exact ServiceAccount name (+0). Timing correlation is helpful but not required (+1). Requires basic K8s knowledge (+1). Total: 2 = easy.",
  "difficulty_appropriate": true,
  "difficulty_comment": "",

  "recommendations": [
    {
      "priority": "high",
      "action": "Refresh ServiceAccount token or create new ServiceAccount with updated token",
      "file": "N/A - manual K8s operation"
    },
    {
      "priority": "medium",
      "action": "Add token expiration monitoring to prevent future incidents",
      "file": "ansible/roles/k8s-deploy/tasks/preflight.yml"
    }
  ],

  "contributing_factors": [
    "Cluster policy auto-rotates ServiceAccount tokens after 90 days",
    "No monitoring for token expiration"
  ],

  "alternative_diagnoses": [
    {
      "category": "configuration",
      "summary": "RBAC permissions removed or changed for deployer ServiceAccount",
      "why_wrong": "Timing of failure (exactly 90 days after last success) strongly suggests token expiration, not RBAC change. RBAC changes would show in K8s audit logs which we'd see in Splunk.",
      "plausibility": "medium",
      "supporting_evidence": ["permission denied error"],
      "contradicting_evidence": ["90-day timing pattern", "no RBAC changes in audit logs"]
    }
  ]
}
```

**What makes this annotation good:**
- Corrects agent's confidence level with reasoning
- Evidence includes temporal correlation (90-day pattern)
- Difficulty is appropriately rated as "easy" despite multiple evidence sources
- Acknowledges evidence limitations ("would be better if...")
- Recommendations include both immediate fix and prevention

---

## Common Patterns

### Evidence Traceability
Always include:
- `source_file`: exact path to the file
- `json_path`: location within the file
- `exact_value` OR `exact_quote`: literal content (use `exact_quote` for strings/code, `exact_value` for numbers/booleans)
- `is_root_cause: true` on exactly ONE evidence item

### Difficulty Scoring
Use the rubric and show your work:
- Cross-source correlation: +3
- Code understanding: +2
- Generic/misleading error: +2
- Variable precedence: +1
- Domain knowledge: +1
- Multiple alternatives: +1
- Timing dependencies: +1

### Alternative Diagnoses
Focus on **plausible** alternatives that were ruled out, not random guesses:
- High plausibility: shares many characteristics, hard to distinguish
- Medium plausibility: some supporting evidence but clear contradictions
- Low plausibility: superficial similarity only

### When Agent is Wrong
Document clearly:
- What the agent said
- What is actually correct
- Why the agent was wrong (which evidence was missed or misinterpreted)
