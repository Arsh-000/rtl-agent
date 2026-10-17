# Round 2 (improve)

**Status:** kept - better area than round 1

**Idea:** merge the equivalent states S_1011 and S_1: 4 states in 2 flip-flops

**LLM diagnosis / plan:**

One-hot encoding helped a little. A bigger saving comes from the state graph itself: state S_1011 goes to S_1 on a 1 and to S_10 on a 0 - exactly like S_1. Because 'found' is computed from the next state, S_1011 never needs to be remembered separately: it can be merged into S_1. That leaves 4 states, which fit in just 2 flip-flops with binary encoding (instead of 5 one-hot flip-flops), and 'found' becomes "in S_101 and the new bit is 1".

| metric | LLM expected | measured | claim correct? |
|---|---|---|---|
| setup_slack | same | improve | NO |
| area | improve | improve | yes |
| power | improve | improve | yes |
