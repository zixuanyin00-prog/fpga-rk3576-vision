

`timescale 1ns / 1ps
module shared_image_process4
    #(parameter W = 1920, H = 1080)
    (input wire clk, rst, valid, sof, eol, fe,
     input wire [95 : 0] rgb,
     input wire [319 : 0] process_config_i,
     input wire [31 : 0] processing_revision_i, enhance_revision_i,
     input wire [6 : 0] select_i,
     output reg [31 : 0] data_o = 0,
     output reg [31 : 0] frame_o = 0,
     output reg [95 : 0] pixels = 0,
     output reg ov = 0, os = 0, oe = 0, ofe = 0,
     output reg [31 : 0] revision_o = 0, enhance_revision_o = 0,
     output reg [319 : 0] process_effective_config_o = 0,
     output wire fault_o);
    function [31 : 0] effective_mask;
        input [319 : 0] c;
        reg [31 : 0] m;
        begin
            m = c[63 : 32] & 32'h00000fff;
            if (c[31 : 0] == 1)
                m = 0;
            if (!m[2]) begin
                m[3] = 0;
                m[7] = 0;
                m[11] = 0;
            end
            if (c[98 : 96] == 0)
                m[3] = 0;
            if (!m[4]) begin
                m[5] = 0;
                m[10] = 0;
            end
            if (c[194 : 192] == 0)
                m[9] = 0;
            if (!(m[4] || m[11]))
                m[6] = 0;
            effective_mask = m;
        end
    endfunction
    function
        signed [10 : 0] gray_delta;
        input [7 : 0] original, filtered, limit;
        input [2 : 0] operation, quarters;
        input enabled;
        reg signed [10 : 0] d, bound;
        reg signed [12 : 0] triple;
        begin
            d = $signed({3'd0, filtered}) - $signed({3'd0, original});
            if (operation == 3 || operation == 4)
                d = -d;
            bound = $signed({3'd0, limit});
            if (d > bound)
                d = bound;
            else if (d < -bound)
                d = -bound;
            triple = $signed(d) + ($signed(d) <<< 1);
            if (!enabled)
                gray_delta = 0;
            else
                case (quarters)
                    1:
                        gray_delta = d >>> 2;
                    2:
                        gray_delta = d >>> 1;
                    3:
                        gray_delta = triple >>> 2;
                    4:
                        gray_delta = d;
                    default:
                        gray_delta = 0;
                endcase
        end
    endfunction
    function [7 : 0] add_clipped;
        input [7 : 0] value;
        input signed [10 : 0] delta;
        reg signed [11 : 0] result;
        begin
            result = $signed({4'd0, value}) + delta;
            add_clipped = result < 0 ? 8'd0 : result > 255 ? 8'd255 : result[7 : 0];
        end
    endfunction
    function [7 : 0] quarter_scale;
        input [7 : 0] value;
        input [2 : 0] quarters;
        reg [9 : 0] triple;
        begin
            triple = {2'd0, value} + {1'd0, value, 1'b0};
            case (quarters)
                1:
                    quarter_scale = value >> 2;
                2:
                    quarter_scale = value >> 1;
                3:
                    quarter_scale = triple >> 2;
                4:
                    quarter_scale = value;
                default:
                    quarter_scale = 0;
            endcase
        end
    endfunction
    function [7 : 0] quarter_mix;
        input [7 : 0] background, ink;
        input [2 : 0] quarters;
        reg [9 : 0] sum;
        begin
            case (quarters)
                1:
                    sum = {2'd0, background} + {1'd0, background, 1'b0} + {2'd0, ink};
                2:
                    sum = {1'd0, background, 1'b0} + {1'd0, ink, 1'b0};
                3:
                    sum = {2'd0, background} + {2'd0, ink} + {1'd0, ink, 1'b0};
                4:
                    sum = {ink, 2'd0};
                default:
                    sum = {background, 2'd0};
            endcase
            quarter_mix = sum[9 : 2];
        end
    endfunction

    reg [319 : 0] input_config = 0;
    reg [31 : 0] input_revision = 0, input_enhance = 0;
    reg input_fault = 0;
    reg [11 : 0] input_x = 0, input_y = 0;
    wire [11 : 0] ix = sof ? 12'd0 : input_x, iy = sof ? 12'd0 : input_y;
    always @(posedge clk) begin
        if (valid) begin
            input_x <= eol ? 0 : ix + 4;
            input_y <= eol ? iy + 1 : iy;
            if (eol != (ix == W - 4) || fe != (eol && iy == H - 1) || iy >= H)
                input_fault <= 1;
            if (sof) begin
                input_config <= {process_config_i[319 : 64], effective_mask(process_config_i),
                                 process_config_i[31 : 0]};
                input_revision <= processing_revision_i;
                input_enhance <= enhance_revision_i;
            end
        end
        if (rst) begin
            input_x <= 0;
            input_y <= 0;
            input_fault <= 0;
            input_config <= 0;
            input_revision <= 0;
            input_enhance <= 0;
        end
    end

    reg [15 : 0] yr[0 : 3], yg[0 : 3], yb[0 : 3];
    reg [31 : 0] luma = 0;
    reg [95 : 0] rgb0 = 0, rgb1 = 0;
    reg [1 : 0] lv = 0, ls = 0, le = 0;
    genvar lane, stage;
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : LUMA
            wire [15 : 0] r = {8'd0, rgb[lane * 24 + 16 +: 8]}, g = {8'd0, rgb[lane * 24 + 8 +: 8]},
                          b = {8'd0, rgb[lane * 24 +: 8]};
            always @(posedge clk) begin
                yr[lane] <= ((r << 6) + (r << 3)) + ((r << 2) + r);
                yg[lane] <= ((g << 7) + (g << 4)) + ((g << 2) + (g << 1));
                yb[lane] <= ((b << 4) + (b << 3)) + ((b << 2) + b);
                luma[lane * 8 +: 8] <= (yr[lane] + yg[lane] + yb[lane]) >> 8;
            end
        end
    endgenerate
    always @(posedge clk) begin
        rgb0 <= rgb;
        rgb1 <= rgb0;
        lv <= {lv[0], valid};
        ls <= {ls[0], sof};
        le <= {le[0], eol};
        if (rst) begin
            lv <= 0;
            ls <= 0;
            le <= 0;
        end
    end
    wire [127 : 0] packed0;
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : PACK
            assign packed0[lane * 32 +: 32] = {luma[lane * 8 +: 8], rgb1[lane * 24 +: 24]};
        end
    endgenerate
    wire [179 : 0] stage_pixels[0 : 3];
    wire [3 : 0] sv, ss, se, sfault;
    wire [11 : 0] sy[0 : 3];
    wire [319 : 0] scfg[0 : 3];
    wire [31 : 0] srev[0 : 3], senh[0 : 3];
    generate
        for (stage = 0; stage < 4; stage = stage + 1) begin : WINDOW_STAGE
            if (stage == 0) begin : FIRST
                shared_process_window4 #(.W(W),
                                         .H(H),
                                         .STAGE(0),
                                         .P(32))
                    process(.clk(clk),
                            .rst(rst),
                            .valid(lv[1]),
                            .sof(ls[1]),
                            .eol(le[1]),
                            .data(packed0),
                            .config_i(input_config),
                            .revision_i(input_revision),
                            .enhance_i(input_enhance),
                            .pixels(stage_pixels[0]),
                            .ov(sv[0]),
                            .os(ss[0]),
                            .oe(se[0]),
                            .oy(sy[0]),
                            .config_o(scfg[0]),
                            .revision_o(srev[0]),
                            .enhance_o(senh[0]),
                            .fault_o(sfault[0]));
            end else begin : NEXT
                shared_process_window4 #(.W(W),
                                         .H(H),
                                         .STAGE(stage),
                                         .P(45))
                    process(.clk(clk),
                            .rst(rst),
                            .valid(sv[stage - 1]),
                            .sof(ss[stage - 1]),
                            .eol(se[stage - 1]),
                            .data(stage_pixels[stage - 1]),
                            .config_i(scfg[stage - 1]),
                            .revision_i(srev[stage - 1]),
                            .enhance_i(senh[stage - 1]),
                            .pixels(stage_pixels[stage]),
                            .ov(sv[stage]),
                            .os(ss[stage]),
                            .oe(se[stage]),
                            .oy(sy[stage]),
                            .config_o(scfg[stage]),
                            .revision_o(srev[stage]),
                            .enhance_o(senh[stage]),
                            .fault_o(sfault[stage]));
            end
        end
    endgenerate
    assign fault_o = input_fault | (|sfault);

    reg [2 : 0] fv = 0, fs = 0, fl = 0;
    reg [11 : 0] fy[0 : 2], flags[0 : 2];
    reg [2 : 0] view[0 : 2], alpha[0 : 2], background[0 : 2];
    reg [23 : 0] ink[0 : 2];
    reg [3 : 0] raw_mask[0 : 2], keep_mask[0 : 2], edge_mask[0 : 2], boundary_mask[0 : 2],
                overlay_mask[0 : 2];
    reg [3 : 0] stat_fg[0 : 2], stat_highlight[0 : 2];
    reg [31 : 0] lumap[0 : 2], grayp[0 : 2];
    reg [319 : 0] pending_config = 0;
    reg [31 : 0] pending_revision = 0, pending_enhance = 0;
    reg [95 : 0] color1 = 0, color2 = 0, color3 = 0;
    reg signed [10 : 0] delta1[0 : 3];
    reg [3 : 0] final_edges = 0, final_keep = 0, final_fg = 0, final_highlight = 0;
    reg [31 : 0] final_luma = 0;
    reg final_statistics = 0;
    reg [11 : 0] final_y = 0;
    integer j;
    wire [319 : 0] active_config = scfg[3];
    wire [11 : 0] active_flags = active_config[43 : 32];
    always @(posedge clk) begin
        fv <= {fv[1 : 0], sv[3]};
        fs <= {fs[1 : 0], ss[3]};
        fl <= {fl[1 : 0], se[3]};
        fy[0] <= sy[3];
        flags[0] <= active_flags;
        view[0] <= active_config[2 : 0];
        alpha[0] <= active_config[186 : 184];
        background[0] <= active_config[189 : 187];
        ink[0] <= active_config[183 : 160];
        for (j = 1; j < 3; j = j + 1) begin
            fy[j] <= fy[j - 1];
            flags[j] <= flags[j - 1];
            view[j] <= view[j - 1];
            alpha[j] <= alpha[j - 1];
            background[j] <= background[j - 1];
            ink[j] <= ink[j - 1];
            raw_mask[j] <= raw_mask[j - 1];
            keep_mask[j] <= keep_mask[j - 1];
            edge_mask[j] <= edge_mask[j - 1];
            boundary_mask[j] <= boundary_mask[j - 1];
            overlay_mask[j] <= overlay_mask[j - 1];
            stat_fg[j] <= stat_fg[j - 1];
            stat_highlight[j] <= stat_highlight[j - 1];
            lumap[j] <= lumap[j - 1];
            grayp[j] <= grayp[j - 1];
        end
        if (sv[3] && ss[3]) begin
            pending_config <= active_config;
            pending_revision <= srev[3];
            pending_enhance <= senh[3];
        end
        ov <= fv[2];
        os <= fv[2] && fs[2];
        oe <= fv[2] && fl[2];
        ofe <= fv[2] && fl[2] && fy[2] == H - 1;
        final_y <= fy[2];
        final_luma <= lumap[2];
        final_edges <= edge_mask[2];
        final_keep <= keep_mask[2];
        final_fg <= stat_fg[2];
        final_highlight <= stat_highlight[2];
        final_statistics <= flags[2][8];
        if (fv[2] && fs[2]) begin
            process_effective_config_o <= pending_config;
            revision_o <= pending_revision;
            enhance_revision_o <= pending_enhance;
        end
        if (rst) begin
            fv <= 0;
            fs <= 0;
            fl <= 0;
            ov <= 0;
            os <= 0;
            oe <= 0;
            ofe <= 0;
            final_statistics <= 0;
            process_effective_config_o <= 0;
            revision_o <= 0;
            enhance_revision_o <= 0;
        end
    end
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : COMPOSE
            wire [44 : 0] center = stage_pixels[3][lane * 45 +: 45];
            wire gated_edge = center[44] && (!active_config[144] || !active_flags[2] || center[34]);
            always @(posedge clk) begin
                color1[lane * 24 +: 24] <= center[23 : 0];
                delta1[lane] <=
                    gray_delta(center[31 : 24], center[42 : 35], active_config[215 : 208],
                               active_config[194 : 192], active_config[202 : 200], active_flags[9]);
                raw_mask[0][lane] <= center[33];
                keep_mask[0][lane] <= center[34];
                edge_mask[0][lane] <= gated_edge;
                boundary_mask[0][lane] <= center[43];
                overlay_mask[0][lane] <= gated_edge || center[43];
                lumap[0][lane * 8 +: 8] <= center[31 : 24];
                grayp[0][lane * 8 +: 8] <= center[42 : 35];
                stat_fg[0][lane] <= center[31 : 24] >= active_config[231 : 224];
                stat_highlight[0][lane] <= center[31 : 24] >= active_config[239 : 232];
                case (view[2])
                    2:
                        pixels[lane * 24 +: 24] <= {24{raw_mask[2][lane]}};
                    3:
                        pixels[lane * 24 +: 24] <= {24{keep_mask[2][lane]}};
                    4:
                        pixels[lane * 24 +: 24] <= {24{edge_mask[2][lane]}};
                    5:
                        pixels[lane * 24 +: 24] <= {3{grayp[2][lane * 8 +: 8]}};
                    6:
                        pixels[lane * 24 +: 24] <= {24{boundary_mask[2][lane]}};
                    default:
                        pixels[lane * 24 +: 24] <=
                            flags[2][6] && overlay_mask[2][lane]
                                ? {quarter_mix(color3[lane * 24 + 16 +: 8], ink[2][23 : 16],
                                               alpha[2]),
                                   quarter_mix(color3[lane * 24 + 8 +: 8], ink[2][15 : 8],
                                               alpha[2]),
                                   quarter_mix(color3[lane * 24 +: 8], ink[2][7 : 0], alpha[2])}
                                : color3[lane * 24 +: 24];
                endcase
            end
            genvar ch;
            for (ch = 0; ch < 3; ch = ch + 1) begin : CHANNEL
                always @(posedge clk) begin
                    color2[lane * 24 + ch * 8 +: 8] <=
                        add_clipped(color1[lane * 24 + ch * 8 +: 8], delta1[lane]);
                    color3[lane * 24 + ch * 8 +: 8] <=
                        flags[1][7] && !keep_mask[1][lane]
                            ? quarter_scale(color2[lane * 24 + ch * 8 +: 8], background[1])
                            : color2[lane * 24 + ch * 8 +: 8];
                end
            end
        end
    endgenerate

    reg [16 : 0] counts[0 : 15], edge_count[0 : 15], fg_count[0 : 15], highlight_count[0 : 15],
                 keep_count[0 : 15];
    reg [24 : 0] brightness[0 : 15];
    reg [16 : 0] snap_count[0 : 15], snap_edge[0 : 15], snap_fg[0 : 15], snap_highlight[0 : 15],
                 snap_keep[0 : 15];
    reg [24 : 0] snap_luma[0 : 15];
    reg [31 : 0] snap_special[0 : 15];
    reg [11 : 0] x = 0;
    wire [11 : 0] px = os ? 12'd0 : x;
    wire [1 : 0] tx = px < W / 4 ? 0 : px < W / 2 ? 1 : px < 3 * W / 4 ? 2 : 3;
    wire [1 : 0] ty = final_y < H / 4 ? 0 : final_y < H / 2 ? 1 : final_y < 3 * H / 4 ? 2 : 3;
    wire [3 : 0] tile = {ty, tx};
    wire [9 : 0] sum_luma = {2'd0, final_luma[7 : 0]} + {2'd0, final_luma[15 : 8]} +
                            {2'd0, final_luma[23 : 16]} + {2'd0, final_luma[31 : 24]};
    wire [2 : 0] sum_edge = {2'd0, final_edges[0]} + {2'd0, final_edges[1]} +
                            {2'd0, final_edges[2]} + {2'd0, final_edges[3]};
    wire [2 : 0] sum_fg = {2'd0, final_fg[0]} + {2'd0, final_fg[1]} + {2'd0, final_fg[2]} +
                          {2'd0, final_fg[3]};
    wire [2 : 0] sum_highlight = {2'd0, final_highlight[0]} + {2'd0, final_highlight[1]} +
                                 {2'd0, final_highlight[2]} + {2'd0, final_highlight[3]};
    wire [2 : 0] sum_keep = {2'd0, final_keep[0]} + {2'd0, final_keep[1]} + {2'd0, final_keep[2]} +
                            {2'd0, final_keep[3]};
    wire [1 : 0] first = final_keep[0] ? 0 : final_keep[1] ? 1 : final_keep[2] ? 2 : 3;
    wire [1 : 0] last = final_keep[3] ? 3 : final_keep[2] ? 2 : final_keep[1] ? 1 : 0;
    reg [20 : 0] total = 0;
    reg [11 : 0] minx = 0, miny = 0, maxx = 0, maxy = 0;
    reg publish = 0;
    reg [319 : 0] finished_config = 0;
    reg [31 : 0] finished_revision = 0, finished_enhance = 0;
    integer k;
    always @(posedge clk) begin
        publish <= ov && ofe;
        if (ov && ofe) begin
            finished_config <= process_effective_config_o;
            finished_revision <= revision_o;
            finished_enhance <= enhance_revision_o;
        end
        if (ov) begin
            x <= oe ? 0 : px + 4;
            for (k = 0; k < 16; k = k + 1) begin
                if (os) begin
                    counts[k] <= final_statistics && k == tile ? 4 : 0;
                    brightness[k] <= final_statistics && k == tile ? sum_luma : 0;
                    edge_count[k] <= final_statistics && k == tile ? sum_edge : 0;
                    fg_count[k] <= final_statistics && k == tile ? sum_fg : 0;
                    highlight_count[k] <= final_statistics && k == tile ? sum_highlight : 0;
                    keep_count[k] <= final_statistics && k == tile ? sum_keep : 0;
                end else if (final_statistics && k == tile) begin
                    counts[k] <= counts[k] + 4;
                    brightness[k] <= brightness[k] + sum_luma;
                    edge_count[k] <= edge_count[k] + sum_edge;
                    fg_count[k] <= fg_count[k] + sum_fg;
                    highlight_count[k] <= highlight_count[k] + sum_highlight;
                    keep_count[k] <= keep_count[k] + sum_keep;
                end
            end
            if (os) begin
                total <= final_statistics ? sum_keep : 0;
                minx <= sum_keep ? px + first : W;
                maxx <= sum_keep ? px + last : 0;
                miny <= sum_keep ? final_y : H;
                maxy <= sum_keep ? final_y : 0;
            end else if (final_statistics) begin
                total <= total + sum_keep;
                if (sum_keep) begin
                    if (px + first < minx)
                        minx <= px + first;
                    if (px + last > maxx)
                        maxx <= px + last;
                    if (final_y < miny)
                        miny <= final_y;
                    maxy <= final_y;
                end
            end
        end
        if (publish && !fault_o) begin
            frame_o <= frame_o + 1;
            for (k = 0; k < 16; k = k + 1) begin
                snap_count[k] <= counts[k];
                snap_luma[k] <= brightness[k];
                snap_edge[k] <= edge_count[k];
                snap_fg[k] <= fg_count[k];
                snap_highlight[k] <= highlight_count[k];
                snap_keep[k] <= keep_count[k];
            end
            snap_special[0] <= 32'h32444e49;
            snap_special[1] <= 2;
            snap_special[2] <= frame_o + 1;
            snap_special[3] <= {29'd0, fault_o, !finished_config[40], finished_config[40]};
            snap_special[4] <= {7'd0,  (finished_config[81 : 80] == 1), finished_config[71 : 64],
                                11'd0, finished_config[98 : 96],        2'b01};
            snap_special[5] <= W;
            snap_special[6] <= H;
            snap_special[7] <= total;
            snap_special[8] <= total ? minx : 0;
            snap_special[9] <= total ? miny : 0;
            snap_special[10] <= total ? maxx + 1 : 0;
            snap_special[11] <= total ? maxy + 1 : 0;
            snap_special[12] <= 0;
            snap_special[13] <= finished_revision;
            snap_special[14] <= finished_enhance;
            snap_special[15] <= 3;
        end
        if (rst) begin
            frame_o <= 0;
            x <= 0;
            total <= 0;
            publish <= 0;
            minx <= 0;
            miny <= 0;
            maxx <= 0;
            maxy <= 0;
            for (k = 0; k < 16; k = k + 1) begin
                counts[k] <= 0;
                brightness[k] <= 0;
                edge_count[k] <= 0;
                fg_count[k] <= 0;
                highlight_count[k] <= 0;
                keep_count[k] <= 0;
                snap_count[k] <= 0;
                snap_luma[k] <= 0;
                snap_edge[k] <= 0;
                snap_fg[k] <= 0;
                snap_highlight[k] <= 0;
                snap_keep[k] <= 0;
                snap_special[k] <= 0;
            end
        end
    end

    reg [3 : 0] read_tile;
    reg [2 : 0] read_bank;
    reg [2 : 0] bank_q = 7;
    reg [16 : 0] count_q = 0, edge_q = 0, fg_q = 0, highlight_q = 0, keep_q = 0;
    reg [24 : 0] luma_q = 0;
    reg [31 : 0] special_q = 0;
    always @* begin
        read_tile = select_i[3 : 0];
        read_bank = 7;
        if (select_i < 80) begin
            case (select_i)
                0, 1, 2, 3, 4:
                    read_tile = 0;
                5, 6, 7, 8, 9:
                    read_tile = 1;
                10, 11, 12, 13, 14:
                    read_tile = 2;
                15, 16, 17, 18, 19:
                    read_tile = 3;
                20, 21, 22, 23, 24:
                    read_tile = 4;
                25, 26, 27, 28, 29:
                    read_tile = 5;
                30, 31, 32, 33, 34:
                    read_tile = 6;
                35, 36, 37, 38, 39:
                    read_tile = 7;
                40, 41, 42, 43, 44:
                    read_tile = 8;
                45, 46, 47, 48, 49:
                    read_tile = 9;
                50, 51, 52, 53, 54:
                    read_tile = 10;
                55, 56, 57, 58, 59:
                    read_tile = 11;
                60, 61, 62, 63, 64:
                    read_tile = 12;
                65, 66, 67, 68, 69:
                    read_tile = 13;
                70, 71, 72, 73, 74:
                    read_tile = 14;
                default:
                    read_tile = 15;
            endcase
            case (select_i)
                0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75:
                    read_bank = 0;
                1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56, 61, 66, 71, 76:
                    read_bank = 1;
                2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57, 62, 67, 72, 77:
                    read_bank = 2;
                3, 8, 13, 18, 23, 28, 33, 38, 43, 48, 53, 58, 63, 68, 73, 78:
                    read_bank = 3;
                default:
                    read_bank = 4;
            endcase
        end else if (select_i < 96)
            read_bank = 6;
        else if (select_i < 112)
            read_bank = 5;
    end
    always @(posedge clk) begin
        count_q <= snap_count[read_tile];
        luma_q <= snap_luma[read_tile];
        edge_q <= snap_edge[read_tile];
        fg_q <= snap_fg[read_tile];
        highlight_q <= snap_highlight[read_tile];
        keep_q <= snap_keep[read_tile];
        special_q <= snap_special[select_i[3 : 0]];
        bank_q <= read_bank;
        case (bank_q)
            0:
                data_o <= {15'd0, count_q};
            1:
                data_o <= {7'd0, luma_q};
            2:
                data_o <= {15'd0, edge_q};
            3:
                data_o <= {15'd0, fg_q};
            4:
                data_o <= {15'd0, highlight_q};
            5:
                data_o <= {15'd0, keep_q};
            6:
                data_o <= special_q;
            default:
                data_o <= 0;
        endcase
        if (rst) begin
            bank_q <= 7;
            data_o <= 0;
        end
    end
endmodule

module shared_process_window4
    #(parameter W = 1920, H = 1080, STAGE = 0, P = 32)
    (input wire clk, rst, valid, sof, eol,
     input wire [4 * P - 1 : 0] data,
     input wire [319 : 0] config_i,
     input wire [31 : 0] revision_i, enhance_i,
     output reg [179 : 0] pixels = 0,
     output reg ov = 0, os = 0, oe = 0,
     output reg [11 : 0] oy = 0,
     output reg [319 : 0] config_o = 0,
     output reg [31 : 0] revision_o = 0, enhance_o = 0,
     output wire fault_o);
    function [8 : 0] shape_mask;
        input [1 : 0] shape;
        begin
            case (shape)
                1:
                    shape_mask = 9'b010111010;
                2:
                    shape_mask = 9'b000111000;
                3:
                    shape_mask = 9'b010010010;
                default:
                    shape_mask = 9'b111111111;
            endcase
        end
    endfunction
    function threshold;
        input [7 : 0] y, lo, hi;
        input [1 : 0] selection;
        input invert;
        begin
            case (selection)
                1:
                    threshold = y < lo;
                2:
                    threshold = y >= lo && y <= hi;
                default:
                    threshold = y >= lo;
            endcase
            threshold = threshold ^ invert;
        end
    endfunction
    function [3 : 0] neighbour_count;
        input [8 : 0] mask;
        reg [1 : 0] a, b, c, d;
        begin
            a = {1'b0, mask[0]} + {1'b0, mask[1]};
            b = {1'b0, mask[2]} + {1'b0, mask[3]};
            c = {1'b0, mask[5]} + {1'b0, mask[6]};
            d = {1'b0, mask[7]} + {1'b0, mask[8]};
            neighbour_count = {2'd0, a} + {2'd0, b} + {2'd0, c} + {2'd0, d};
        end
    endfunction
    reg [319 : 0] before_config = 0, frame_config = 0;
    reg [31 : 0] before_revision = 0, frame_revision = 0, before_enhance = 0, frame_enhance = 0;
    always @(posedge clk) begin
        if (valid && sof) begin
            before_config <= config_i;
            before_revision <= revision_i;
            before_enhance <= enhance_i;
        end
        if (rst) begin
            before_config <= 0;
            before_revision <= 0;
            before_enhance <= 0;
        end
    end
    wire wv, ws, we;
    wire [11 : 0] wy;
    wire [6 * P - 1 : 0] top, mid, bot;
    isp_window4 #(.W(W),
                  .H(H),
                  .P(P),
                  .REFLECT(0))
        window(clk,
               rst,
               valid,
               sof,
               eol,
               data,
               wv,
               ws,
               we,
               wy,
               top,
               mid,
               bot,
               fault_o);
    wire [319 : 0] cfg = ws ? before_config : frame_config;
    wire [11 : 0] en = cfg[43 : 32];
    wire [2 : 0] binary_op = cfg[98 : 96], gray_op = cfg[194 : 192];
    wire first_max = gray_op == 2 || gray_op == 4;
    wire gray_apply = en[9] && (cfg[196] || STAGE < 2);
    wire gray_max = (cfg[196] ? (STAGE < 2) : (STAGE == 0)) ? first_max : !first_max;
    always @(posedge clk) begin
        if (wv && ws) begin
            frame_config <= before_config;
            frame_revision <= before_revision;
            frame_enhance <= before_enhance;
        end
        if (rst) begin
            frame_config <= 0;
            frame_revision <= 0;
            frame_enhance <= 0;
        end
    end
    reg [3 : 0] vp = 0, sp = 0, ep = 0;
    reg [11 : 0] yp[0 : 3];
    integer j;
    always @(posedge clk) begin
        vp <= {vp[2 : 0], wv};
        sp <= {sp[2 : 0], ws};
        ep <= {ep[2 : 0], we};
        yp[0] <= wy;
        for (j = 1; j < 4; j = j + 1)
            yp[j] <= yp[j - 1];
        ov <= vp[3];
        os <= vp[3] && sp[3];
        oe <= vp[3] && ep[3];
        oy <= yp[3];
        if (vp[3] && sp[3]) begin
            config_o <= frame_config;
            revision_o <= frame_revision;
            enhance_o <= frame_enhance;
        end
        if (rst) begin
            vp <= 0;
            sp <= 0;
            ep <= 0;
            ov <= 0;
            os <= 0;
            oe <= 0;
            config_o <= 0;
            revision_o <= 0;
            enhance_o <= 0;
        end
    end
    genvar lane, n;
    generate
        for (lane = 0; lane < 4; lane = lane + 1) begin : PIXEL
            wire[44 : 0] sample[0 : 8];
            for (n = 0; n < 3; n = n + 1) begin : NEIGHBOUR
                assign sample[n] = {{(45 - P){1'b0}}, top[(lane + n) * P +: P]};
                assign sample[3 + n] = {{(45 - P){1'b0}}, mid[(lane + n) * P +: P]};
                assign sample[6 + n] = {{(45 - P){1'b0}}, bot[(lane + n) * P +: P]};
            end
            wire [71 : 0] gray_window;
            wire [8 : 0] selected, mask_window, edge_window;
            for (n = 0; n < 9; n = n + 1) begin : VALUE
                assign selected[n] = threshold(sample[n][31 : 24], cfg[71 : 64], cfg[79 : 72],
                                               cfg[81 : 80], cfg[82]);
                if (STAGE == 0) begin : INITIAL
                    assign gray_window[n * 8 +: 8] = sample[n][31 : 24];
                    assign mask_window[n] = 1'b0;
                    assign edge_window[n] = 1'b0;
                end else begin : LATER
                    assign gray_window[n * 8 +: 8] = sample[n][42 : 35];
                    assign mask_window[n] = sample[n][34];
                    if (STAGE == 1)
                        assign edge_window[n] = sample[n][32];
                    else
                        assign edge_window[n] = sample[n][44];
                end
            end
            wire [8 : 0] structure = shape_mask(cfg[101 : 100]);
            wire eroded = &(selected | ~structure), dilated = |(selected & structure);
            wire mask_eroded = &(mask_window | ~structure),
                 mask_dilated = |(mask_window & structure);
            wire [7 : 0] gray_result;
            shared_minmax9 gray_tree(.clk(clk),
                                     .values(gray_window),
                                     .maximum(gray_max),
                                     .result(gray_result));
            reg [44 : 0] center[0 : 3];
            reg [3 : 0] maskp = 0, boundaryp = 0, edgep = 0, grayp = 0, rawfgp = 0;
            integer q;
            reg [9 : 0] gx_positive = 0, gx_negative = 0, gy_positive = 0, gy_negative = 0;
            reg signed [10 : 0] gx = 0, gy = 0;
            wire [10 : 0] ax = gx[10] ? -gx : gx, ay = gy[10] ? -gy : gy;
            wire [11 : 0] magnitude = {1'b0, ax} + {1'b0, ay};
            reg [11 : 0] threshold0 = 0, threshold1 = 0;
            reg sobel0 = 0, sobel1 = 0;
            reg [1 : 0] sobelp = 0;
            always @(posedge clk) begin
                center[0] <= sample[4];
                for (q = 1; q < 4; q = q + 1)
                    center[q] <= center[q - 1];
                grayp <= {grayp[2 : 0], gray_apply};
                if (STAGE == 0) begin

                    gx_positive <= ({2'd0, sample[2][31 : 24]} + {2'd0, sample[8][31 : 24]}) +
                                   {1'd0, sample[5][31 : 24], 1'b0};
                    gx_negative <= ({2'd0, sample[0][31 : 24]} + {2'd0, sample[6][31 : 24]}) +
                                   {1'd0, sample[3][31 : 24], 1'b0};
                    gy_positive <= ({2'd0, sample[6][31 : 24]} + {2'd0, sample[8][31 : 24]}) +
                                   {1'd0, sample[7][31 : 24], 1'b0};
                    gy_negative <= ({2'd0, sample[0][31 : 24]} + {2'd0, sample[2][31 : 24]}) +
                                   {1'd0, sample[1][31 : 24], 1'b0};
                    gx <= $signed({1'b0, gx_positive}) - $signed({1'b0, gx_negative});
                    gy <= $signed({1'b0, gy_positive}) - $signed({1'b0, gy_negative});
                    threshold0 <= cfg[139 : 128];
                    threshold1 <= threshold0;
                    sobel0 <= en[4];
                    sobel1 <= sobel0;
                    sobelp <= {sobelp[0], sobel1 && magnitude > threshold1};
                    rawfgp <= {rawfgp[2 : 0], en[2] && selected[4]};
                    maskp <= {maskp[2 : 0], !en[2]                               ? 1'b1
                                            : !en[3]                             ? selected[4]
                                            : (binary_op == 1 || binary_op == 3) ? eroded
                                                                                 : dilated};
                    boundaryp <= 0;
                    edgep <= 0;
                end else begin
                    rawfgp <= {rawfgp[2 : 0], sample[4][33]};
                    if (STAGE == 1) begin
                        maskp <= {maskp[2 : 0], en[3] && binary_op == 3   ? mask_dilated
                                                : en[3] && binary_op == 4 ? mask_eroded
                                                                          : sample[4][34]};
                        edgep <=
                            {edgep[2 : 0], en[5] ? (edge_window[4] &&
                                                    neighbour_count(edge_window) >= cfg[143 : 140])
                                                 : edge_window[4]};
                        boundaryp <= 0;
                    end else if (STAGE == 2) begin
                        maskp <= {maskp[2 : 0], sample[4][34]};
                        edgep <= {edgep[2 : 0], en[10] ? |edge_window : edge_window[4]};
                        boundaryp <=
                            {boundaryp[2 : 0], en[11] && mask_window[4] && !(&mask_window)};
                    end else begin
                        maskp <= {maskp[2 : 0], sample[4][34]};
                        edgep <= {edgep[2 : 0], en[10] ? &edge_window : edge_window[4]};
                        boundaryp <= {boundaryp[2 : 0], sample[4][43]};
                    end
                end
                pixels[lane * 45 +: 45] <= {
                    STAGE == 0 ? sobelp[1] : edgep[3],
                    boundaryp[3],
                    grayp[3] ? gray_result : (STAGE == 0 ? center[3][31 : 24] : center[3][42 : 35]),
                    maskp[3],
                    rawfgp[3],
                    STAGE == 0 ? sobelp[1] : center[3][32],
                    center[3][31 : 0]};
                if (rst) begin
                    maskp <= 0;
                    boundaryp <= 0;
                    edgep <= 0;
                    grayp <= 0;
                    rawfgp <= 0;
                    sobelp <= 0;
                end
            end
        end
    endgenerate
endmodule

module shared_minmax9
    (input wire clk,
     input wire [71 : 0] values,
     input wire maximum,
     output reg [7 : 0] result = 0);
    function [7 : 0] choose;
        input [7 : 0] a, b;
        input largest;
        begin
            choose = largest ? (a > b ? a : b) : (a < b ? a : b);
        end
    endfunction
    reg [7 : 0] a0 = 0, a1 = 0, a2 = 0, a3 = 0, b0 = 0, b1 = 0, c0 = 0, extra0 = 0, extra1 = 0,
                extra2 = 0;
    reg max0 = 0, max1 = 0, max2 = 0;
    always @(posedge clk) begin
        a0 <= choose(values[7 : 0], values[15 : 8], maximum);
        a1 <= choose(values[23 : 16], values[31 : 24], maximum);
        a2 <= choose(values[39 : 32], values[47 : 40], maximum);
        a3 <= choose(values[55 : 48], values[63 : 56], maximum);
        extra0 <= values[71 : 64];
        max0 <= maximum;
        b0 <= choose(a0, a1, max0);
        b1 <= choose(a2, a3, max0);
        extra1 <= extra0;
        max1 <= max0;
        c0 <= choose(b0, b1, max1);
        extra2 <= extra1;
        max2 <= max1;
        result <= choose(c0, extra2, max2);
    end
endmodule
