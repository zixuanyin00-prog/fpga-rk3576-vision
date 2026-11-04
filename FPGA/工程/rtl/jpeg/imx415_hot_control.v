

module imx415_hot_control
    #(parameter TRANSACTION_TIMEOUT = 25000, REQUEST_TIMEOUT = 75000000,
      parameter PERIOD_MIN = 405000, PERIOD_MAX = 430000)
    (input wire clk, rst, boot_done,
     input wire request_toggle,
     input wire [127 : 0] command,
     input wire window_toggle, frame_toggle,
     input wire i2c_busy, i2c_error,
     input wire [7 : 0] i2c_data,
     output reg i2c_req = 0, i2c_read = 0,
     output reg [31 : 0] i2c_wr = 0,
     output reg done_toggle = 0,
     output reg [511 : 0] result = 0);
    reg [2 : 0] req_sync = 0, win_sync = 0, frame_sync = 0;
    reg win_seen = 0, frame_seen = 0, req_seen = 0;
    reg [31 : 0] age = 0, period = 0, frame_seq = 0, deadline = 0, timeout = 0;
    reg [31 : 0] req_id = 0, target_shr = 0, target_gain = 0, flags = 0, generation = 0;
    reg [31 : 0] previous_shr = 0, previous_gain = 0, error = 0;
    reg [19 : 0] shr = 0, vmax = 0;
    reg [15 : 0] hmax = 0;
    reg [8 : 0] gain = 0;
    reg [7 : 0] wd = 0, addmode = 0, adbit = 0, hadd = 0, vadd = 0, master = 0, hold_reg = 0;
    reg [16 : 0] read_mask = 0;
    reg [3 : 0] state = 0;
    reg [1 : 0] phase = 0;
    reg [4 : 0] step = 0;
    reg nack = 0, write_attempted = 0;
    reg [2 : 0] settle = 0;
    reg [15 : 0] error_address = 0, addr;
    reg [7 : 0] value;
    wire new_frame = frame_sync[2] != frame_seen;
    wire window = win_sync[2] != win_seen;
    localparam IDLE = 0, WAIT_WINDOW = 1, START = 2, BUSY = 3, NEXT = 4, CHECK = 5, SETTLE = 6,
               FINISH = 7;
    always @* begin
        addr = 0;
        value = 0;
        if (phase == 1)
            case (step)
                0: begin
                    addr = 16'h3001;
                    value = 1;
                end
                1: begin
                    addr = 16'h3050;
                    value = target_shr[7 : 0];
                end
                2: begin
                    addr = 16'h3051;
                    value = target_shr[15 : 8];
                end
                3: begin
                    addr = 16'h3052;
                    value = {4'd0, target_shr[19 : 16]};
                end
                4: begin
                    addr = 16'h3090;
                    value = target_gain[7 : 0];
                end
                5: begin
                    addr = 16'h3091;
                    value = 0;
                end
                6: begin
                    addr = 16'h3001;
                    value = 0;
                end
                default: begin
                    addr = 0;
                    value = 0;
                end
            endcase
        else
            case (step)
                0:
                    addr = 16'h3050;
                1:
                    addr = 16'h3051;
                2:
                    addr = 16'h3052;
                3:
                    addr = 16'h3090;
                4:
                    addr = 16'h3091;
                5:
                    addr = 16'h3024;
                6:
                    addr = 16'h3025;
                7:
                    addr = 16'h3026;
                8:
                    addr = 16'h3028;
                9:
                    addr = 16'h3029;
                10:
                    addr = 16'h302c;
                11:
                    addr = 16'h3022;
                12:
                    addr = 16'h3031;
                13:
                    addr = 16'h3020;
                14:
                    addr = 16'h3021;
                15:
                    addr = 16'h3003;
                16:
                    addr = 16'h3001;
                default:
                    addr = 0;
            endcase
    end
    always @(posedge clk) begin
        req_sync <= {req_sync[1 : 0], request_toggle};
        win_sync <= {win_sync[1 : 0], window_toggle};
        frame_sync <= {frame_sync[1 : 0], frame_toggle};
        win_seen <= win_sync[2];
        frame_seen <= frame_sync[2];
        if (age != 32'hffffffff)
            age <= age + 1;
        if (new_frame) begin
            period <= age + 1;
            age <= 0;
            frame_seq <= frame_seq + 1;
        end
        if (state != IDLE)
            deadline <= deadline + 1;
        case (state)
            IDLE: begin
                i2c_req <= 0;
                if (req_sync[2] != req_seen && boot_done && !i2c_busy) begin
                    req_seen <= req_sync[2];
                    req_id <= command[31 : 0];
                    target_shr <= command[63 : 32];
                    target_gain <= command[95 : 64];
                    flags <= command[127 : 96];
                    phase <= 0;
                    step <= 0;
                    read_mask <= 0;
                    error <= 0;
                    error_address <= 0;
                    write_attempted <= 0;
                    deadline <= 0;
                    state <= WAIT_WINDOW;
                end
            end
            WAIT_WINDOW:
                if (window && !i2c_busy && age < PERIOD_MAX) begin
                    i2c_wr <= {value, addr[7 : 0], addr[15 : 8], 8'h34};
                    i2c_read <= phase != 1;
                    i2c_req <= 1;
                    timeout <= 0;
                    nack <= 0;
                    state <= START;
                    if (phase == 1)
                        write_attempted <= 1;
                end
            START: begin
                timeout <= timeout + 1;
                if (i2c_busy) begin
                    i2c_req <= 0;
                    state <= BUSY;
                end
                if (i2c_error)
                    nack <= 1;
                if (timeout >= TRANSACTION_TIMEOUT) begin
                    error <= 2;
                    error_address <= addr;
                    state <= FINISH;
                end
            end
            BUSY: begin
                timeout <= timeout + 1;
                if (i2c_error)
                    nack <= 1;
                if (!i2c_busy) begin
                    if (nack || i2c_error) begin
                        error <= 1;
                        error_address <= addr;
                        state <= FINISH;
                    end else begin
                        if (phase != 1) begin
                            read_mask[step] <= 1;
                            case (step)
                                0:
                                    shr[7 : 0] <= i2c_data;
                                1:
                                    shr[15 : 8] <= i2c_data;
                                2:
                                    shr[19 : 16] <= i2c_data[3 : 0];
                                3:
                                    gain[7 : 0] <= i2c_data;
                                4:
                                    gain[8] <= i2c_data[0];
                                5:
                                    vmax[7 : 0] <= i2c_data;
                                6:
                                    vmax[15 : 8] <= i2c_data;
                                7:
                                    vmax[19 : 16] <= i2c_data[3 : 0];
                                8:
                                    hmax[7 : 0] <= i2c_data;
                                9:
                                    hmax[15 : 8] <= i2c_data;
                                10:
                                    wd <= i2c_data;
                                11:
                                    addmode <= i2c_data;
                                12:
                                    adbit <= i2c_data;
                                13:
                                    hadd <= i2c_data;
                                14:
                                    vadd <= i2c_data;
                                15:
                                    master <= i2c_data;
                                16:
                                    hold_reg <= i2c_data;
                            endcase
                        end
                        state <= NEXT;
                    end
                end
                if (timeout >= TRANSACTION_TIMEOUT) begin
                    error <= 2;
                    error_address <= addr;
                    state <= FINISH;
                end
            end
            NEXT: begin
                if ((phase == 1 && step == 6) || (phase != 1 && step == 16))
                    state <= CHECK;
                else begin
                    step <= step + 1;
                    state <= WAIT_WINDOW;
                end
            end
            CHECK: begin
                if (phase == 1) begin
                    phase <= 2;
                    step <= 0;
                    read_mask <= 0;
                    state <= WAIT_WINDOW;
                end else if (read_mask != 17'h1ffff) begin
                    error <= 3;
                    state <= FINISH;
                end else if (vmax != 2283 || hmax != 542 || wd != 0 || addmode != 1 || adbit != 0 ||
                             hadd != 1 || vadd != 1 || master != 0 || period < PERIOD_MIN ||
                             period > PERIOD_MAX || age > PERIOD_MAX) begin
                    error <= 4;
                    state <= FINISH;
                end else if (phase == 0 && flags != 1) begin
                    previous_shr <= shr;
                    previous_gain <= gain;
                    if (flags > 2 || target_shr < 8 || target_shr > 2279 || target_gain > 100) begin
                        error <= 5;
                        state <= FINISH;
                    end else if (hold_reg != 0 && flags != 2) begin
                        error <= 6;
                        state <= FINISH;
                    end else begin
                        phase <= 1;
                        step <= 0;
                        state <= WAIT_WINDOW;
                    end
                end else if (hold_reg != 0 ||
                             (phase == 2 && (shr != target_shr || gain != target_gain))) begin
                    error <= 7;
                    state <= FINISH;
                end else begin
                    settle <= 0;
                    state <= SETTLE;
                end
            end
            SETTLE:
                if (new_frame) begin
                    if (settle == 2) begin
                        if (phase == 2)
                            generation <= generation + 1;
                        state <= FINISH;
                    end else
                        settle <= settle + 1;
                end
            FINISH: begin
                i2c_req <= 0;
                result[0 * 32 +: 32] <= req_id;
                result[1 * 32 +: 32] <= error;
                result[2 * 32 +: 32] <= {12'd0, shr};
                result[3 * 32 +: 32] <= {23'd0, gain};
                result[4 * 32 +: 32] <= {12'd0, vmax};
                result[5 * 32 +: 32] <= {16'd0, hmax};
                result[6 * 32 +: 32] <= {8'd0,    hold_reg, 4'd0, master[0],  vadd[0],
                                         hadd[0], adbit[0], 6'd0, addmode[0], wd[0]};
                result[7 * 32 +: 32] <= {15'd0, read_mask};
                result[8 * 32 +: 32] <= period;
                result[9 * 32 +: 32] <= 0;
                result[10 * 32 +: 32] <= error == 0 ? 3 : (write_attempted ? 4 : 0);
                result[11 * 32 +: 32] <= {16'd0, error_address};
                result[12 * 32 +: 32] <= previous_shr;
                result[13 * 32 +: 32] <= previous_gain;
                result[14 * 32 +: 32] <= generation;
                result[15 * 32 +: 32] <= frame_seq;
                done_toggle <= req_seen;
                state <= IDLE;
            end
        endcase
        if (state != IDLE && state != FINISH && deadline >= REQUEST_TIMEOUT) begin
            error <= 8;
            state <= FINISH;
            i2c_req <= 0;
        end
        if (rst) begin
            req_sync <= 0;
            win_sync <= 0;
            frame_sync <= 0;
            win_seen <= 0;
            frame_seen <= 0;
            req_seen <= 0;
            state <= IDLE;
            done_toggle <= 0;
            result <= 0;
            i2c_req <= 0;
            i2c_read <= 0;
            i2c_wr <= 0;
            age <= 0;
            period <= 0;
            frame_seq <= 0;
            generation <= 0;
            deadline <= 0;
            timeout <= 0;
            shr <= 0;
            gain <= 0;
            vmax <= 0;
            hmax <= 0;
            read_mask <= 0;
        end
    end
endmodule
