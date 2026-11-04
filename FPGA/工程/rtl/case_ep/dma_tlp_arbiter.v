`timescale 1ns / 1ps
`default_nettype none
module dma_tlp_arbiter
    #(parameter WIDTH = 256)
    (input wire clk_i, rst_i,
     input wire [WIDTH - 1 : 0] cpl_bus_i, dma_bus_i,
     input wire cpl_hv_i, cpl_dv_i, cpl_eot_i, dma_hv_i, dma_dv_i, dma_eot_i,
     output wire cpl_halt_o, dma_halt_o,
     output wire [WIDTH - 1 : 0] tx_bus_o,
     output wire tx_hv_o, tx_dv_o, tx_eot_o,
     input wire tx_halt_i);
    reg locked = 0, chosen_dma = 0;
    wire select_dma = locked ? chosen_dma : (!(cpl_hv_i || cpl_dv_i) && (dma_hv_i || dma_dv_i));
    assign tx_bus_o = select_dma ? dma_bus_i : cpl_bus_i;
    assign tx_hv_o = select_dma ? dma_hv_i : cpl_hv_i;
    assign tx_dv_o = select_dma ? dma_dv_i : cpl_dv_i;
    assign tx_eot_o = select_dma ? dma_eot_i : cpl_eot_i;
    assign cpl_halt_o = tx_halt_i || select_dma;
    assign dma_halt_o = tx_halt_i || !select_dma;
    always @(posedge clk_i) begin
        if (!locked && (tx_hv_o || tx_dv_o)) begin
            locked <= 1;
            chosen_dma <= select_dma;
        end
        if ((tx_hv_o || tx_dv_o) && tx_eot_o && !tx_halt_i)
            locked <= 0;
        if (rst_i) begin
            locked <= 0;
            chosen_dma <= 0;
        end
    end
endmodule
`default_nettype wire
