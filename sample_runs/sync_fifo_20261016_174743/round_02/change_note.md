# Round 2 (improve)

**Status:** kept - better timing than round 0

**Idea:** look-ahead head register: precompute next cycle's dout so the read multiplexer leaves the output path

**LLM diagnosis / plan:**

The worst path runs from the read pointer through the 16-to-1 read multiplexer to the dout port, which must also leave room for the output delay. I will compute the NEXT head-of-queue value one cycle early (from the next read pointer, or din when that slot is being written) and keep it in a register, so dout comes from a register instead of through the multiplexer. Because the original reset is asynchronous and shows mem[0] at once, dout shows mem[0] while reset is active.

| metric | LLM expected | measured | claim correct? |
|---|---|---|---|
| setup_slack | improve | improve | yes |
| area | worsen | worsen | yes |
| power | same | worsen | NO |
