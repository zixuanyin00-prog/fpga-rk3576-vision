

`timescale 1ns / 1ps
module isp_window4
    #(parameter W = 1920, H = 1080, P = 10, REFLECT = 0)
    (input wire clk, rst,
     input wire valid, sof, eol,
     input wire [4 * P - 1 : 0] data,
     output reg ov = 0, os = 0, oe = 0,
     output reg [11 : 0] oy = 0,
     output reg [6 * P - 1 : 0] top = 0, mid = 0, bot = 0,
     output reg fault = 0);
    localparam G = W / 4;
    reg [4 * P - 1 : 0] line0[0 : G - 1], line1[0 : G - 1];
    reg [11 : 0] x = 0, y = 0;
    reg [2 : 0] flush_delay = 0;
    reg flushing = 0;
    reg [11 : 0] fx = 0;
    wire input_step = valid && !flushing;
    wire step = input_step || flushing;
    wire [11 : 0] ax = flushing ? fx : (sof ? 12'd0 : x), ay = flushing ? H : (sof ? 12'd0 : y);
    reg rv = 0;
    reg [11 : 0] rx = 0, ry = 0;
    reg [4 * P - 1 : 0] a = 0, b = 0, c = 0;
    reg [4 * P - 1 : 0] pa = 0, pb = 0, pc = 0;
    reg [P - 1 : 0] la = 0, lb = 0, lc = 0;
    reg tail = 0;
    reg [11 : 0] py = 0;
    function [6 * P - 1 : 0] make_window;
        input [P - 1 : 0] left, right;
        input [4 * P - 1 : 0] center;
        begin
            make_window = {right, center, left};
        end
    endfunction
    always @(posedge clk) begin
        rv <= 0;
        ov <= 0;
        os <= 0;
        oe <= 0;
        if (input_step) begin
            if ((eol != (ax == G - 1)) || ay >= H)
                fault <= 1;
            line0[ax] <= line1[ax];
            line1[ax] <= data;
            x <= eol ? 0 : ax + 1;
            y <= eol ? ay + 1 : ay;
            if (eol && ay == H - 1)
                flush_delay <= 4;
        end
        if (flush_delay != 0) begin
            flush_delay <= flush_delay - 1;
            if (flush_delay == 1) begin
                flushing <= 1;
                fx <= 0;
            end
        end
        if (flushing) begin
            fx <= fx + 1;
            if (fx == G - 1)
                flushing <= 0;
            if (valid)
                fault <= 1;
        end
        if (step && ay > 0) begin
            a <= ay == 1 ? (REFLECT ? data : line1[ax]) : line0[ax];
            b <= line1[ax];
            c <= flushing ? (REFLECT ? line0[ax] : line1[ax]) : data;
            rv <= 1;
            rx <= ax;
            ry <= ay - 1;
        end
        if (rv) begin
            if (rx != 0) begin
                ov <= 1;
                os <= py == 0 && rx == 1;
                oe <= 0;
                oy <= py;
                top <= make_window(la, a[0 +: P], pa);
                mid <= make_window(lb, b[0 +: P], pb);
                bot <= make_window(lc, c[0 +: P], pc);
            end
            pa <= a;
            pb <= b;
            pc <= c;
            py <= ry;
            la <= rx == 0 ? (REFLECT ? a[P +: P] : a[0 +: P]) : pa[3 * P +: P];
            lb <= rx == 0 ? (REFLECT ? b[P +: P] : b[0 +: P]) : pb[3 * P +: P];
            lc <= rx == 0 ? (REFLECT ? c[P +: P] : c[0 +: P]) : pc[3 * P +: P];
            if (rx == G - 1)
                tail <= 1;
        end else if (tail) begin
            tail <= 0;
            ov <= 1;
            os <= G == 1 && py == 0;
            oe <= 1;
            oy <= py;
            top <= make_window(la, REFLECT ? pa[2 * P +: P] : pa[3 * P +: P], pa);
            mid <= make_window(lb, REFLECT ? pb[2 * P +: P] : pb[3 * P +: P], pb);
            bot <= make_window(lc, REFLECT ? pc[2 * P +: P] : pc[3 * P +: P], pc);
        end
        if (sof && valid && tail)
            fault <= 1;
        if (rst) begin
            x <= 0;
            y <= 0;
            fx <= 0;
            flush_delay <= 0;
            flushing <= 0;
            rv <= 0;
            ov <= 0;
            tail <= 0;
            fault <= 0;
            os <= 0;
            oe <= 0;
        end
    end
endmodule
