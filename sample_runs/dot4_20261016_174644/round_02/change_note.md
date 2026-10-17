# Round 2 (improve)

**Status:** kept - better timing than round 0

**Idea:** pipeline: register the four signed products, then add them as a balanced tree (+1 cycle latency)

**LLM diagnosis / plan:**

The previous attempt was rejected because unsigned products change the result for negative numbers, so the signed arithmetic must stay. The real problem is that four multipliers and three adders sit between two registers. Latency may grow by up to 2 cycles, so register the four products (one extra pipeline stage) and add them as a balanced tree in the next cycle. All outputs, including out_valid, are delayed by exactly one cycle.

| metric | LLM expected | measured | claim correct? |
|---|---|---|---|
| setup_slack | improve | improve | yes |
| area | worsen | worsen | yes |
| power | worsen | worsen | yes |
