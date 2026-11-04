

`timescale 1ns / 1ps
module rgb_roi_chain
    #(parameter SW = 1920, SH = 1080, W = 512, H = 288, WATCHDOG = 250000000, AUTO_PREVIEW = 0,
                PREVIEW_QUAD = 0, AUTO_NATIVE = 0, SLOT_COUNT = 4, SLOT_STRIDE = 'h600000,
                META_OFFSET = 'h5ff000, STOP_OFFSET = 'h17ffff0, EXTERNAL_QUEUE = 0,
                EXTERNAL_SOURCE_ID = 0, CHANNEL_ID = 1, PROTOCOL = 8, DIAGNOSTIC_CONTEXT = 0,
                FRAME_ARBITRATION = 0)
    (output wire q_wen, q_reset, q_ready,
     output wire [101 : 0] q_wdata,
     input wire q_full, q_valid,
     input wire [101 : 0] q_data,
     input wire [31 : 0] source_id_i, enhance_revision_i, processing_revision_i, epoch_i,
     output wire [31 : 0] current_source_id,
     input wire clk, rst, enable, source_error,
     input wire [31 : 0] base,
     input wire [95 : 0] pixels,
     input wire valid, sof, eol, fe,
     input wire command_valid, command_roi, command_continuous,
     input wire [31 : 0] command_id, command_origin, command_size,
     output wire command_ready,
     input wire frame_grant,
     output wire frame_waiting,
     output wire [127 : 0] tx_data_o,
     output wire [63 : 0] tx_addr_o,
     output wire [12 : 0] tx_bytes_o,
     output wire [7 : 0] tx_be_o,
     output wire tx_hv_o, tx_dv_o, tx_eot_o,
     input wire tx_halt_i,
     output wire busy_o, stopped_o, fault_o,
     output wire [3 : 0] fault_code_o,
     output wire [31 : 0] completed_id_o, timer_o,
     output wire [63 : 0] debug_o,
     output reg [15 : 0] diagnostic_cause_o = 0,
     output wire [127 : 0] diagnostic_context_o,
     output wire diagnostic_event_o);
    wire reset_chain, allowed, dma_busy, dma_fault;
    wire [3 : 0] dma_code;
    wire source_ready, source_done, source_fault, sv, sl, sr;
    wire [5 : 0] source_fault_events;
    wire [95 : 0] source_fault_context;
    wire [31 : 0] frame_enhance_revision, frame_processing_revision, frame_epoch;
    wire [95 : 0] quad_pixels;
    wire [3 : 0] quad_mask;
    wire [23 : 0] rgb;
    wire [31 : 0] request_id, sensor_id;
    wire roi;
    wire [11 : 0] x, y, frame_width, frame_height;
    wire [23 : 0] frame_pixels;
    reg inflight = 0, approved = 0, timeout_fault = 0;
    reg [31 : 0] auto_id = 1;

    reg command_pending = 0, stream_active = 0, pending_stream = 0, pending_roi = 0, active_roi = 0;
    reg [31 : 0] pending_id = 0, pending_origin = 0, pending_size = 0, active_id = 0,
                 active_origin = 0, active_size = 0;
    wire launch_ready = enable && allowed && source_ready && !inflight && !reset_chain;
    wire accept = launch_ready && (AUTO_PREVIEW  ? 1'b1
                                   : AUTO_NATIVE ? (command_pending || stream_active)
                                                 : command_valid);
    wire [31 : 0] selected_id =
                      AUTO_NATIVE ? (command_pending ? pending_id : active_id) : command_id;
    wire [31 : 0] selected_origin = AUTO_NATIVE ? (command_pending ? pending_origin : active_origin)
                                                : command_origin;
    wire [31 : 0] selected_size =
                      AUTO_NATIVE ? (command_pending ? pending_size : active_size) : command_size;
    wire selected_roi = AUTO_NATIVE ? (command_pending ? pending_roi : active_roi) : command_roi;
    always @(posedge clk) begin
        if (AUTO_NATIVE && command_valid && command_ready) begin
            command_pending <= 1;
            pending_id <= command_id;
            pending_origin <= command_origin;
            pending_size <= command_size;
            pending_roi <= command_roi;
            pending_stream <= command_continuous;
        end
        if (AUTO_NATIVE && accept && command_pending) begin
            command_pending <= 0;
            active_id <= pending_id;
            active_origin <= pending_origin;
            active_size <= pending_size;
            active_roi <= pending_roi;
            stream_active <= pending_stream;
        end
        if (reset_chain) begin
            command_pending <= 0;
            stream_active <= 0;
            pending_id <= 0;
            active_id <= 0;
            pending_size <= 0;
            active_size <= 0;
        end
    end
    always @(posedge clk) begin
        if (accept && AUTO_PREVIEW)
            auto_id <= auto_id == 32'hffffffff ? 32'd1 : auto_id + 1;
        if (reset_chain)
            auto_id <= 1;
    end
    reg [31 : 0] seen_completed = 0, deadline = 0;
    assign command_ready = AUTO_NATIVE ? (enable && !command_pending && !reset_chain && !fault_o)
                                       : launch_ready;
    rgb_roi_source #(.SW(SW),
                     .SH(SH),
                     .W(W),
                     .H(H),
                     .QUAD(!AUTO_PREVIEW || PREVIEW_QUAD),
                     .FULL_FRAME(AUTO_PREVIEW && PREVIEW_QUAD),
                     .EXTERNAL_QUEUE(EXTERNAL_QUEUE),
                     .EXTERNAL_SOURCE_ID(EXTERNAL_SOURCE_ID),
                     .FRAME_ARBITRATION(FRAME_ARBITRATION))
        source(.q_wen(q_wen),
               .q_reset(q_reset),
               .q_ready(q_ready),
               .q_wdata(q_wdata),
               .q_full(q_full),
               .q_valid(q_valid),
               .q_data(q_data),

               .source_id_i(source_id_i),
               .enhance_revision_i(enhance_revision_i),
               .processing_revision_i(processing_revision_i),
               .epoch_i(epoch_i),
               .frame_enhance_revision(frame_enhance_revision),
               .frame_processing_revision(frame_processing_revision),
               .frame_epoch(frame_epoch),
               .current_source_id(current_source_id),
               .clk(clk),
               .rst(reset_chain),
               .command_valid(accept),
               .command_roi(selected_roi),
               .command_x(selected_origin[11 : 0]),
               .command_y(selected_origin[27 : 16]),
               .command_width(selected_size[11 : 0]),
               .command_height(selected_size[27 : 16]),
               .frame_grant(frame_grant),
               .frame_waiting(frame_waiting),
               .command_id(AUTO_PREVIEW ? auto_id : selected_id),
               .command_ready(source_ready),
               .pixels(pixels),
               .valid(valid),
               .sof(sof),
               .eol(eol),
               .fe(fe),
               .source_error(source_error),
               .quad_pixels(quad_pixels),
               .quad_mask(quad_mask),
               .rgb(rgb),
               .out_valid(sv),
               .out_last(sl),
               .ready(sr),
               .done(source_done),
               .fault(source_fault),
               .result_id(request_id),
               .source_frame_id(sensor_id),
               .result_roi(roi),
               .result_x(x),
               .result_y(y),
               .result_width(frame_width),
               .result_height(frame_height),
               .result_pixels(frame_pixels),
               .fault_events_o(source_fault_events),
               .fault_context_o(source_fault_context));

    reg diagnostic_enable_d = 0;
    wire diagnostic_session_start = enable && !diagnostic_enable_d;
    wire [15 : 0] diagnostic_event_cause = {3'd0,          dma_fault,    dma_code,
                                            timeout_fault, source_error, source_fault_events};
    assign diagnostic_event_o =
               enable && (|source_fault_events || source_error || timeout_fault || dma_fault);
    wire diagnostic_latch = diagnostic_cause_o == 0 && diagnostic_event_o;
    always @(posedge clk) begin
        diagnostic_enable_d <= enable;
        if (diagnostic_latch)
            diagnostic_cause_o <= diagnostic_event_cause;
        if (diagnostic_session_start)
            diagnostic_cause_o <= 0;
        if (rst) begin
            diagnostic_enable_d <= 0;
            diagnostic_cause_o <= 0;
        end
    end
    generate
        if (DIAGNOSTIC_CONTEXT) begin : FAULT_CONTEXT
            reg [23 : 0] saved_sent = 0, saved_xy = 0, saved_beats = 0;
            reg [31 : 0] saved_source = 0;
            assign diagnostic_context_o = {saved_source, 8'd0, saved_xy,  8'd0,
                                           saved_beats,  8'd0, saved_sent};
            always @(posedge clk) begin
                if (diagnostic_latch) begin
                    saved_sent <= source_fault_context[23 : 0];
                    saved_xy <= source_fault_context[55 : 32];
                    saved_beats <= source_fault_context[87 : 64];
                    saved_source <= sensor_id;
                end
                if (rst || diagnostic_session_start) begin
                    saved_sent <= 0;
                    saved_xy <= 0;
                    saved_beats <= 0;
                    saved_source <= 0;
                end
            end
        end else begin : NO_FAULT_CONTEXT
            assign diagnostic_context_o = 128'd0;
        end
    endgenerate
    wire [127 : 0] data, packed_data;
    wire [15 : 0] keep, packed_keep;
    wire dv, dl, dr, packed_valid, packed_last, packed_ready;
    generate
        if (AUTO_PREVIEW && !PREVIEW_QUAD) begin : PREVIEW_PACK
            wire [31 : 0] narrow_data;
            wire [3 : 0] narrow_keep;
            assign packed_data = {96'd0, narrow_data};
            assign packed_keep = {12'd0, narrow_keep};
            rgb_pack pack(.clk(clk),
                          .rst(reset_chain),
                          .rgb(rgb),
                          .valid(sv),
                          .last(sl),
                          .ready(sr),
                          .data(narrow_data),
                          .keep(narrow_keep),
                          .out_valid(packed_valid),
                          .out_last(packed_last),
                          .out_ready(packed_ready));
            assign data = packed_data;
            assign keep = packed_keep;
            assign dv = packed_valid;
            assign dl = packed_last;
            assign packed_ready = dr;
        end else begin : RECT_PACK
            rgb_quad_pack pack(.clk(clk),
                               .rst(reset_chain),
                               .pixels(quad_pixels),
                               .mask(quad_mask),
                               .valid(sv),
                               .last(sl),
                               .ready(sr),
                               .data(packed_data),
                               .keep(packed_keep),
                               .out_valid(packed_valid),
                               .out_last(packed_last),
                               .out_ready(packed_ready));

            reg [127 : 0] beat_data = 0;
            reg [15 : 0] beat_keep = 0;
            reg beat_valid = 0, beat_last = 0;
            assign packed_ready = !beat_valid || dr;
            assign data = beat_data;
            assign keep = beat_keep;
            assign dv = beat_valid;
            assign dl = beat_last;
            always @(posedge clk) begin
                if (packed_ready) begin
                    beat_valid <= packed_valid;
                    if (packed_valid) begin
                        beat_data <= packed_data;
                        beat_keep <= packed_keep;
                        beat_last <= packed_last;
                    end
                end
                if (reset_chain) begin
                    beat_valid <= 0;
                    beat_data <= 0;
                    beat_keep <= 0;
                    beat_last <= 0;
                end
            end
        end
    endgenerate
    always @(posedge clk) begin
        if (accept) begin
            inflight <= 1;
            approved <= 0;
            deadline <= 0;
        end
        if (inflight) begin
            if (deadline == WATCHDOG - 1)
                timeout_fault <= 1;
            else
                deadline <= deadline + 1;
        end
        if (source_done)
            approved <= 1;
        if (completed_id_o != seen_completed) begin
            seen_completed <= completed_id_o;
            inflight <= 0;
        end
        if (reset_chain) begin
            inflight <= 0;
            approved <= 0;
            deadline <= 0;
            timeout_fault <= 0;
            seen_completed <= completed_id_o;
        end
    end
    rgb_stream_dma #(.MAX_BYTES(AUTO_PREVIEW ? W * H * 3 : 518400 * 3),
                     .PROTOCOL(PROTOCOL),
                     .WIDTH(W),
                     .HEIGHT(H),
                     .SLOT_COUNT(SLOT_COUNT),
                     .SLOT_STRIDE(SLOT_STRIDE),
                     .META_OFFSET(META_OFFSET),
                     .STOP_OFFSET(STOP_OFFSET),
                     .BEAT_BYTES(AUTO_PREVIEW && !PREVIEW_QUAD ? 4 : 16))
        dma(.clk_i(clk),
            .rst_i(rst),
            .enable_i(enable),
            .base_i(base),
            .source_error_i(source_error || source_fault || timeout_fault),
            .source_approved_i(approved),
            .frame_width_i(frame_width),
            .frame_height_i(frame_height),
            .frame_pixels_i(frame_pixels),
            .request_id_i(request_id),
            .source_id_i(sensor_id),
            .enhance_revision_i(frame_enhance_revision),
            .processing_revision_i(frame_processing_revision),
            .epoch_i(frame_epoch),
            .frame_format_i(32'h33424752),
            .frame_flags_i((CHANNEL_ID << 8) | {30'd0, roi, 1'b1}),
            .frame_info_i({8'd0, y, x}),
            .s_data_i(data),
            .s_keep_i(keep),
            .s_valid_i(dv),
            .s_last_i(dl),
            .s_ready_o(dr),
            .tx_data_o(tx_data_o),
            .tx_addr_o(tx_addr_o),
            .tx_bytes_o(tx_bytes_o),
            .tx_be_o(tx_be_o),
            .tx_hv_o(tx_hv_o),
            .tx_dv_o(tx_dv_o),
            .tx_eot_o(tx_eot_o),
            .tx_halt_i(tx_halt_i),
            .capture_allowed_o(allowed),
            .capture_reset_o(reset_chain),
            .busy_o(dma_busy),
            .stopped_o(stopped_o),
            .fault_o(dma_fault),
            .fault_code_o(dma_code),
            .completed_id_o(completed_id_o),
            .timer_o(timer_o),
            .last_bytes_o());
    assign busy_o = dma_busy || inflight;
    assign fault_o = dma_fault || source_fault || timeout_fault;
    assign fault_code_o = timeout_fault ? 4'd7 : source_fault ? 4'd11 : dma_code;
    assign debug_o = {request_id, sensor_id};
endmodule
