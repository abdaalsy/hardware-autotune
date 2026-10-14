`ifdef COCOTB_SIM
initial begin
    $dumpfile("dump.vcd"); // Cocotb/Plusargs overrides this string name dynamically
    $dumpvars(0, VoiceCircuit_top); // Dumps all signals inside the top module
end
`endif
`default_nettype none

module VoiceCircuit_top (
);
