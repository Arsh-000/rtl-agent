# Round 1 (explore)

**Status:** rejected - worse timing than round 0

**Idea:** Gray-only storage: 8 flip-flops, convert to binary and back each cycle

**LLM diagnosis / plan:**

Architecture candidate: store only the Gray value. Each cycle, convert Gray back to binary (a prefix XOR), add or subtract one, and convert to Gray again. This halves the number of flip-flops (8 instead of 16) at the cost of a longer logic path.

| metric | LLM expected | measured | claim correct? |
|---|---|---|---|
| setup_slack | worsen | worsen | yes |
| area | improve | improve | yes |
| power | improve | improve | yes |
