# rtl-agent report: sync_fifo

Status: **finished**

> Offline test run: the scripted provider replayed hand-written responses (not an LLM).

## The design

sync_fifo: 16-entry x 8-bit synchronous FIFO built from flip-flops.

The storage is a register array (mem). Write and read pointers walk
through it. An occupancy counter drives the registered full/empty flags.
The output dout is read straight from the array through a 16-to-1
multiplexer selected by the read pointer.

| port | direction | width | role |
|---|---|---|---|
| clk | input | 1 | clock |
| rst_n | input | 1 | reset (active-low, async) |
| wr_en | input | 1 | data / control input |
| din | input | 8 | data / control input |
| rd_en | input | 1 | data / control input |
| dout | output | 8 | output |
| full | output | 1 | output |
| empty | output | 1 | output |

- top module: sync_fifo
- clock period: 0.7 ns (1429 MHz)
- flip-flop bits: 15
- memory bits: 128
- files: sync_fifo.v (56 lines)
- starting point: the user's RTL

## Summary

- Starting point (round 0): setup slack -0.111 ns, hold slack 0.019 ns, area 1151.5 um2, power 1.2125 mW, 15 flip-flop bits.
- Best version: round 2 - setup slack 0.002 ns (+0.113 ns), area 1222.3 um2 (+6.1%), power 1.2616 mW (+4.0%), extra latency 0 cycle(s).
- 2 proposal(s): 1 kept, 0 correct but not better, 1 failed checks, 0 duplicate(s).
- Round 2 (improve) kept: look-ahead head register: precompute next cycle's dout so the read multiplexer leaves the output path - slack +0.113 ns, area +6.1%, power +4.0%.
- Targets met: timing.
- Stopped because: success: all targets met (best = round 2)

## Rounds

| # | phase | change | setup WS | area | power | latency | verdict |
|---|---|---|---|---|---|---|---|
| 0 | baseline | original design | -0.111 | 1151.5 | 1.2125 | 0 | baseline: starting point |
| 1 | improve | simplify the storage write enable: write the free slot whenever the FIFO is not full | n/a | n/a | n/a |  | invalid: behaviour differs from the original |
| 2 | improve | look-ahead head register: precompute next cycle's dout so the read multiplexer leaves the output path | 0.002 | 1222.3 | 1.2616 | 0 | kept: better timing than round 0 |
