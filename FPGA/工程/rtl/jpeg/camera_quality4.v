

module camera_quality4
    (input wire clk, rst, valid, sof, eol, fe, source_error,
     input wire [95 : 0] pixels,
     input wire [31 : 0] source_id, origin,
     input wire freeze,
     input wire [6 : 0] select,
     output reg [31 : 0] data);
    reg [11 : 0] x = 0, y = 0, xlo = 0, xhi = 512, ylo = 0, yhi = 288;
    reg [31 : 0] frame_origin = 0;
    reg bad = 0;
    reg [95 : 0] p0 = 0;
    reg [3 : 0] mask0 = 0, mask1 = 0, clip1 = 0;
    reg v0 = 0, s0 = 0, f0 = 0, b0 = 0, v1 = 0, s1 = 0, f1 = 0, b1 = 0, v2 = 0, s2 = 0, f2 = 0,
        b2 = 0;
    reg [31 : 0] id0 = 0, id1 = 0, id2 = 0, origin0 = 0, origin1 = 0, origin2 = 0;
    reg [7 : 0] luma1[0 : 3];
    wire [9 : 0] lane_luma[0 : 3];
    genvar k;
    generate
        for (k = 0; k < 4; k = k + 1) begin : LUMA
            assign lane_luma[k] = {2'b0, p0[k * 24 + 16 +: 8]} + {1'b0, p0[k * 24 + 8 +: 8], 1'b0} +
                                  {2'b0, p0[k * 24 +: 8]};
        end
    endgenerate
    reg [2 : 0] n2 = 0, d2 = 0, c2 = 0;
    reg [9 : 0] total2 = 0;
    reg [8 : 0] diff2 = 0;
    reg [1 : 0] np2 = 0;
    reg [2 : 0] h2[0 : 7];
    reg [17 : 0] count = 0, dark = 0, clip = 0, hist[0 : 7];
    reg [25 : 0] luma_sum = 0;
    reg [24 : 0] diff_sum = 0;
    reg [16 : 0] pairs = 0;
    reg [31 : 0] snapshot[0 : 23];
    reg have_frame = 0;
    reg [31 : 0] serial = 0;
    wire [3 : 0] dark1 = {luma1[3] < 24, luma1[2] < 24, luma1[1] < 24, luma1[0] < 24} & mask1;
    wire [3 : 0] clipped1 = clip1 & mask1;
    wire pair_a = mask1[0] & mask1[1], pair_b = mask1[2] & mask1[3];
    wire [7 : 0] da = luma1[0] > luma1[1] ? luma1[0] - luma1[1] : luma1[1] - luma1[0];
    wire [7 : 0] db = luma1[2] > luma1[3] ? luma1[2] - luma1[3] : luma1[3] - luma1[2];
    function [2 : 0] pop4;
        input [3 : 0] a;
        begin
            pop4 = {2'd0, a[0]} + {2'd0, a[1]} + {2'd0, a[2]} + {2'd0, a[3]};
        end
    endfunction
    integer i;
    always @(posedge clk) begin

        p0 <= pixels;
        v0 <= valid;
        s0 <= sof;
        f0 <= fe;
        b0 <= bad | source_error;
        id0 <= source_id;
        origin0 <= sof ? origin : frame_origin;
        for (i = 0; i < 4; i = i + 1) begin
            if (sof)
                mask0[i] <= origin[27 : 16] == 0 && origin[11 : 0] <= i;
            else
                mask0[i] <= (x + i >= xlo && x + i < xhi && y >= ylo && y < yhi);
        end
        if (valid) begin
            if (sof) begin
                frame_origin <= origin;
                xlo <= origin[11 : 0];
                xhi <= origin[11 : 0] + 12'd512;
                ylo <= origin[27 : 16];
                yhi <= origin[27 : 16] + 12'd288;
                x <= 4;
                y <= 0;
                bad <= source_error;
                b0 <= source_error;
            end else begin
                if (eol) begin
                    x <= 0;
                    y <= y + 1;
                end else
                    x <= x + 4;
                if (x >= 1920 || y >= 1080 || (eol && x != 1916)) begin
                    bad <= 1;
                    b0 <= 1;
                end
            end
        end
        if (source_error)
            bad <= 1;

        v1 <= v0;
        s1 <= s0;
        f1 <= f0;
        b1 <= b0;
        id1 <= id0;
        origin1 <= origin0;
        mask1 <= mask0;
        for (i = 0; i < 4; i = i + 1) begin
            luma1[i] <= lane_luma[i][9 : 2];
            clip1[i] <=
                p0[i * 24 +: 8] >= 250 || p0[i * 24 + 8 +: 8] >= 250 || p0[i * 24 + 16 +: 8] >= 250;
        end

        v2 <= v1;
        s2 <= s1;
        f2 <= f1;
        b2 <= b1;
        id2 <= id1;
        origin2 <= origin1;
        n2 <= pop4(mask1);
        d2 <= pop4(dark1);
        c2 <= pop4(clipped1);
        total2 <= ({2'd0, luma1[0]} & {10{mask1[0]}}) + ({2'd0, luma1[1]} & {10{mask1[1]}}) +
                  ({2'd0, luma1[2]} & {10{mask1[2]}}) + ({2'd0, luma1[3]} & {10{mask1[3]}});
        diff2 <= ({1'b0, da} & {9{pair_a}}) + ({1'b0, db} & {9{pair_b}});
        np2 <= {1'b0, pair_a} + {1'b0, pair_b};
        for (i = 0; i < 8; i = i + 1)
            h2[i] <= pop4({(luma1[3][7 : 5] == i && mask1[3]), (luma1[2][7 : 5] == i && mask1[2]),
                           (luma1[1][7 : 5] == i && mask1[1]), (luma1[0][7 : 5] == i && mask1[0])});

        if (v2) begin
            if (s2) begin
                have_frame <= 1;
                count <= n2;
                luma_sum <= total2;
                dark <= d2;
                clip <= c2;
                diff_sum <= diff2;
                pairs <= np2;
                for (i = 0; i < 8; i = i + 1)
                    hist[i] <= h2[i];
            end else begin
                count <= count + n2;
                luma_sum <= luma_sum + total2;
                dark <= dark + d2;
                clip <= clip + c2;
                diff_sum <= diff_sum + diff2;
                pairs <= pairs + np2;
                for (i = 0; i < 8; i = i + 1)
                    hist[i] <= hist[i] + h2[i];
            end
        end
        if (f2 && have_frame) begin
            have_frame <= 0;
            if (!freeze) begin
                serial <= serial + 1;
                snapshot[0] <= 32'h31545351;
                snapshot[1] <= serial + 1;
                snapshot[2] <= id2;
                snapshot[3] <= origin2;
                snapshot[4] <= count + (v2 ? n2 : 0);
                snapshot[5] <= luma_sum + (v2 ? total2 : 0);
                snapshot[6] <= dark + (v2 ? d2 : 0);
                snapshot[7] <= clip + (v2 ? c2 : 0);
                snapshot[8] <= diff_sum + (v2 ? diff2 : 0);
                snapshot[9] <= pairs + (v2 ? np2 : 0);
                snapshot[10] <= {31'd0, b2};
                snapshot[11] <= 32'h01200200;
                for (i = 0; i < 8; i = i + 1)
                    snapshot[12 + i] <= hist[i] + (v2 ? h2[i] : 0);
                snapshot[20] <= 1;
            end
        end
        if (rst) begin
            x <= 0;
            y <= 0;
            xlo <= 0;
            xhi <= 512;
            ylo <= 0;
            yhi <= 288;
            frame_origin <= 0;
            bad <= 0;
            p0 <= 0;
            mask0 <= 0;
            mask1 <= 0;
            clip1 <= 0;
            v0 <= 0;
            s0 <= 0;
            f0 <= 0;
            b0 <= 0;
            v1 <= 0;
            s1 <= 0;
            f1 <= 0;
            b1 <= 0;
            v2 <= 0;
            s2 <= 0;
            f2 <= 0;
            b2 <= 0;
            id0 <= 0;
            id1 <= 0;
            id2 <= 0;
            origin0 <= 0;
            origin1 <= 0;
            origin2 <= 0;
            have_frame <= 0;
            count <= 0;
            luma_sum <= 0;
            dark <= 0;
            clip <= 0;
            diff_sum <= 0;
            pairs <= 0;
            serial <= 0;
            n2 <= 0;
            d2 <= 0;
            c2 <= 0;
            total2 <= 0;
            diff2 <= 0;
            np2 <= 0;
            for (i = 0; i < 4; i = i + 1)
                luma1[i] <= 0;
            for (i = 0; i < 8; i = i + 1) begin
                hist[i] <= 0;
                h2[i] <= 0;
            end
            for (i = 0; i < 24; i = i + 1)
                snapshot[i] <= 0;
        end
    end
    always @*
        data = select < 24 ? snapshot[select] : 0;
endmodule
