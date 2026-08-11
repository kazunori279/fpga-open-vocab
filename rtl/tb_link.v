// Self-checking testbench for link_core, run at both M2 pin configurations.
//
//     iverilog -g2005 -o /tmp/tb tb_link.v link_core.v link_narrow.v link_wide.v
//     /tmp/tb
//
// What this proves, and what it deliberately does not
// ---------------------------------------------------
// It proves the RTL is functionally right: the XOR reduction, the shift
// register, the inversion, the latency, the heartbeat divider, the sticky
// diagnostics, and that both top-level wrappers elaborate with their ports
// connected the way the .isf files assume.
//
// It does NOT try to predict the maximum link frequency. The first draft of
// this file had a "must fail at 125 MHz" case and it passed, which was the
// useful result: in a source-synchronous link read by a correlator, running too
// fast does not corrupt data, it slides the alignment offset by one bit and the
// correlator silently absorbs it. Failure appears only when the MCU's sample
// instant lands inside the return line's transition window, which depends on
// real jitter and real board parasitics. Efinity's timing report and the board
// measurement own that number; this file must not pretend to.
//
// So the negative control is a different one, and a real one: a solder bridge
// or misrouted jumper shorting a data line to the return line past the fabric.
// That is the failure the inverted echo exists to catch, so it is the failure
// the testbench should demonstrate.
//
// The heartbeat dividers are scaled down (FLAG_DIV 15 -> 5, LED_DIV 22 -> 8) so
// the simulation runs in milliseconds. The property under test,
// f(flag) = f(clk_32m) / 2**(FLAG_DIV+1), is scale-invariant.

`timescale 1ns / 1ps
`default_nettype none

