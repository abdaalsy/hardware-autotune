TOPLEVEL = VoiceCircuit_top
SRC_DIR = cmn/src
FPGA_BUILD_DIR = fpga/build

VERILOG_SOURCES = $(wildcard $(SRC_DIR)/*.sv)

VTR_ROOT ?= ./vtr-verilog-to-routing
VPR = $(VTR_ROOT)/vpr/vpr
ARCH_XML = $(VTR_ROOT)/vtr_flow/arch/timing/k6_N10_mem32K_40nm.xml

FPGA_EBLIF = $(FPGA_BUILD_DIR)/$(TOPLEVEL).eblif

CHAN_WIDTH ?= 100

FPGA_SIM_DIR = fpga/sim
SIM ?= icarus
TOPLEVEL_LAND ?= verilog
COMPILE_ARGS += -g2012
MODULE = $(FPGA_SIM_DIR)/test_voice_circuit
PLUSARGS += +dumpfile=$(FPGA_SIM_DIR)/VoiceCircuit_sim.vcd
WAVES = 1

# note: these workflows don't target a certain fpga, gonna need to edit these to target a specific one later on

synth_fpga:
	yosys -p "read_verilog -sv $(VERILOG_SOURCES); hierarchy -top $(TOPLEVEL); proc; opt; fsm; opt; memory; opt; techmap; opt; stat"
pnr_fpga:
	yosys -p "read_verilog -sv $(VERILOG_SOURCES); hierarchy -top $(TOPLEVEL); proc; opt; fsm; opt; memory; opt; techmap; opt; write_blif -attr -param $(EBLIF)"
	$(VPR) $(ARCH_XML) $(EBLIF) --route_chan_width $(CHAN_WIDTH)
sim_fpga: all
	@echo "Simulation complete, launching gtkwave"
	gtkwave $(FPGA_SIM_DIR)/VoiceCircuit_sim.vcd
include $(shell ./venv/bin/cocotb-config --makefiles)/Makefile.sim
