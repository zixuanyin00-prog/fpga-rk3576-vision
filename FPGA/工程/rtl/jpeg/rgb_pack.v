
`timescale 1ns / 1ps
module rgb_pack
    (input wire clk, rst,
     input wire [23 : 0] rgb,
     input wire valid, last,
     output wire ready,
     output wire [31 : 0] data,
     output wire [3 : 0] keep,
     output wire out_valid, out_last,
     input wire out_ready);
    reg [63 : 0] buffer = 0;
    reg [3 : 0] count = 0;
    reg ending = 0;
    assign out_valid = count >= 4 || (ending && count != 0);
    assign out_last = ending && count <= 4;
    assign keep = count >= 4 ? 4'hf : count == 3 ? 4'h7 : count == 2 ? 4'h3 : 4'h1;
    assign data = buffer[31 : 0];
    wire pop = out_valid && out_ready;
    wire [3 : 0] left = pop ? (count >= 4 ? count - 4 : 0) : count;
    wire [63 : 0] shifted = pop ? buffer >> 32 : buffer;
    assign ready = !ending && left <= 5;
    always @(posedge clk) begin
        if (pop) begin
            buffer <= shifted;
            count <= left;
            if (out_last)
                ending <= 0;
        end
        if (valid && ready) begin
            buffer <= shifted | ({40'd0, rgb[7 : 0], rgb[15 : 8], rgb[23 : 16]} << (left * 8));
            count <= left + 3;
            if (last)
                ending <= 1;
        end
        if (rst) begin
            buffer <= 0;
            count <= 0;
            ending <= 0;
        end
    end
endmodule

module rgb_quad_pack
    (input wire clk, rst,
     input wire [95 : 0] pixels,
     input wire [3 : 0] mask,
     input wire valid, last,
     output wire ready,
     output wire [127 : 0] data,
     output wire [15 : 0] keep,
     output wire out_valid, out_last,
     input wire out_ready);
    reg [223 : 0] buffer = 0;
    reg [5 : 0] count = 0;
    reg ending = 0;
    wire pop = out_valid && out_ready;
    wire [5 : 0] left = pop ? (count >= 16 ? count - 16 : 0) : count;
    wire [223 : 0] shifted = pop ? buffer >> 128 : buffer;
    reg [95 : 0] compact;
    reg [3 : 0] bytes;
    integer k;
    always @* begin
        compact = 0;
        bytes = 0;
        for (k = 0; k < 4; k = k + 1)
            if (mask[k]) begin
                compact[bytes * 8 +: 24] = {pixels[k * 24 +: 8], pixels[k * 24 + 8 +: 8],
                                            pixels[k * 24 + 16 +: 8]};
                bytes = bytes + 3;
            end
    end
    assign ready = !ending && left <= 16;
    assign data = buffer[127 : 0];
    assign out_valid = count >= 16 || (ending && count != 0);
    assign out_last = ending && count <= 16;
    assign keep = count >= 16 ? 16'hffff : ((17'd1 << count) - 1);
    always @(posedge clk) begin
        if (pop) begin
            buffer <= shifted;
            count <= left;
            if (out_last)
                ending <= 0;
        end
        if (valid && ready) begin
            buffer <= shifted | ({128'd0, compact} << (left * 8));
            count <= left + bytes;
            if (last)
                ending <= 1;
        end
        if (rst) begin
            buffer <= 0;
            count <= 0;
            ending <= 0;
        end
    end
endmodule
