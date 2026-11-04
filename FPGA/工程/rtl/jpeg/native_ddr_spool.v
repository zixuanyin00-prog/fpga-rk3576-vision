

`timescale 1ns / 1ps
module native_ddr_spool
    (input wire refclk, core_clk, reset, wen,
     input wire [101 : 0] wdata,
     input wire wen1,
     input wire [101 : 0] wdata1,
     output wire full1,
     input wire ready1,
     output wire valid1,
     output wire [101 : 0] data1,
     input wire wen2,
     input wire [101 : 0] wdata2,
     output wire full2,
     input wire ready2,
     output wire valid2,
     output wire [101 : 0] data2,
     output wire full,
     input wire ready,
     output wire valid,
     output wire [101 : 0] data,
     output wire error,
     output wire [31 : 0] status,
     output wire [13 : 0] ddr_addr,
     output wire [2 : 0] ddr_ba,
     output wire ddr_cke, ddr_odt, ddr_cs_n, ddr_ras_n, ddr_cas_n, ddr_we_n,
     output wire ddr_ck_p, ddr_ck_n, ddr_reset_n,
     inout wire [1 : 0] ddr_dm, ddr_dqs_p, ddr_dqs_n,
     inout wire [15 : 0] ddr_dq);

    wire clk100, locked, mem_clk, calibrated;
    wire [7 : 0] cal_error, cal_step;
    wire [27 : 0] awaddr, araddr;
    wire [127 : 0] wd, rd;
    wire awvalid, awready, wvalid, wlast, wready, bvalid, bready, arvalid, arready, rvalid, rlast,
         rready;
    wire [1 : 0] bresp, rresp;
    wire [27 : 0] qa[0 : 2], qr[0 : 2];
    wire [127 : 0] qw[0 : 2];
    wire [2 : 0] qav, qwv, qwl, qbr, qarv, qrr, qerror;
    wire [31 : 0] fifo_status[0 : 2];
    wire [2 : 0] qwen = {wen2, wen1, wen}, qready = {ready2, ready1, ready};
    wire [101 : 0] qinput[0 : 2];
    assign qinput[0] = wdata;
    assign qinput[1] = wdata1;
    assign qinput[2] = wdata2;
    wire [101 : 0] qoutput[0 : 2];
    wire [2 : 0] qfull, qvalid;
    assign full = qfull[0];
    assign full1 = qfull[1];
    assign full2 = qfull[2];
    assign valid = qvalid[0];
    assign valid1 = qvalid[1];
    assign valid2 = qvalid[2];
    assign data = qoutput[0];
    assign data1 = qoutput[1];
    assign data2 = qoutput[2];
    assign error = |qerror;
    (* async_reg = "true" *) reg [15 : 0] diag_sync0 = 0, diag_sync1 = 0;
    always @(posedge core_clk) begin
        diag_sync0 <= {cal_error, cal_step};
        diag_sync1 <= diag_sync0;
    end
    assign status = {8'hdd, diag_sync1, 4'd0, qerror,
                     fifo_status[0][0] & fifo_status[1][0] & fifo_status[2][0]};
    ddr_clk clock_source(.refclk(refclk),
                         .reset(1'b0),
                         .lock(locked),
                         .clk0_out(clk100));

    wire arb_arst = reset || !calibrated;
    (* async_reg = "true" *) reg [1 : 0] arb_reset = 3;
    always @(posedge mem_clk or posedge arb_arst)
        if (arb_arst)
            arb_reset <= 3;
        else
            arb_reset <= {arb_reset[0], 1'b0};
    reg write_granted = 0, read_granted = 0;
    reg [1 : 0] write_owner = 0, read_owner = 0, last_write = 2, last_read = 2;
    function [1 : 0] choose_queue;
        input [2 : 0] pending;
        input [1 : 0] last;
        begin
            choose_queue = 0;
            if (pending[2])
                choose_queue = 2;
            else
                case (last)
                    0:
                        if (pending[1])
                            choose_queue = 1;
                        else if (pending[2])
                            choose_queue = 2;
                        else
                            choose_queue = 0;
                    1:
                        if (pending[2])
                            choose_queue = 2;
                        else if (pending[0])
                            choose_queue = 0;
                        else
                            choose_queue = 1;
                    default:
                        if (pending[0])
                            choose_queue = 0;
                        else if (pending[1])
                            choose_queue = 1;
                        else
                            choose_queue = 2;
                endcase
        end
    endfunction
    always @(posedge mem_clk) begin
        if (!write_granted) begin

            if (|qav && (qav[2] || !qarv[2])) begin
                write_granted <= 1;
                write_owner <= choose_queue(qav, last_write);
            end
        end else if (bvalid && bready) begin
            write_granted <= 0;
            if (write_owner != 2)
                last_write <= write_owner;
        end
        if (!read_granted) begin
            if (|qarv) begin
                read_granted <= 1;
                read_owner <= choose_queue(qarv, last_read);
            end
        end else if (rvalid && rready && rlast) begin
            read_granted <= 0;
            if (read_owner != 2)
                last_read <= read_owner;
        end
        if (arb_reset[1]) begin
            write_granted <= 0;
            read_granted <= 0;
            write_owner <= 0;
            read_owner <= 0;
            last_write <= 2;
            last_read <= 2;
        end
    end
    assign awaddr = qa[write_owner];
    assign awvalid = write_granted && qav[write_owner];
    assign wd = qw[write_owner];
    assign wvalid = write_granted && qwv[write_owner];
    assign wlast = qwl[write_owner];
    assign bready = write_granted && qbr[write_owner];
    assign araddr = qr[read_owner];
    assign arvalid = read_granted && qarv[read_owner];
    assign rready = read_granted && qrr[read_owner];
    genvar lane;
    generate
        for (lane = 0; lane < 3; lane = lane + 1) begin : ROI_QUEUE
            rect_ddr_fifo #(.RING_BITS(lane == 2 ? 21 : 19),
                            .BASE_ADDR(lane == 2 ? 28'h1000000 : lane * 28'h0800000),
                            .FIFO_BITS(9),
                            .INGRESS_BITS(lane == 2 ? 11 : 12),
                            .READ_PRIORITY(lane == 2))
                queue(.core_clk(core_clk),
                      .reset(reset),
                      .wen(qwen[lane]),
                      .wdata(qinput[lane]),
                      .full(qfull[lane]),
                      .ready(qready[lane]),
                      .valid(qvalid[lane]),
                      .data(qoutput[lane]),
                      .mem_clk(mem_clk),
                      .calibrated(calibrated),
                      .error(qerror[lane]),
                      .status(fifo_status[lane]),
                      .awaddr(qa[lane]),
                      .awvalid(qav[lane]),
                      .awready(awready && write_granted && write_owner == lane),
                      .wd(qw[lane]),
                      .wvalid(qwv[lane]),
                      .wlast(qwl[lane]),
                      .wready(wready && write_granted && write_owner == lane),
                      .bresp(bresp),
                      .bvalid(bvalid && write_granted && write_owner == lane),
                      .bready(qbr[lane]),
                      .araddr(qr[lane]),
                      .arvalid(qarv[lane]),
                      .arready(arready && read_granted && read_owner == lane),
                      .rd(rd),
                      .rresp(rresp),
                      .rvalid(rvalid && read_granted && read_owner == lane),
                      .rlast(rlast),
                      .rready(qrr[lane]));
        end
    endgenerate
    ui_ddr controller(.sys_rst_n(locked && !reset),
                      .sys_clk(clk100),
                      .ddr_addr(ddr_addr),
                      .ddr_ba(ddr_ba),
                      .ddr_cke(ddr_cke),
                      .ddr_odt(ddr_odt),
                      .ddr_cs_n(ddr_cs_n),
                      .ddr_ras_n(ddr_ras_n),
                      .ddr_cas_n(ddr_cas_n),
                      .ddr_we_n(ddr_we_n),
                      .ddr_ck_p(ddr_ck_p),
                      .ddr_ck_n(ddr_ck_n),
                      .ddr_reset_n(ddr_reset_n),
                      .ddr_dm(ddr_dm),
                      .ddr_dq(ddr_dq),
                      .ddr_dqs_p(ddr_dqs_p),
                      .ddr_dqs_n(ddr_dqs_n),
                      .dfi_clk(mem_clk),
                      .ddr_init_cal_done(calibrated),
                      .ddr_init_cal_err(cal_error),
                      .ddr_init_cal_step(cal_step),
                      .axi_aclk(mem_clk),
                      .axi_aresetn(calibrated),
                      .axi_awid(4'd0),
                      .axi_awaddr(awaddr),
                      .axi_awlen(8'd63),
                      .axi_awsize(3'd4),
                      .axi_awburst(2'd1),
                      .axi_awlock(1'b0),
                      .axi_awcache(4'd0),
                      .axi_awprot(3'd0),
                      .axi_awqos(4'd0),
                      .axi_awvalid(awvalid),
                      .axi_awready(awready),
                      .axi_wdata(wd),
                      .axi_wstrb(16'hffff),
                      .axi_wlast(wlast),
                      .axi_wvalid(wvalid),
                      .axi_wready(wready),
                      .axi_bresp(bresp),
                      .axi_bvalid(bvalid),
                      .axi_bready(bready),
                      .axi_arid(4'd0),
                      .axi_araddr(araddr),
                      .axi_arlen(8'd63),
                      .axi_arsize(3'd4),
                      .axi_arburst(2'd1),
                      .axi_arlock(1'b0),
                      .axi_arcache(4'd0),
                      .axi_arprot(3'd0),
                      .axi_arqos(4'd0),
                      .axi_arvalid(arvalid),
                      .axi_arready(arready),
                      .axi_rdata(rd),
                      .axi_rresp(rresp),
                      .axi_rlast(rlast),
                      .axi_rvalid(rvalid),
                      .axi_rready(rready),
                      .usr_ref_type(2'd0),
                      .usr_ref_req(1'b0),
                      .pzq_cal_done_i(1'b0),
                      .user_update(1'b0),
                      .user_ram_addr(8'd0),
                      .user_ram_wr_en(1'b0),
                      .user_ram_rd_en(1'b0),
                      .user_ram_wr_data(32'd0),
                      .uart_rxd(1'b1));
endmodule
