# rtl-agent with every tool it needs (Yosys 0.69, OpenSTA 3.1.0, Icarus Verilog, Python packages
# and the example cell library), so a new user installs only Docker.
#
#   Build once (20-40 minutes):   docker build -t rtl-agent .
#   Then run with the helper:     scripts/docker-run.sh run examples/dot_product/config.yaml
#   Or the web page:              bash scripts/docker-web.sh   (then open http://localhost:8501)
#   (or by hand, see README "Running with Docker")
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PATH=/opt/eda/bin:$PATH \
    JOBS=4 \
    PYTHONPATH=/opt/rtl-agent \
    RTL_AGENT_LIB_DIR=/opt/rtl-agent/libs \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONDONTWRITEBYTECODE=1

# 1. EDA tools: the same script a user would run on Ubuntu, so both stay in step.
COPY scripts/install_eda_tools.sh /tmp/install_eda_tools.sh
RUN bash /tmp/install_eda_tools.sh \
 && rm -rf /root/eda-src /var/lib/apt/lists/* /tmp/install_eda_tools.sh

# 2. Python packages (pip, not apt: Ubuntu's python3-pydantic is version 1, rtl-agent needs 2).
WORKDIR /opt/rtl-agent
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# 3. rtl-agent itself and the example cell library (sed removes Windows line endings, in case
#    the files were copied through a Windows tool; .gitattributes prevents it for git clones).
COPY . .
RUN sed -i 's/\r$//' scripts/*.sh \
 && bash scripts/get_library.sh && python3 -m rtl_agent check-tools

# 4. A home folder any user id can use. "~/rtl-agent" points to the copy above, so configs that
#    use a path like ~/rtl-agent/libs/nangate45_typ.lib work inside the container too.
ENV HOME=/home/rtl
RUN mkdir -p /home/rtl && ln -s /opt/rtl-agent /home/rtl/rtl-agent && chmod 777 /home/rtl

WORKDIR /work
# the web interface (python3 -m rtl_agent web) listens on this port
EXPOSE 8501
CMD ["python3", "-m", "rtl_agent", "check-tools"]
