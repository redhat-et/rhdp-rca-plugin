---
name: rca-knowledge-base
description: Query the historical RCA knowledge base to answer questions about past root cause analyses. Use when users ask about patterns in past failures, common root causes for specific services or platforms, whether similar issues have been seen before, or want to search historical RCA data.
allowed-tools:
  - Bash
---

# RCA Knowledge Base

Search and synthesize answers from historical root cause analyses stored in a pgvector database. This skill turns raw vector similarity search results into clear, evidence-backed answers.

## When to Use

- User asks about patterns in past failures ("What usually causes sandbox failures on AWS?")
- User wants to know if a similar issue has been seen before ("Have we seen DNS resolution issues?")
- User asks about common root causes for a service, platform, or catalog item
- User wants historical context before investigating a new failure

## Prerequisites

This skill requires pgvector to be configured. The following environment variables must be set in `.claude/settings.json` under `env`:

- `PGVECTOR_HOST`
- `PGVECTOR_DB_NAME`
- `PGVECTOR_DB_USER`
- `PGVECTOR_DB_PASSWORD`

RCA analyses must have been previously embedded using the root-cause-analysis skill's embed command.

## Instructions

### Step 1: Prepare the environment [Bash]

Ensure the root-cause-analysis virtual environment exists:

```bash
cd ../root-cause-analysis && (test -d .venv || (python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt))
```

If the venv creation or dependency install fails, stop and report the error to the user.

### Step 2: Formulate the search query [Claude]

Rephrase the user's natural language question into failure-domain terms that align with how RCA embeddings are structured. The embeddings contain:

- Root cause category and summary
- Platform, catalog item, and environment
- Cloud provider, environment type, action
- Contributing factors
- Failed task names, actions, and error messages

**Query formulation guidelines:**

- Extract key technical terms: service names, error messages, infrastructure components, platform names
- Use failure-domain language rather than conversational phrasing
- Example: "Why do sandbox deploys keep failing?" → "sandbox deployment failure"
- Example: "Have we seen issues with AWS credentials?" → "AWS credential authentication failure"

**Optional filters** — if the user's question implies a specific scope, use these CLI flags:

- `--category <cat>` — one of: `configuration`, `infrastructure`, `workload_bug`, `credential`, `resource`, `dependency`
- `--catalog-item <item>` — a specific workload or catalog item name

### Step 3: Run the similarity search [Bash]

```bash
cd ../root-cause-analysis && .venv/bin/python scripts/cli.py similar --text "<formulated_query>" --limit 5
```

Adjust `--limit` up to 10 if the user asks for more results. Add `--category` or `--catalog-item` flags if determined in Step 2.

If the command fails with a pgvector configuration error, tell the user to set the required environment variables listed in Prerequisites and stop.

If the command returns an empty JSON array `[]`, tell the user no similar past analyses were found. Suggest they:
- Broaden the search terms
- Verify that RCA analyses have been embedded (via the root-cause-analysis skill's embed command)

### Step 4: Synthesize the results [Claude]

Transform the JSON results into a clear answer. The JSON array contains objects with these fields:

| Field | Description |
|-------|-------------|
| `job_id` | The analyzed job identifier |
| `root_cause_category` | Category of the root cause |
| `root_cause_summary` | Human-readable summary of the root cause |
| `catalog_item` | The workload or catalog item involved |
| `confidence` | Confidence level of the analysis (high/medium/low) |
| `analyzed_at` | When the analysis was performed |
| `github_paths` | Relevant configuration file paths |
| `distance` | Cosine distance — lower means more similar |

**Synthesis guidelines:**

1. **Answer the question directly** — lead with a one or two sentence answer
2. **Group by pattern** — if multiple results share the same root cause category or similar summaries, group them to show frequency
3. **Cite evidence** — reference job IDs, confidence levels, and dates
4. **Indicate match quality** — distance < 0.5 is a strong match, 0.5–0.8 is moderate, > 0.8 is weak. Flag weak matches explicitly
5. **Mention relevant files** — when `github_paths` is non-empty, include them so the user can investigate
6. **Note limitations** — if all matches are weak or few results returned, say so

**Output format:**

```
## Answer

<Direct answer to the user's question in 1-2 sentences>

## Evidence from Past Analyses

### Pattern: <root_cause_category> (<count> occurrences)

- **Job <job_id>** (<analyzed_at>, confidence: <confidence>, similarity: <strong/moderate/weak>)
  <root_cause_summary>
  Files: <github_paths if present>

<Repeat for each pattern group>

## Recommendations

<Actionable suggestions based on the patterns found>
```
