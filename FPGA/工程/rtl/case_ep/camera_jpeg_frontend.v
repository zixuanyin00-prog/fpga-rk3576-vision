

`resetall
`timescale 1ns / 1ps
`default_nettype none

module camera_jpeg_frontend
    (output wire q_wen, q_reset, q_ready,
     output wire [101 : 0] q_wdata,
     output wire q1_wen, q1_reset, q1_ready,
     output wire [101 : 0] q1_wdata,
     input wire q1_full, q1_valid,
     input wire [101 : 0] q1_data,
     output wire q2_wen, q2_reset, q2_ready,
     output wire [101 : 0] q2_wdata,
     input wire q2_full, q2_valid,
     input wire [101 : 0] q2_data,
     input wire q_full, q_valid,
     input wire [101 : 0] q_data,
     input wire q_error,
     input wire [383 : 0] enhance_config_i,
     input wire enhance_toggle_i,
     output reg [31 : 0] enhance_revision_o = 0, enhance_source_o = 0,
     input wire [127 : 0] sensor_command_i,
     input wire sensor_toggle_i,
     output wire sensor_done_o,
     output wire [511 : 0] sensor_result_o,
     input wire [31 : 0] quality_origin_i,
     input wire quality_freeze_i,
     input wire [6 : 0] quality_select_i,
     output wire [31 : 0] quality_data_o,
     input wire [319 : 0] process_config_i,
     output reg [319 : 0] process_effective_config_o = {32'd0, 32'd32, 32'd61568, 32'd1573123,
                                                        32'd452984576, 32'd69760, 32'd3, 32'd65344,
                                                        32'd0, 32'd1},
     output reg [31 : 0] process_effective_revision_o = 1, process_effective_source_o = 0,
     input wire [6 : 0] feature_select_i,
     output wire [31 : 0] feature_data_o, feature_frame_o,
     output wire feature_fault_o,
     input wire [31 : 0] roi_size_i, processing_revision_i, epoch_i,
     input wire [31 : 0] capture_config_i, capture_origin_i, capture_size_i, capture_id_i,
     input wire capture_valid_i,
     output wire capture_ready_o,
     output wire [31 : 0] capture_completed_o, capture_source_o,
     input wire [31 : 0] compression_config_i, compression_origin_i, roi_id_i,
     input wire roi_valid_i,
     output wire roi_ready_o,
     input wire clk_ref_i, clk_core_i, pcie_app_rst_ni, rst_core_i, enable_i,
     input wire [127 : 0] isp_config_i,
     input wire isp_toggle_i,
     input wire [31 : 0] base_i,
     input wire manual_wb_i,
     input wire [9 : 0] wb_r_i, wb_b_i,
     output wire [127 : 0] tx_data_o,
     output wire [63 : 0] tx_addr_o,
     output wire [12 : 0] tx_bytes_o,
     output wire [7 : 0] tx_be_o,
     output wire tx_hv_o, tx_dv_o, tx_eot_o,
     input wire tx_halt_i,
     output wire busy_o, stopped_o, fault_o,
     output wire [3 : 0] fault_code_o,
     output wire [31 : 0] completed_o, timer_o,
     output wire [63 : 0] native_debug_o,
     output wire [255 : 0] fault_diagnostic_o,
     output wire camera_pll_lock_o, camera_ready_o, camera_lane_error_o, camera_capture_error_o,
     output wire S_clk_25m, O_clk_rst, O_cam_scl,
     inout wire IO_cam_sda,
     input wire I_clk_hs_p,
     input wire [3 : 0] I_data_hs_p, I_data_lp_p, I_data_lp_n);
    localparam [319 : 0] PROCESS_DEFAULT = {32'd0,         32'd32,    32'd61568, 32'd1573123,
                                            32'd452984576, 32'd69760, 32'd3,     32'd65344,
                                            32'd0,         32'd1};

    wire S_clk_70m;
    wire S_clk_25m_w;
    wire S_pll_lock, config_pll_lock, mclk_lock, mclk27;
    assign S_pll_lock = config_pll_lock & mclk_lock;
    camera_mclk27 u_mclk27(.refclk(clk_ref_i),
                           .reset(1'b0),
                           .lock(mclk_lock),
                           .clk0_out(),
                           .clk1_out(mclk27),
                           .clk2_out(),
                           .clk3_out(),
                           .clk4_out(),
                           .clk5_out(),
                           .clk6_out());
    wire S_hs_rx_clk;
    wire isp_reset, isp_fault;

    ui_pll X_ui_pll(.refclk(clk_ref_i),
                    .reset(1'b0),
                    .lock(config_pll_lock),
                    .clk0_out(S_clk_70m),
                    .clk1_out(S_clk_25m_w),
                    .clk2_out(),
                    .clk3_out(),
                    .clk4_out(),
                    .clk5_out(),
                    .clk6_out());

    assign S_clk_25m = mclk27;

    wire S_cam_cfg_done;
    wire boot_done, hot_req, hot_read, hot_busy, hot_error;
    wire [31 : 0] hot_data;
    wire [7 : 0] hot_read_data;
    wire [31 : 0] preview_source_id;
    wire [95 : 0] rgb_pixels, quality_pixels;
    wire rgb_valid, rgb_sof, rgb_eol, rgb_fe, quality_valid, quality_sof, quality_eol, quality_fe;
    wire [31 : 0] rgb_revision;
    wire [95 : 0] processed_pixels;
    wire processed_valid, processed_sof, processed_eol, processed_fe, processing_fault;
    wire [31 : 0] processed_revision, processed_enhance_revision;
    wire [319 : 0] frame_process_config, common_effective_config;
    wire [31 : 0] frame_process_revision;
    always @(posedge clk_core_i) begin
        if (rgb_valid && rgb_sof) begin
            enhance_revision_o <= rgb_revision;
            enhance_source_o <= preview_source_id + 1;
        end
        if (!pcie_app_rst_ni) begin
            enhance_revision_o <= 0;
            enhance_source_o <= 0;
        end
    end
    reg sensor_window_toggle = 0, sensor_frame_toggle = 0;
    reg [11 : 0] sensor_row = 0;
    always @(posedge clk_core_i) begin
        if (rgb_valid) begin
            if (rgb_sof) begin
                sensor_row <= 0;
                sensor_frame_toggle <= ~sensor_frame_toggle;
            end else if (rgb_eol) begin
                sensor_row <= sensor_row + 1'b1;
                if (sensor_row == 256)
                    sensor_window_toggle <= ~sensor_window_toggle;
            end
        end
        if (isp_reset) begin
            sensor_row <= 0;
            sensor_window_toggle <= 0;
            sensor_frame_toggle <= 0;
        end
    end
    wire sensor_done_25;
    wire [511 : 0] sensor_result_25;
    reg [2 : 0] sensor_done_sync = 0;
    reg [511 : 0] sensor_snapshot = 0;
    reg sensor_done_core = 0;
    always @(posedge clk_core_i) begin
        sensor_done_sync <= {sensor_done_sync[1 : 0], sensor_done_25};
        if (sensor_done_sync[2] != sensor_done_core) begin
            sensor_snapshot <= sensor_result_25;

            sensor_snapshot[9 * 32 +: 32] <= preview_source_id + 1;
            sensor_done_core <= sensor_done_sync[2];
        end
        if (!pcie_app_rst_ni) begin
            sensor_done_sync <= 0;
            sensor_snapshot <= 0;
            sensor_done_core <= 0;
        end
    end
    assign sensor_done_o = sensor_done_core;
    assign sensor_result_o = sensor_snapshot;
    imx415_hot_control hot_sensor(.clk(S_clk_25m_w),
                                  .rst(!S_pll_lock || !pcie_app_rst_ni),
                                  .boot_done(boot_done),
                                  .request_toggle(sensor_toggle_i),
                                  .command(sensor_command_i),
                                  .window_toggle(sensor_window_toggle),
                                  .frame_toggle(sensor_frame_toggle),
                                  .i2c_busy(hot_busy),
                                  .i2c_error(hot_error),
                                  .i2c_data(hot_read_data),
                                  .i2c_req(hot_req),
                                  .i2c_read(hot_read),
                                  .i2c_wr(hot_data),
                                  .done_toggle(sensor_done_25),
                                  .result(sensor_result_25));
    ui_cfg_imx415 #(.CLK_DIV(25_000_000 / 100_000 - 1))
        X_ui_cfg_imx415(.I_clk(S_clk_25m_w),
                        .I_rst_n(S_pll_lock),
                        .I_ae_req(1'b0),
                        .I_ae_data(8'd0),
                        .I_hot_data(hot_data),
                        .I_hot_req(hot_req),
                        .I_hot_read(hot_read),
                        .O_hot_data(hot_read_data),
                        .O_hot_busy(hot_busy),
                        .O_hot_error(hot_error),
                        .O_startup_done(boot_done),
                        .O_cam_scl(O_cam_scl),
                        .IO_cam_sda(IO_cam_sda),
                        .O_cfg_done(),
                        .O_ae_cfg_done());
    assign S_cam_cfg_done = boot_done;

    wire S_hs_rx_valid;
    wire [31 : 0] S_hs_rx_data;
    wire [3 : 0] S_lane_error;

    mipi_dphy_rx_over_lvds_wrapper #(.DEVICE("PH1"),
                                     .LANE_NUM(4))
        X_mipi_dphy_rx_over_lvds_wrapper(.I_lp_clk(S_clk_70m),
                                         .I_rst(~S_pll_lock),
                                         .I_mipi_clk_p(I_clk_hs_p),
                                         .I_mipi_data_hs_p(I_data_hs_p),
                                         .I_mipi_data_lp_p(I_data_lp_p),
                                         .I_mipi_data_lp_n(I_data_lp_n),
                                         .I_lane_hs_invert(4'b0000),
                                         .I_lane_lp_invert(4'b0000),
                                         .O_hs_rx_clk(S_hs_rx_clk),
                                         .O_hs_rx_valid(S_hs_rx_valid),
                                         .O_hs_rx_data(S_hs_rx_data),
                                         .O_lp_rx_lane0_p(),
                                         .O_lp_rx_lane0_n(),
                                         .O_lane_error(S_lane_error));

    wire S_csi_frame_start;
    wire S_csi_frame_end;
    wire S_csi_valid;
    wire [31 : 0] S_csi_data;

    ui_csi_unpacket_4lane X_ui_csi_unpacket_4lane(.I_clk(S_hs_rx_clk),
                                                  .I_rst_n(S_pll_lock),
                                                  .I_hs_valid(S_hs_rx_valid),
                                                  .I_hs_data(S_hs_rx_data),
                                                  .O_csi_frame_start(S_csi_frame_start),
                                                  .O_csi_frame_end(S_csi_frame_end),
                                                  .O_csi_valid(S_csi_valid),
                                                  .O_csi_data(S_csi_data));

    wire S_raw10_frame_start;
    wire S_raw10_frame_end;
    wire S_raw10_valid;
    wire [39 : 0] S_raw10_data;

    ui_raw10_unpacket_4lane X_ui_raw10_unpacket_4lane(.I_clk(S_hs_rx_clk),
                                                      .I_rst_n(S_pll_lock),
                                                      .I_csi_frame_start(S_csi_frame_start),
                                                      .I_csi_frame_end(S_csi_frame_end),
                                                      .I_csi_valid(S_csi_valid),
                                                      .I_csi_data(S_csi_data),
                                                      .O_raw10_frame_start(S_raw10_frame_start),
                                                      .O_raw10_frame_end(S_raw10_frame_end),
                                                      .O_raw10_valid(S_raw10_valid),
                                                      .O_raw10_data(S_raw10_data));

    wire [39 : 0] S_axis_tdata;
    wire S_axis_tlast;
    wire S_axis_tuser;
    wire S_axis_tvalid;
    wire [9 : 0] S_axis_tdest;

    ui_image_correction #(.DATA_WIDTH(40),
                          .TDEST_WIDTH(10))
        X_ui_image_correction(.I_clk(S_hs_rx_clk),
                              .I_rst_n(S_pll_lock),
                              .I_raw_data(S_raw10_data),
                              .I_raw_valid(S_raw10_valid),
                              .I_raw_frame_start(S_raw10_frame_start),
                              .I_raw_frame_end(S_raw10_frame_end),
                              .O_raw_tdata(S_axis_tdata),
                              .O_raw_tlast(S_axis_tlast),
                              .O_raw_tdest(S_axis_tdest),
                              .O_raw_tvalid(S_axis_tvalid),
                              .O_raw_tuser(S_axis_tuser),
                              .O_raw_tready(1'b1));

    wire cam_ready_25 = S_pll_lock & S_cam_cfg_done;

    wire [39 : 0] captured_raw;
    wire captured_valid, captured_sof, captured_eol, captured_error;
    isp_capture_cdc raw_cdc(.cam_clk(S_hs_rx_clk),
                            .core_clk(clk_core_i),
                            .arst(~(pcie_app_rst_ni & S_pll_lock)),
                            .valid(S_axis_tvalid),
                            .sof(S_axis_tuser),
                            .eol(S_axis_tlast),
                            .raw(S_axis_tdata),
                            .cam_error(|S_lane_error),
                            .out_raw(captured_raw),
                            .out_valid(captured_valid),
                            .out_sof(captured_sof),
                            .out_eol(captured_eol),
                            .out_error(captured_error),
                            .core_reset(isp_reset));

    shared_image_process4 #(.W(1920),
                            .H(1080))
        common_processing(.clk(clk_core_i),
                          .rst(isp_reset),
                          .valid(rgb_valid),
                          .sof(rgb_sof),
                          .eol(rgb_eol),
                          .fe(rgb_fe),
                          .rgb(rgb_pixels),
                          .process_config_i(frame_process_config),
                          .select_i(feature_select_i),
                          .processing_revision_i(frame_process_revision),
                          .enhance_revision_i(rgb_revision),
                          .process_effective_config_o(common_effective_config),
                          .revision_o(processed_revision),
                          .enhance_revision_o(processed_enhance_revision),
                          .data_o(feature_data_o),
                          .frame_o(feature_frame_o),
                          .fault_o(feature_fault_o),
                          .pixels(processed_pixels),
                          .ov(processed_valid),
                          .os(processed_sof),
                          .oe(processed_eol),
                          .ofe(processed_fe));
    assign processing_fault = feature_fault_o;
    camera_quality4 quality(.clk(clk_core_i),
                            .rst(isp_reset),
                            .valid(quality_valid),
                            .sof(quality_sof),
                            .eol(quality_eol),
                            .fe(quality_fe),
                            .pixels(quality_pixels),
                            .source_id(preview_source_id),
                            .origin(quality_origin_i),
                            .freeze(quality_freeze_i),
                            .select(quality_select_i),
                            .data(quality_data_o),
                            .source_error(captured_error | isp_fault));

    isp4 parallel_isp(.enhance_config_i(enhance_config_i),
                      .enhance_toggle_i(enhance_toggle_i),
                      .rgb_revision(rgb_revision),
                      .process_config_i(process_config_i),
                      .processing_revision_i(processing_revision_i),
                      .process_frame_config_o(frame_process_config),
                      .process_frame_revision_o(frame_process_revision),
                      .quality_pixels(quality_pixels),
                      .quality_valid(quality_valid),
                      .quality_sof(quality_sof),
                      .quality_eol(quality_eol),
                      .quality_fe(quality_fe),
                      .rgb_pixels(rgb_pixels),
                      .rgb_valid(rgb_valid),
                      .rgb_sof(rgb_sof),
                      .rgb_eol(rgb_eol),
                      .rgb_fe(rgb_fe),
                      .clk(clk_core_i),
                      .rst(isp_reset),
                      .valid(captured_valid),
                      .sof(captured_sof),
                      .eol(captured_eol),
                      .raw(captured_raw),
                      .config_i(isp_config_i),
                      .config_toggle_i(isp_toggle_i),
                      .awb_enable_i(!manual_wb_i),
                      .manual_r_i(wb_r_i),
                      .manual_b_i(wb_b_i),
                      .error(isp_fault));

    wire [127 : 0] rgb_tx_data_o;
    assign tx_data_o = rgb_tx_data_o;
    wire [63 : 0] rgb_tx_addr_o;
    assign tx_addr_o = rgb_tx_addr_o;
    wire [12 : 0] rgb_tx_bytes_o;
    assign tx_bytes_o = rgb_tx_bytes_o;
    wire [7 : 0] rgb_tx_be_o;
    assign tx_be_o = rgb_tx_be_o;
    wire rgb_tx_hv_o;
    assign tx_hv_o = rgb_tx_hv_o;
    wire rgb_tx_dv_o;
    assign tx_dv_o = rgb_tx_dv_o;
    wire rgb_tx_eot_o;
    assign tx_eot_o = rgb_tx_eot_o;
    wire rgb_busy_o;
    assign busy_o = rgb_busy_o;
    wire rgb_stopped_o;
    assign stopped_o = rgb_stopped_o;
    wire rgb_fault_o;
    assign fault_o = rgb_fault_o;
    wire [3 : 0] rgb_fault_code_o;
    assign fault_code_o = rgb_fault_code_o;
    wire [31 : 0] rgb_completed_id_o;
    assign completed_o = rgb_completed_id_o;
    wire [31 : 0] rgb_timer_o;
    assign timer_o = rgb_timer_o;
    wire [63 : 0] rgb_debug_o;
    assign native_debug_o = rgb_debug_o;

    wire [127 : 0] stream_data[0 : 2];
    wire [63 : 0] stream_addr[0 : 2];
    wire [12 : 0] stream_bytes[0 : 2];
    wire [7 : 0] stream_be[0 : 2];
    wire [2 : 0] stream_hv, stream_dv, stream_eot, stream_busy, stream_stop, stream_fault;
    wire [15 : 0] raw_diagnostic_cause, fine_diagnostic_cause, capture_diagnostic_cause;
    wire [127 : 0] raw_diagnostic_context;
    wire [2 : 0] diagnostic_events;
    reg [31 : 0] first_fault_flags = 0;
    reg diagnostic_enable_d = 0;
    wire diagnostic_session_start = enable_i && !diagnostic_enable_d;

    always @(posedge clk_core_i) begin
        diagnostic_enable_d <= enable_i;
        if (enable_i && first_fault_flags == 0 && |diagnostic_events)
            first_fault_flags <= {25'd0,
                                  diagnostic_events[2],
                                  diagnostic_events[1],
                                  diagnostic_events[0],
                                  q_error,
                                  processing_fault,
                                  isp_fault,
                                  captured_error};
        if (diagnostic_session_start)
            first_fault_flags <= 0;
        if (rst_core_i || isp_reset) begin
            diagnostic_enable_d <= 0;
            first_fault_flags <= 0;
        end
    end
    assign fault_diagnostic_o = {16'd0,
                                 capture_diagnostic_cause,
                                 16'd0,
                                 fine_diagnostic_cause,
                                 raw_diagnostic_context,
                                 16'd0,
                                 raw_diagnostic_cause,
                                 first_fault_flags};
    wire [3 : 0] stream_code[0 : 2];
    wire [31 : 0] stream_completed[0 : 2], stream_timer[0 : 2];
    wire [63 : 0] stream_debug[0 : 2];
    reg granted = 0;
    reg [1 : 0] grant = 0, last_grant = 2;
    wire halt = tx_halt_i;
    function [1 : 0] choose_stream;
        input [2 : 0] pending;
        input [1 : 0] last;
        begin
            choose_stream = 3;
            case (last)
                0:
                    if (pending[1])
                        choose_stream = 1;
                    else if (pending[2])
                        choose_stream = 2;
                    else if (pending[0])
                        choose_stream = 0;
                1:
                    if (pending[2])
                        choose_stream = 2;
                    else if (pending[0])
                        choose_stream = 0;
                    else if (pending[1])
                        choose_stream = 1;
                default:
                    if (pending[0])
                        choose_stream = 0;
                    else if (pending[1])
                        choose_stream = 1;
                    else if (pending[2])
                        choose_stream = 2;
            endcase
        end
    endfunction

    wire [1 : 0] next_grant = stream_dv[0] ? 2'd0 : choose_stream(stream_dv, last_grant);
    always @(posedge clk_core_i) begin
        if (!granted) begin
            if (next_grant != 3) begin
                granted <= 1;
                grant <= next_grant;
            end
        end else if (!halt && stream_dv[grant] && stream_eot[grant]) begin
            granted <= 0;
            if (grant != 0)
                last_grant <= grant;
        end
        if (rst_core_i || isp_reset) begin
            granted <= 0;
            grant <= 0;
            last_grant <= 2;
        end
    end
    assign rgb_tx_data_o = stream_data[grant];
    assign rgb_tx_addr_o = stream_addr[grant];
    assign rgb_tx_bytes_o = stream_bytes[grant];
    assign rgb_tx_be_o = stream_be[grant];
    assign rgb_tx_hv_o = granted && stream_hv[grant];
    assign rgb_tx_dv_o = granted && stream_dv[grant];
    assign rgb_tx_eot_o = granted && stream_eot[grant];
    assign rgb_busy_o = |stream_busy;
    assign rgb_stopped_o = &stream_stop;
    assign rgb_fault_o = |stream_fault;
    assign rgb_fault_code_o = stream_fault[0]   ? stream_code[0]
                              : stream_fault[1] ? stream_code[1]
                                                : stream_code[2];
    assign rgb_completed_id_o = stream_completed[0];
    assign rgb_timer_o = stream_timer[0];
    assign rgb_debug_o = stream_debug[0];
    assign capture_completed_o = stream_completed[2];
    assign capture_source_o = stream_debug[2][31 : 0];

    reg [31 : 0] preview_sensor_id = 0;
    always @(posedge clk_core_i) begin
        if (processed_valid && processed_sof) begin
            preview_sensor_id <= preview_sensor_id + 1;
            process_effective_config_o <= common_effective_config;
            process_effective_revision_o <= processed_revision;
            process_effective_source_o <= preview_sensor_id + 1;
        end
        if (rst_core_i || isp_reset) begin
            preview_sensor_id <= 0;
            process_effective_config_o <= PROCESS_DEFAULT;
            process_effective_revision_o <= 1;
            process_effective_source_o <= 0;
        end
    end
    assign preview_source_id = preview_sensor_id;
    wire [127 : 0] raw_data;
    wire [63 : 0] raw_addr;
    wire [12 : 0] raw_bytes;
    wire [7 : 0] raw_be;
    wire raw_hv, raw_dv, raw_eot, raw_busy, raw_stop, raw_fault, raw_queue_reset;
    wire [3 : 0] raw_code;
    wire [31 : 0] raw_completed, raw_timer;
    wire [63 : 0] raw_debug;
    assign q2_reset = raw_queue_reset;
    assign stream_data[0] = raw_data;
    assign stream_addr[0] = raw_addr;
    assign stream_bytes[0] = raw_bytes;
    assign stream_be[0] = raw_be;
    assign stream_hv[0] = raw_hv;
    assign stream_dv[0] = raw_dv;
    assign stream_eot[0] = raw_eot;
    assign stream_busy[0] = raw_busy;
    assign stream_stop[0] = raw_stop;
    assign stream_fault[0] = raw_fault;
    assign stream_code[0] = raw_code;
    assign stream_completed[0] = raw_completed;
    assign stream_timer[0] = raw_timer;
    assign stream_debug[0] = raw_debug;

    rgb_roi_chain #(.W(1920),
                    .H(1080),
                    .AUTO_PREVIEW(1),
                    .PREVIEW_QUAD(1),
                    .SLOT_COUNT(2),
                    .DIAGNOSTIC_CONTEXT(1),
                    .SLOT_STRIDE('h600000),
                    .META_OFFSET('h5ff000),
                    .STOP_OFFSET('hbffff0),
                    .EXTERNAL_QUEUE(1),
                    .EXTERNAL_SOURCE_ID(1),
                    .CHANNEL_ID(0))
        raw_chain(.source_id_i(preview_source_id),
                  .enhance_revision_i(processed_enhance_revision),
                  .processing_revision_i(processed_revision),
                  .epoch_i(epoch_i),
                  .q_wen(q2_wen),
                  .q_reset(raw_queue_reset),
                  .q_ready(q2_ready),
                  .q_wdata(q2_wdata),
                  .q_full(q2_full),
                  .q_valid(q2_valid),
                  .q_data(q2_data),
                  .clk(clk_core_i),
                  .rst(rst_core_i | isp_reset),
                  .enable(enable_i),
                  .base(base_i),
                  .source_error(captured_error | isp_fault | processing_fault | q_error),
                  .pixels(processed_pixels),
                  .valid(processed_valid),
                  .sof(processed_sof),
                  .eol(processed_eol),
                  .fe(processed_fe),
                  .command_continuous(1'b1),
                  .command_valid(1'b0),
                  .command_ready(),
                  .command_roi(1'b0),
                  .command_id(32'd0),
                  .command_origin(32'd0),
                  .command_size(32'd0),
                  .current_source_id(),
                  .tx_data_o(raw_data),
                  .tx_addr_o(raw_addr),
                  .tx_bytes_o(raw_bytes),
                  .tx_be_o(raw_be),
                  .tx_hv_o(raw_hv),
                  .tx_dv_o(raw_dv),
                  .tx_eot_o(raw_eot),
                  .tx_halt_i(halt || !granted || grant != 0),
                  .busy_o(raw_busy),
                  .stopped_o(raw_stop),
                  .fault_o(raw_fault),
                  .fault_code_o(raw_code),
                  .completed_id_o(raw_completed),
                  .timer_o(raw_timer),
                  .debug_o(raw_debug),
                  .diagnostic_cause_o(raw_diagnostic_cause),
                  .diagnostic_context_o(raw_diagnostic_context),
                  .diagnostic_event_o(diagnostic_events[0]));

    wire [1 : 0] roi_frame_waiting, roi_frame_grant;
    reg last_roi_frame = 0;
    assign roi_frame_grant[0] = roi_frame_waiting[0] && (!roi_frame_waiting[1] || last_roi_frame);
    assign roi_frame_grant[1] = roi_frame_waiting[1] && (!roi_frame_waiting[0] || !last_roi_frame);
    always @(posedge clk_core_i) begin
        if (processed_valid && processed_sof && |roi_frame_waiting)
            last_roi_frame <= roi_frame_grant[1];
        if (rst_core_i || isp_reset)
            last_roi_frame <= 0;
    end
    rgb_roi_chain #(.AUTO_NATIVE(1),
                    .SLOT_COUNT(1),
                    .SLOT_STRIDE('h200000),
                    .META_OFFSET('h1ff000),
                    .STOP_OFFSET('h1ffff0),
                    .EXTERNAL_QUEUE(1),
                    .EXTERNAL_SOURCE_ID(1),
                    .CHANNEL_ID(1),
                    .FRAME_ARBITRATION(1))
        fine_chain(.source_id_i(preview_source_id),
                   .enhance_revision_i(processed_enhance_revision),
                   .processing_revision_i(processed_revision),
                   .epoch_i(epoch_i),
                   .q_wen(q_wen),
                   .q_reset(q_reset),
                   .q_ready(q_ready),
                   .q_wdata(q_wdata),
                   .q_full(q_full),
                   .q_valid(q_valid),
                   .q_data(q_data),
                   .clk(clk_core_i),
                   .rst(rst_core_i | isp_reset),
                   .enable(enable_i),
                   .base(base_i + 32'hc00000),
                   .source_error(captured_error | isp_fault | processing_fault | q_error),
                   .pixels(processed_pixels),
                   .valid(processed_valid),
                   .sof(processed_sof),
                   .eol(processed_eol),
                   .fe(processed_fe),
                   .command_continuous(compression_config_i[1]),
                   .command_valid(roi_valid_i),
                   .command_ready(roi_ready_o),
                   .command_roi(1'b1),
                   .command_id(roi_id_i),
                   .command_origin(compression_origin_i),
                   .command_size(roi_size_i),
                   .frame_grant(roi_frame_grant[0]),
                   .frame_waiting(roi_frame_waiting[0]),
                   .tx_data_o(stream_data[1]),
                   .tx_addr_o(stream_addr[1]),
                   .tx_bytes_o(stream_bytes[1]),
                   .tx_be_o(stream_be[1]),
                   .tx_hv_o(stream_hv[1]),
                   .tx_dv_o(stream_dv[1]),
                   .tx_eot_o(stream_eot[1]),
                   .tx_halt_i(halt || !granted || grant != 1),
                   .busy_o(stream_busy[1]),
                   .stopped_o(stream_stop[1]),
                   .fault_o(stream_fault[1]),
                   .fault_code_o(stream_code[1]),
                   .completed_id_o(stream_completed[1]),
                   .timer_o(stream_timer[1]),
                   .debug_o(stream_debug[1]),
                   .diagnostic_cause_o(fine_diagnostic_cause),
                   .diagnostic_context_o(),
                   .diagnostic_event_o(diagnostic_events[1]));
    rgb_roi_chain #(.AUTO_NATIVE(1),
                    .SLOT_COUNT(1),
                    .SLOT_STRIDE('h200000),
                    .META_OFFSET('h1ff000),
                    .STOP_OFFSET('h1ffff0),
                    .EXTERNAL_QUEUE(1),
                    .EXTERNAL_SOURCE_ID(1),
                    .CHANNEL_ID(2),
                    .FRAME_ARBITRATION(1))
        capture_chain(.source_id_i(preview_source_id),
                      .enhance_revision_i(processed_enhance_revision),
                      .processing_revision_i(processed_revision),
                      .epoch_i(epoch_i),
                      .q_wen(q1_wen),
                      .q_reset(q1_reset),
                      .q_ready(q1_ready),
                      .q_wdata(q1_wdata),
                      .q_full(q1_full),
                      .q_valid(q1_valid),
                      .q_data(q1_data),
                      .clk(clk_core_i),
                      .rst(rst_core_i | isp_reset),
                      .enable(enable_i),
                      .base(base_i + 32'he00000),
                      .source_error(captured_error | isp_fault | processing_fault | q_error),
                      .pixels(processed_pixels),
                      .valid(processed_valid),
                      .sof(processed_sof),
                      .eol(processed_eol),
                      .fe(processed_fe),
                      .command_continuous(capture_config_i[1]),
                      .command_valid(capture_valid_i),
                      .command_ready(capture_ready_o),
                      .command_roi(1'b1),
                      .command_id(capture_id_i),
                      .command_origin(capture_origin_i),
                      .command_size(capture_size_i),
                      .frame_grant(roi_frame_grant[1]),
                      .frame_waiting(roi_frame_waiting[1]),
                      .tx_data_o(stream_data[2]),
                      .tx_addr_o(stream_addr[2]),
                      .tx_bytes_o(stream_bytes[2]),
                      .tx_be_o(stream_be[2]),
                      .tx_hv_o(stream_hv[2]),
                      .tx_dv_o(stream_dv[2]),
                      .tx_eot_o(stream_eot[2]),
                      .tx_halt_i(halt || !granted || grant != 2),
                      .busy_o(stream_busy[2]),
                      .stopped_o(stream_stop[2]),
                      .fault_o(stream_fault[2]),
                      .fault_code_o(stream_code[2]),
                      .completed_id_o(stream_completed[2]),
                      .timer_o(stream_timer[2]),
                      .debug_o(stream_debug[2]),
                      .diagnostic_cause_o(capture_diagnostic_cause),
                      .diagnostic_context_o(),
                      .diagnostic_event_o(diagnostic_events[2]));
    assign camera_capture_error_o = fault_o;
    assign camera_pll_lock_o = S_pll_lock;
    assign camera_ready_o = cam_ready_25;
    assign camera_lane_error_o = |S_lane_error;
    assign O_clk_rst = 1'b1;
endmodule

`resetall

