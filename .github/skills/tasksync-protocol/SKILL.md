# TaskSync Protocol — Integration Skill

## Purpose

This skill defines the mandatory **TaskSync checkpointing rules** that every orchestrated agent must follow. Every action — no matter how small — must be bookended by a checkpoint so the human stays in the loop.

---

## Core Rules

1. **Every step MUST end with `vscode/askQuestions`.**  
   No step is complete until the user has seen the result and responded.

2. **Session never terminates independently.**  
   The only valid termination is when the user responds with "Selesai".

3. **Token management.**  
   If the session exceeds 10 turns, write `.tasksync_status.md` at the workspace root and instruct the user to restart.

4. **Concise confirmations.**  
   After completing a step, the confirmation message must follow the format:  
   `Done. [result summary in ≤ 3 sentences]`

5. **Subagent aggregation.**  
   All subagent results must be aggregated into the main session before presenting to the user. Never expose raw subagent dumps.

6. **Parallel work acknowledgement.**  
   When parallel subagents are launched, inform the user:  
   `Launching [N] parallel sub-agents: [list of agents and their tasks]`

7. **Emergency override.**  
   If the user types "STOP" or "EXIT", immediately save state to `.tasksync_status.md` and halt.

---

## Checkpoint Template

After every step, present a checkpoint using this pattern:

```json
ask_questions({
  "questions": [
    {
      "header": "[Step Name] — TaskSync Checkpoint",
      "question": "[Brief summary of what was done]. Ready for next instruction?",
      "multiSelect": false,
      "allowFreeformInput": true,
      "options": [
        { "label": "Continue — proceed to next step", "recommended": true },
        { "label": "Pause — I want to review first" },
        { "label": "Change direction — describe below" }
      ]
    }
  ]
})
```

---

## .tasksync_status.md Format

```markdown
# TaskSync Status

**Date:** [ISO date]
**Session Turn:** [N]
**Active Mode:** [Single-Plan | Multi-Ticket | Brainstorm | Feature-Dev]
**Current Step:** [Step name]
**PLAN file:** [path or N/A]

## Completed Steps

- [x] Step 1 — Repository Intake
- [x] Step 2 — Planning

## Pending Steps

- [ ] Step 3 — Implementation
- [ ] Step 4 — Review

## Active Subagents

- [none | list of running agents and their tasks]

## Notes

[Any blockers, deviations, or context needed to resume]
```

---

## Integration with Todo Tool

Every TaskSync checkpoint corresponds to a todo state transition:

| Event            | Todo action                        |
| ---------------- | ---------------------------------- |
| Step starts      | Mark as `in-progress`              |
| Checkpoint shown | Wait for user approval             |
| User approves    | Mark as `completed`, start next    |
| User pauses      | Keep as `in-progress`, note reason |
| Deviation found  | Mark as `blocked`                  |
