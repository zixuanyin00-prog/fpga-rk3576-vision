

`timescale 1ns / 1ps
module rect_ddr_fifo
    #(parameter RING_BITS = 19, BASE_ADDR = 0, FIFO_BITS = 9, INGRESS_BITS = FIFO_BITS,
                READ_PRIORITY = 0)
    (input wire core_clk, reset, wen,
     input wire [101 : 0] wdata,
     output wire full,
     input wire ready,
     output wire valid,
     output wire [101 : 0] data,
     input wire mem_clk, calibrated,
     output wire error,
     output wire [31 : 0] status,
     output wire [27 : 0] awaddr,
     output wire awvalid,
     input wire awready,
     output wire [127 : 0] wd,
     output wire wvalid, wlast,
     input wire wready,
     input wire [1 : 0] bresp,
     input wire bvalid,
     output wire bready,
     output wire [27 : 0] araddr,
     output wire arvalid,
     input wire arready,
     input wire [127 : 0] rd,
     input wire [1 : 0] rresp,
     input wire rvalid, rlast,
     output wire rready);
    wire arst = reset || !calibrated;
    (* async_reg = "true" *) reg [1 : 0] core_reset = 3, mem_reset = 3;
    always @(posedge core_clk or posedge arst)
        if (arst)
            core_reset <= 3;
        else
            core_reset <= {core_reset[0], 1'b0};
    always @(posedge mem_clk or posedge arst)
        if (arst)
            mem_reset <= 3;
        else
            mem_reset <= {mem_reset[0], 1'b0};
    reg failed = 0;
    (* async_reg = "true" *) reg [1 : 0] fault_sync = 0;
    always @(posedge core_clk) begin
        fault_sync <= {fault_sync[0], failed};
        if (core_reset[1])
            fault_sync <= 0;
    end
    assign error = fault_sync[1];
    reg [5 : 0] word_mod = 0, padding = 0;
    wire ifull, iv, ipop, ofull, ov, opop;
    wire [127 : 0] idata, odata;
    wire [INGRESS_BITS : 0] available;
    wire [FIFO_BITS : 0] output_used;
    wire push = (wen || padding != 0) && !ifull && !core_reset[1];
    wire [127 : 0] input_data = padding != 0 ? 128'd0 : {1'b1, 25'd0, wdata};
    assign full = ifull || padding != 0 || core_reset[1] || error;
    always @(posedge core_clk) begin
        if (push) begin
            word_mod <= word_mod + 1'b1;
            if (padding != 0)
                padding <= padding - 1'b1;
            else if (wdata[101])
                padding <= 6'd0 - (word_mod + 1'b1);
        end
        if (core_reset[1]) begin
            word_mod <= 0;
            padding <= 0;
        end
    end
    rect_async_fifo #(.WIDTH(128),
                      .ABITS(INGRESS_BITS))
        ingress(.wclk(core_clk),
                .wrst(core_reset[1]),
                .wen(push),
                .wdata(input_data),
                .full(ifull),
                .wused(),
                .rclk(mem_clk),
                .rrst(mem_reset[1]),
                .ready(ipop),
                .valid(iv),
                .data(idata),
                .available(available));
    rect_async_fifo #(.WIDTH(128),
                      .ABITS(FIFO_BITS))
        egress(.wclk(mem_clk),
               .wrst(mem_reset[1]),
               .wen(rvalid && rready),
               .wdata(rd),
               .full(ofull),
               .wused(output_used),
               .rclk(core_clk),
               .rrst(core_reset[1]),
               .ready(opop),
               .valid(ov),
               .data(odata),
               .available());
    assign valid = ov && odata[127] && !core_reset[1] && !error;
    assign data = odata[101 : 0];
    assign opop = ov && (!odata[127] || ready);

    reg [1 : 0] ws = 0, rs = 0;
    reg [5 : 0] wi = 0, ri = 0;
    reg [31 : 0] committed = 0, consumed = 0;
    wire [31 : 0] occupied = committed - consumed;
    assign awaddr = BASE_ADDR + {committed[RING_BITS - 1 : 0], 4'd0};
    assign awvalid = ws == 1;
    assign wd = idata;
    assign wvalid = ws == 2 && iv;
    assign wlast = wi == 63;
    assign ipop = wvalid && wready;
    assign bready = ws == 3;
    assign araddr = BASE_ADDR + {consumed[RING_BITS - 1 : 0], 4'd0};
    assign arvalid = rs == 1;
    assign rready = rs == 2 && !ofull;
    assign status = {16'hdd31, 14'd0, error, !core_reset[1]};
    always @(posedge mem_clk) begin
        case (ws)
            0:
                if (!failed && available >= 64 && occupied <= (1 << RING_BITS) - 64)
                    ws <= 1;
            1:
                if (awready) begin
                    ws <= 2;
                    wi <= 0;
                end
            2:
                if (wvalid && wready) begin
                    wi <= wi + 1'b1;
                    if (wlast)
                        ws <= 3;
                end
            3:
                if (bvalid) begin
                    if (bresp != 0)
                        failed <= 1;
                    else
                        committed <= committed + 64;
                    ws <= 0;
                end
        endcase
        case (rs)

            0:
                if (!failed && occupied >= 64 && output_used <= (1 << FIFO_BITS) - 64 &&
                    (READ_PRIORITY || available < 64))
                    rs <= 1;
            1:
                if (arready) begin
                    rs <= 2;
                    ri <= 0;
                end
            2:
                if (rvalid && rready) begin
                    if (rresp != 0 || rlast != (ri == 63))
                        failed <= 1;
                    ri <= ri + 1'b1;
                    if (rlast) begin
                        consumed <= consumed + 64;
                        rs <= 0;
                    end
                end
        endcase
        if (occupied > (1 << RING_BITS))
            failed <= 1;
        if (mem_reset[1]) begin
            ws <= 0;
            rs <= 0;
            wi <= 0;
            ri <= 0;
            committed <= 0;
            consumed <= 0;
            failed <= 0;
        end
    end
endmodule

module rect_async_fifo
    #(parameter WIDTH = 128, ABITS = 10)
    (input wire wclk, wrst, wen,
     input wire [WIDTH - 1 : 0] wdata,
     output wire full,
     output wire [ABITS : 0] wused,
     input wire rclk, rrst, ready,
     output wire valid,
     output wire [WIDTH - 1 : 0] data,
     output wire [ABITS : 0] available);
    reg [WIDTH - 1 : 0] mem[0 : (1 << ABITS) - 1];
    reg [ABITS : 0] wb = 0, wg = 0, rb = 0, rg = 0;
    (* async_reg = "true" *) reg [ABITS : 0] rg1 = 0, rg2 = 0, wg1 = 0, wg2 = 0;
    reg rv = 0;
    reg [WIDTH - 1 : 0] q = 0;
    function [ABITS : 0] binary;
        input [ABITS : 0] gray;
        integer i;
        begin
            binary[ABITS] = gray[ABITS];
            for (i = ABITS - 1; i >= 0; i = i - 1)
                binary[i] = binary[i + 1] ^ gray[i];
        end
    endfunction
    wire [ABITS : 0] wn = wb + 1'b1, rn = rb + 1'b1;
    wire empty = rg == wg2;
    assign full = wg == {~rg2[ABITS : ABITS - 1], rg2[ABITS - 2 : 0]};
    assign wused = wb - binary(rg2);
    assign available = binary(wg2) - rb + rv;
    assign valid = rv;
    assign data = q;
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
                q <= mem[rb[ABITS - 1 : 0]];
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
