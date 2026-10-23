"""rtl-agent: an LLM-driven loop that generates and optimizes RTL.

The LLM proposes Verilog. Open-source EDA tools (Yosys, OpenSTA, Icarus
Verilog) measure every proposal. Only measured, functionally-checked
results are ever kept.

Start reading at ``controller.py`` - it is the loop that ties everything
together. ``docs/GUIDE.md`` walks through every file in plain language.
"""

__version__ = "0.2.0"
