// M6a: gemm_tile against golden vectors cut from the real model.
//
//     make -C rtl tb_gemm
//
// The vectors come from firmware/gen_gemm_vec.c, whose golden accumulators are
// fgx_conv_acc() in firmware/encoder.c - the same `static inline` the MCU runs.
// Nothing in this file computes a convolution, deliberately: a testbench that
// contains its own reference proves only that two of my loops agree.
//
// Everything is data-driven from cases.hex, so adding coverage is a line in
// gen_gemm_vec.c and no change here. Each case supplies its own geometry,
// blocking, and split-K pass count; this file only knows the protocol:
//
//     per pass:  load the strip, rewind and load the weight stream, RUN
//     then:      DRAIN, and compare every word in order
//
// `run_init` is asserted on pass 0 only. That is what lets the tile skip
// zeroing 8 KB of accumulators - the k = 0 tap of the first pass writes its
// product directly - and it is also the one control signal whose failure mode
// is a silent factor-of-two, so the split-K cases exist to pin it down.
//
// M15 adds a second readout rather than replacing the first. Every case still
// drains int32 and is checked against fgx_conv_acc(); a case whose geometry
// gb_geom() accepted for rq is then drained *again*, with cfg_rq raised, and
// checked against fgx_code_fixed(). Two properties come out of that pairing
// that one readout could not give:
//
//   - the accumulators are the same ones. DRAIN does not write accram - the
//     only driver is the s4_val writeback - so the second readout reads
//     exactly the array the first one just proved correct. A code mismatch is
//     therefore the epilogue and never the MAC array.
//   - cfg_rq is exercised in both positions on the same configured tile, which
//     is the claim the shipped firmware makes when m7 runs both modes in one
//     boot.
//
// The requantize table goes into the strip at RQBASE, written through the same
// act_we port the link uses for CMD_RQP. It is written once per case, before
// the passes, and survives them: gb_geom() refuses rq for any block whose
// a_len reaches RQBASE, so the activation loads below never touch it.

`timescale 1ns / 1ps

`ifndef VECDIR
 `define VECDIR "build/vec"
`endif

