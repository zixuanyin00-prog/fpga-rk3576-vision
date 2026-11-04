

`timescale 1ns / 1ps
module rgb_roi_source
    #(parameter SW = 1920, SH = 1080, W = 512, H = 288, MAX_PIXELS = 518400, FIFO_BITS = 13,
                QUAD = 0, FULL_FRAME = 0, EXTERNAL_QUEUE = 0, EXTERNAL_SOURCE_ID = 0,
                FRAME_ARBITRATION = 0)
    (output wire q_wen, q_reset, q_ready,
     output wire [101 : 0] q_wdata,
     input wire q_full, q_valid,
     input wire [101 : 0] q_data,
     input wire [31 : 0] source_id_i, enhance_revision_i, processing_revision_i, epoch_i,
     output reg [31 : 0] frame_enhance_revision = 0, frame_processing_revision = 0, frame_epoch = 0,
     output wire [31 : 0] current_source_id,
     input wire clk, rst,
     input wire command_valid, command_roi,
     input wire [11 : 0] command_x, command_y, command_width, command_height,
     input wire [31 : 0] command_id,
     output wire command_ready,
     input wire frame_grant,
     output wire frame_waiting,
     input wire [95 : 0] pixels,
     input wire valid, sof, eol, fe, source_error,
     output wire [95 : 0] quad_pixels,
     output wire [3 : 0] quad_mask,
     output wire [23 : 0] rgb,
     output wire out_valid, out_last,
     input wire ready,
     output reg done = 0, fault = 0,
     output reg [31 : 0] result_id = 0, source_frame_id = 0,
     output reg [11 : 0] result_width = W, result_height = H,
     output reg [23 : 0] result_pixels = W * H,
     output reg result_roi = 0,
     output reg [11 : 0] result_x = 0, result_y = 0,
     output wire [5 : 0] fault_events_o,
     output wire [95 : 0] fault_context_o);
    reg pending = 0, active = 0, draining = 0, bad = 0;
    reg [31 : 0] sensor_id = 0;
    assign current_source_id = sensor_id;
    reg [11 : 0] x = 0, y = 0;
    reg [15 : 0] phase_x = 0, phase_y = 0;
    reg [23 : 0] beats = 0;
    assign frame_waiting = pending;
    wire start = pending && valid && sof && (!FRAME_ARBITRATION || frame_grant);
    wire capturing = active || start;
    wire [11 : 0] cx = start ? 0 : x, cy = start ? 0 : y;
    wire [15 : 0] px = start ? 0 : phase_x, py = start ? 0 : phase_y;
    wire [17 : 0] next_phase_x = {2'b0, phase_x} + 4 * W;
    wire [3 : 0] mask;
    genvar k;
    generate
        for (k = 0; k < 4; k = k + 1) begin : SELECT
            wire [16 : 0] phase = px + k * W;
            wire [16 : 0] reduced = phase >= SW ? phase - SW : phase;
            wire [12 : 0] column = {1'b0, cx} + k;
            assign mask[k] = FULL_FRAME ? 1'b1
                             : result_roi
                                 ? (column >= result_x && column < result_x + result_width &&
                                    cy >= result_y && cy < result_y + result_height)
                                 : (reduced < W && py < H);
        end
    endgenerate
    reg marker = 0, marker_good = 0;
    wire full, fv, pop;
    wire [101 : 0] packet;
    wire write_pixels = valid && capturing && mask != 0;
    wire wen = write_pixels || marker;
    wire [101 : 0] wdata = marker ? {(marker_good ? 2'b10 : 2'b11), 100'd0} : {2'b00, mask, pixels};
    assign q_wen = wen && !fault;
    assign q_reset = rst;
    assign q_wdata = wdata;
    assign q_ready = pop;
    generate
        if (EXTERNAL_QUEUE) begin : DDR_QUEUE
            assign full = q_full;
            assign fv = q_valid;
            assign packet = q_data;
        end else begin : LOCAL_QUEUE
            camera_hist_fifo #(.WIDTH(102),
                               .ABITS(FIFO_BITS))
                queue(.wclk(clk),
                      .wrst(rst),
                      .wen(wen && !fault),
                      .wdata(wdata),
                      .full(full),
                      .rclk(clk),
                      .rrst(rst),
                      .ready(pop),
                      .valid(fv),
                      .data(packet));
        end
    endgenerate
    reg [1 : 0] lane = 0;
    reg [23 : 0] sent = 0;
    wire [3 : 0] remaining = packet[99 : 96] & (4'hf << lane);
    wire [1 : 0] index = remaining[0] ? 0 : remaining[1] ? 1 : remaining[2] ? 2 : 3;
    wire last_lane = (remaining & (4'hf << (index + 1))) == 0;
    assign quad_pixels = packet[95 : 0];
    assign quad_mask = packet[99 : 96];
    wire [2 : 0] selected_count = {2'd0, packet[96]} + {2'd0, packet[97]} + {2'd0, packet[98]} +
                                  {2'd0, packet[99]};
    assign rgb = packet[index * 24 +: 24];
    assign out_valid = fv && !packet[101] && remaining != 0 && !fault && sent < result_pixels;
    assign out_last = QUAD ? (sent + selected_count == result_pixels) : (sent == result_pixels - 1);
    wire advance = out_valid && ready;
    assign pop = fv && (packet[101] || (advance && (QUAD || last_lane)));
    assign command_ready = !pending && !active && !draining && !fault && !full;

    wire invalid_command =
             command_id == 0 || (command_roi && (command_width == 0 || command_height == 0 ||
                                                 command_width * command_height > MAX_PIXELS ||
                                                 {1'b0, command_x} + {1'b0, command_width} > SW ||
                                                 {1'b0, command_y} + {1'b0, command_height} > SH));
    assign fault_events_o = {!rst && command_valid && command_ready && invalid_command,
                             !rst && fv && pop && packet[101] && sent != result_pixels,
                             !rst && fv && pop && packet[101] && packet[100],
                             !rst && marker && write_pixels,
                             capturing && source_error,
                             !rst && wen && full};
    assign fault_context_o = {8'd0, beats, 8'd0, y, x, 8'd0, sent};
    always @(posedge clk) begin
        done <= 0;
        marker <= 0;
        if (valid && sof)
            sensor_id <= sensor_id + 1;
        if (command_valid && command_ready) begin
            if (invalid_command)
                fault <= 1;
            else begin
                result_width <= command_roi ? command_width : W;
                result_height <= command_roi ? command_height : H;
                result_pixels <= command_roi ? command_width * command_height : W * H;
                pending <= 1;
                result_id <= command_id;
                result_roi <= command_roi;
                result_x <= command_roi ? command_x : 0;
                result_y <= command_roi ? command_y : 0;
            end
        end
        if ((wen && full) || (capturing && source_error) || (marker && write_pixels))
            fault <= 1;
        if (start) begin
            frame_enhance_revision <= enhance_revision_i;
            frame_processing_revision <= processing_revision_i;
            frame_epoch <= epoch_i;
            pending <= 0;
            active <= 1;
            draining <= 1;
            bad <= eol;
            source_frame_id <= EXTERNAL_SOURCE_ID ? source_id_i + 1 : sensor_id + 1;
            x <= 4;
            y <= 0;
            beats <= 1;
            phase_x <= 4 * W >= SW ? 4 * W - SW : 4 * W;
            phase_y <= 0;
        end else if (active && valid) begin
            beats <= beats + 1;
            if (sof || y >= SH || x >= SW)
                bad <= 1;
            if (eol) begin
                if (x != SW - 4)
                    bad <= 1;
                x <= 0;
                y <= y + 1;
                phase_x <= 0;
                phase_y <= phase_y + H >= SH ? phase_y + H - SH : phase_y + H;
            end else begin
                x <= x + 4;
                phase_x <= next_phase_x >= 2 * SW ? next_phase_x - 2 * SW
                           : next_phase_x >= SW   ? next_phase_x - SW
                                                  : next_phase_x;
            end
        end
        if (active && fe) begin
            active <= 0;
            marker <= 1;
            marker_good <=
                !bad && !source_error &&
                ((beats == SW * SH / 4 && x == 0 && y == SH && !valid) ||
                 (beats == SW * SH / 4 - 1 && x == SW - 4 && y == SH - 1 && valid && eol));
        end
        if (advance && !QUAD)
            lane <= last_lane ? 0 : index + 1;
        if (out_valid && ready)
            sent <= sent + (QUAD ? selected_count : 1);
        if (fv && pop && packet[101]) begin
            if (packet[100] || sent != result_pixels)
                fault <= 1;
            else if (!fault)
                done <= 1;
            draining <= 0;
            sent <= 0;
            lane <= 0;
        end
        if (rst) begin
            frame_enhance_revision <= 0;
            frame_processing_revision <= 0;
            frame_epoch <= 0;
            result_width <= W;
            result_height <= H;
            result_pixels <= W * H;
            pending <= 0;
            active <= 0;
            draining <= 0;
            bad <= 0;
            fault <= 0;
            done <= 0;
            sensor_id <= 0;
            source_frame_id <= 0;
            result_id <= 0;
            result_roi <= 0;
            result_x <= 0;
            result_y <= 0;
            x <= 0;
            y <= 0;
            phase_x <= 0;
            phase_y <= 0;
            beats <= 0;
            marker <= 0;
            marker_good <= 0;
            lane <= 0;
            sent <= 0;
        end
    end
endmodule
