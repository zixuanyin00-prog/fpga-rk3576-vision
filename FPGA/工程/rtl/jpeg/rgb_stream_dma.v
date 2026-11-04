

`timescale 1ns / 1ps
module rgb_stream_dma
    #(parameter PROTOCOL = 8, MAX_BYTES = 491520, WIDTH = 640, HEIGHT = 360, SLOT_STRIDE = 'h600000,
                META_OFFSET = 'h5ff000, STOP_OFFSET = 'h17ffff0, SLOT_COUNT = 4, BEAT_BYTES = 16,
                INPUT_TIMEOUT = 20000000)
    (input wire clk_i, rst_i, enable_i,
     input wire [31 : 0] base_i,
     input wire source_error_i, source_approved_i,
     input wire [11 : 0] frame_width_i, frame_height_i,
     input wire [23 : 0] frame_pixels_i,
     input wire [31 : 0] request_id_i, source_id_i, enhance_revision_i, processing_revision_i,
                         epoch_i,
     input wire [31 : 0] frame_format_i, frame_flags_i, frame_info_i,
     input wire [127 : 0] s_data_i,
     input wire [15 : 0] s_keep_i,
     input wire s_valid_i, s_last_i,
     output wire s_ready_o,
     output wire [127 : 0] tx_data_o,
     output wire [63 : 0] tx_addr_o,
     output wire [12 : 0] tx_bytes_o,
     output wire [7 : 0] tx_be_o,
     output wire tx_hv_o, tx_dv_o, tx_eot_o,
     input wire tx_halt_i,
     output wire capture_allowed_o, capture_reset_o, busy_o, stopped_o,
     output reg fault_o = 0,
     output reg [3 : 0] fault_code_o = 0,
     output reg [31 : 0] completed_id_o = 0, timer_o = 0, last_bytes_o = 0);
    localparam IDLE = 0, INIT = 1, WAIT_FRAME = 2, CLEAR = 3, FILL = 4, DATA = 5, META = 6,
               COMMIT = 7, STOP = 8, SEND = 9, APPROVE = 10, CRC_TAIL = 11;
    localparam K_INIT = 0, K_START = 1, K_CLEAR = 2, K_DATA = 3, K_META = 4, K_COMMIT = 5,
               K_STOP = 6;

    localparam [63 : 0] MAX_PADDED_BYTES = ((64'd0 + MAX_BYTES + 64'd3) >> 2) << 2;
    localparam METADATA_COVERED =
                   MAX_BYTES <= 32'hffffffff - BEAT_BYTES && MAX_PADDED_BYTES <= META_OFFSET;
    reg [31 : 0] W_WORD = WIDTH, H_WORD = HEIGHT, expected_bytes = WIDTH * HEIGHT * 3;
    reg [3 : 0] state = 0, kind = 0;
    reg armed = 0;
    reg [1 : 0] slot = 0, init_slot = 0;
    reg [31 : 0] base = 0, slot_base = 0, offset = 0, actual = 0, crc = 32'hffffffff, id = 0;
    reg [31 : 0] request_id = 0, source_id = 0, enhance_revision = 0, processing_revision = 0,
                 epoch = 0;
    reg [31 : 0] dma_start = 0, format = 0, flags = 0, info = 0, timeout_count = 0;
    reg [7 : 0] fill_bytes = 0, packet_bytes = 0;
    reg final_packet = 0;
    reg [127 : 0] payload[0 : 7];
    reg [2 : 0] tx_index = 0, tx_last = 0;
    reg [31 : 0] tx_address = 0;
    reg [12 : 0] tx_bytes = 0;
    wire abort_requested = armed && (!enable_i || fault_o || source_error_i);
    wire [31 : 0] padded = (actual + 32'd3) & 32'hfffffffc;
    function [31 : 0] crc_byte;
        input [31 : 0] c;
        input [7 : 0] d;
        reg [31 : 0] t;
        integer k;
        begin
            t = c ^ d;
            for (k = 0; k < 8; k = k + 1)
                t = (t >> 1) ^ ({32{t[0]}} & 32'hedb88320);
            crc_byte = t;
        end
    endfunction
    function [31 : 0] crc_full;
        input [31 : 0] c;
        input [127 : 0] d;
        reg [31 : 0] t;
        integer b;
        begin
            t = c;
            for (b = 0; b < BEAT_BYTES; b = b + 1)
                t = crc_byte(t, d[b * 8 +: 8]);
            crc_full = t;
        end
    endfunction
    reg [127 : 0] tail_data = 0;
    reg [4 : 0] tail_count = 0;

    reg [4 : 0] bytes;
    reg mask_ok;
    integer bi;
    reg padding_ok;
    always @* begin
        bytes = 0;
        mask_ok = 1;
        case (s_keep_i)
            16'h0001:
                bytes = 1;
            16'h0003:
                bytes = 2;
            16'h0007:
                bytes = 3;
            16'h000f:
                bytes = 4;
            16'h001f:
                bytes = 5;
            16'h003f:
                bytes = 6;
            16'h007f:
                bytes = 7;
            16'h00ff:
                bytes = 8;
            16'h01ff:
                bytes = 9;
            16'h03ff:
                bytes = 10;
            16'h07ff:
                bytes = 11;
            16'h0fff:
                bytes = 12;
            16'h1fff:
                bytes = 13;
            16'h3fff:
                bytes = 14;
            16'h7fff:
                bytes = 15;
            16'hffff:
                bytes = 16;
            default: begin
                bytes = 0;
                mask_ok = 0;
            end
        endcase
        padding_ok = 1;
        for (bi = 0; bi < 16; bi = bi + 1)
            if (!s_keep_i[bi] && s_data_i[bi * 8 +: 8] != 0)
                padding_ok = 0;
    end
    wire keep_ok = mask_ok && bytes <= BEAT_BYTES && (bytes == BEAT_BYTES || s_last_i);
    wire [4 : 0] stored = (bytes + 5'd3) & 5'b11100;
    assign s_ready_o = state == FILL && enable_i && !fault_o && !source_error_i;
    assign tx_data_o = payload[tx_index];
    assign tx_addr_o = {32'd0, tx_address};
    assign tx_bytes_o = tx_bytes;
    assign tx_be_o = tx_bytes == 4 ? 8'h0f : 8'hff;
    assign tx_hv_o = state == SEND && tx_index == 0;
    assign tx_dv_o = state == SEND;
    assign tx_eot_o = state == SEND && tx_index == tx_last;
    assign capture_allowed_o = state == WAIT_FRAME && enable_i && !fault_o;
    assign capture_reset_o = rst_i || !armed || !enable_i || fault_o || source_error_i;
    assign busy_o = armed && state != WAIT_FRAME;
    assign stopped_o = !armed && state == IDLE;
    integer n;
    initial begin
        if (BEAT_BYTES != 4 && BEAT_BYTES != 16)
            $fatal(1, "RGB DMA supports only 4-byte or 16-byte beats");
        if ((SLOT_COUNT != 1 && SLOT_COUNT != 2 && SLOT_COUNT != 4) || MAX_BYTES > META_OFFSET ||
            STOP_OFFSET != SLOT_COUNT * SLOT_STRIDE - 16)
            $fatal(1, "Invalid stream DMA geometry");
    end
    always @(posedge clk_i) begin
        timer_o <= timer_o + 1;
        if (armed && enable_i && source_error_i) begin
            fault_o <= 1;
            fault_code_o <= 4;
        end
        if (abort_requested && state != SEND && state != STOP)
            state <= STOP;
        else
            case (state)
                IDLE:
                    if (!enable_i) begin
                        fault_o <= 0;
                        fault_code_o <= 0;
                    end else if (!fault_o) begin
                        if (base_i[11 : 0] != 0 || base_i > 32'hffffffff - STOP_OFFSET - 16) begin
                            fault_o <= 1;
                            fault_code_o <= 1;
                        end else begin
                            base <= base_i;
                            slot_base <= base_i;
                            slot <= 0;
                            init_slot <= 0;
                            armed <= 1;
                            state <= INIT;
                        end
                    end
                INIT: begin
                    payload[0] <= 0;
                    tx_address <= base + init_slot * SLOT_STRIDE + META_OFFSET + 48;
                    tx_bytes <= 16;
                    tx_index <= 0;
                    tx_last <= 0;
                    kind <= K_INIT;
                    state <= SEND;
                end
                WAIT_FRAME:
                    if (s_valid_i) begin
                        id <= id == 32'hffffffff ? 32'd1 : id + 1;
                        actual <= 0;
                        offset <= 0;
                        crc <= 32'hffffffff;
                        W_WORD <= {20'd0, frame_width_i};
                        H_WORD <= {20'd0, frame_height_i};
                        expected_bytes <= frame_pixels_i * 3;
                        enhance_revision <= enhance_revision_i;
                        processing_revision <= processing_revision_i;
                        epoch <= epoch_i;
                        request_id <= request_id_i;
                        source_id <= source_id_i;
                        dma_start <= timer_o;
                        format <= frame_format_i;
                        flags <= frame_flags_i;
                        info <= frame_info_i;
                        fill_bytes <= 0;
                        timeout_count <= 0;
                        state <= CLEAR;
                    end

                CLEAR: begin
                    payload[0] <= 0;
                    tx_address <= slot_base + META_OFFSET;
                    tx_bytes <= 16;
                    tx_index <= 0;
                    tx_last <= 0;
                    kind <= K_CLEAR;
                    state <= SEND;
                end
                FILL:
                    if (s_valid_i && s_ready_o) begin
                        timeout_count <= 0;
                        if (!keep_ok || !padding_ok || actual + bytes > MAX_BYTES ||
                            (!METADATA_COVERED && offset + fill_bytes + stored > META_OFFSET) ||
                            fill_bytes[3 : 0] + stored > 16) begin
                            fault_o <= 1;
                            fault_code_o <= 2;
                        end

                        begin

                            if (BEAT_BYTES == 16)
                                payload[fill_bytes[6 : 4]] <= s_data_i;
                            else
                                payload[fill_bytes[6 : 4]][fill_bytes[3 : 2] * 32 +: 32] <=
                                    s_data_i[31 : 0];
                            if (bytes == BEAT_BYTES)
                                crc <= crc_full(crc, s_data_i);
                            else begin
                                tail_data <= s_data_i;
                                tail_count <= bytes;
                            end
                            actual <= actual + bytes;
                            fill_bytes <= fill_bytes + stored;
                            if (fill_bytes + stored == 128 || s_last_i) begin
                                packet_bytes <= fill_bytes + stored;
                                final_packet <= s_last_i;
                                if (s_last_i)
                                    state <= bytes == BEAT_BYTES ? APPROVE : CRC_TAIL;
                                else
                                    state <= DATA;
                            end
                        end
                    end else if (timeout_count >= INPUT_TIMEOUT - 1) begin
                        fault_o <= 1;
                        fault_code_o <= 5;
                    end else
                        timeout_count <= timeout_count + 1;
                CRC_TAIL: begin
                    crc <= crc_byte(crc, tail_data[7 : 0]);
                    tail_data <= tail_data >> 8;
                    tail_count <= tail_count - 1;
                    if (tail_count == 1)
                        state <= APPROVE;
                end
                APPROVE:
                    if (source_approved_i) begin
                        if (actual != expected_bytes) begin
                            fault_o <= 1;
                            fault_code_o <= 2;
                        end else
                            state <= DATA;
                    end else if (timeout_count >= INPUT_TIMEOUT - 1) begin
                        fault_o <= 1;
                        fault_code_o <= 5;
                    end else
                        timeout_count <= timeout_count + 1;
                DATA: begin
                    tx_address <= slot_base + offset;
                    tx_bytes <= packet_bytes;
                    tx_index <= 0;
                    tx_last <= (packet_bytes - 1) >> 4;
                    kind <= K_DATA;
                    state <= SEND;
                end
                META: begin
                    payload[0] <= {~crc, actual, id, 32'h32434441};
                    payload[1] <= {timer_o, dma_start, source_id, request_id};
                    payload[2] <= {format, 32'd8, flags, info};
                    payload[3] <= {32'd0, padded, H_WORD, W_WORD};
                    payload[4] <= {request_id, epoch, processing_revision, enhance_revision};
                    payload[5] <= {96'd0, 32'h38504d52};
                    tx_address <= slot_base + META_OFFSET;
                    tx_bytes <= 96;
                    tx_index <= 0;
                    tx_last <= 5;
                    kind <= K_META;
                    state <= SEND;
                end
                COMMIT: begin
                    payload[0] <= {id, padded, H_WORD, W_WORD};
                    tx_address <= slot_base + META_OFFSET + 48;
                    tx_bytes <= 16;
                    tx_index <= 0;
                    tx_last <= 0;
                    kind <= K_COMMIT;
                    state <= SEND;
                end
                STOP: begin
                    payload[0] <= {32'h53544f50, 32'd0, 32'd0, 32'h53544f50};
                    tx_address <= base + STOP_OFFSET;
                    tx_bytes <= 16;
                    tx_index <= 0;
                    tx_last <= 0;
                    kind <= K_STOP;
                    state <= SEND;
                end
                SEND:
                    if (!tx_halt_i) begin
                        if (tx_index != tx_last)
                            tx_index <= tx_index + 1;
                        else if (kind == K_STOP) begin
                            armed <= 0;
                            state <= IDLE;
                        end else if (abort_requested)
                            state <= STOP;
                        else
                            case (kind)
                                K_INIT:
                                    if (init_slot != SLOT_COUNT - 1) begin
                                        init_slot <= init_slot + 1;
                                        state <= INIT;
                                    end else begin
                                        payload[0] <= 0;
                                        tx_address <= base + STOP_OFFSET;
                                        tx_bytes <= 16;
                                        tx_index <= 0;
                                        tx_last <= 0;
                                        kind <= K_START;
                                        state <= SEND;
                                    end
                                K_START:
                                    state <= WAIT_FRAME;
                                K_CLEAR: begin
                                    for (n = 0; n < 8; n = n + 1)
                                        payload[n] <= 0;
                                    fill_bytes <= 0;
                                    timeout_count <= 0;
                                    state <= FILL;
                                end
                                K_DATA: begin
                                    offset <= offset + packet_bytes;
                                    fill_bytes <= 0;
                                    timeout_count <= 0;
                                    for (n = 0; n < 8; n = n + 1)
                                        payload[n] <= 0;
                                    state <= final_packet ? META : FILL;
                                end
                                K_META:
                                    state <= COMMIT;
                                K_COMMIT: begin
                                    completed_id_o <= id;
                                    last_bytes_o <= actual;
                                    slot <= slot == SLOT_COUNT - 1 ? 0 : slot + 1;
                                    slot_base <=
                                        slot == SLOT_COUNT - 1 ? base : slot_base + SLOT_STRIDE;
                                    state <= WAIT_FRAME;
                                end
                                default: begin
                                    fault_o <= 1;
                                    fault_code_o <= 6;
                                    state <= STOP;
                                end
                            endcase
                    end
                default: begin
                    fault_o <= 1;
                    fault_code_o <= 6;
                    state <= STOP;
                end
            endcase
        if (rst_i) begin
            state <= 0;
            kind <= 0;
            armed <= 0;
            slot <= 0;
            init_slot <= 0;
            base <= 0;
            slot_base <= 0;
            offset <= 0;
            actual <= 0;
            crc <= 32'hffffffff;
            id <= 0;
            timeout_count <= 0;
            tx_index <= 0;
            tx_last <= 0;
            tx_address <= 0;
            tx_bytes <= 0;
            fill_bytes <= 0;
            packet_bytes <= 0;
            final_packet <= 0;
            fault_o <= 0;
            fault_code_o <= 0;
            completed_id_o <= 0;
            timer_o <= 0;
            last_bytes_o <= 0;
        end
    end
endmodule
