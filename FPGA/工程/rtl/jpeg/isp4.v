

`timescale 1ns / 1ps
module isp4
    #(parameter W = 1920, H = 1080)
    (input wire clk, rst, valid, sof, eol,
     input wire [39 : 0] raw,
     input wire [127 : 0] config_i,
     input wire config_toggle_i,
     input wire [383 : 0] enhance_config_i,
     input wire enhance_toggle_i,
     input wire [319 : 0] process_config_i,
     input wire [31 : 0] processing_revision_i,
     output wire [319 : 0] process_frame_config_o,
     output wire [31 : 0] process_frame_revision_o,
     output wire [31 : 0] rgb_revision,
     output wire [95 : 0] quality_pixels,
     output wire quality_valid, quality_sof, quality_eol, quality_fe,
     input wire awb_enable_i,
     input wire [9 : 0] manual_r_i, manual_b_i,
     output wire [95 : 0] rgb_pixels,
     output wire rgb_valid, rgb_sof, rgb_eol, rgb_fe,
     output wire error);
    (* async_reg = "true" *) reg [1 : 0] cfg_sync = 0;
    reg cfg_seen = 0;
    reg [127 : 0] pending = 128'h00000000000001000000010001000100;
    reg [127 : 0] cfg = 128'h00000000000001000000010001000100;
    localparam [319 : 0] PROC_DEFAULT = {32'd0,        32'd32,       32'h0000f080, 32'h00180103,
                                         32'h1affff00, 32'h00011080, 32'd3,        32'h0000ff40,
                                         32'd0,        32'd1};
    reg [319 : 0] proc_raw = PROC_DEFAULT, proc_tone = PROC_DEFAULT, proc_gauss = PROC_DEFAULT,
                  proc_adjust = PROC_DEFAULT, proc_emitted = PROC_DEFAULT;
    reg [31 : 0] prev_raw = 1, prev_tone = 1, prev_gauss = 1, prev_adjust = 1, prev_emitted = 1;
    reg [127 : 0] base_tone = 128'h00000000000001000000010001000100,
                  base_gauss = 128'h00000000000001000000010001000100,
                  base_adjust = 128'h00000000000001000000010001000100;
    reg [383 : 0] enhance_tone = 0, enhance_gauss = 0, enhance_adjust = 0;

    always @(posedge clk) begin
        cfg_sync <= {cfg_sync[0], config_toggle_i};
        if (cfg_sync[1] != cfg_seen) begin
            pending <= config_i;
            cfg_seen <= cfg_sync[1];
        end
        if (valid && sof) begin
            cfg <= pending;
            proc_raw <= process_config_i;
            prev_raw <= processing_revision_i;
        end
        if (rst) begin
            proc_raw <= PROC_DEFAULT;
            prev_raw <= 1;
        end
    end
    reg [1 : 0] enhance_sync = 0;
    reg enhance_seen = 0;
    reg [383 : 0] enhance_pending = {32'd256, 32'd224, 32'd192, 32'd160, 32'd128, 32'd96,
                                     32'd64,  32'd32,  32'd0,   32'd0,   32'd0,   32'd256},
                  enhance_active = {32'd256, 32'd224, 32'd192, 32'd160, 32'd128, 32'd96,
                                    32'd64,  32'd32,  32'd0,   32'd0,   32'd0,   32'd256};
    always @(posedge clk) begin
        enhance_sync <= {enhance_sync[0], enhance_toggle_i};
        if (enhance_sync[1] != enhance_seen) begin
            enhance_pending <= enhance_config_i;
            enhance_seen <= enhance_sync[1];
        end
        if (valid && sof)
            enhance_active <= enhance_pending;
    end
    wire v0, s0, e0, f0;
    wire [11 : 0] row0;
    wire [59 : 0] a, b, c;
    isp_window4 #(.W(W),
                  .H(H),
                  .P(10),
                  .REFLECT(1))
        raw_window(clk,
                   rst,
                   valid,
                   sof,
                   eol,
                   raw,
                   v0,
                   s0,
                   e0,
                   row0,
                   a,
                   b,
                   c,
                   f0);
    wire [95 : 0] rgb;
    genvar lane;
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : D
            wire [9 : 0] center = b[(lane + 1) * 10 +: 10];
            wire [10 : 0] horizontal = {1'b0, b[lane * 10 +: 10]} +
                                       {1'b0, b[(lane + 2) * 10 +: 10]};
            wire [10 : 0] vertical = {1'b0, a[(lane + 1) * 10 +: 10]} +
                                     {1'b0, c[(lane + 1) * 10 +: 10]};
            wire [11 : 0] diagonal = {2'b0, a[lane * 10 +: 10]} + {2'b0, a[(lane + 2) * 10 +: 10]} +
                                     {2'b0, c[lane * 10 +: 10]} + {2'b0, c[(lane + 2) * 10 +: 10]};
            wire [11 : 0] cross_sum = {1'b0, horizontal} + {1'b0, vertical};
            reg [9 : 0] rr, gg, bb;
            always @* begin
                if (!row0[0] && lane % 2 == 0) begin
                    rr = center;
                    gg = (cross_sum + 2) >> 2;
                    bb = (diagonal + 2) >> 2;
                end else if (row0[0] && lane % 2 == 1) begin
                    bb = center;
                    gg = (cross_sum + 2) >> 2;
                    rr = (diagonal + 2) >> 2;
                end else if (!row0[0]) begin
                    gg = center;
                    rr = (horizontal + 1) >> 1;
                    bb = (vertical + 1) >> 1;
                end else begin
                    gg = center;
                    rr = (vertical + 1) >> 1;
                    bb = (horizontal + 1) >> 1;
                end
            end
            reg [9 : 0] rreg, greg, breg;
            always @(posedge clk) begin
                rreg <= rr;
                greg <= gg;
                breg <= bb;
            end
            wire [7 : 0] tr, tg, tb;
            isp_tone tone_r(clk,
                            rreg,
                            tr);
            isp_tone tone_g(clk,
                            greg,
                            tg);
            isp_tone tone_b(clk,
                            breg,
                            tb);
            assign rgb[lane * 24 +: 24] = {tr, tg, tb};
        end
    endgenerate
    reg [1 : 0] vt = 0, st = 0, et = 0;
    always @(posedge clk) begin
        vt <= {vt[0], v0};
        st <= {st[0], s0};
        et <= {et[0], e0};
        if (rst) begin
            vt <= 0;
            st <= 0;
            et <= 0;
        end
    end

    always @(posedge clk) begin
        if (vt[1] && st[1]) begin
            proc_tone <= proc_raw;
            prev_tone <= prev_raw;
            base_tone <= cfg;
            enhance_tone <= enhance_active;
        end
        if (rst) begin
            proc_tone <= PROC_DEFAULT;
            prev_tone <= 1;
        end
    end
    wire v1, s1, e1, f1;
    wire [11 : 0] row1;
    wire [143 : 0] rtop, rmid, rbot;
    isp_window4 #(.W(W),
                  .H(H),
                  .P(24))
        rgb_window(clk,
                   rst,
                   vt[1],
                   st[1],
                   et[1],
                   rgb,
                   v1,
                   s1,
                   e1,
                   row1,
                   rtop,
                   rmid,
                   rbot,
                   f1);
    wire [319 : 0] gauss_frame = (v1 && s1) ? proc_tone : proc_gauss;
    wire [15 : 0] gaussian_strength = (gauss_frame[2 : 0] == 1 || !gauss_frame[32])
                                          ? 16'd0
                                          : gauss_frame[271 : 256];
    reg [15 : 0] gaussian_strength0 = 0, gaussian_strength1 = 0;
    always @(posedge clk) begin
        gaussian_strength0 <= gaussian_strength;
        gaussian_strength1 <= gaussian_strength0;
        if (v1 && s1) begin
            proc_gauss <= proc_tone;
            prev_gauss <= prev_tone;
            base_gauss <= base_tone;
            enhance_gauss <= enhance_tone;
        end
        if (rst) begin
            proc_gauss <= PROC_DEFAULT;
            prev_gauss <= 1;
            gaussian_strength0 <= 0;
            gaussian_strength1 <= 0;
        end
    end
    wire [95 : 0] filtered;
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : G
            genvar ch;
            for (ch = 0; ch < 3; ch = ch + 1) begin : C
                wire [7 : 0] p00 = rtop[lane * 24 + ch * 8 +: 8],
                             p01 = rtop[(lane + 1) * 24 + ch * 8 +: 8],
                             p02 = rtop[(lane + 2) * 24 + ch * 8 +: 8];
                wire [7 : 0] p10 = rmid[lane * 24 + ch * 8 +: 8],
                             p11 = rmid[(lane + 1) * 24 + ch * 8 +: 8],
                             p12 = rmid[(lane + 2) * 24 + ch * 8 +: 8];
                wire [7 : 0] p20 = rbot[lane * 24 + ch * 8 +: 8],
                             p21 = rbot[(lane + 1) * 24 + ch * 8 +: 8],
                             p22 = rbot[(lane + 2) * 24 + ch * 8 +: 8];
                reg [9 : 0] h0, h1, h2;
                reg [11 : 0] sum;
                reg [15 : 0] blend;
                reg [7 : 0] center0, center1;
                always @(posedge clk) begin
                    h0 <= {2'b0, p00} + {1'b0, p01, 1'b0} + {2'b0, p02};
                    h1 <= {2'b0, p10} + {1'b0, p11, 1'b0} + {2'b0, p12};
                    h2 <= {2'b0, p20} + {1'b0, p21, 1'b0} + {2'b0, p22};
                    center0 <= p11;
                    center1 <= center0;
                    sum <= {2'b0, h0} + {1'b0, h1, 1'b0} + {2'b0, h2};
                    blend <= ((sum >> 4) * gaussian_strength1 +
                              center1 * (16'd256 - gaussian_strength1)) >>
                             8;
                end
                assign filtered[lane * 24 + ch * 8 +: 8] = blend[7 : 0];
            end
        end
    endgenerate
    reg [2 : 0] vf = 0, sf = 0, ef = 0;
    reg [11 : 0] rowf[0 : 2];
    always @(posedge clk) begin
        vf <= {vf[1 : 0], v1};
        sf <= {sf[1 : 0], s1};
        ef <= {ef[1 : 0], e1};
        rowf[0] <= row1;
        rowf[1] <= rowf[0];
        rowf[2] <= rowf[1];
        if (rst)
            vf <= 0;
    end
    wire [95 : 0] adjusted;
    wire [127 : 0] adjust_frame = (vf[2] && sf[2]) ? base_gauss : base_adjust;
    always @(posedge clk) begin
        if (vf[2] && sf[2]) begin
            proc_adjust <= proc_gauss;
            prev_adjust <= prev_gauss;
            base_adjust <= base_gauss;
            enhance_adjust <= enhance_gauss;
        end
        if (rst) begin
            proc_adjust <= PROC_DEFAULT;
            prev_adjust <= 1;
        end
    end
    wire [9 : 0] awb_r, awb_b;
    isp_awb4 #(.W(W),
               .H(H))
        awb(.clk(clk),
            .rst(rst),
            .enable(awb_enable_i),
            .valid(vf[2]),
            .sof(sf[2]),
            .last_pixel(ef[2] && rowf[2] == H - 1),
            .rgb(filtered[23 : 0]),
            .gain_r(awb_r),
            .gain_b(awb_b));

    reg [15 : 0] frame_r = 256, frame_b = 256;
    always @(posedge clk) begin
        if (vf[2] && sf[2]) begin
            frame_r <= awb_enable_i ? awb_r : manual_r_i;
            frame_b <= awb_enable_i ? awb_b : manual_b_i;
        end
        if (rst) begin
            frame_r <= 256;
            frame_b <= 256;
        end
    end
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : A
            isp_adjust adjust(clk,
                              filtered[lane * 24 +: 24],
                              (vf[2] && sf[2] ? (awb_enable_i ? {6'd0, awb_r} : {6'd0, manual_r_i})
                                              : frame_r),
                              (vf[2] && sf[2] ? (awb_enable_i ? {6'd0, awb_b} : {6'd0, manual_b_i})
                                              : frame_b),
                              adjust_frame[47 : 32],
                              adjust_frame[63 : 48],
                              adjusted[lane * 24 +: 24]);
        end
    endgenerate
    reg [3 : 0] va = 0, sa = 0, ea = 0;
    reg [11 : 0] rowa[0 : 3];
    integer i;
    always @(posedge clk) begin
        va <= {va[2 : 0], vf[2]};
        sa <= {sa[2 : 0], sf[2]};
        ea <= {ea[2 : 0], ef[2]};
        rowa[0] <= rowf[2];
        for (i = 1; i < 4; i = i + 1)
            rowa[i] <= rowa[i - 1];
        if (rst)
            va <= 0;
    end

    wire [15 : 0] histogram_strength = (proc_adjust[2 : 0] == 1 || !proc_adjust[33])
                                           ? 16'd0
                                           : proc_adjust[303 : 288];
    wire [383 : 0] selected_enhance = {enhance_adjust[383 : 48], histogram_strength,
                                       enhance_adjust[31 : 0]};
    isp_rgb_enhance enhance(.clk(clk),
                            .rst(rst),
                            .valid(va[3]),
                            .sof(sa[3]),
                            .eol(ea[3]),
                            .fe(va[3] && ea[3] && rowa[3] == H - 1),
                            .pixels(adjusted),
                            .config_i(selected_enhance),
                            .out_pixels(rgb_pixels),
                            .out_valid(rgb_valid),
                            .out_sof(rgb_sof),
                            .out_eol(rgb_eol),
                            .out_fe(rgb_fe),
                            .out_revision(rgb_revision),
                            .stats_pixels(quality_pixels),
                            .stats_valid(quality_valid),
                            .stats_sof(quality_sof),
                            .stats_eol(quality_eol),
                            .stats_fe(quality_fe));
    assign process_frame_config_o = (rgb_valid && rgb_sof) ? proc_adjust : proc_emitted;
    assign process_frame_revision_o = (rgb_valid && rgb_sof) ? prev_adjust : prev_emitted;
    always @(posedge clk) begin
        if (rgb_valid && rgb_sof) begin
            proc_emitted <= proc_adjust;
            prev_emitted <= prev_adjust;
        end
        if (rst) begin
            proc_emitted <= PROC_DEFAULT;
            prev_emitted <= 1;
        end
    end
    assign error = f0 | f1;
endmodule

module isp_adjust
    (input wire clk,
     input wire [23 : 0] rgb,
     input wire [15 : 0] rg, bg, contrast,
     input wire signed [15 : 0] brightness,
     output wire [23 : 0] out);
    genvar c;
    generate
        for (c = 0; c < 3; c = c + 1) begin : C
            reg [23 : 0] gained;
            reg signed [24 : 0] centered;
            reg signed [40 : 0] contrasted;
            reg signed [32 : 0] result;
            wire [15 : 0] gain = c == 2 ? rg : c == 0 ? bg : 16'd256;
            always @(posedge clk) begin
                gained <= rgb[c * 8 +: 8] * gain;
                centered <= $signed({1'b0, gained}) - 25'sd32640;
                contrasted <= centered * $signed({1'b0, contrast});
                result <= (contrasted >>> 16) + brightness + 33'sd128;
            end

            assign out[c * 8 +: 8] = result < 0 ? 0 : result > 255 ? 255 : result[7 : 0];
        end
    endgenerate
endmodule

module isp_awb4
    #(parameter W = 1920, H = 1080)
    (input wire clk, rst, enable, valid, sof, last_pixel,
     input wire [23 : 0] rgb,
     output reg [9 : 0] gain_r = 256, gain_b = 256);
    wire [7 : 0] r = rgb[23 : 16], g = rgb[15 : 8], b = rgb[7 : 0];
    wire [7 : 0] hi = r > g ? (r > b ? r : b) : (g > b ? g : b);
    wire [7 : 0] lo = r < g ? (r < b ? r : b) : (g < b ? g : b);
    wire neutral = lo >= 24 && hi <= 232 && hi - lo <= 64;
    reg[3 : 0] sample = 0;
    reg [23 : 0] sum_r = 0, sum_g = 0, sum_b = 0;
    reg [15 : 0] count = 0;
    reg [2 : 0] phase = 0;
    reg [33 : 0] red = 0, blue = 0, target = 0, deadband = 0;
    reg [9 : 0] next_r = 256, next_b = 256;
    always @(posedge clk) begin
        if (valid) begin
            sample <= sample + 1;
            if (sof) begin
                sample <= 1;
                sum_r <= 0;
                sum_g <= 0;
                sum_b <= 0;
                count <= 0;
                gain_r <= next_r;
                gain_b <= next_b;
            end else if (sample == 0 && neutral && enable) begin
                sum_r <= sum_r + r;
                sum_g <= sum_g + g;
                sum_b <= sum_b + b;
                count <= count + 1;
            end
            if (last_pixel)
                phase <= 1;
        end
        case (phase)
            1: begin
                red <= sum_r * gain_r;
                blue <= sum_b * gain_b;
                target <= {2'd0, sum_g, 8'd0};
                deadband <= {8'd0, sum_g, 2'd0};
                phase <= 2;
            end
            2: begin
                if (enable && count >= 256) begin
                    if (red + deadband < target && next_r < 512)
                        next_r <= next_r + 1;
                    else if (red > target + deadband && next_r > 128)
                        next_r <= next_r - 1;
                    if (blue + deadband < target && next_b < 512)
                        next_b <= next_b + 1;
                    else if (blue > target + deadband && next_b > 128)
                        next_b <= next_b - 1;
                end
                phase <= 0;
            end
        endcase
        if (rst) begin
            sample <= 0;
            sum_r <= 0;
            sum_g <= 0;
            sum_b <= 0;
            count <= 0;
            phase <= 0;
            red <= 0;
            blue <= 0;
            target <= 0;
            deadband <= 0;
            gain_r <= 256;
            gain_b <= 256;
            next_r <= 256;
            next_b <= 256;
        end
    end
endmodule