// Array bounds, emitted alongside the vectors themselves so the memories are
// exactly full. Found via -I$(VEC); a missing one is a compile error rather
// than a run that quietly reads X.
`include "vecsizes.vh"

module tb;

    localparam integer NMAC   = 8;
    localparam integer NFIELD = `VEC_NFIELD;
    localparam integer STRIPD = 2048;
    // Spelled the same way gemm_tile.v spells it, and derived rather than
    // written as 1792, so a change to STRIPD moves both ends together.
    localparam integer RQBASE = STRIPD - 256;

    reg clk = 0;
    always #5 clk = ~clk;

    reg [31:0]            cases  [0:`VEC_NCASE*NFIELD];   // count, then the fields
    reg [8*`VEC_LABEL-1:0] labels [0:`VEC_NCASE-1];
    reg [7:0]             actm   [0:`VEC_ACTN-1];
    reg [7:0]             wgtm   [0:`VEC_WGTN-1];
    reg [31:0]            goldm  [0:`VEC_GOLDN-1];
    // M15. codem is indexed by the same offset as goldm - one code per
    // accumulator - so no second offset is carried in the descriptor. Non-rq
    // cases still occupy their rows, filled with poison.
    reg [7:0]             codem  [0:`VEC_GOLDN-1];
    reg [7:0]             rqpm   [0:`VEC_RQPN-1];

    // ---- DUT ---------------------------------------------------------------
    reg signed [9:0] cfg_H, cfg_W, cfg_OW;
    reg              cfg_stride2, cfg_unsigned_in, cfg_w4;
    reg              cfg_rq = 0;
    reg [10:0]       cfg_strip_rw, cfg_strip_ch;
    reg [9:0]        cfg_oy0, cfg_ox0;
    reg [7:0]        cfg_P;
    reg [4:0]        cfg_QG;
    reg [9:0]        cfg_K;

    reg         act_we = 0, wgt_we = 0, wgt_rst = 0;
    reg         run = 0, run_init = 0, drain = 0, dout_ready = 0;
    reg [10:0]  act_addr;
    reg [7:0]   act_data, wgt_data;
    wire        busy, dout_valid;
    wire [31:0] dout;

    // M16. The milestone's claim is about *tile cycles*, and this is the only
    // place on the laptop where they can be counted - the wire report measures
    // RUN on the board and the plan model only predicts it. Summing busy over
    // every pass of every case gives one number that is directly comparable
    // between KPACK = 0 and KPACK = 1 on identical vectors; bit-exactness alone
    // would happily pass a build that had paired nothing.
    integer runcyc = 0;
    always @(posedge clk) if (busy) runcyc = runcyc + 1;

    // RQ(1) and not the default: this is the build that ships, and a testbench
    // that instantiated the default would leave M15's engine untested while
    // still printing PASS over the rq columns of cases.hex.
    //
    // M16's KPACK is on the command line rather than pinned, because both values
    // have to stay green and for opposite reasons: 0 is the regression - the
    // parameter must fold away to the M15 netlist bit for bit - and 1 is the new
    // claim. The goldens are the same file in both runs, which is the whole
    // correctness argument for pairing taps: integer addition is associative, so
    // summing two products before the accumulator cannot move a result.
    //
    //   make -C rtl tb_gemm                      KPACK = 0
    //   make -C rtl tb_gemm KPACK=1              KPACK = 1
    //
    // -P names the *module*, which is `tb` and not the file - a wrong name is
    // accepted silently and leaves the default in place, so the PASS line prints
    // KPACK back rather than trusting the command line.
    parameter integer KPACK = 0;

    gemm_tile #(.NMAC(NMAC), .ADEPTH(256), .WDEPTH(256), .STRIPD(STRIPD),
                .RQ(1), .KPACK(KPACK)) dut (
        .clk(clk),
        .cfg_H(cfg_H), .cfg_W(cfg_W), .cfg_OW(cfg_OW),
        .cfg_stride2(cfg_stride2),
        .cfg_strip_rw(cfg_strip_rw), .cfg_strip_ch(cfg_strip_ch),
        .cfg_oy0(cfg_oy0), .cfg_ox0(cfg_ox0),
        .cfg_unsigned_in(cfg_unsigned_in), .cfg_w4(cfg_w4),
        .cfg_rq(cfg_rq),
        .cfg_P(cfg_P), .cfg_QG(cfg_QG), .cfg_K(cfg_K),
        .act_we(act_we), .act_addr(act_addr), .act_data(act_data),
        .wgt_we(wgt_we), .wgt_data(wgt_data), .wgt_rst(wgt_rst),
        .run(run), .run_init(run_init), .busy(busy),
        .drain(drain), .dout(dout), .dout_valid(dout_valid),
        .dout_ready(dout_ready)
    );

    // ---- descriptor fields, unpacked per case -------------------------------
    integer c_layer, c_H, c_W, c_OW, c_st2, c_us, c_srw, c_sch;
    integer c_oy0, c_ox0, c_P, c_QG, c_K, c_np;
    integer c_aoff, c_alen, c_woff, c_wlen, c_goff, c_glen, c_w4;
    integer c_rq, c_qoff, c_qlen;

    integer ncase, ci, pass, i, cerr, errs, checks, nrq;
    integer got, want;

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

    // ---- M15: the requantize table into the strip ---------------------------
    // Through act_we, at RQBASE, in exactly the six-byte-per-channel layout
    // gw_rqp_pack() wrote and in drain-walk order. This is what gemm_link's
    // CMD_RQP does; here it is the same bytes without the framing, so a
    // tb_gemm failure is the tile and a tb_gemm_link failure is the link.
    task load_rqp;
        begin
            for (i = 0; i < c_qlen; i = i + 1) begin
                @(negedge clk);
                act_we   = 1;
                act_addr = RQBASE[10:0] + i[10:0];
                act_data = rqpm[c_qoff + i];
            end
            @(negedge clk); act_we = 0;
        end
    endtask

    // ---- one pass: strip, weights, run --------------------------------------
    // The strip is written from address 0 every pass. Split-K reuses the buffer
    // rather than partitioning it, which is exactly what the link will do - the
    // whole point of sub-blocking K is to bound these two buffers.
    task do_pass;
        input integer p;
        begin
            for (i = 0; i < c_alen; i = i + 1) begin
                @(negedge clk);
                act_we   = 1;
                act_addr = i[10:0];
                act_data = actm[c_aoff + p*c_alen + i];
            end
            @(negedge clk); act_we = 0;

            @(negedge clk); wgt_rst = 1;
            @(negedge clk); wgt_rst = 0;
            for (i = 0; i < c_wlen; i = i + 1) begin
                @(negedge clk);
                wgt_we   = 1;
                wgt_data = wgtm[c_woff + p*c_wlen + i];
            end
            @(negedge clk); wgt_we = 0;

            @(negedge clk); run = 1; run_init = (p == 0);
            @(negedge clk); run = 0; run_init = 0;
            wait (!busy);
        end
    endtask

    // ---- drain and compare ---------------------------------------------------
    // Drain order is (g, j, p) and gold.hex is written in that same order, so
    // this is a straight linear walk. A transposed readout would show up as
    // every word wrong rather than as a plausible small error.
    //
    // `code` picks the golden and, with it, the width. At rq the tile drives
    // dout[7:0] and zeroes the rest, and the upper 24 bits are compared too -
    // an epilogue that leaked a sign extension would otherwise be invisible
    // here and would then reach the host as a byte, correctly, only because
    // gemm_link truncates.
    task check_drain;
        input integer code;
        integer w;
        begin
            cerr = 0;
            @(negedge clk); drain = 1;
            @(negedge clk); drain = 0; dout_ready = 1;
            for (w = 0; w < c_glen; w = w + 1) begin
                while (!dout_valid) @(posedge clk);
                got  = code ? dout : $signed(dout);
                want = code ? {24'd0, codem[c_goff + w]}
                            : $signed(goldm[c_goff + w]);
                checks = checks + 1;
                if (got !== want) begin
                    cerr = cerr + 1;
                    if (cerr <= 4)
                        $display("      %0sword %0d (q=%0d p=%0d): got %0d want %0d",
                                 code ? "code " : "", w, w / c_P, w % c_P,
                                 got, want);
                end
                @(posedge clk);
                @(negedge clk);
            end
            dout_ready = 0;
            wait (!busy);
            errs = errs + cerr;
        end
    endtask

    initial begin
        $readmemh({`VECDIR, "/cases.hex"},  cases);
        $readmemh({`VECDIR, "/labels.hex"}, labels);
        $readmemh({`VECDIR, "/act.hex"},    actm);
        $readmemh({`VECDIR, "/wgt.hex"},    wgtm);
        $readmemh({`VECDIR, "/gold.hex"},   goldm);
        $readmemh({`VECDIR, "/code.hex"},   codem);
        $readmemh({`VECDIR, "/rqp.hex"},    rqpm);

        ncase  = cases[0];
        errs   = 0;
        checks = 0;
        nrq    = 0;

        // vecsizes.vh and cases.hex are written by the same run of
        // gen_gemm_vec.c, so disagreement means a stale mix of the two.
        if (ncase !== `VEC_NCASE) begin
            $display("FAIL - %0s holds %0d cases, vecsizes.vh says %0d; regenerate",
                     `VECDIR, ncase, `VEC_NCASE);
            $finish;
        end

        $display("gemm_tile vs fgx_conv_acc(), %0d cases from %0s", ncase, `VECDIR);
        @(negedge clk);

        for (ci = 0; ci < ncase; ci = ci + 1) begin
            unpack(ci);

            cfg_H           = c_H[9:0];
            cfg_W           = c_W[9:0];
            cfg_OW          = c_OW[9:0];
            cfg_stride2     = c_st2[0];
            cfg_unsigned_in = c_us[0];
            cfg_w4          = c_w4[0];
            cfg_strip_rw    = c_srw[10:0];
            cfg_strip_ch    = c_sch[10:0];
            cfg_oy0         = c_oy0[9:0];
            cfg_ox0         = c_ox0[9:0];
            cfg_P           = c_P[7:0];
            cfg_QG          = c_QG[4:0];
            cfg_K           = c_K[9:0];
            cfg_rq          = 1'b0;

            // Before the passes and not after, so that the activation loads
            // run *over* it: if a case ever appeared whose a_len reached
            // RQBASE, the codes would come out wrong here rather than the
            // overlap being hidden by writing the table last.
            if (c_rq) load_rqp;

            for (pass = 0; pass < c_np; pass = pass + 1)
                do_pass(pass);

            check_drain(0);

            $display("  %0s  %0s : layer %0d  P=%0d Q=%0d K=%0d x%0d passes, %0d words",
                     cerr ? "FAIL" : "ok  ", labels[ci], c_layer,
                     c_P, c_QG*NMAC, c_K, c_np, c_glen);

            // The same accumulators, read out a second time through the
            // epilogue. accram has one driver and it is the s4_val writeback,
            // so nothing above has changed between the two readouts.
            if (c_rq) begin
                nrq    = nrq + 1;
                cfg_rq = 1'b1;
                check_drain(1);
                cfg_rq = 1'b0;
                $display("  %0s  %0s : rq, %0d codes vs fgx_code_fixed()",
                         cerr ? "FAIL" : "ok  ", labels[ci], c_glen);
            end
        end

        // gb_geom() decides which cases run rq, so a change that made it refuse
        // everything would leave every line above green and M15 untested. Same
        // assertion gen_gemm_vec.c makes on the other side of the file.
        if (nrq == 0) begin
            $display("\nFAIL - no case exercised rq; the vectors do not cover M15");
            $finish;
        end

        if (errs == 0)
            $display("\nPASS (%0d words, all bit-exact; %0d of %0d cases also at rq)\n  KPACK=%0d, %0d busy cycles over all passes",
                     checks, nrq, ncase, KPACK, runcyc);
        else
            $display("\nFAIL (%0d of %0d mismatched)", errs, checks);
        $finish;
    end

    // Long by construction: conv7 sweeps 192 single-channel passes, which is
    // the real traffic pattern for Q=128 and not an artifact of the testbench.
    initial begin
        #400000000;
        $display("FAIL (timeout)");
        $finish;
    end

endmodule
