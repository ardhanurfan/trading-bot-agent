# Brainstorm Protocol — Skill

## Purpose

This skill governs how **multi-agent brainstorm sessions** are conducted when the user provides a vague, open-ended, or strategic prompt that benefits from diverse perspectives before committing to implementation.

---

## When to Use Brainstorm Mode

Trigger brainstorm mode when the user's input contains any of:

- "Bagaimana menurut kalian…" / "What do you think about…"
- "Ide untuk…" / "Ideas for…"
- "Brainstorm…"
- "Diskusi tentang…" / "Let's discuss…"
- "Pros and cons of…"
- "Should we…"
- Any open design question without a clear implementation path

---

## Brainstorm Session Structure

### Phase 0 — Topic Framing

The moderator (running-prompt) extracts:

1. **Core question** — what decision needs to be made
2. **Context** — relevant codebase components
3. **Constraints** — time, complexity, existing architecture
4. **Desired output** — recommendation, ADR, feature spec, or free exploration

### Phase 1 — Perspective Assignment

Assign 3–5 perspectives depending on topic:

| Perspective        | Focus                                      | Agent            |
| ------------------ | ------------------------------------------ | ---------------- |
| Tech Architect     | System design, patterns, maintainability   | `architecture`   |
| Trading Strategist | Market logic, signal quality, P&L impact   | `data-analyst`   |
| Risk Manager       | Risk exposure, fail-safes, compliance      | `security-audit` |
| Feature Developer  | Implementation complexity, effort estimate | `feature-dev`    |
| Test Engineer      | Testability, edge cases, observability     | `test-engineer`  |
| DevOps Engineer    | Deployment, scaling, operational concerns  | `devops`         |

Select only the 2–4 most relevant perspectives for the topic. Do not spawn all 6 unless warranted.

### Phase 2 — Parallel Perspective Generation

Spawn all selected perspective sub-agents **in parallel**:

```jsonc
// Each agent gets this prompt structure
{
  "agent": "[perspective-agent-name]",
  "prompt": "You are the [Perspective Name] in a brainstorm session. Topic: [topic]. Context: [codebase context]. Your task: provide a concise perspective (200–400 words) covering: (1) your key observations, (2) your top 2–3 recommendations, (3) risks or concerns from your viewpoint, (4) one open question for the other perspectives. Return ONLY your perspective — do not summarize others. Do not call askQuestions.",
}
```

### Phase 3 — Cross-Pollination Round

After all perspectives return, the moderator:

1. Identifies **points of agreement** across perspectives
2. Identifies **points of conflict** or tension
3. Generates 2–3 **synthesis questions** — questions that a single perspective could not answer alone

Optional: run a second round where each agent responds to the synthesis questions. Keep this optional to save tokens.

### Phase 4 — Synthesis & Recommendation

The moderator synthesizes all perspectives into:

```markdown
## Brainstorm Summary — [Topic]

### Consensus Points

- [point 1]
- [point 2]

### Tension Areas

- [area 1]: [Tech Architect says X, Risk Manager says Y]

### Recommended Direction

[1–2 paragraphs synthesizing the strongest recommendation]

### Open Questions (for user to decide)

1. [question requiring human judgment]
2. [question requiring human judgment]

### Next Steps

- [ ] [action 1]
- [ ] [action 2]
```

### Phase 5 — Checkpoint

Present the summary to the user via `askQuestions` with options:

- Proceed to PLAN.md based on recommendation
- Request deeper dive on one perspective
- Start over with different framing
- Convert to Feature-Dev mode

---

## Token Budget for Brainstorm

- Each perspective: 400 words max
- Synthesis summary: 600 words max
- Total session overhead: < 3000 tokens

If the topic requires deeper analysis, convert to a single full planning session instead.

---

## Output Artifact

Every brainstorm produces a `docs/brainstorm-[slug].md` file:

```markdown
---
date: [ISO date]
topic: [topic]
perspectives: [list of agents used]
status: Completed | Converted-to-Plan
---

# Brainstorm: [Topic]

## Individual Perspectives

### Tech Architect

[content]

### Trading Strategist

[content]
...

## Synthesis

[content]

## Decision

[user's choice from checkpoint]
```
