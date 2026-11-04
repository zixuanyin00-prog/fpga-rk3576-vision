

module elbi_dma
    (enhance_config_o, enhance_toggle_o, enhance_revision_i, enhance_source_i, sensor_command_o,
     sensor_toggle_o, sensor_done_i, sensor_result_i, quality_origin_o, quality_freeze_o,
     quality_select_o, quality_data_i, roi_size_o, preview_compression_o, archive_mode_o,
     compression_config_o, compression_origin_o, roi_id_o, roi_valid_o, roi_ready_i,
     capture_config_o, capture_origin_o, capture_size_o, capture_id_o, capture_valid_o,
     capture_ready_i, capture_completed_i, capture_source_i, epoch_o, processing_revision_o,
     process_config_o, process_effective_config_i, process_effective_revision_i,
     process_effective_source_i, isp_config_o, isp_toggle_o, industrial_config_o, feature_select_o,
     feature_config_o, feature_data_i, feature_frame_i, feature_fault_i, native_debug_i,
     ddr_status_i, fault_diagnostic_i, core_clk, core_rst_n, ptr_rst_i, access_en, ext_lbc_din,
     ext_lbc_ack, lbc_ext_addr, lbc_ext_cs, lbc_ext_wr, lbc_ext_rom_access, lbc_ext_io_access,
     lbc_ext_dout, lbc_ext_bar_num, lbc_ext_vfunc_active, lbc_ext_vfunc_num,

     enable_o, ack_valid_o, ack_frame_id_o, frame_ready_i, busy_i, pending_i, camera_status_i,
     frame_id_i, frame_mode_i, frame_crc_i, start_ptr_i, wr_ptr_i, stream_done_i, rd_ptr_o,
     ts_done_i, ts_publish_i, timer_i, published_i, captured_i, dma_base_o, dma_page_bases_o,
     dma_page_valid_o, dma_busy_i, dma_fault_i, dma_last_i, wb_manual_o, wb_r_o, wb_b_o, gaussian_o,
     equalize_o, dma_stopped_i, dma_code_i);
    localparam [383 : 0] ENHANCE_IDENTITY = {32'd256, 32'd224, 32'd192, 32'd160, 32'd128, 32'd96,
                                             32'd64,  32'd32,  32'd0,   32'd0,   32'd0,   32'd256};
    output reg [383 : 0] enhance_config_o = ENHANCE_IDENTITY;
    output reg enhance_toggle_o = 0;
    input wire [31 : 0] enhance_revision_i, enhance_source_i;
    reg [383 : 0] enhance_shadow = ENHANCE_IDENTITY;
    reg [31 : 0] enhance_rejected = 0;
    wire enhance_valid = enhance_shadow[31 : 0] >= 64 && enhance_shadow[31 : 0] <= 1024 &&
                         enhance_shadow[63 : 32] <= 256 && enhance_shadow[95 : 64] != 0 &&
                         enhance_shadow[95 : 64] != enhance_config_o[95 : 64] &&
                         enhance_shadow[96 +: 32] <= enhance_shadow[128 +: 32] &&
                         enhance_shadow[128 +: 32] <= enhance_shadow[160 +: 32] &&
                         enhance_shadow[160 +: 32] <= enhance_shadow[192 +: 32] &&
                         enhance_shadow[192 +: 32] <= enhance_shadow[224 +: 32] &&
                         enhance_shadow[224 +: 32] <= enhance_shadow[256 +: 32] &&
                         enhance_shadow[256 +: 32] <= enhance_shadow[288 +: 32] &&
                         enhance_shadow[288 +: 32] <= enhance_shadow[320 +: 32] &&
                         enhance_shadow[320 +: 32] <= enhance_shadow[352 +: 32] &&
                         enhance_shadow[383 : 352] <= 256;
    output reg [127 : 0] sensor_command_o = 0;
    output reg sensor_toggle_o = 0;
    input wire sensor_done_i;
    input wire [511 : 0] sensor_result_i;
    reg [127 : 0] sensor_shadow = 0;
    reg [31 : 0] sensor_rejected = 0;
    output reg [31 : 0] quality_origin_o = 0;
    output reg quality_freeze_o = 0;
    output reg [6 : 0] quality_select_o = 0;
    input wire [31 : 0] quality_data_i;
    output reg [31 : 0] preview_compression_o = 32'h00010000;
    output reg archive_mode_o = 0;
    output reg [31 : 0] compression_config_o = 0, compression_origin_o = 0, roi_id_o = 0;
    output reg [31 : 0] roi_size_o = 32'h01200200;
    reg [31 : 0] roi_size_shadow = 32'h01200200;
    output reg roi_valid_o = 0;
    input wire roi_ready_i;
    reg [31 : 0] id_shadow = 0;
    reg [31 : 0] compression_shadow = 0, origin_shadow = 0;
    output reg [127 : 0] isp_config_o = 128'h00000000000001000000010001000100;
    output reg isp_toggle_o = 0;
    reg [127 : 0] isp_shadow = 128'h00000000000001000000010001000100;
    output reg [31 : 0] industrial_config_o = 32'h0040000d;
    output reg [31 : 0] epoch_o = 0, processing_revision_o = 1;

    localparam [319 : 0] PROCESS_DEFAULT = {32'd0,         32'd32,    32'd61568, 32'd1573123,
                                            32'd452984576, 32'd69760, 32'd3,     32'd65344,
                                            32'd0,         32'd1};
    output reg [319 : 0] process_config_o = PROCESS_DEFAULT;
    input wire [319 : 0] process_effective_config_i;
    input wire [31 : 0] process_effective_revision_i, process_effective_source_i;
    reg [319 : 0] process_shadow = PROCESS_DEFAULT;
    reg [9 : 0] process_shadow_valid = 10'h3ff;
    reg [3 : 0] process_select = 0;
    reg process_rejected = 0;
    reg [31 : 0] process_reject_count = 0;

    reg [31 : 0] process_shadow_word = 1, process_effective_word = 1;
    function process_word_valid;
        input [3 : 0] word_index;
        input [31 : 0] value;
        begin
            case (word_index)
                0:
                    process_word_valid = value <= 6;
                1:
                    process_word_valid = (value & 32'hfffff000) == 0;
                2:
                    process_word_valid = value[31 : 19] == 0 && value[17 : 16] <= 2 &&
                                         (value[17 : 16] != 2 || value[7 : 0] <= value[15 : 8]);
                3:
                    process_word_valid = (value & 32'hffffffc8) == 0 && value[2 : 0] <= 4;
                4:
                    process_word_valid =
                        value[31 : 17] == 0 && value[11 : 0] <= 2040 && value[15 : 12] <= 8;
                5:
                    process_word_valid =
                        value[31 : 30] == 0 && value[26 : 24] <= 4 && value[29 : 27] <= 4;
                6:
                    process_word_valid =
                        (value & 32'hff00f8e8) == 0 && value[2 : 0] <= 4 && value[10 : 8] <= 4;
                7:
                    process_word_valid = value[31 : 16] == 0;
                8, 9:
                    process_word_valid = value <= 256;
                default:
                    process_word_valid = 0;
            endcase
        end
    endfunction
    output reg [31 : 0] capture_config_o = 0, capture_origin_o = 0, capture_size_o = 32'h01200200,
                        capture_id_o = 0;
    output reg capture_valid_o = 0;
    input wire capture_ready_i;
    input wire [31 : 0] capture_completed_i, capture_source_i;
    reg [31 : 0] capture_control_shadow = 0, capture_origin_shadow = 0,
                 capture_size_shadow = 32'h01200200, capture_id_shadow = 0;
    output reg [6 : 0] feature_select_o = 0;
    output reg [23 : 0] feature_config_o = 24'hf08080;
    input wire [31 : 0] feature_data_i, feature_frame_i;
    input wire feature_fault_i;
    input wire [63 : 0] native_debug_i;
    input wire [31 : 0] ddr_status_i;
    input wire [255 : 0] fault_diagnostic_i;
    output wire [31 : 0] dma_base_o;

    output reg [191 : 0] dma_page_bases_o = 0;
    output reg [15 : 0] dma_page_valid_o = 0;
    reg [4 : 0] dma_page_index = 0;
    input wire dma_busy_i, dma_fault_i;
    input wire [31 : 0] dma_last_i;
    output reg wb_manual_o = 0;
    output reg [9 : 0] wb_r_o = 256, wb_b_o = 256;
    output reg gaussian_o = 1, equalize_o = 1;
    input wire dma_stopped_i;
    input wire [3 : 0] dma_code_i;
    reg [31 : 0] dma_base = 0, dma_high = 0, wb_shadow_mode = 0, wb_shadow_r = 256,
                 wb_shadow_b = 256, bad_config = 0;
    assign dma_base_o = dma_base;

    input core_clk;
    input core_rst_n;
    input ptr_rst_i;
    input access_en;
    output [63 : 0] ext_lbc_din;
    output [1 : 0] ext_lbc_ack;
    input [31 : 0] lbc_ext_addr;
    input [1 : 0] lbc_ext_cs;
    input [3 : 0] lbc_ext_wr;
    input lbc_ext_rom_access;
    input lbc_ext_io_access;
    input [31 : 0] lbc_ext_dout;
    input [2 : 0] lbc_ext_bar_num;
    input lbc_ext_vfunc_active;
    input [1 : 0] lbc_ext_vfunc_num;

    output enable_o;
    output ack_valid_o;
    output [31 : 0] ack_frame_id_o;
    input frame_ready_i;
    input busy_i;
    input pending_i;
    input [2 : 0] camera_status_i;
    input [31 : 0] frame_id_i;
    input [1 : 0] frame_mode_i;
    input [31 : 0] frame_crc_i;
    input [31 : 0] start_ptr_i;
    input [31 : 0] wr_ptr_i;
    input stream_done_i;
    output [31 : 0] rd_ptr_o;
    input [31 : 0] ts_done_i;
    input [31 : 0] ts_publish_i;
    input [31 : 0] timer_i;
    input [31 : 0] published_i;
    input [31 : 0] captured_i;

    always @(posedge core_clk) begin
        process_shadow_word <= process_shadow[process_select * 32 +: 32];
        process_effective_word <= process_effective_config_i[process_select * 32 +: 32];
    end

    localparam PAYLOAD_BYTES = 6220800;
    localparam CW = 1920, CH = 1080;
    localparam RING_BYTES = 4147200;

    reg [31 : 0] lbc_ext_addr_ff[1 : 0];
    reg [1 : 0] lbc_ext_cs_ff[1 : 0];
    reg [3 : 0] lbc_ext_wr_ff[1 : 0];
    reg lbc_ext_rom_access_ff[1 : 0];
    reg lbc_ext_io_access_ff[1 : 0];
    reg [31 : 0] lbc_ext_dout_ff[1 : 0];
    reg [2 : 0] lbc_ext_bar_num_ff[1 : 0];
    reg lbc_ext_vfunc_active_ff[1 : 0];
    reg [1 : 0] lbc_ext_vfunc_num_ff[1 : 0];

    reg pend0, pend1, pair_pend;
    reg [31 : 0] addr_lat0, addr_lat1;

    reg feature_write_lat0, feature_write_lat1;
    reg [3 : 0] wr_lat0, wr_lat1;
    reg [31 : 0] dout_lat0, dout_lat1;

    reg [2 : 0] srv_state;
    localparam S_IDLE = 3'd0, S_DATA = 3'd1, S_DATA2 = 3'd2, S_ACK = 3'd3, S_PTR_DIFF = 3'd4,
               S_PTR_CHECK = 3'd5, S_PTR_COMMIT = 3'd6;
    reg srv_slot;
    reg srv_pair;
    reg srv_wr;
    reg [31 : 0] srv_addr;
    reg [31 : 0] srv_data_lo, srv_data_hi;
    reg [31 : 0] rdata_reg;

    reg [31 : 0] ptr_cand;
    reg [3 : 0] ptr_be_snap;
    reg [31 : 0] rd_snap, wr_snap;
    reg [31 : 0] ptr_delta, ptr_avail;
    reg ptr_ok;

    reg [31 : 0] scratch;
    reg enable_reg;
    reg [31 : 0] invalid_ack;
    reg [31 : 0] invalid_rdptr;
    reg [31 : 0] rd_ptr;
    reg ack_valid_reg;
    reg [31 : 0] ack_frame_id_reg;

    reg [31 : 0] frame_end_ptr;

    reg ptr_void;

    assign enable_o = enable_reg;
    assign ack_valid_o = ack_valid_reg;
    assign ack_frame_id_o = ack_frame_id_reg;
    assign rd_ptr_o = rd_ptr;

    wire acc_ok = ~lbc_ext_vfunc_active_ff[0] & (lbc_ext_bar_num_ff[0][2 : 1] == 2'b00);
    wire addr_chg = (lbc_ext_addr_ff[0] != lbc_ext_addr_ff[1]);
    wire new0 = acc_ok & lbc_ext_cs_ff[0][0] & ~pend0 & (~lbc_ext_cs_ff[1][0] | addr_chg);
    wire new1 = acc_ok & lbc_ext_cs_ff[0][1] & ~pend1 & (~lbc_ext_cs_ff[1][1] | addr_chg);

    wire pair_new = new0 & new1 & ~lbc_ext_cs_ff[1][0] & ~lbc_ext_cs_ff[1][1];

    assign ext_lbc_din = {srv_data_hi, srv_data_lo};
    assign ext_lbc_ack = (srv_state == S_ACK) ? (srv_pair ? 2'b11 : {srv_slot, ~srv_slot}) : 2'b00;

    always @(posedge core_clk or negedge core_rst_n) begin
        if (~core_rst_n) begin
            lbc_ext_addr_ff[0] <= 32'd0;
            lbc_ext_addr_ff[1] <= 32'd0;
            lbc_ext_cs_ff[0] <= 2'd0;
            lbc_ext_cs_ff[1] <= 2'd0;
            lbc_ext_wr_ff[0] <= 4'd0;
            lbc_ext_wr_ff[1] <= 4'd0;
            lbc_ext_rom_access_ff[0] <= 1'b0;
            lbc_ext_rom_access_ff[1] <= 1'b0;
            lbc_ext_io_access_ff[0] <= 1'b0;
            lbc_ext_io_access_ff[1] <= 1'b0;
            lbc_ext_dout_ff[0] <= 32'd0;
            lbc_ext_dout_ff[1] <= 32'd0;
            lbc_ext_bar_num_ff[0] <= 3'd0;
            lbc_ext_bar_num_ff[1] <= 3'd0;
            lbc_ext_vfunc_active_ff[0] <= 1'b0;
            lbc_ext_vfunc_active_ff[1] <= 1'b0;
            lbc_ext_vfunc_num_ff[0] <= 2'd0;
            lbc_ext_vfunc_num_ff[1] <= 2'd0;
        end else begin
            lbc_ext_addr_ff[1] <= lbc_ext_addr_ff[0];
            lbc_ext_addr_ff[0] <= lbc_ext_addr;
            lbc_ext_cs_ff[1] <= lbc_ext_cs_ff[0];
            lbc_ext_cs_ff[0] <= lbc_ext_cs;
            lbc_ext_wr_ff[1] <= lbc_ext_wr_ff[0];
            lbc_ext_wr_ff[0] <= lbc_ext_wr;
            lbc_ext_rom_access_ff[1] <= lbc_ext_rom_access_ff[0];
            lbc_ext_rom_access_ff[0] <= lbc_ext_rom_access;
            lbc_ext_io_access_ff[1] <= lbc_ext_io_access_ff[0];
            lbc_ext_io_access_ff[0] <= lbc_ext_io_access;
            lbc_ext_dout_ff[1] <= lbc_ext_dout_ff[0];
            lbc_ext_dout_ff[0] <= lbc_ext_dout;
            lbc_ext_bar_num_ff[1] <= lbc_ext_bar_num_ff[0];
            lbc_ext_bar_num_ff[0] <= lbc_ext_bar_num;
            lbc_ext_vfunc_active_ff[1] <= lbc_ext_vfunc_active_ff[0];
            lbc_ext_vfunc_active_ff[0] <= lbc_ext_vfunc_active;
            lbc_ext_vfunc_num_ff[1] <= lbc_ext_vfunc_num_ff[0];
            lbc_ext_vfunc_num_ff[0] <= lbc_ext_vfunc_num;
        end
    end

    wire p_req = pend0 | pend1;
    wire p_slot = ~pend0;
    wire p_pair = pend0 & pend1 & pair_pend;
    wire [31 : 0] p_addr = p_slot ? addr_lat1 : addr_lat0;
    wire [3 : 0] p_wr = p_slot ? wr_lat1 : wr_lat0;
    wire [31 : 0] p_dout = p_slot ? dout_lat1 : dout_lat0;
    wire p_start = (srv_state == S_IDLE) & p_req;
    wire p_win = p_addr[16];

    wire [29 : 0] p_idx = {12'd0, p_addr[19 : 2]};

    always @(posedge core_clk or negedge core_rst_n) begin
        if (~core_rst_n) begin
            pend0 <= 1'b0;
            pend1 <= 1'b0;
            pair_pend <= 1'b0;
            addr_lat0 <= 32'd0;
            addr_lat1 <= 32'd0;
            feature_write_lat0 <= 1'b0;
            feature_write_lat1 <= 1'b0;
            wr_lat0 <= 4'd0;
            wr_lat1 <= 4'd0;
            dout_lat0 <= 32'd0;
            dout_lat1 <= 32'd0;
        end else begin
            if (new0) begin
                pend0 <= 1'b1;
                addr_lat0 <= lbc_ext_addr_ff[0];
                feature_write_lat0 <=
                    lbc_ext_addr_ff[0][19 : 2] == 18'd29 && lbc_ext_wr_ff[0] == 4'hf;
                wr_lat0 <= lbc_ext_wr_ff[0];
                dout_lat0 <= lbc_ext_dout_ff[0];
            end
            if (new1) begin
                pend1 <= 1'b1;

                addr_lat1 <= pair_new ? lbc_ext_addr_ff[0] + 32'd4 : lbc_ext_addr_ff[0];
                feature_write_lat1 <=
                    lbc_ext_wr_ff[0] == 4'hf && (pair_new ? lbc_ext_addr_ff[0][19 : 2] == 18'd28
                                                          : lbc_ext_addr_ff[0][19 : 2] == 18'd29);
                wr_lat1 <= lbc_ext_wr_ff[0];
                dout_lat1 <= lbc_ext_dout_ff[0];
            end
            if (pair_new)
                pair_pend <= 1'b1;
            if (srv_state == S_ACK) begin
                if (srv_pair) begin
                    pend0 <= 1'b0;
                    pend1 <= 1'b0;
                    pair_pend <= 1'b0;
                end else if (srv_slot)
                    pend1 <= 1'b0;
                else
                    pend0 <= 1'b0;
            end
        end
    end

    wire p_wreg = p_start & (|p_wr) & ~p_win;

    wire ack_write = p_wreg & (p_idx == 6'd14);
    wire rdptr_write = p_wreg & (p_idx == 6'd15);

    always @(posedge core_clk or negedge core_rst_n) begin
        if (~core_rst_n) begin
            srv_state <= S_IDLE;
            srv_slot <= 1'b0;
            srv_pair <= 1'b0;
            srv_wr <= 1'b0;
            srv_addr <= 32'd0;
            srv_data_lo <= 32'd0;
            srv_data_hi <= 32'd0;
            rdata_reg <= 32'd0;
            ptr_cand <= 32'd0;
            ptr_be_snap <= 4'd0;
            rd_snap <= 32'd0;
            wr_snap <= 32'd0;
            ptr_delta <= 32'd0;
            ptr_avail <= 32'd0;
            ptr_ok <= 1'b0;
        end else begin
            case (srv_state)
                S_IDLE: begin
                    if (p_req) begin
                        srv_slot <= p_slot;
                        srv_pair <= p_pair;
                        srv_wr <= |p_wr;
                        srv_addr <= p_addr;
                        if (~|p_wr & ~p_win)
                            rdata_reg <= rd_reg(p_idx);
                        if (rdptr_write & ~p_pair) begin

                            ptr_cand <= p_dout;
                            ptr_be_snap <= p_wr;
                            rd_snap <= rd_ptr;
                            wr_snap <= wr_ptr_i;
                            srv_state <= S_PTR_DIFF;
                        end else begin
                            srv_state <= S_DATA;
                        end
                    end
                end
                S_PTR_DIFF: begin
                    ptr_delta <= ptr_cand - rd_snap;
                    ptr_avail <= wr_snap - rd_snap;
                    srv_state <= S_PTR_CHECK;
                end
                S_PTR_CHECK: begin
                    ptr_ok <= (ptr_be_snap[3 : 0] == 4'hF) & (ptr_cand[1 : 0] == 2'd0) &
                              (ptr_delta <= ptr_avail);
                    srv_state <= S_PTR_COMMIT;
                end
                S_PTR_COMMIT: begin

                    srv_state <= S_DATA;
                end
                S_DATA: begin

                    srv_data_lo <= srv_addr[16] ? 32'd0 : rdata_reg;
                    if (srv_pair & ~srv_wr) begin
                        if (srv_addr[16])
                            srv_state <= S_DATA2;
                        else begin
                            srv_data_hi <= rd_reg({12'd0, srv_addr[19 : 2]} + 30'd1);
                            srv_state <= S_ACK;
                        end
                    end else begin
                        srv_data_hi <= srv_addr[16] ? 32'd0 : rdata_reg;
                        srv_state <= S_ACK;
                    end
                end
                S_DATA2: begin
                    srv_data_hi <= 32'd0;
                    srv_state <= S_ACK;
                end
                default:
                    srv_state <= S_IDLE;
            endcase
        end
    end

    function [31 : 0] be32;
        input [31 : 0] oldv;
        input [31 : 0] newv;
        input [3 : 0] be;
        integer b;
        begin
            be32 = oldv;
            for (b = 0; b < 4; b = b + 1)
                if (be[b])
                    be32[b * 8 +: 8] = newv[b * 8 +: 8];
        end
    endfunction

    wire ack_ok = ack_write & (p_wr == 4'hF) & frame_ready_i & (p_dout != 32'd0) &
                  (p_dout == frame_id_i) & stream_done_i & (rd_ptr == frame_end_ptr);

    always @(posedge core_clk or negedge core_rst_n) begin
        if (~core_rst_n) begin
            enhance_config_o <= ENHANCE_IDENTITY;
            enhance_shadow <= ENHANCE_IDENTITY;
            enhance_toggle_o <= 0;
            enhance_rejected <= 0;
            sensor_command_o <= 0;
            sensor_toggle_o <= 0;
            sensor_shadow <= 0;
            sensor_rejected <= 0;
            quality_origin_o <= 0;
            quality_select_o <= 0;
            quality_freeze_o <= 0;
            industrial_config_o <= 32'h0040000d;
            processing_revision_o <= 1;
            epoch_o <= 0;
            process_config_o <= PROCESS_DEFAULT;
            process_shadow <= PROCESS_DEFAULT;
            process_shadow_valid <= 10'h3ff;
            process_select <= 0;
            process_rejected <= 0;
            process_reject_count <= 0;
            capture_config_o <= 0;
            capture_origin_o <= 0;
            capture_size_o <= 32'h01200200;
            capture_id_o <= 0;
            capture_valid_o <= 0;
            capture_control_shadow <= 0;
            capture_origin_shadow <= 0;
            capture_size_shadow <= 32'h01200200;
            capture_id_shadow <= 0;
            feature_select_o <= 0;
            feature_config_o <= 24'hf08080;
            roi_size_o <= 32'h01200200;
            roi_size_shadow <= 32'h01200200;
            preview_compression_o <= 32'h00010000;
            archive_mode_o <= 0;
            compression_config_o <= 0;
            compression_origin_o <= 0;
            compression_shadow <= 0;
            origin_shadow <= 0;
            roi_id_o <= 0;
            id_shadow <= 0;
            roi_valid_o <= 0;
            dma_page_bases_o <= 0;
            dma_page_valid_o <= 0;
            dma_page_index <= 0;
            dma_base <= 0;
            dma_high <= 0;
            wb_shadow_mode <= 0;
            wb_shadow_r <= 256;
            wb_shadow_b <= 256;
            bad_config <= 0;
            wb_manual_o <= 0;
            wb_r_o <= 256;
            wb_b_o <= 256;
            gaussian_o <= 0;
            equalize_o <= 0;
            scratch <= 32'd0;
            enable_reg <= 1'b0;
            invalid_ack <= 32'd0;
            invalid_rdptr <= 32'd0;
            rd_ptr <= 32'd0;
            ack_valid_reg <= 1'b0;
            ack_frame_id_reg <= 32'd0;
            frame_end_ptr <= 32'd0;
            ptr_void <= 1'b0;
        end else begin
            ack_valid_reg <= 1'b0;
            frame_end_ptr <= start_ptr_i + PAYLOAD_BYTES;
            if (roi_valid_o && roi_ready_i || !enable_reg)
                roi_valid_o <= 0;
            if (capture_valid_o && capture_ready_i || !enable_reg)
                capture_valid_o <= 0;
            if (p_start && (p_slot ? feature_write_lat1 : feature_write_lat0))
                feature_select_o <= p_dout[6 : 0];
            if (p_wreg) begin
                case (p_idx)
                    30'd196:
                        if (p_wr == 15 && !p_pair && p_dout < 10)
                            process_select <= p_dout[3 : 0];
                        else begin
                            process_rejected <= 1;
                            process_reject_count <= process_reject_count + 1;
                        end
                    30'd197:
                        if (p_wr == 15 && !p_pair) begin
                            process_shadow[process_select * 32 +: 32] <= p_dout;
                            process_shadow_valid[process_select] <=
                                process_word_valid(process_select, p_dout);
                        end else begin
                            process_rejected <= 1;
                            process_reject_count <= process_reject_count + 1;
                        end
                    30'd198:
                        if (p_wr == 15 && !p_pair && (&process_shadow_valid) && p_dout != 0 &&
                            p_dout != processing_revision_o) begin
                            process_config_o <= process_shadow;
                            processing_revision_o <= p_dout;
                            process_rejected <= 0;
                        end else begin
                            process_rejected <= 1;
                            process_reject_count <= process_reject_count + 1;
                        end

                    30'd136:
                        if (p_wr == 15)
                            capture_control_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    30'd137:
                        if (p_wr == 15)
                            capture_origin_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    30'd138:
                        if (p_wr == 15)
                            capture_id_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    30'd139:
                        if (p_wr == 15)
                            capture_size_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    30'd140:
                        if (p_wr == 15 && p_dout == 32'h524f4931 && enable_reg && capture_ready_i &&
                            !capture_valid_o && capture_id_shadow != 0 &&
                            (capture_control_shadow == 1 || capture_control_shadow == 3) &&
                            capture_origin_shadow[15 : 12] == 0 &&
                            capture_origin_shadow[31 : 28] == 0 &&
                            capture_size_shadow[15 : 12] == 0 &&
                            capture_size_shadow[31 : 28] == 0 && capture_size_shadow[11 : 0] != 0 &&
                            capture_size_shadow[27 : 16] != 0 &&
                            capture_size_shadow[11 : 0] * capture_size_shadow[27 : 16] <= 518400 &&
                            {1'b0, capture_origin_shadow[11 : 0]} +
                                    {1'b0, capture_size_shadow[11 : 0]} <=
                                1920 &&
                            {1'b0, capture_origin_shadow[27 : 16]} +
                                    {1'b0, capture_size_shadow[27 : 16]} <=
                                1080) begin
                            capture_config_o <= capture_control_shadow;
                            capture_origin_o <= capture_origin_shadow;
                            capture_size_o <= capture_size_shadow;
                            capture_id_o <= capture_id_shadow;
                            capture_valid_o <= 1;
                        end else
                            bad_config <= bad_config + 1;
                    30'd145:
                        if (p_wr == 15 && !enable_reg && dma_stopped_i && !dma_busy_i &&
                            p_dout != 0)
                            epoch_o <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    30'd128:
                        if (p_wr == 15 && !enable_reg && dma_stopped_i && !dma_busy_i &&
                            p_dout < 16)
                            dma_page_index <= p_dout[4 : 0];
                        else
                            bad_config <= bad_config + 1;
                    30'd129:
                        if (p_wr == 15 && !enable_reg && dma_stopped_i && !dma_busy_i &&
                            dma_page_index < 16 && p_dout != 0 && p_dout[19 : 0] == 0) begin
                            dma_page_bases_o[dma_page_index * 12 +: 12] <= p_dout[31 : 20];
                            dma_page_valid_o[dma_page_index] <= 1;
                        end else
                            bad_config <= bad_config + 1;
                    30'd121:
                        if (p_wr == 15)
                            roi_size_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    30'd106:
                        if (p_wr == 15)
                            enhance_shadow[0 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd107:
                        if (p_wr == 15)
                            enhance_shadow[32 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd108:
                        if (p_wr == 15)
                            enhance_shadow[96 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd109:
                        if (p_wr == 15)
                            enhance_shadow[128 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd110:
                        if (p_wr == 15)
                            enhance_shadow[160 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd111:
                        if (p_wr == 15)
                            enhance_shadow[192 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd112:
                        if (p_wr == 15)
                            enhance_shadow[224 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd113:
                        if (p_wr == 15)
                            enhance_shadow[256 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd114:
                        if (p_wr == 15)
                            enhance_shadow[288 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd115:
                        if (p_wr == 15)
                            enhance_shadow[320 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd116:
                        if (p_wr == 15)
                            enhance_shadow[352 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd117:
                        if (p_wr == 15)
                            enhance_shadow[64 +: 32] <= p_dout;
                        else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd118:
                        if (p_wr == 15 && !p_pair && p_dout == 32'h31545541 && enhance_valid) begin
                            enhance_config_o <= enhance_shadow;
                            enhance_toggle_o <= ~enhance_toggle_o;
                        end else
                            enhance_rejected <= enhance_rejected + 1;
                    30'd66:
                        if (p_wr == 15)
                            sensor_shadow[31 : 0] <= p_dout;
                        else
                            sensor_rejected <= sensor_rejected + 1;
                    30'd67:
                        if (p_wr == 15)
                            sensor_shadow[63 : 32] <= p_dout;
                        else
                            sensor_rejected <= sensor_rejected + 1;
                    30'd68:
                        if (p_wr == 15)
                            sensor_shadow[95 : 64] <= p_dout;
                        else
                            sensor_rejected <= sensor_rejected + 1;
                    30'd69:
                        if (p_wr == 15)
                            sensor_shadow[127 : 96] <= p_dout;
                        else
                            sensor_rejected <= sensor_rejected + 1;
                    30'd70:
                        if (p_wr == 15 && !p_pair && p_dout == 32'h31584d49 && camera_status_i[0] &&
                            sensor_toggle_o == sensor_done_i && sensor_shadow[31 : 0] != 0 &&
                            sensor_shadow[31 : 0] != sensor_command_o[31 : 0] &&
                            sensor_shadow[127 : 96] <= 2 && sensor_shadow[95 : 64] <= 100 &&
                            sensor_shadow[63 : 32] >= 8 && sensor_shadow[63 : 32] <= 2279) begin
                            sensor_command_o <= sensor_shadow;
                            sensor_toggle_o <= ~sensor_toggle_o;
                        end else
                            sensor_rejected <= sensor_rejected + 1;
                    30'd96:
                        if (p_wr == 15 && p_dout[15 : 0] <= 1408 && p_dout[31 : 16] <= 792)
                            quality_origin_o <= p_dout;
                    30'd97:
                        if (p_wr == 15 && p_dout <= 1)
                            quality_freeze_o <= p_dout[0];
                    30'd98:
                        if (p_wr == 15 && p_dout < 32)
                            quality_select_o <= p_dout[6 : 0];
                    30'd125:
                        if (!(p_wr == 15 && !enable_reg && dma_stopped_i && !dma_busy_i &&
                              p_dout == 32'h00010000))
                            bad_config <= bad_config + 1;
                    6'd63:
                        if (p_wr == 4'hf && p_dout == 0 && !enable_reg && dma_stopped_i &&
                            !dma_busy_i) begin
                            compression_config_o <= 0;
                            compression_origin_o <= 0;
                            compression_shadow <= 0;
                            origin_shadow <= 0;
                            roi_valid_o <= 0;
                            roi_id_o <= 0;
                        end else
                            bad_config <= bad_config + 1;
                    6'd57:
                        if (p_wr == 4'hf)
                            compression_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    6'd58:
                        if (p_wr == 4'hf)
                            origin_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    6'd59:
                        if (p_wr == 4'hf)
                            id_shadow <= p_dout;
                        else
                            bad_config <= bad_config + 1;
                    6'd60:
                        if (p_wr == 4'hf && p_dout == 32'h524f4931 && enable_reg && roi_ready_i &&
                            !roi_valid_o && id_shadow != 0 &&
                            (compression_shadow == 1 || compression_shadow == 3) &&
                            origin_shadow[15 : 12] == 0 && origin_shadow[31 : 28] == 0 &&
                            ((!compression_shadow[0] && origin_shadow == 0) ||
                             (compression_shadow[0] && roi_size_shadow[15 : 12] == 0 &&
                              roi_size_shadow[31 : 28] == 0 && roi_size_shadow[11 : 0] != 0 &&
                              roi_size_shadow[27 : 16] != 0 &&
                              roi_size_shadow[11 : 0] * roi_size_shadow[27 : 16] <= 518400 &&
                              {1'b0, origin_shadow[11 : 0]} + {1'b0, roi_size_shadow[11 : 0]} <=
                                  1920 &&
                              {1'b0, origin_shadow[27 : 16]} + {1'b0, roi_size_shadow[27 : 16]} <=
                                  1080))) begin
                            compression_config_o <= compression_shadow;
                            compression_origin_o <= origin_shadow;
                            roi_size_o <= roi_size_shadow;
                            roi_id_o <= id_shadow;
                            roi_valid_o <= 1;
                        end else
                            bad_config <= bad_config + 1;
                    6'd52:
                        if (p_wr == 4'hf)
                            isp_shadow[31 : 0] <= p_dout;
                    6'd53:
                        if (p_wr == 4'hf)
                            isp_shadow[63 : 32] <= p_dout;
                    6'd54:
                        if (p_wr == 4'hf)
                            isp_shadow[95 : 64] <= p_dout;
                    6'd55:
                        if (p_wr == 4'hf)
                            isp_shadow[127 : 96] <= p_dout;
                    6'd56:
                        if (p_wr == 4'hf && p_dout == 32'h49535043) begin
                            isp_config_o <= isp_shadow;
                            isp_toggle_o <= ~isp_toggle_o;
                        end

                    6'd39:
                        if (p_wr == 4'hf && (p_dout & 32'hfe0000e2) == 0 && p_dout[0] &&
                            p_dout[4 : 2] <= 4) begin
                            industrial_config_o <= p_dout;
                        end else
                            bad_config <= bad_config + 1'b1;
                    6'd36:
                        if (p_wr == 4'hf) begin
                            feature_config_o <= p_dout[23 : 0];
                        end
                    6'd2:
                        scratch <= be32(scratch, p_dout, p_wr);
                    6'd3:
                        if (p_wr == 4'hf) begin
                            if (!p_dout[0])
                                enable_reg <= 0;
                            else if (dma_base == 32'h01000000 && dma_high == 0 && epoch_o != 0 &&
                                     dma_stopped_i && (&dma_page_valid_o))
                                enable_reg <= 1;
                            else
                                bad_config <= bad_config + 1'b1;
                        end
                    6'd32:
                        if (p_wr == 4'hf && !enable_reg && !dma_busy_i)
                            dma_base <= p_dout;
                        else
                            bad_config <= bad_config + 1'b1;
                    6'd33:
                        if (p_wr == 4'hf && !enable_reg && !dma_busy_i)
                            dma_high <= p_dout;
                        else
                            bad_config <= bad_config + 1'b1;
                    6'd40:
                        if (p_wr == 4'hf && p_dout <= 1)
                            wb_shadow_mode <= p_dout;
                        else
                            bad_config <= bad_config + 1'b1;
                    6'd41:
                        if (p_wr == 4'hf && p_dout >= 128 && p_dout <= 512)
                            wb_shadow_r <= p_dout;
                        else
                            bad_config <= bad_config + 1'b1;
                    6'd42:
                        if (p_wr == 4'hf && p_dout >= 128 && p_dout <= 512)
                            wb_shadow_b <= p_dout;
                        else
                            bad_config <= bad_config + 1'b1;
                    6'd43:
                        if (p_wr == 4'hf && p_dout == 32'h5742434d) begin
                            wb_manual_o <= wb_shadow_mode[0];
                            wb_r_o <= wb_shadow_r[9 : 0];
                            wb_b_o <= wb_shadow_b[9 : 0];
                        end else
                            bad_config <= bad_config + 1'b1;
                    6'd45:
                        if (p_wr == 4'hf && p_dout == 0) begin
                            gaussian_o <= p_dout[0];
                            equalize_o <= p_dout[1];
                        end else
                            bad_config <= bad_config + 1'b1;
                    6'd14: begin

                        if (!ptr_rst_i) begin
                            if (ack_ok) begin
                                ack_valid_reg <= 1'b1;
                                ack_frame_id_reg <= p_dout;
                            end else begin
                                invalid_ack <= invalid_ack + 32'd1;
                            end
                        end
                    end
                    default:
                        ;
                endcase
            end

            if (ptr_rst_i)
                ptr_void <= 1'b1;
            else if (srv_state == S_IDLE)
                ptr_void <= 1'b0;
            if (srv_state == S_PTR_COMMIT && ~ptr_rst_i && ~ptr_void) begin
                if (ptr_ok)
                    rd_ptr <= ptr_cand;
                else
                    invalid_rdptr <= invalid_rdptr + 32'd1;
            end
            if (ptr_rst_i)
                rd_ptr <= 32'd0;
        end
    end

    function [31 : 0] rd_reg;
        input [29 : 0] idx;
        begin
            case (idx)
                30'd192:
                    rd_reg = 32'h32435250;
                30'd193:
                    rd_reg = 1;
                30'd194:
                    rd_reg = 32'h00000fff;
                30'd195:
                    rd_reg = 10;
                30'd196:
                    rd_reg = {28'd0, process_select};
                30'd197:
                    rd_reg = process_shadow_word;
                30'd198:
                    rd_reg = 0;
                30'd199:
                    rd_reg = processing_revision_o;
                30'd200:
                    rd_reg = process_effective_revision_i;
                30'd201:
                    rd_reg = process_effective_source_i;
                30'd202:
                    rd_reg = process_effective_word;
                30'd203:
                    rd_reg = process_shadow_word;
                30'd204:
                    rd_reg = {29'd0, process_effective_source_i != 0, process_rejected,
                              processing_revision_o != process_effective_revision_i};
                30'd205:
                    rd_reg = process_reject_count;
                30'd206:
                    rd_reg = 7;
                30'd148:
                    rd_reg = 32'h31474446;
                30'd149:
                    rd_reg = fault_diagnostic_i[0 +: 32];
                30'd150:
                    rd_reg = fault_diagnostic_i[32 +: 32];
                30'd151:
                    rd_reg = fault_diagnostic_i[64 +: 32];
                30'd152:
                    rd_reg = fault_diagnostic_i[96 +: 32];
                30'd153:
                    rd_reg = fault_diagnostic_i[128 +: 32];
                30'd154:
                    rd_reg = fault_diagnostic_i[160 +: 32];
                30'd155:
                    rd_reg = 32'd2073600;
                30'd156:
                    rd_reg = fault_diagnostic_i[192 +: 32];
                30'd157:
                    rd_reg = fault_diagnostic_i[224 +: 32];
                30'd136:
                    rd_reg = capture_config_o;
                30'd137:
                    rd_reg = capture_origin_o;
                30'd138:
                    rd_reg = capture_id_o;
                30'd139:
                    rd_reg = capture_size_o;
                30'd140:
                    rd_reg = 0;
                30'd141:
                    rd_reg = {31'd0, capture_ready_i && !capture_valid_o};
                30'd142:
                    rd_reg = capture_completed_i;
                30'd143:
                    rd_reg = capture_source_i;
                30'd144:
                    rd_reg = 32'h33525453;
                30'd145:
                    rd_reg = epoch_o;
                30'd146:
                    rd_reg = 518400;
                30'd147:
                    rd_reg = processing_revision_o;
                30'd128:
                    rd_reg = {27'd0, dma_page_index};
                30'd129:
                    rd_reg = {dma_page_bases_o[dma_page_index * 12 +: 12], 20'd0};
                30'd130:
                    rd_reg = 32'h314d4753;
                30'd131:
                    rd_reg = 32'h01001014;
                30'd132:
                    rd_reg = {14'd0, dma_page_valid_o};
                30'd125:
                    rd_reg = preview_compression_o;
                30'd126:
                    rd_reg = 0;
                30'd127:
                    rd_reg = 0;
                30'd124:
                    rd_reg = ddr_status_i;
                30'd121:
                    rd_reg = roi_size_o;
                30'd122:
                    rd_reg = 32'h31544352;
                30'd123:
                    rd_reg = 3;
                30'd104:
                    rd_reg = 32'h31545541;
                30'd105:
                    rd_reg = 1;
                30'd106:
                    rd_reg = enhance_config_o[0 +: 32];
                30'd107:
                    rd_reg = enhance_config_o[32 +: 32];
                30'd108:
                    rd_reg = enhance_config_o[96 +: 32];
                30'd109:
                    rd_reg = enhance_config_o[128 +: 32];
                30'd110:
                    rd_reg = enhance_config_o[160 +: 32];
                30'd111:
                    rd_reg = enhance_config_o[192 +: 32];
                30'd112:
                    rd_reg = enhance_config_o[224 +: 32];
                30'd113:
                    rd_reg = enhance_config_o[256 +: 32];
                30'd114:
                    rd_reg = enhance_config_o[288 +: 32];
                30'd115:
                    rd_reg = enhance_config_o[320 +: 32];
                30'd116:
                    rd_reg = enhance_config_o[352 +: 32];
                30'd117:
                    rd_reg = enhance_config_o[64 +: 32];
                30'd118:
                    rd_reg = enhance_rejected;
                30'd119:
                    rd_reg = enhance_revision_i;
                30'd120:
                    rd_reg = enhance_source_i;
                30'd64:
                    rd_reg = 32'h31584d49;
                30'd65:
                    rd_reg = 32'd1;
                30'd66:
                    rd_reg = sensor_command_o[31 : 0];
                30'd67:
                    rd_reg = sensor_command_o[63 : 32];
                30'd68:
                    rd_reg = sensor_command_o[95 : 64];
                30'd69:
                    rd_reg = sensor_command_o[127 : 96];
                30'd70:
                    rd_reg = sensor_rejected;
                30'd71:
                    rd_reg = {31'd0, sensor_toggle_o != sensor_done_i};
                30'd96:
                    rd_reg = quality_origin_o;
                30'd97:
                    rd_reg = {31'd0, quality_freeze_o};
                30'd98:
                    rd_reg = {25'd0, quality_select_o};
                30'd99:
                    rd_reg = quality_data_i;
                6'd63:
                    rd_reg = {31'd0, archive_mode_o};
                6'd62:
                    rd_reg = 0;
                6'd57:
                    rd_reg = compression_config_o;
                6'd58:
                    rd_reg = compression_origin_o;
                6'd59:
                    rd_reg = roi_id_o;
                6'd60:
                    rd_reg = 32'h524f4931;
                6'd61:
                    rd_reg = {31'd0, roi_ready_i && !roi_valid_o};
                6'd52:
                    rd_reg = isp_config_o[31 : 0];
                6'd53:
                    rd_reg = isp_config_o[63 : 32];
                6'd54:
                    rd_reg = isp_config_o[95 : 64];
                6'd55:
                    rd_reg = isp_config_o[127 : 96];
                6'd56:
                    rd_reg = {31'd0, isp_toggle_o};

                6'd29:
                    rd_reg = {25'd0, feature_select_o};
                6'd30:
                    rd_reg = feature_data_i;
                6'd31:
                    rd_reg = feature_frame_i;
                6'd39:
                    rd_reg = industrial_config_o;
                6'd51:
                    rd_reg = 32'h31444e49;
                6'd36:
                    rd_reg = {8'd0, feature_config_o};
                6'd37:
                    rd_reg = {31'd0, feature_fault_i};
                6'd38:
                    rd_reg = 32'h34544653;
                6'd0:
                    rd_reg = 32'h50434531;
                6'd1:
                    rd_reg = 32'h001c0000;
                6'd2:
                    rd_reg = scratch;
                6'd3:
                    rd_reg = {31'd0, enable_reg};
                6'd4:
                    rd_reg = PAYLOAD_BYTES;
                6'd5:
                    rd_reg = 32'd0;
                6'd6:
                    rd_reg = {26'd0,
                              pending_i,
                              camera_status_i[2],
                              camera_status_i[1],
                              camera_status_i[0],
                              busy_i,
                              frame_ready_i};
                6'd7:
                    rd_reg = RING_BYTES;
                6'd8:
                    rd_reg = timer_i;
                6'd9:
                    rd_reg = published_i;
                6'd10:
                    rd_reg = captured_i;
                6'd11:
                    rd_reg = invalid_rdptr;
                6'd12:
                    rd_reg = wr_ptr_i;
                6'd13:
                    rd_reg = {31'd0, frame_ready_i};
                6'd14:
                    rd_reg = 32'd0;
                6'd15:
                    rd_reg = rd_ptr;
                6'd16:
                    rd_reg = frame_ready_i ? frame_id_i : 32'd0;
                6'd17:
                    rd_reg = frame_ready_i ? PAYLOAD_BYTES : 32'd0;
                6'd18:
                    rd_reg = ts_done_i;
                6'd19:
                    rd_reg = ts_publish_i;
                6'd20:
                    rd_reg = frame_ready_i ? frame_crc_i : 32'd0;
                6'd21:
                    rd_reg = {31'd0, frame_ready_i};
                6'd22:
                    rd_reg = frame_ready_i ? start_ptr_i : 32'd0;
                6'd23:
                    rd_reg = frame_ready_i ? 32'h50434631 : 32'd0;
                6'd24:
                    rd_reg = frame_ready_i ? 32'd8 : 32'd0;
                6'd25:
                    rd_reg = CW;
                6'd26:
                    rd_reg = CH;
                6'd27:
                    rd_reg = 32'h33424752;
                6'd28:
                    rd_reg = invalid_ack;
                6'd32:
                    rd_reg = dma_base;
                6'd33:
                    rd_reg = dma_high;
                6'd34:
                    rd_reg = {24'd0,       dma_code_i, dma_stopped_i,
                              dma_fault_i, dma_busy_i, enable_reg};
                6'd35:
                    rd_reg = dma_last_i;
                6'd40:
                    rd_reg = wb_shadow_mode;
                6'd41:
                    rd_reg = wb_shadow_r;
                6'd42:
                    rd_reg = wb_shadow_b;
                6'd43:
                    rd_reg = {11'd0, wb_manual_o, wb_b_o, wb_r_o};
                6'd44:
                    rd_reg = bad_config;
                6'd45:
                    rd_reg = {30'd0, equalize_o, gaussian_o};
                6'd46:
                    rd_reg = 32'd250000000;
                6'd47:
                    rd_reg = 32'd7;
                6'd49:
                    rd_reg = native_debug_i[31 : 0];
                6'd50:
                    rd_reg = native_debug_i[63 : 32];
                6'd48:
                    rd_reg = 32'h33534956;

                default:
                    rd_reg =
                        (idx >= 72 && idx < 88) ? sensor_result_i[(idx - 72) * 32 +: 32] : 32'd0;
            endcase
        end
    endfunction
endmodule