// ---------------------------------------------------------------------------
// One configuration under test.
// ---------------------------------------------------------------------------
module link_check #(
    parameter integer WIDTH = 1,
    parameter integer LAT   = 8
) (
    input  wire        start,
    output reg         done     = 1'b0,
    output reg  [31:0] failures = 32'd0
);

    localparam integer N      = 4096;   // bits per run
    localparam integer MAXOFF = 32;     // alignment search window

    localparam integer TB_FLAG_DIV = 5; // synthesis uses 15
    localparam integer TB_LED_DIV  = 8; // synthesis uses 22

    // Board delays. Forgix traces are a few mm so flight is negligible;
    // clk-to-out on a Trion IO buffer is the term that actually bites.
    localparam real T_FLIGHT = 1.0;  // MCU -> FPGA, per signal
    localparam real T_CO     = 6.0;  // capture edge -> return valid at the MCU
    localparam real T_LEAD   = 1.0;  // MCU samples the return this early

    // ----------------------------------------------------------- DUT wiring

    reg clk_32m = 1'b0;
    always #15.625 clk_32m = ~clk_32m;      // 32 MHz

    reg             link_clk = 1'b0;
    reg [WIDTH-1:0] data     = {WIDTH{1'b0}};

    wire             clk_fpga;
    wire [WIDTH-1:0] data_fpga;
    assign #T_FLIGHT clk_fpga  = link_clk;
    assign #T_FLIGHT data_fpga = data;

    wire ret_fpga, flag, led_r_n, led_g_n, led_b_n;
    wire ret_rp;
    assign #T_CO ret_rp = ret_fpga;

    // The fault case: a data line bridged to the return line, fabric bypassed.
    wire ret_shorted;
    assign #T_FLIGHT ret_shorted = data[WIDTH-1];

    reg  short_link = 1'b0;
    wire ret_sampled = short_link ? ret_shorted : ret_rp;

    link_core #(
        .WIDTH   (WIDTH),
        .LATENCY (LAT),
        .FLAG_DIV(TB_FLAG_DIV),
        .LED_DIV (TB_LED_DIV)
    ) dut (
        .clk_32m  (clk_32m),
        .link_clk (clk_fpga),
        .link_data(data_fpga),
        .link_ret (ret_fpga),
        .flag     (flag),
        .led_r_n  (led_r_n),
        .led_g_n  (led_g_n),
        .led_b_n  (led_b_n)
    );

    // ----------------------------------------------------------- stimulus

    real period;
    reg  sent [0:N-1];      // the XOR-reduced bit the FPGA should have seen
    reg  got  [0:N-1];
    integer i, off, best_off, best_err, errs;
    reg exp_bit;
    real margin;

    // Galois 16-bit maximal-length LFSR, taps 16/14/13/11. The host harness and
    // the RP firmware generate the identical sequence, so a mismatch localises
    // to the wire rather than to whoever produced the data.
    reg [15:0] lfsr;

    function lfsr_next;
        input dummy;
        begin
            lfsr_next = lfsr[0];
            lfsr = (lfsr >> 1) ^ (lfsr[0] ? 16'hB400 : 16'h0000);
        end
    endfunction

    integer w;

    task cycle;
        input [WIDTH-1:0] in_word;
        output            out_bit;
        begin
            link_clk = 1'b0;
            data     = in_word;
            #(period / 2.0 - T_LEAD);
            out_bit  = ret_sampled;     // sampled late in the low phase
            #(T_LEAD);
            link_clk = 1'b1;            // FPGA captures data here
            #(period / 2.0);
        end
    endtask

    reg [WIDTH-1:0] word;

    task drive_and_align;
        input real mhz;
        begin
            period   = 1000.0 / mhz;
            lfsr     = 16'hACE1;
            link_clk = 1'b0;
            #100;

            for (i = 0; i < N; i = i + 1) begin
                for (w = 0; w < WIDTH; w = w + 1)
                    word[w] = lfsr_next(1'b0);
                sent[i] = ^word;
                cycle(word, got[i]);
            end

            // The same brute-force search the host runs: the true latency is
            // whatever offset minimises the error count, so a link that is
            // merely mis-phased stays distinguishable from one that is corrupt.
            best_off = -1;
            best_err = N + 1;
            for (off = 0; off < MAXOFF; off = off + 1) begin
                errs = 0;
                for (i = 0; i + off < N; i = i + 1) begin
                    exp_bit = ~sent[i];         // the core inverts
                    if (got[i+off] !== exp_bit) errs = errs + 1;
                end
                if (errs < best_err) begin
                    best_err = errs;
                    best_off = off;
                end
            end
        end
    endtask

    task run_good;
        input real mhz;
        begin
            short_link = 1'b0;
            drive_and_align(mhz);

            // Setup margin at the MCU: the return is valid T_FLIGHT+T_CO after
            // the capture edge and is sampled T_LEAD before the next one.
            margin = (1000.0 / mhz) - T_LEAD - T_FLIGHT - T_CO;

            $display("    %6.1f MHz : offset %0d, %0d/%0d errors, %0.1f ns margin, %0.1f MB/s -> %0s",
                     mhz, best_off, best_err, N, margin,
                     mhz * WIDTH / 8.0,
                     (best_err == 0) ? "CLEAN" : "BROKEN");

            if (best_err != 0) begin
                $display("      FAIL: expected a clean link at %0.1f MHz", mhz);
                failures = failures + 1;
            end
            if (best_off != LAT) begin
                $display("      FAIL: expected round-trip offset %0d, got %0d", LAT, best_off);
                failures = failures + 1;
            end
        end
    endtask

    task run_shorted;
        input real mhz;
        begin
            short_link = 1'b1;
            drive_and_align(mhz);
            $display("    shorted    : best offset %0d, %0d/%0d errors -> %0s",
                     best_off, best_err, N,
                     (best_err == 0) ? "UNDETECTED" : "detected");
            if (best_err == 0) begin
                $display("      FAIL: a data-to-return bridge looked like a working link");
                failures = failures + 1;
            end
            short_link = 1'b0;
        end
    endtask

    // ---------------------------------------------------------- run script

    integer flag_edges = 0;
    always @(posedge flag) flag_edges = flag_edges + 1;

    real t0, t1, flag_hz, flag_expect;

    initial begin
        @(posedge start);
        $display("  WIDTH=%0d, LATENCY=%0d, T_CO=%0.1f ns", WIDTH, LAT, T_CO);

        run_good(12.5);
        run_good(25.0);
        run_good(50.0);
        run_good(75.0);
        run_shorted(25.0);

        // Heartbeat: proof of life that does not depend on link_clk at all.
        flag_expect = 32.0e6 / (2.0 ** (TB_FLAG_DIV + 1));
        t0 = $realtime; flag_edges = 0;
        #500_000;                                     // 500 us
        t1 = $realtime;
        flag_hz = flag_edges / ((t1 - t0) * 1.0e-9);
        $display("    flag       : %0d edges in %0.0f us -> %0.0f Hz (expect %0.0f)",
                 flag_edges, (t1 - t0) / 1000.0, flag_hz, flag_expect);
        if (flag_hz < flag_expect * 0.97 || flag_hz > flag_expect * 1.03) begin
            $display("      FAIL: heartbeat out of range");
            failures = failures + 1;
        end

        // Sticky diagnostics should all have latched by now.
        if (led_b_n !== 1'b0) begin
            $display("      FAIL: blue LED should be lit (link_clk ticked)");
            failures = failures + 1;
        end
        if (led_r_n !== 1'b0) begin
            $display("      FAIL: red LED should be lit (every data line toggled)");
            failures = failures + 1;
        end
        $display("    LEDs       : r_n=%b g_n=%b b_n=%b", led_r_n, led_g_n, led_b_n);

        done = 1'b1;
    end

endmodule

// ---------------------------------------------------------------------------
// Top: run both configurations, and elaborate both synthesis wrappers so a
// port-name or width mistake in them is a compile error rather than a surprise
// in Efinity three steps later.
// ---------------------------------------------------------------------------
module tb_link;

    reg  start_narrow = 1'b0, start_wide = 1'b0;
    wire done_narrow, done_wide;
    wire [31:0] fail_narrow, fail_wide;

    link_check #(.WIDTH(1)) narrow (
        .start(start_narrow), .done(done_narrow), .failures(fail_narrow));
    link_check #(.WIDTH(3)) wide (
        .start(start_wide), .done(done_wide), .failures(fail_wide));

    // Elaboration-only instances of the real tops.
    reg        w_clk32 = 1'b0, w_clk = 1'b0, w_mosi = 1'b0;
    reg  [2:0] w_data  = 3'b0;
    wire n_miso, n_flag, n_r, n_g, n_b;
    wire w_ret, w_r, w_g, w_b;

    link_narrow u_narrow (
        .clk_32m(w_clk32), .link_clk(w_clk), .link_mosi(w_mosi),
        .link_miso(n_miso), .link_flag(n_flag),
        .led_r_n(n_r), .led_g_n(n_g), .led_b_n(n_b));

    link_wide u_wide (
        .clk_32m(w_clk32), .link_clk(w_clk),
        .link_d0(w_data[0]), .link_d1(w_data[1]), .link_d2(w_data[2]),
        .link_ret(w_ret), .led_r_n(w_r), .led_g_n(w_g), .led_b_n(w_b));

    initial begin
        $display("M2 link testbench");
        $display("  configuration A - link_narrow (no board modification)");
        start_narrow = 1'b1;
        @(posedge done_narrow);

        $display("  configuration C - link_wide (PIN2 <-> PIN17 jumper)");
        start_wide = 1'b1;
        @(posedge done_wide);

        if (fail_narrow + fail_wide == 0) $display("PASS");
        else $display("FAIL (%0d checks)", fail_narrow + fail_wide);
        $finish;
    end

endmodule

`default_nettype wire
