# Round 1 (improve)

**Status:** invalid - behaviour differs from the original

**Idea:** use unsigned 8x8 multipliers to shorten the critical path

**LLM diagnosis / plan:**

The critical path runs from the input registers through four 8x8 multipliers and a chain of three adders. Signed multipliers need extra sign-handling logic, so switching to unsigned multiplication should give a shorter, smaller multiplier.

