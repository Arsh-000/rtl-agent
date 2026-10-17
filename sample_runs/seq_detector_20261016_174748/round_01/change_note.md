# Round 1 (improve)

**Status:** kept - better area than round 0

**Idea:** one-hot state encoding (5 flip-flops)

**LLM diagnosis / plan:**

The next-state logic decodes a 3-bit binary state, which needs comparators on every transition. One-hot encoding (one flip-flop per state) makes each next-state bit a small OR of AND terms, which is usually faster and can be simpler.

| metric | LLM expected | measured | claim correct? |
|---|---|---|---|
| setup_slack | improve | worsen | NO |
| area | improve | improve | yes |
| power | same | improve | NO |
