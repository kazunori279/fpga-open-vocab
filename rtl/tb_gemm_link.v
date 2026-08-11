// M6a: the whole board, over the wire.
//
//     make -C rtl tb_gemm_link         configuration A, one forward lane
//     make -C rtl tb_gemm_link_wide    configuration C, three  (-DLINK_WIDE)
//
// tb_gemm.v pokes gemm_tile's ports directly - a byte per clock, control signals
// asserted by hand. This one instantiates the real top and reaches it only
// through the link pins, LSB-first, exactly as firmware/link.pio will. Same
// golden vectors, same bit-exactness bar; what is added is every way the framing
// layer can lose a byte between them.
//
// M7f made it two configurations from one source rather than two files. The
// alternative was a copy, and a copy of a testbench is a copy that stops being
// run - which would leave the configuration that has never touched hardware as
// the one with the weaker check, exactly backwards.
//
// So this is not a protocol smoke test with a stub attached. It is the M6c
// experiment run in simulation, minus the pad delays - which is the one thing a
// testbench cannot model and the return preamble exists to absorb.
//
// It also serves the purpose the plan assigned to an elaboration-only instance
// of the top: a port mistake in gemm_top.v is a compile error here. Exercising
// it costs nothing extra, so it is exercised.
//
// The host side below is deliberately written the way host/m6.py has to work:
// hunt for the preamble bit-by-bit, then trust the byte boundary; check rxcrc to
// prove the command landed and txcrc to prove the response did; keep clocking
// while the tile computes, because link_clk is the tile's only clock.

`timescale 1ns / 1ps

`ifndef VECDIR
 `define VECDIR "build/vec"
`endif

`include "vecsizes.vh"

module tb;

    localparam integer NMAC   = 8;
    localparam integer NFIELD = `VEC_NFIELD;

    localparam [31:0] PREAMBLE = 32'h3c5ac3a5;   // wire order a5 c3 5a 3c
    localparam [31:0] CRCPOLY  = 32'hedb88320;

    localparam [7:0] CMD_CFG   = 8'h01,
                     CMD_ACT   = 8'h02,
                     CMD_WGT   = 8'h03,
                     CMD_RUN   = 8'h04,
                     CMD_DRAIN = 8'h05,
                     CMD_NOP   = 8'h06,
                     CMD_LED   = 8'h07,
                     CMD_RQP   = 8'h08;

    // gemm_tile's RQBASE, restated only so the comment in cmd_rqp has something
    // to point at - the host never sends an address, which is the whole reason
    // the base is a power-of-two boundary.
    localparam integer RQBASE = 2048 - 256;

    // The two clocks are coprime on purpose: nothing may depend on their phase.
    reg clk   = 0;  always #5  clk   = ~clk;   // link_clk
    reg clk32 = 0;  always #17 clk32 = ~clk32; // clk_32m, heartbeat only

    // LW forward lanes, one back. Everything below this instance is written in
    // bits and bytes and does not know which configuration it is driving; the
    // regrouping lives in `pump` and `tick0`, which is the same division of
    // labour gemm_link.v uses on the other end.
`ifdef LINK_WIDE
    localparam integer LW = 3;
`else
    localparam integer LW = 1;
`endif

    reg  [LW-1:0] dat = {LW{1'b0}};
    wire miso;
    wire led_r_n, led_g_n, led_b_n;

    // RQ(1) in both, and not the default. RQ = 0 stays a build flag rather than
    // a simulated configuration on purpose: its logic is a strict subset - the
    // engine is inside `if (RQ != 0)` and cfg_rq then has no reader - so the
    // interesting failure is the mode mux, which only exists at RQ = 1 and is
    // exercised here in both positions on the same DUT.
    //
    // M16's KPACK is a knob rather than a constant, for the opposite reason: it
    // is not a subset either way. At 0 the tile is M15's exactly, at 1 the whole
    // second tap exists, and the claim is that the *same* goldens pass both - so
    // both have to be run. `make tb_gemm_link KPACK=1`, same as tb_gemm, and the
    // -P names the module `tb` and not the file. The banner prints it back.
    parameter integer KPACK = 0;

`ifdef LINK_WIDE
    gemm_top_wide #(.RQ(1), .KPACK(KPACK)) dut (
        .clk_32m  (clk32),
        .link_clk (clk),
        .link_d0  (dat[0]),
        .link_d1  (dat[1]),
        .link_d2  (dat[2]),
        .link_ret (miso),
        .led_r_n  (led_r_n),
        .led_g_n  (led_g_n),
        .led_b_n  (led_b_n)
    );
