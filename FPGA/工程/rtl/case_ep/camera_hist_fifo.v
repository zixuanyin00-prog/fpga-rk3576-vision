

`timescale 1ns / 1ps
`default_nettype none
module camera_hist_fifo
    #(parameter WIDTH = 36,
      parameter ABITS = 8)
    (input wire wclk, wrst, wen,
     input wire [WIDTH - 1 : 0] wdata,
     output wire full,
     input wire rclk, rrst,
     input wire ready,
     output wire valid,
     output wire [WIDTH - 1 : 0] data);
    reg [WIDTH - 1 : 0] mem[0 : (1 << ABITS) - 1];
    reg [ABITS : 0] wb = 0, wg = 0, rb = 0, rg = 0;
    (* async_reg = "true" *) reg [ABITS : 0] rg1 = 0, rg2 = 0, wg1 = 0, wg2 = 0;
    reg rv = 0;
    reg [WIDTH - 1 : 0] rd = 0;
    wire empty = (rg == wg2);
    wire [ABITS : 0] wn = wb + 1'b1, rn = rb + 1'b1;
    assign full = (wg == {~rg2[ABITS : ABITS - 1], rg2[ABITS - 2 : 0]});
    assign valid = rv;
    assign data = rd;
    always @(posedge wclk) begin
        rg1 <= rg;
        rg2 <= rg1;
        if (wen && !full) begin
            mem[wb[ABITS - 1 : 0]] <= wdata;
            wb <= wn;
            wg <= (wn >> 1) ^ wn;
        end
        if (wrst) begin
            wb <= 0;
            wg <= 0;
            rg1 <= 0;
            rg2 <= 0;
        end
    end
    always @(posedge rclk) begin
        wg1 <= wg;
        wg2 <= wg1;
        if (!rv || ready) begin
            rv <= !empty;
            if (!empty) begin
                rd <= mem[rb[ABITS - 1 : 0]];
                rb <= rn;
                rg <= (rn >> 1) ^ rn;
            end
        end
        if (rrst) begin
            rb <= 0;
            rg <= 0;
            wg1 <= 0;
            wg2 <= 0;
            rv <= 0;
        end
    end
endmodule
`default_nettype wire
