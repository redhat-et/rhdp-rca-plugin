# Example Annotations

This file contains example annotations of RCA outputs to help you understand what "good" looks like.

## Example 1: Configuration Error (Correct Diagnosis)

**Scenario**: Missing Kubernetes credentials in test environment

```json
{
  "job_id": "1234567891",
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
      "source_file": ".analysis/1234567891/step1_job_context.json",
      "json_path": "failed_tasks[0].duration",
      "exact_value": 923.0,
      "message": "Task 'List project namespaces' failed with MODULE FAILURE after 923 seconds (15+ minutes)",
      "confidence": "high",
      "is_root_cause": false
    },
    {
      "source": "step4",
      "source_file": ".analysis/1234567891/step4_github_fetch_history.json",
      "json_path": "github_fetches[2].status",
      "exact_value": 404,
      "github_path": "acme-org/catalog-configs:tests/acme-empty-config/prod.yaml",
      "message": "All configuration files missing (404): tests/acme-empty-config/prod.yaml, tests/acme-empty-config/common.yaml, tests/account.yaml, common.yaml",
      "confidence": "high",
      "is_root_cause": true
    },
    {
      "source": "step3",
      "source_file": ".analysis/1234567891/step3_correlation.json",
      "json_path": "correlation.method",
      "exact_value": "no_correlation_possible",
      "message": "No Splunk correlation possible - namespace field is empty. This is a test environment without actual Kubernetes infrastructure.",
      "confidence": "medium",
      "is_root_cause": false
    }
  ],

  "evidence_feedback": "Complete - all key pieces are captured.",

  "difficulty": "medium",
  "difficulty_score": 4,
  "difficulty_justification": "Evidence Availability (+2): Requires AAP + GitHub correlation to identify missing kubeconfig file. Confusion Factors (+1): Generic MODULE FAILURE error message. Domain Expertise (+1): Needs understanding of Ansible variable precedence and test environment configs. Total: 4 = medium.",
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