`else
    wire flag;

    gemm_top #(.RQ(1), .KPACK(KPACK)) dut (
        .clk_32m  (clk32),
        .link_clk (clk),
        .link_mosi(dat[0]),
        .link_miso(miso),
        .link_flag(flag),
        .led_r_n  (led_r_n),
        .led_g_n  (led_g_n),
        .led_b_n  (led_b_n)
    );
`endif

    // ---- vectors -------------------------------------------------------------
    reg [31:0]             cases  [0:`VEC_NCASE*NFIELD];
    reg [8*`VEC_LABEL-1:0] labels [0:`VEC_NCASE-1];
    reg [7:0]              actm   [0:`VEC_ACTN-1];
    reg [7:0]              wgtm   [0:`VEC_WGTN-1];
    reg [31:0]             goldm  [0:`VEC_GOLDN-1];
    reg [7:0]              codem  [0:`VEC_GOLDN-1];   // M15, same offsets
    reg [7:0]              rqpm   [0:`VEC_RQPN-1];

    integer c_layer, c_H, c_W, c_OW, c_st2, c_us, c_srw, c_sch, c_w4;
    integer c_oy0, c_ox0, c_P, c_QG, c_K, c_np;
    integer c_aoff, c_alen, c_woff, c_wlen, c_goff, c_glen;
    integer c_rq, c_qoff, c_qlen;

    integer ncase, ci, pass, w, cerr, errs, checks, fatal, nrq;

    // Bit-serial framing makes this testbench 8x slower than tb_gemm on loads
    // and 32x on the readout, so a full six-case run is minutes, not seconds.
    // `+cases=1` runs case 0 and the resynchronisation test only, which is the
    // right granularity for iterating on the protocol.
    integer maxcase;
    integer got, want;

    // ---- host-side link model ------------------------------------------------
    // Same reflected CRC-32 the DUT computes, written out again rather than
    // shared: a bug copied into both sides cancels, and a checker that cannot
    // fail is not a checker.
    function [31:0] hcrc;
        input [31:0] c;
        input        b;
        begin
            hcrc = (c >> 1) ^ ({32{c[0] ^ b}} & CRCPOLY);
        end
    endfunction

    reg [31:0] scrc;      // CRC of what we sent (payload only)
    reg [31:0] rcrc;      // CRC of what we received
    reg        rcrc_en;
    reg [31:0] hunt;
    reg        rbit;
    reg [7:0]  rv;
    reg [7:0]  r_status;
    reg [31:0] r_rxcrc, r_txcrc, r_calc, r_word;
    // `bi` belongs to sbyte / pbyte / rbyte and to nothing else. Verilog-2005
    // tasks are static, so a loop in the stimulus block that both uses bi and
    // calls one of those tasks does not nest - it spins forever, because sbyte
    // leaves bi at 8 every time. `bo` is the outer counter for exactly that
    // reason and the two must never be confused.
    integer    bi, bo, nspin;

    // The two directions no longer advance together, and that asymmetry is the
    // whole of what configuration C changes for a host. Forward, LW bits leave
    // per clock; back, one does. So a forward *bit* and a link *clock* are
    // different units at LW=3 and the driver has to name which it means.
    //
    // `pump` is the forward unit: it buffers a bit and spends a clock once it
    // has a full group. `tick0` is the clock unit: it spends one clock with the
    // forward lanes idle and samples the return line. Every task that reads
    // `rbit` uses tick0; every task that sends uses pump.
    reg [2:0] wbuf = 3'b000;
    integer   wq   = 0;

    task pump;
        input tv;
        begin
            wbuf = {tv, wbuf[2:1]};        // [2] newest; the group's LSB is oldest
            wq   = wq + 1;
            if (wq == LW) begin
                @(negedge clk);
                rbit = miso;
                dat  = wbuf[2 -: LW];
                wq   = 0;
            end
        end
    endtask

    // Anything still in the group buffer is flushed first. At LW=3 a byte does
    // not end on a group boundary, so when the host stops sending, the last one
    // or two bits of the last payload byte are still buffered - and the DUT
    // cannot assemble that byte, or answer, until they are clocked. The padding
    // is zeros, which the receiver has stopped listening to by then.
    task tick0;
        begin
            while (wq != 0) pump(1'b0);
            @(negedge clk);
            rbit = miso;
            dat  = {LW{1'b0}};
        end
    endtask

    task sbyte;                       // framing byte, outside the CRC
        input [7:0] v;
        begin
            for (bi = 0; bi < 8; bi = bi + 1) pump(v[bi]);
        end
    endtask

    task pbyte;                       // payload byte, inside the CRC
        input [7:0] v;
        begin
            for (bi = 0; bi < 8; bi = bi + 1) begin
                scrc = hcrc(scrc, v[bi]);
                pump(v[bi]);
            end
        end
    endtask

    task hdr;
        input [7:0]  c;
        input [15:0] l;
        begin
            sbyte(8'ha5); sbyte(8'h5a);
            sbyte(c); sbyte(l[7:0]); sbyte(l[15:8]);
            scrc = 32'hffff_ffff;
        end
    endtask

    task rbyte;
        begin
            for (bi = 0; bi < 8; bi = bi + 1) begin
                tick0;                // idle out; the DUT is not listening
                rv[bi] = rbit;
                if (rcrc_en) rcrc = hcrc(rcrc, rbit);
            end
        end
    endtask

    task rword;
        begin
            rbyte; r_word[7:0]   = rv;
            rbyte; r_word[15:8]  = rv;
            rbyte; r_word[23:16] = rv;
            rbyte; r_word[31:24] = rv;
        end
    endtask

    // Bit-by-bit, because the byte boundary of the return path is not known a
    // priori - on hardware it is offset by the PIO input synchroniser and the
    // pad delay. The bound is generous: a RUN response waits out the whole sweep.
    task find_pre;
        input integer limit;
        begin
            hunt   = 32'h0;
            nspin  = 0;
            while (hunt !== PREAMBLE && nspin < limit) begin
                tick0;
                hunt  = {rbit, hunt[31:1]};
                nspin = nspin + 1;
            end
            if (hunt !== PREAMBLE) begin
                $display("      FAIL - no preamble in %0d clocks", limit);
                errs  = errs + 1;
                fatal = 1;
            end
        end
    endtask

    task resp_head;
        input integer limit;
        begin
            find_pre(limit);
            rcrc    = 32'hffff_ffff;
            rcrc_en = 1;
            rbyte; r_status      = rv;
            rbyte; r_rxcrc[7:0]  = rv;
            rbyte; r_rxcrc[15:8] = rv;
            rbyte; r_rxcrc[23:16]= rv;
            rbyte; r_rxcrc[31:24]= rv;
        end
    endtask

    // txcrc covers status..data, so the expected value is frozen here, at the
    // first bit that is not covered.
    task resp_tail;
        input [7:0] expect_cmd;
        begin
            r_calc  = ~rcrc;
            rcrc_en = 0;
            rbyte; r_txcrc[7:0]   = rv;
            rbyte; r_txcrc[15:8]  = rv;
            rbyte; r_txcrc[23:16] = rv;
            rbyte; r_txcrc[31:24] = rv;

            if (r_txcrc !== r_calc) begin
                $display("      FAIL - txcrc %08h, computed %08h", r_txcrc, r_calc);
                errs = errs + 1;
            end
            if (r_rxcrc !== ~scrc) begin
                $display("      FAIL - rxcrc %08h, sent %08h (command corrupted)",
                         r_rxcrc, ~scrc);
                errs = errs + 1;
            end
            if (!r_status[0]) begin
                $display("      FAIL - status %02h has no frame marker", r_status);
                errs = errs + 1;
            end
            if (r_status[7:4] !== expect_cmd[3:0]) begin
                $display("      FAIL - status echoes cmd %0h, sent %0h",
                         r_status[7:4], expect_cmd[3:0]);
                errs = errs + 1;
            end
        end
    endtask

    // ---- commands --------------------------------------------------------------
    // `rq` is M15's mode and it is an argument rather than c_rq, because every
    // rq case is configured twice - once off, once on - and the second CFG is
    // what proves the bit is a mode and not a build.
    task cmd_cfg;
        input rq;
        begin
            hdr(CMD_CFG, 16'd20);
            pbyte(c_H[7:0]);   pbyte(c_H[15:8]);
            pbyte(c_W[7:0]);   pbyte(c_W[15:8]);
            pbyte(c_OW[7:0]);  pbyte(c_OW[15:8]);
            pbyte(c_srw[7:0]); pbyte(c_srw[15:8]);
            pbyte(c_sch[7:0]); pbyte(c_sch[15:8]);
            pbyte(c_oy0[7:0]); pbyte(c_oy0[15:8]);
            pbyte(c_ox0[7:0]); pbyte(c_ox0[15:8]);
            pbyte(c_K[7:0]);   pbyte(c_K[15:8]);
            pbyte(c_P[7:0]);
            pbyte(c_QG[7:0]);
            // Bit 2 is M14's weight width and bit 3 is M15's requantize. Same
            // byte, same 20-byte CFG - see gw_cfg_pack(), which this task is
            // the wire-level twin of.
            pbyte({4'b0, rq, c_w4[0], c_us[0], c_st2[0]});
            pbyte(8'h00);                       // reserved
            resp_head(4000);
            if (!fatal) resp_tail(CMD_CFG);
        end
    endtask

    // M15. Six bytes a channel, in drain-walk order, straight into the strip at
    // RQBASE - the host sends no address at all, because gemm_link forms it as
    // {RQBASE[AW-1:8], pcnt[7:0]} and the length check caps pcnt at 191. So a
    // wrong RQBASE is not a wrong write here, it is a silently wrong *read* in
    // the tile, and the only thing that catches it is the codes below.
    task cmd_rqp;
        integer i;
        begin
            hdr(CMD_RQP, c_qlen[15:0]);
            for (i = 0; i < c_qlen; i = i + 1)
                pbyte(rqpm[c_qoff + i]);
            resp_head(4000);
            if (!fatal) resp_tail(CMD_RQP);
        end
    endtask

    task cmd_act;
        input integer p;
        integer i;
        begin
            hdr(CMD_ACT, c_alen[15:0]);
            for (i = 0; i < c_alen; i = i + 1)
                pbyte(actm[c_aoff + p*c_alen + i]);
            resp_head(4000);
            if (!fatal) resp_tail(CMD_ACT);
        end
    endtask

    task cmd_wgt;
        input integer p;
        integer i;
        begin
            hdr(CMD_WGT, c_wlen[15:0]);
            for (i = 0; i < c_wlen; i = i + 1)
                pbyte(wgtm[c_woff + p*c_wlen + i]);
            resp_head(4000);
            if (!fatal) resp_tail(CMD_WGT);
        end
    endtask

    // No polling and no ready pin. The response cannot arrive until the sweep is
    // done, and the clocks that carry the hunt are the same clocks the sweep
    // runs on - so waiting and driving are one loop. The bound covers K*Q/8*P
    // plus flushes for the widest case here (conv2: 72*2*128).
    task cmd_run;
        input integer p;
        begin
            hdr(CMD_RUN, 16'd1);
            pbyte(p == 0 ? 8'h01 : 8'h00);
            resp_head(200000);
            if (!fatal) resp_tail(CMD_RUN);
        end
    endtask

    task cmd_nop;
        begin
            hdr(CMD_NOP, 16'd0);
            resp_head(4000);
            if (!fatal) resp_tail(CMD_NOP);
        end
    endtask

    // M11. Two duty bytes, red then green, and an ordinary acknowledgement -
    // the command needs no special handling on the return path, which is most
    // of why it was cheap to add.
    task cmd_led;
        input [7:0] r;
        input [7:0] g;
        begin
            hdr(CMD_LED, 16'd2);
            pbyte(r);
            pbyte(g);
            resp_head(4000);
            if (!fatal) resp_tail(CMD_LED);
        end
    endtask

    // ---- drain and compare -------------------------------------------------
    // `code` is M15, and it changes the *wire format*, not just the golden: the
    // response body is one byte per accumulator instead of four, so the count of
    // rbyte calls is what proves gemm_link's T_DATA refetches every byte. Get
    // that wrong in either direction and the frame runs long or short, txcrc
    // fails, and this says so before any value is compared.
    task drain_check;
        input integer code;
        begin
            cerr = 0;
            hdr(CMD_DRAIN, 16'd0);
            resp_head(4000);
            if (!fatal) begin
                for (w = 0; w < c_glen; w = w + 1) begin
                    if (code) begin
                        rbyte;
                        got  = rv;
                        want = codem[c_goff + w];
                    end else begin
                        rword;
                        got  = $signed(r_word);
                        want = $signed(goldm[c_goff + w]);
                    end
                    checks = checks + 1;
                    if (got !== want) begin
                        cerr = cerr + 1;
                        if (cerr <= 4)
                            $display("      %0sword %0d (q=%0d p=%0d): got %0d want %0d",
                                     code ? "code " : "", w, w / c_P, w % c_P,
                                     got, want);
                    end
                end
                resp_tail(CMD_DRAIN);
                if (r_status[1]) begin
                    $display("      FAIL - underrun: the tile ran out of words early");
                    cerr = cerr + 1;
                end
                errs = errs + cerr;
            end
        end
    endtask

    task unpack;
        input integer c;
        integer b;
        begin
            b = 1 + c * NFIELD;
            c_layer = cases[b+0];  c_H    = cases[b+1];  c_W    = cases[b+2];
            c_OW    = cases[b+3];  c_st2  = cases[b+4];  c_us   = cases[b+5];
            c_srw   = cases[b+6];  c_sch  = cases[b+7];  c_oy0  = cases[b+8];
            c_ox0   = cases[b+9];  c_P    = cases[b+10]; c_QG   = cases[b+11];
            c_K     = cases[b+12]; c_np   = cases[b+13]; c_aoff = cases[b+14];
            c_alen  = cases[b+15]; c_woff = cases[b+16]; c_wlen = cases[b+17];
            c_goff  = cases[b+18]; c_glen = cases[b+19]; c_w4 = cases[b+20];
            c_rq    = cases[b+21]; c_qoff = cases[b+22]; c_qlen = cases[b+23];
        end
    endtask

    // ------------------------------------------------------------------------
    initial begin
        $readmemh({`VECDIR, "/cases.hex"},  cases);
        $readmemh({`VECDIR, "/labels.hex"}, labels);
        $readmemh({`VECDIR, "/act.hex"},    actm);
        $readmemh({`VECDIR, "/wgt.hex"},    wgtm);
        $readmemh({`VECDIR, "/gold.hex"},   goldm);
        $readmemh({`VECDIR, "/code.hex"},   codem);
        $readmemh({`VECDIR, "/rqp.hex"},    rqpm);

        ncase   = cases[0];
        errs    = 0;
        checks  = 0;
        fatal   = 0;
        nrq     = 0;
        rcrc_en = 0;
        scrc    = 32'hffff_ffff;

        if (ncase !== `VEC_NCASE) begin
            $display("FAIL - %0s holds %0d cases, vecsizes.vh says %0d; regenerate",
                     `VECDIR, ncase, `VEC_NCASE);
            $finish;
        end

        maxcase = ncase;
        if ($value$plusargs("cases=%d", maxcase) && maxcase > ncase)
            maxcase = ncase;

        $display("%0s over %0d forward lane%0s and 1 back, KPACK=%0d, %0d of %0d cases from %0s",
`ifdef LINK_WIDE
                 "gemm_top_wide",
`else
                 "gemm_top",
`endif
                 LW, LW == 1 ? "" : "s", KPACK, maxcase, ncase, `VECDIR);
        // vvp buffers stdout when it is a file, so without this a run that takes
        // minutes looks like a hang with an empty log for its whole duration.
        $fflush;

        // 64 idle clocks first. The DUT comes up hunting and idle is all zeroes,
        // so this must not produce anything - and dbg_seen must go high anyway,
        // which is the LED that separates "unconfigured" from "misclocked".
        for (bo = 0; bo < 64; bo = bo + 1) @(negedge clk);
        if (led_b_n !== 1'b0) begin
            $display("  FAIL - dbg_seen never set; the blue LED would lie");
            errs = errs + 1;
        end
        if (led_r_n !== 1'b1) begin
            $display("  FAIL - dbg_err set before any frame was sent");
            errs = errs + 1;
        end

        for (ci = 0; ci < maxcase && !fatal; ci = ci + 1) begin
            unpack(ci);
            cmd_cfg(1'b0);
            for (pass = 0; pass < c_np && !fatal; pass = pass + 1) begin
                cmd_act(pass);
                cmd_wgt(pass);
                cmd_run(pass);
            end
            if (!fatal) drain_check(0);

            $display("  %0s  %0s : layer %0d  P=%0d Q=%0d K=%0d x%0d passes, %0d words",
                     cerr ? "FAIL" : "ok  ", labels[ci], c_layer,
                     c_P, c_QG*NMAC, c_K, c_np, c_glen);
            $fflush;

            // M15, the same accumulators a second time. DRAIN does not write
            // accram, so this reads the array the int32 pass just proved, and
            // the whole difference is one CFG bit, one CMD_RQP, and a body a
            // quarter the length. RQP after the CFG that turns rq on, which is
            // the order run_block() issues them in - nothing depends on it in
            // the RTL, and a testbench that used the other order would stop
            // being evidence about the firmware.
            if (c_rq && !fatal) begin
                nrq = nrq + 1;
                cmd_cfg(1'b1);
                if (!fatal) cmd_rqp;
                if (!fatal) drain_check(1);
                $display("  %0s  %0s : rq, %0d codes vs fgx_code_fixed()",
                         cerr ? "FAIL" : "ok  ", labels[ci], c_glen);
                $fflush;
            end
        end

        // Only meaningful on a full run; `+cases=1` deliberately stops short.
        if (!fatal && maxcase == ncase && nrq == 0) begin
            $display("  FAIL - no case exercised rq; the vectors do not cover M15");
            errs = errs + 1;
        end

        // ---- resynchronisation ------------------------------------------------
        // A CFG whose length is one byte adrift is exactly what a lost bit looks
        // like, and it is the case that must not reach the tile: a scrambled
        // geometry produces a plausible wrong tensor. The DUT cannot skip a
        // payload it does not believe, so it drops the frame and re-hunts, and
        // the host learns about it from the next NOP.
        if (!fatal) begin
            $display("  -- bad frame, resync, and sticky fault readback");

            hdr(CMD_CFG, 16'd21);
            for (bo = 0; bo < 21; bo = bo + 1) sbyte(8'h00);

            hunt = 32'h0;
            for (bo = 0; bo < 400; bo = bo + 1) begin
                tick0;
                hunt = {rbit, hunt[31:1]};
                if (hunt === PREAMBLE) begin
                    $display("      FAIL - answered a frame it should have dropped");
                    errs = errs + 1;
                end
            end
            if (led_r_n !== 1'b0) begin
                $display("      FAIL - bad_frame did not light the red LED");
                errs = errs + 1;
            end

            cmd_nop;                               // reports, and clears
            if (!fatal && !r_status[2]) begin
                $display("      FAIL - NOP status %02h does not report bad_frame",
                         r_status);
                errs = errs + 1;
            end

            cmd_nop;                               // must now be clean
            if (!fatal && r_status[2]) begin
                $display("      FAIL - bad_frame survived the NOP that clears it");
                errs = errs + 1;
            end

            // And the link still works afterwards - resync is the whole point.
            unpack(0);
            cmd_cfg(1'b0);
            if (!fatal)
                $display("      ok   dropped, reported, cleared, still talking");
        end

        // ---- M11: D1 as a score meter ------------------------------------
        // The mode switch is the part that has to be checked in simulation,
        // because it is the part that could silently break every milestone
        // behind it: until a CMD_LED arrives D1 must behave exactly as it did
        // through M10, and m7 and m8 depend on that without ever asking.
        if (!fatal) begin
            $display("  -- D1: bring-up meanings held, then handed over");

            if (led_b_n !== 1'b0 || dut.own_s[1] !== 1'b0) begin
                $display("      FAIL - D1 left bring-up mode with no LED command");
                errs = errs + 1;
            end

            cmd_led(8'hff, 8'h00);                    // full red, no green

            if (!fatal) begin
                if (dut.u_link.led_r_duty !== 8'hff ||
                    dut.u_link.led_g_duty !== 8'h00 ||
                    dut.u_link.led_own    !== 1'b1) begin
                    $display("      FAIL - duties %02h/%02h own=%b after CMD_LED",
                             dut.u_link.led_r_duty, dut.u_link.led_g_duty,
                             dut.u_link.led_own);
                    errs = errs + 1;
                end

                // Three slew ticks, one per 32768 clk_32m. Simulating the whole
                // 0 -> 255 sweep is 261 ms of model time and proves nothing
                // extra; what matters is that the duty *moves*, the right way,
                // and that blue has gone out - which needs no slew at all.
                repeat (100000) @(posedge clk32);

                if (led_b_n !== 1'b1) begin
                    $display("      FAIL - blue still lit after the host took D1");
                    errs = errs + 1;
                end
                if (dut.r_cur === 8'h00 || dut.g_cur !== 8'h00) begin
                    $display("      FAIL - slew r=%02h g=%02h, wanted r rising, g held",
                             dut.r_cur, dut.g_cur);
                    errs = errs + 1;
                end
            end

            if (!fatal) begin
                cmd_led(8'h00, 8'hff);                // and back the other way
                repeat (100000) @(posedge clk32);
                if (dut.g_cur === 8'h00) begin
                    $display("      FAIL - green never started to rise");
                    errs = errs + 1;
                end
            end

            // A three-byte LED frame is a lost bit, not a request, and it has
            // to be dropped for the same reason a 21-byte CFG is: the payload
            // would otherwise land one byte out and set a colour nobody asked
            // for. Zeros after the header so the debris cannot form a SYNC.
            if (!fatal) begin
                hdr(CMD_LED, 16'd3);
                pbyte(8'h00); pbyte(8'h00); pbyte(8'h00);

                hunt = 32'h0;
                for (bo = 0; bo < 400; bo = bo + 1) begin
                    tick0;
                    hunt = {rbit, hunt[31:1]};
                    if (hunt === PREAMBLE) begin
                        $display("      FAIL - answered a 3-byte LED frame");
                        errs = errs + 1;
                    end
                end

                cmd_nop;
                if (!fatal && !r_status[2]) begin
                    $display("      FAIL - over-long LED frame did not set bad_frame");
                    errs = errs + 1;
                end
                cmd_nop;                              // leave it clean
            end

            if (!fatal && errs == 0)
                $display("      ok   legacy held, latched, slewing, bad length dropped");
        end

        if (errs == 0)
            $display("\nPASS (%0d words over the wire, all bit-exact; %0d cases also at rq)",
                     checks, nrq);
        else
            $display("\nFAIL (%0d errors, %0d words checked)", errs, checks);
        $finish;
    end

    // Serial is 8x the byte-parallel testbench on loads and 32x on the readout,
    // on top of the same sweeps, so the ceiling is correspondingly higher.
    initial begin
        #4000000000;
        $display("FAIL (timeout)");
        $finish;
    end

endmodule
