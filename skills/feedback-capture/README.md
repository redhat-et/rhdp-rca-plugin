# Feedback Capture Skill

This skill allows the agent to solicit, capture, categorize, and persistently store user feedback during an interaction.

## Overview

The Feedback Capture skill is designed to:
1.  **Ask** the user if they want to provide feedback.
2.  **Categorize** the received feedback into predefined categories (e.g., Complexity, Clarity, Accuracy).
3.  **Summarize** the context of the interaction.
4.  **Record** the structured feedback to a local file using a Python script.

## Components

- **`SKILL.md`**: Defines the agent's behavior, including the steps to ask for feedback, determining the category, and the command to run the formatting script.
- **`scripts/mlflow_feedback.py`**: A Python script that handles the actual writing of feedback to disk.
    - **Input**: Category, Feedback text, Context summary, Skill name.
    - **Output**: Trace analysis on ML Flow (default).

## Usage

This skill is typically triggered when an interaction completes or when the user indicates a desire to give feedback. The agent will automatically handle the classification and execution of the recording script without burdening the user with formatting details.

### Script Arguments

The `mlflow_feedback.py` script accepts the following arguments:
- `--category`: The category of the feedback (e.g., "Positive", "Bug").
- `--skill`: The name of the skill being evaluated.
- `--feedback`: The actual text of the user's feedback.
- `--context`: A summary of the interaction context.
- `--file`: (Optional) Path to the output file (default: `~/feedback.txt`).
