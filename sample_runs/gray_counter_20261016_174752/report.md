# rtl-agent report: gray_counter

Status: **finished**

> Offline test run: the scripted provider replayed hand-written responses (not an LLM).

## The design

An 8-bit up/down counter whose value is output in Gray code.
- On every rising clock edge where 'en' is 1, the internal binary count
  goes up by 1 if 'up' is 1, or down by 1 if 'up' is 0. It wraps around
  (255 + 1 = 0, 0 - 1 = 255).
- 'rst' is an active-high synchronous reset that sets the count to 0.
- 'gray' is the current count converted to Gray code: gray = count ^ (count >> 1).
  It must be a registered output (it changes only on clock edges), and it changes on the
  SAME clock edge as the count: after every edge, gray already shows the Gray code of the new
  count (no extra cycle of delay). After reset, gray is 0.

| port | direction | width | role |
|---|---|---|---|
| clk | input | 1 | clock |
| rst | input | 1 | reset (active-high, sync) |
| en | input | 1 | data / control input |
| up | input | 1 | data / control input |
| gray | output | 8 | output |

- top module: gray_counter
- clock period: 0.45 ns (2222 MHz)
- flip-flop bits: 16
- memory bits: 0
- files: gray_counter.v (20 lines)
- starting point: written by the LLM from the specification

## Summary

- Starting point (round 0): setup slack -0.106 ns, hold slack 0.048 ns, area 150.6 um2, power 0.2292 mW, 16 flip-flop bits.
- Best version: round 3 - setup slack 0.035 ns (+0.141 ns), area 306.7 um2 (+103.7%), power 0.4731 mW (+106.4%), extra latency 0 cycle(s).
- 3 proposal(s): 2 kept, 1 correct but not better, 0 failed checks, 0 duplicate(s).
- Round 2 (explore) kept: parallel increment and decrement with late select on 'up' - slack +0.078 ns, area +5.3%, power +1.8%.
- Round 3 (improve) kept: look-ahead registers for count+1 and count-1 so the Gray output needs only XOR + select - slack +0.064 ns, area +93.5%, power +102.8%.
- Targets met: timing.
- Stopped because: success: all targets met (best = round 3)

## Rounds

| # | phase | change | setup WS | area | power | latency | verdict |
|---|---|---|---|---|---|---|---|
| 0 | spec | first RTL generated from the spec | -0.106 | 150.6 | 0.2292 | 0 | baseline: starting point |
| 1 | explore | Gray-only storage: 8 flip-flops, convert to binary and back each cycle | -0.455 | 114.6 | 0.1483 | 0 | rejected: worse timing than round 0 |
| 2 | explore | parallel increment and decrement with late select on 'up' | -0.029 | 158.5 | 0.2333 | 0 | kept: better timing than round 0 |
| 3 | improve | look-ahead registers for count+1 and count-1 so the Gray output needs only XOR + select | 0.035 | 306.7 | 0.4731 | 0 | kept: better timing than round 2 |
