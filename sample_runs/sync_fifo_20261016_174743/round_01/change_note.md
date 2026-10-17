# Round 1 (improve)

**Status:** invalid - behaviour differs from the original

**Idea:** simplify the storage write enable: write the free slot whenever the FIFO is not full

**LLM diagnosis / plan:**

The storage write enable is wr_en AND not full, decoded for 16 slots. Writing the free slot on every cycle while the FIFO is not full removes wr_en from that logic; the written value only becomes visible once wr_ptr moves past the slot, so the queue contents should not change.

