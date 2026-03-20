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
- **`scripts/formatting.py`**: Default file-based feedback capture. Appends feedback to `feedback.txt` with session context.
    - **Input**: Category, Feedback text, Context summary, Skill name.
    - **Output**: Appends a formatted entry (with a timestamp and incrementing ID) to `feedback.txt`.
    - Also logs to MLflow if available (optional enhancement).
- **`scripts/mlflow_feedback.py`**: Optional MLflow-only feedback logging. Requires MLflow to be installed and configured.

## Usage

This skill is typically triggered when an interaction completes or when the user indicates a desire to give feedback. The agent will automatically handle the classification and execution of the recording script without burdening the user with formatting details.

### Script Arguments

The `formatting.py` script accepts the following arguments:
- `--category`: The category of the feedback (e.g., "Positive", "Bug").
- `--skill`: The name of the skill being evaluated.
- `--feedback`: The actual text of the user's feedback.
- `--context`: A summary of the interaction context.
