# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

RHDP RCA Plugin - a Claude Code marketplace containing 5 skills for Red Hat Demo Platform root cause analysis.

## Repository Structure

```
rhdp-rca-plugin/
├── skills/           # 5 skills: template-skill, logs-fetcher, root-cause-analysis, context-fetcher, feedback-capture
├── docs/             # Agent skills spec, contributing guidelines
└── experiments/      # Experimental prototypes
```

## Success Criteria

Skills should help RHDP users:
- **Quickly identify root causes** of infrastructure failures
- **Save time** by automating log correlation and analysis
- **Understand issues** through clear, actionable insights
- **Trust results** with evidence-based recommendations

Measure success through user feedback (use feedback-capture skill) and reduced incident resolution time.
