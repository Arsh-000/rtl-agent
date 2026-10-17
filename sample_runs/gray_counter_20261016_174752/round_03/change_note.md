# Round 3 (improve)

**Status:** kept - better timing than round 2

**Idea:** look-ahead registers for count+1 and count-1 so the Gray output needs only XOR + select

**LLM diagnosis / plan:**

The best version (round 2) still misses timing by a small margin. Its critical path is count register -> 8-bit increment/decrement carry chain -> Gray XOR -> select on 'up' -> gray register. Keep count+1 and count-1 ready in look-ahead registers (inc_r, dec_r). Then the gray register only needs one XOR and a 2:1 select, and the carry chains move to the inc_r/dec_r registers, which have a whole cycle with no XOR after them. Outputs are unchanged cycle for cycle; it costs 16 extra flip-flops.

| metric | LLM expected | measured | claim correct? |
|---|---|---|---|
| setup_slack | improve | improve | yes |
| area | worsen | worsen | yes |
| power | worsen | worsen | yes |
