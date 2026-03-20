---
name: feedback-capture
description: Ask user if they would like to provide feedback. Call scripts/formatting.py. Don't ask any follow up questions.
allowed-tools:
  - write
  - read_file
---

# Feedback Capture
Step 1 [Claude] Ask user if they would like to provide feedback.
Step 2 [Claude] IF the user provides feedback. Select a feedback category that best suits feedback
Step 3 [Claude] Summarize user feedback as users-feedback, and summarize what happened as context.
Step 4 [Claude] You MUST run scripts/formatting.py \
  --category {Category} \
  --skill {Skills} \
  --feedback {users-feedback} \
  --context {Summary of what happened, include what you did, explain your output quickly}

## Usage
1. Never provide the user with options
2. What ever comment they provide assume thats the feedback
3. You MUST use scripts/formatting.py

## Feedback format
Take the comment the user gave as feedback and create the following inputs for scripts/formatting.py and you must call scripts/formatting.py
Select one of the following as the category: [ Complexity, Clarity, Accuracy, Performance, Search Quality, Interpretation, Positive]
Discern what type of feedback this issue is, given a short 1 to 2 word label and insert this.
Example: user feedback: "It keeps repeating the same solution to the code", Category: "Repetition"

Run: python scripts/formatting.py \
  --category {Category} \
  --skill {Skills} \
  --feedback {users-feedback} \
  --context {Summary of what happened, include what you did, explain your output quickly}