`timescale 1ns / 1ps
module isp_capture_cdc
    (input wire cam_clk, core_clk, arst, valid, sof, eol,
     input wire [39 : 0] raw,
     input wire cam_error,
     output wire [39 : 0] out_raw,
     output wire out_valid, out_sof, out_eol, out_error, core_reset);

    (* async_reg = "true" *) reg [1 : 0] cam_reset = 3, isp_reset = 3;
    always @(posedge cam_clk or posedge arst)
        if (arst)
            cam_reset <= 3;
        else
            cam_reset <= {cam_reset[0], 1'b0};
    always @(posedge core_clk or posedge arst)
        if (arst)
            isp_reset <= 3;
        else
            isp_reset <= {isp_reset[0], 1'b0};
    assign core_reset = isp_reset[1];
    reg aligned = 0, failed = 0;
    wire write_pixel = valid && (aligned || sof) && !cam_reset[1];
    wire full;
    wire [41 : 0] packet;

    reg stage_valid = 0;
    reg [41 : 0] stage_packet = 0;
    always @(posedge cam_clk) begin
        stage_valid <= write_pixel;
        if (write_pixel)
            stage_packet <= {sof, eol, raw};
        if (valid && sof)
            aligned <= 1;
        if ((stage_valid && full) || cam_error)
            failed <= 1;
        if (cam_reset[1]) begin
            aligned <= 0;
            failed <= 0;
            stage_valid <= 0;
        end
    end
    camera_hist_fifo #(.WIDTH(42),
                       .ABITS(10))
        raw_fifo(.wclk(cam_clk),
                 .wrst(cam_reset[1]),
                 .wen(stage_valid),
                 .wdata(stage_packet),
                 .full(full),
                 .rclk(core_clk),
                 .rrst(isp_reset[1]),
                 .ready(1'b1),
                 .valid(out_valid),
                 .data(packet));
    assign {out_sof, out_eol, out_raw} = packet;
    (* async_reg = "true" *) reg [1 : 0] error_sync = 0;
    always @(posedge core_clk) begin
        error_sync <= {error_sync[0], failed};
        if (isp_reset[1])
            error_sync <= 0;
    end
    assign out_error = error_sync[1];
endmodule
