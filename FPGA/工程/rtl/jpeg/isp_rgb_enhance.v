

module isp_rgb_enhance
    (input wire clk, rst, valid, sof, eol, fe,
     input wire [95 : 0] pixels,
     input wire [383 : 0] config_i,
     output wire [95 : 0] out_pixels,
     output wire out_valid, out_sof, out_eol, out_fe,
     output wire [31 : 0] out_revision,
     output wire [95 : 0] stats_pixels,
     output wire stats_valid, stats_sof, stats_eol, stats_fe);
    reg [5 : 0] v = 0, s = 0, e = 0, f = 0;
    reg [31 : 0] revisions[0 : 5];
    integer j;
    always @(posedge clk) begin
        v <= {v[4 : 0], valid};
        s <= {s[4 : 0], sof};
        e <= {e[4 : 0], eol};
        f <= {f[4 : 0], fe};
        revisions[0] <= config_i[95 : 64];
        for (j = 1; j < 6; j = j + 1)
            revisions[j] <= revisions[j - 1];
        if (rst) begin
            v <= 0;
            s <= 0;
            e <= 0;
            f <= 0;
        end
    end
    assign out_valid = v[5];
    assign out_sof = s[5];
    assign out_eol = e[5];
    assign out_fe = f[5];
    assign out_revision = revisions[5];
    assign stats_valid = v[1];
    assign stats_sof = s[1];
    assign stats_eol = e[1];
    assign stats_fe = f[1];
    genvar lane, c;
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : L
            reg [23 : 0] p1 = 0, p2 = 0, p3 = 0, p4 = 0, p5 = 0;
            wire [9 : 0] weighted = {2'd0, p1[23 : 16]} + {1'd0, p1[15 : 8], 1'b0} +
                                    {2'd0, p1[7 : 0]};
            reg [7 : 0] y2 = 0, y3 = 0;
            reg [8 : 0] left3 = 0;
            reg [13 : 0] product3 = 0;
            wire [8 : 0] left = config_i[96 + y2[7 : 5] * 32 +: 9];
            wire [8 : 0] right = config_i[128 + y2[7 : 5] * 32 +: 9];
            wire signed [10 : 0] offset = $signed({1'b0, left3}) +
                                          $signed({1'b0, product3[13 : 5]}) - $signed({3'd0, y3});
            reg signed [26 : 0] blend4 = 0;
            always @(posedge clk) begin
                p2 <= p1;
                y2 <= weighted[9 : 2];
                p3 <= p2;
                y3 <= y2;
                left3 <= left;
                product3 <= (right - left) * y2[4 : 0];
                p4 <= p3;
                blend4 <= offset * $signed({1'b0, config_i[47 : 32]});
            end
            for (c = 0; c < 3; c = c + 1) begin : C
                reg [23 : 0] gained0 = 0;
                wire signed [27 : 0] corrected = $signed({20'd0, p4[c * 8 +: 8]}) + (blend4 >>> 8);
                always @(posedge clk) begin
                    gained0 <= pixels[lane * 24 + c * 8 +: 8] * config_i[15 : 0];
                    p1[c * 8 +: 8] <= gained0[23 : 8] > 255 ? 8'd255 : gained0[15 : 8];
                    p5[c * 8 +: 8] <= corrected < 0     ? 8'd0
                                      : corrected > 255 ? 8'd255
                                                        : corrected[7 : 0];
                end
            end
            assign out_pixels[lane * 24 +: 24] = p5;
            assign stats_pixels[lane * 24 +: 24] = p1;
        end
    endgenerate
endmodule
