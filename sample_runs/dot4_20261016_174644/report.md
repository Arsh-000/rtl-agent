# rtl-agent report: dot4

Status: **finished**

> Offline test run: the scripted provider replayed hand-written responses (not an LLM).

## The design

dot4: dot product of two vectors of four signed 8-bit numbers.

y = a0*b0 + a1*b1 + a2*b2 + a3*b3

Inputs are registered, the four multiplies and three adds happen in ONE
clock cycle, and the result is registered. That long combinational path
(register -> 4 multipliers -> adder chain -> register) is the critical
path the agent has to deal with.

| port | direction | width | role |
|---|---|---|---|
| clk | input | 1 | clock |
| rst_n | input | 1 | reset (active-low, async) |
| in_valid | input | 1 | data / control input |
| a | input | 32 | data / control input |
| b | input | 32 | data / control input |
| y | output | 18 | output |
| out_valid | output | 1 | output |

- top module: dot4
- clock period: 1.2 ns (833 MHz)
- flip-flop bits: 84
- memory bits: 0
- files: dot4.v (52 lines)
- starting point: the user's RTL

## Summary

- Starting point (round 0): setup slack -0.368 ns, hold slack 0.019 ns, area 2686.3 um2, power 1.1299 mW, 84 flip-flop bits.
- Best version: round 2 - setup slack 0.060 ns (+0.428 ns), area 2859.0 um2 (+6.4%), power 1.3233 mW (+17.1%), extra latency 1 cycle(s).
- 2 proposal(s): 1 kept, 0 correct but not better, 1 failed checks, 0 duplicate(s).
- Round 2 (improve) kept: pipeline: register the four signed products, then add them as a balanced tree (+1 cycle latency) - slack +0.428 ns, area +6.4%, power +17.1%.
- Targets met: timing.
- Stopped because: success: all targets met (best = round 2)

## Rounds

| # | phase | change | setup WS | area | power | latency | verdict |
|---|---|---|---|---|---|---|---|
| 0 | baseline | original design | -0.368 | 2686.3 | 1.1299 | 0 | baseline: starting point |
| 1 | improve | use unsigned 8x8 multipliers to shorten the critical path | n/a | n/a | n/a |  | invalid: behaviour differs from the original |
| 2 | improve | pipeline: register the four signed products, then add them as a balanced tree (+1 cycle latency) | 0.060 | 2859.0 | 1.3233 | 1 | kept: better timing than round 0 |
