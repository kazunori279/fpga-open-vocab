// M6: byte framing between the MCU and gemm_tile.
//
// WIDTH bits out, one bit back, both LSB-first, both clocked by link_clk - the
// same wire discipline firmware/link.pio already implements and M2 measured at
// 8.94 MB/s (WIDTH=1) and 26.81 MB/s (WIDTH=3) with zero errors. This module
// turns that bit stream into commands and turns accumulators back into a bit
// stream.
//
// **The MCU must keep clocking while the tile computes.** link_clk is the tile's
// only clock, so "wait for busy" and "keep the clock running" are the same
// instruction. That is why RUN has no separate ready pin and no polling loop:
// the host clocks idle bytes and watches the return line for the response
// preamble. `link_flag` stays what it was in M2 - a free-running heartbeat off
// clk_32m - because its job is to distinguish "unconfigured" from "misclocked",
// and that job is unchanged.
//
// FRAME, host -> FPGA:
//
//     a5 5a        SYNC, hunted bit-by-bit whenever the receiver is idle
//     cmd          1 byte
//     len          2 bytes, little-endian
//     payload      len bytes
//
// FRAME, FPGA -> host:
//
//     a5 c3 5a 3c  PREAMBLE
//     status       1 byte
//     rxcrc        4 bytes LE - CRC32 of the forward payload, as received
//     data         DRAIN only: 4*P*Q bytes, int32 LE, channel-major
//     txcrc        4 bytes LE - CRC32 of status..data
//
// Two CRCs and not one. `txcrc` proves the response arrived intact; `rxcrc`
// proves the *command* did, which is the direction that actually matters - a
// corrupted weight byte produces a plausible wrong tensor and nothing else
// notices. Both are the reflected CRC-32 of zlib.crc32 taken in wire order, so
// the host checks them with one stdlib call and no table here.
//
// The preamble replaces M2's brute-force correlator. The return path has a fixed
// but unknown pipeline latency (M2 measured offset 10, made of PIO input
// synchronisers and pad delays); a 32-bit marker finds it in one pass instead of
// sweeping 64 offsets, and a false lock is caught by txcrc rather than believed.
//
// **Half duplex by protocol, full duplex by wire.** While a response is going
// out the receiver is off, so the idle bytes the host clocks to pull the
// response back are swallowed rather than parsed. After the last txcrc bit the
// receiver returns to SYNC hunt, and since idle is all zeroes and SYNC begins
// with a 1, realignment is automatic. That is what makes the link recover from
// a desync without a reset line - there is no reset line to have.
//
// WIDTH is 1 (configuration A) or 3 (configuration C, which needs the
// PIN2<->PIN17 jumper). The return line is one bit in both, so only the receive
// side is parameterised and the whole transmit block below is untouched by it.
//
// 3 does not divide 8, which is the entire difficulty of WIDTH=3 and the reason
// this used to be a compile error. Three consequences, each handled where it
// arises rather than by a mode bit:
//
//   - SYNC can end on any of the three lanes, so the hunt tests three
//     alignments and records how many bits of the same clock are already
//     header (`sync_res`, 0..2).
//   - The byte boundary walks: bytes come out 3 per 8 clocks, at gaps of 3, 3,
//     2 clocks. `rx_bc` carries the leftover bit count instead of a position.
//   - The payload's first and last bits land mid-clock-group, so the forward
//     CRC folds assembled bytes rather than wire bits. See crc8.
//
// This mirrors what firmware/link.pio already does on the other end, where the
// autopull threshold is 24 rather than 32 for the same reason.

`timescale 1ns / 1ps
`default_nettype none

module gemm_link #(
    parameter integer WIDTH  = 1,
    parameter integer AW     = 11,   // strip address bits
    parameter integer CW     = 10,   // spatial coordinate bits
    parameter integer PW     = 8,
    parameter integer GW     = 5,
    parameter integer KW     = 10,
    // Payload ceilings, checked per frame. A length past these means the
    // receiver has locked onto the wrong bit, not that the host wants a big
    // transfer, so the frame is dropped rather than written into the tile.
    parameter integer ACTMAX = 2048,
    parameter integer WGTMAX = 2048
) (
    input  wire                 clk,        // link_clk
    input  wire [WIDTH-1:0]     rx,
    output wire                 tx,

    // ---- tile configuration, held for a whole block ------------------------
    output reg  signed [CW-1:0] cfg_H,
    output reg  signed [CW-1:0] cfg_W,
    output reg  signed [CW-1:0] cfg_OW,
    output reg                  cfg_stride2,
    output reg         [AW-1:0] cfg_strip_rw,
    output reg         [AW-1:0] cfg_strip_ch,
    output reg         [CW-1:0] cfg_oy0,
    output reg         [CW-1:0] cfg_ox0,
    output reg                  cfg_unsigned_in,
    // M14. 1 = the WGT payload for this block is nibbles, two output channels
    // per byte. Per block and not per bitstream: conv0's weights stayed 8-bit
    // for accuracy and conv0 runs on this tile like every other layer.
    output reg                  cfg_w4,
    // M15. 1 = the tile applies the requantize epilogue and DRAIN returns one
    // byte per accumulator instead of four. Per block for the same reason w4
    // is: conv7 emits floats and keeps the int32 readout, and the accumulator
    // sweep that guards the MAC array has to stay runnable in the same
    // bitstream. This also changes the *framing* of the response - see T_DATA.
    output reg                  cfg_rq,
    output reg         [PW-1:0] cfg_P,
    output reg         [GW-1:0] cfg_QG,
    output reg         [KW-1:0] cfg_K,

    // ---- tile buffers and control ------------------------------------------
    output reg                  act_we,
    output reg         [AW-1:0] act_addr,
    output reg         [7:0]    act_data,
    output reg                  wgt_we,
    output reg         [7:0]    wgt_data,
    output reg                  wgt_rst,
    output reg                  run,
    output reg                  run_init,
    input  wire                 busy,
    output reg                  drain,
    input  wire        [31:0]   dout,
    input  wire                 dout_valid,
    output wire                 dout_ready,

    // ---- diagnostics, for the LEDs -----------------------------------------
    output reg                  dbg_seen = 1'b0,   // link_clk has ticked
    output wire                 dbg_err,           // any sticky fault

    // ---- M11: D1 as a score meter -------------------------------------------
    // Two duties, latched from a CMD_LED payload and held. This module does
    // nothing with them - the PWM and the slew limiter live in the top level,
    // on clk_32m, because link_clk stops between frames and a stopped PWM is a
    // frozen colour. What crosses out of here is just a number.
    //
    // `led_own` is sticky and never clears. Until the first CMD_LED the top
    // level keeps D1's bring-up meanings exactly as they were; m7 and m8 never
    // send one, so their LED behaviour is unchanged by construction rather than
    // by inspection.
    output reg  [7:0]           led_r_duty = 8'h00,
    output reg  [7:0]           led_g_duty = 8'h00,
    output reg                  led_own    = 1'b0
);

    generate
        if (WIDTH != 1 && WIDTH != 3) begin : gen_unsupported
            // Deliberate elaboration failure. Only the two widths the board can
            // physically wire are implemented; anything else would need the
            // hunt and the regrouper re-derived, not just a parameter changed.
            unsupported_link_width_only_1_and_3_are_implemented u();
        end
    endgenerate

    // Hunt needs 16 bits plus the WIDTH-1 alignments that can straddle a clock;
    // byte assembly needs 8 plus the same.
    localparam integer HUNTW = 16 + WIDTH - 1;
    localparam integer SRW   =  8 + WIDTH - 1;

    localparam [15:0] SYNC     = 16'h5aa5;        // on the wire: a5 then 5a
    localparam [31:0] PREAMBLE = 32'h3c5ac3a5;    // on the wire: a5 c3 5a 3c
    localparam [31:0] CRCPOLY  = 32'hedb88320;    // reflected CRC-32

    localparam [7:0] CMD_CFG   = 8'h01,
                     CMD_ACT   = 8'h02,
                     CMD_WGT   = 8'h03,
                     CMD_RUN   = 8'h04,
                     CMD_DRAIN = 8'h05,
                     CMD_NOP   = 8'h06,   // ping; clears the sticky faults
                     CMD_LED   = 8'h07,   // M11: two duty bytes for D1
                     CMD_RQP   = 8'h08;   // M15: requantize params into strip

    // M15. The requantize table lives in the top of the tile's strip array,
    // which is 2,048 bytes against a largest block of 1,536 - see the long note
    // in gemm_tile.v about why it is there and not in a memory block of its
    // own. CMD_ACT cannot reach it: R_PAY writes `act_addr <= pcnt` and pcnt
    // starts at zero for every frame, so a strip write above 191 bytes is
    // simply not expressible. Hence a command rather than an offset field.
    //
    // RQBASE is 1792, which is 11'b111_0000_0000, so the write address is the
    // concatenation below and not an add. That is the whole reason gemm_tile
    // picked 1792 over the 1856 its table actually needs.
    localparam integer RQBASE = ACTMAX - 256;
    localparam integer RQMAXB = 192;    // 32 channels x 6 bytes

    localparam integer CFGB = 20;          // CFG payload bytes
    localparam integer CFGW = 8 * CFGB;

    // One bit of reflected CRC-32. Input bits arrive LSB-first within each byte,
    // which is exactly the order this update wants, so wire order and byte order
    // need no reconciling anywhere.
    function [31:0] crc1;
        input [31:0] c;
        input        b;
        begin
            crc1 = (c >> 1) ^ ({32{c[0] ^ b}} & CRCPOLY);
        end
    endfunction

    // Eight of those, LSB-first, which is one whole byte.
    //
    // The receive side folds assembled bytes where the transmit side folds wire
    // bits, and that asymmetry is deliberate. Transmit shifts one bit per clock
    // in every configuration, so it has a bit to fold every clock and no
    // boundary to get wrong. Receive at WIDTH=3 does not: the payload's first
    // and last bits land in the middle of a three-bit group, so a bit-serial
    // fold would need a per-lane mask with two boundary cases, and each is a
    // place to be one bit out for an entire frame - a fault that reproduces as
    // a plausible wrong tensor. Folding the byte the dispatcher was handed
    // cannot disagree with what the tile was given.
    //
    // It costs about one LUT level, not eight: the stages collapse, and every
    // output bit ends up an XOR of at most nine terms.
    function [31:0] crc8;
        input [31:0] c;
        input [7:0]  d;
        integer i;
        reg [31:0] t;
        begin
            t = c;
            for (i = 0; i < 8; i = i + 1)
                t = (t >> 1) ^ ({32{t[0] ^ d[i]}} & CRCPOLY);
            crc8 = t;
        end
    endfunction

    localparam [2:0] R_HUNT = 3'd0,
                     R_HDR  = 3'd1,
                     R_PAY  = 3'd2,
                     R_EXEC = 3'd3,
                     R_WAIT = 3'd4,   // RUN: tile is computing
                     R_TX   = 3'd5,
                     R_CHK  = 3'd6;   // one clock to act on frame_ok

    localparam [2:0] T_PRE  = 3'd0,
                     T_STAT = 3'd1,
                     T_RXC  = 3'd2,
                     T_DATA = 3'd3,
                     T_TXC  = 3'd4;

    reg [2:0] state = R_HUNT;
    reg [2:0] tph   = T_PRE;

    // ------------------------------------------------------------------------
    // Receive: bit hunt, then byte assembly.
    //
    // rx_go is combinational, not a registered strobe. A registered one would
    // present the byte a cycle after the eighth bit, by which time rx_sr has
    // already taken a ninth - the classic way to lose one bit at every state
    // change. Consuming rx_nx on the same cycle keeps the bit counter free-
    // running and the byte boundary fixed from configuration onward.
    // ------------------------------------------------------------------------
    reg  [HUNTW-1:0] hunt_sr = {HUNTW{1'b0}};
    wire [HUNTW-1:0] hunt_nx = {rx, hunt_sr[HUNTW-1:WIDTH]};  // [0] oldest

    // One candidate alignment per lane. sync_hit[j] means SYNC's last bit
    // arrived on lane j this clock, which leaves WIDTH-1-j bits of the same
    // clock already belonging to the header.
    wire [WIDTH-1:0] sync_hit;
    genvar gj;
    generate
        for (gj = 0; gj < WIDTH; gj = gj + 1) begin : gen_hunt
            assign sync_hit[gj] = (hunt_nx[gj +: 16] == SYNC);
        end
    endgenerate

    wire sync_any = |sync_hit;

    // Lowest set lane wins, because lanes are ordered in time within a clock
    // and the earliest match is the one a bit-at-a-time hunt would have found.
    // Two lanes cannot both match unless SYNC is self-similar at a shift of one
    // or two, which 0x5aa5 is not; if it somehow did, the length check and the
    // CRC catch the wrong pick and the frame is dropped, which is what they are
    // for. The loop counts down so that the j=0 assignment lands last.
    reg [2:0] sync_res;
    integer   sj;
    always @* begin
        sync_res = 3'd0;
        for (sj = WIDTH - 1; sj >= 0; sj = sj - 1)
            if (sync_hit[sj]) sync_res = (WIDTH - 1) - sj[2:0];
    end

    reg  [SRW-1:0] rx_sr;
    wire [SRW-1:0] rx_sr_nx = {rx, rx_sr[SRW-1:WIDTH]};

    // rx_bc is bits held over from previous clocks, 0..7 - not a position. At
    // WIDTH=1 it counts 0..7 and behaves exactly as the bit counter it replaces.
    reg  [2:0] rx_bc = 3'd0;
    wire [3:0] rx_bn = rx_bc + WIDTH[3:0];   // bits available now, 1..10

    // R_CHK is in here, and it has to be: the host does not pause. The clock
    // R_CHK spends deciding carries payload bits, and dropping them is a slip
    // through the rest of the frame - which the CRC would catch and nothing
    // would explain. One clock is provably enough at WIDTH=3 too: a byte leaves
    // rx_bc at 0, 1 or 2, so the next strobe is at least two clocks away and
    // never lands inside R_CHK.
    wire       rx_en = (state == R_HDR) || (state == R_CHK) || (state == R_PAY);

    // rx_bn >= 8. Bounded above by 10, so bit 3 alone decides it.
    wire       rx_go = rx_en && rx_bn[3];

    // The byte is the eight oldest unconsumed bits. At WIDTH=1 rx_off is
    // constant zero and this is the fixed slice it always was; at WIDTH=3 the
    // boundary walks through three positions with period 8 clocks, so it is a
    // three-way mux - one LUT level, not a barrel shifter. The pad bits are
    // never selected and exist only to keep the part-select in range at WIDTH=1.
    wire [3:0]     rx_off4 = SRW[3:0] - rx_bn;
    wire [1:0]     rx_off  = rx_bn[3] ? rx_off4[1:0] : 2'd0;
    wire [SRW+2:0] rx_win  = {3'b000, rx_sr_nx};
    wire [7:0]     rx_nx   = rx_win[rx_off +: 8];

    // hunt_sr is NOT shifted here. It is cleared by the control block on every
    // resynchronisation, and a register driven from two always blocks is a
    // simulation race that iverilog resolves by source order and Verific
    // rejects outright (VDB-1000). Both are right to object: "whichever block
    // ran last" is not a design. The shift lives with the clear, below.
    //
    // rx_sr, by contrast, shifts unconditionally. It has to: at WIDTH=3 the
    // bits after SYNC on the locking clock are already header, and the clock
    // that locks is one where rx_en is still low. At WIDTH=1 there is no such
    // residue and the extra shifts are overwritten long before the first byte.
    always @(posedge clk) begin
        dbg_seen <= 1'b1;
        rx_sr <= rx_sr_nx;
        // rx_bn[2:0] is rx_bn-8 when a byte went out and rx_bn otherwise, since
        // rx_bn only reaches bit 3 on the clocks that emit. One expression, and
        // at WIDTH=1 it is the same increment that wrapped at 8 by construction.
        if (rx_en)                                rx_bc <= rx_bn[2:0];
        else if ((state == R_HUNT) && sync_any)   rx_bc <= sync_res;
        else                                      rx_bc <= 3'd0;
    end

    // Set with frame_ok when the length bytes land, read one clock later in
    // R_CHK to choose between R_PAY and R_EXEC.
    reg         has_pay;

    // ---- forward CRC, over payload bytes only ------------------------------
    //
    // Exactly the bytes R_PAY dispatches, folded on the clock it dispatches
    // them. That is the same bit set the bit-serial version covered - it had to
    // include R_CHK, whose wire bit is bit 0 of payload byte 0, and exclude it
    // for empty frames so crc32(b"") came out right - but stated over bytes it
    // needs neither the special case nor has_pay, and it is the only form that
    // survives WIDTH=3. An empty payload leaves rxcrc at the seed, and R_EXEC
    // complements it to 0, which is what zlib.crc32(b"") returns.
    reg  [31:0] rxcrc;
    wire        rxcrc_en = rx_go && (state == R_PAY);
    wire [31:0] rxcrc_nx = rxcrc_en ? crc8(rxcrc, rx_nx) : rxcrc;
    reg  [31:0] rxout;

    // ------------------------------------------------------------------------
    // Frame state.
    // ------------------------------------------------------------------------
    reg [7:0]     cmd;
    reg [7:0]     len_lo;    // low header byte, held until the high one lands
    reg [15:0]    pcnt;      // payload bytes consumed, = strip address for ACT
    reg [15:0]    rem;       // payload bytes outstanding
    reg           pay_last;  // rem will hit zero on the next byte
    reg [1:0]     hcnt;      // header bytes consumed
    reg [CFGW-1:0] cfg_sr;
    reg           seen_busy;
    reg           underrun  = 1'b0;
    reg           bad_frame = 1'b0;

    assign dbg_err = underrun | bad_frame;

    // A frame is rejected before any of it reaches the tile. len is checked
    // against the command, not against a single global maximum, because the
    // fixed-size commands are the cheap ones to police and a CFG that is one
    // byte adrift would otherwise scramble the whole geometry.
    //
    // The check is spread over the three header bytes instead of evaluated at
    // the end of them. Written the obvious way - a six-way mux of 16-bit
    // compares against {rx_nx, len_lo} - it was a five-deep cone hanging off the
    // bit that had just finished arriving, and on a device where every LUT
    // costs 0.000 ns and every net costs 1.5-6 ns, depth is the only thing that
    // matters. Each byte is 8 clocks apart, so the work goes where the time is:
    //
    //   hcnt 0 : cmd lands      -> decode the limit and the comparison kind
    //   hcnt 1 : len_lo lands   -> resolve the low byte against it
    //   hcnt 2 : len_hi lands   -> one 8-bit compare, then register the verdict
    //   R_CHK  : act on it      -> a single registered bit
    wire [15:0] len_nx = {rx_nx, len_lo};

    reg [15:0] len_lim;    // permitted length for this command
    reg        len_exact;  // 1: must equal len_lim; 0: must be <= it
    reg        cmd_known;  // 0: not a command at all, reject whatever the length
    reg        lo_eq, lo_le;
    reg        frame_ok;

    wire hi_eq = (rx_nx == len_lim[15:8]);
    wire hi_lt = (rx_nx <  len_lim[15:8]);
    wire len_ok = len_exact ? (hi_eq && lo_eq)
                            : (hi_lt || (hi_eq && lo_le));

    // ------------------------------------------------------------------------
    // Transmit.
    // ------------------------------------------------------------------------
    reg        tx_en = 1'b0;
    reg [7:0]  tx_sr;
    reg [2:0]  tx_bc;
    reg [31:0] tsh;         // remaining bytes of the current 4-byte group
    reg [1:0]  tidx;

    assign tx = tx_en & tx_sr[0];

    wire tx_last = tx_en && (tx_bc == 3'd7);

    // The preamble is outside the checksum: the host uses it to find the byte
    // boundary, so it has to be recognisable before anything can be verified.
    wire        txcrc_en = (tph == T_STAT) || (tph == T_RXC) || (tph == T_DATA);
    reg  [31:0] txcrc;
    wire [31:0] txcrc_nx = (tx_en && txcrc_en) ? crc1(txcrc, tx_sr[0]) : txcrc;
    wire [31:0] txfin    = ~txcrc_nx;

    // Sampled at dispatch, not at T_STAT. NOP exists to read the sticky faults
    // back and it also clears them, so a combinational status byte would report
    // the post-clear value and NOP could never see the fault it was sent for.
    // The consequence is that a fault raised *during* a response (an underrun in
    // T_DATA) shows up on the next frame, which is the only place it can.
    wire [7:0] status_now = {cmd[3:0], busy, bad_frame, underrun, 1'b1};
    reg  [7:0] stat_r;

    // ---- drain word prefetch -------------------------------------------------
    // dout_valid rises once per accumulator, so its rising edge is the latch
    // event and no ready/valid bookkeeping is needed. drdy is pulsed at the
    // *start* of a word rather than at its end, which buys the tile 32 link
    // clocks to produce the next one against the 3 cycles it actually needs.
    reg        dv_d = 1'b0;
    wire       dv_rise = dout_valid && !dv_d;
    reg [31:0] wcur;
    reg        have = 1'b0;
    reg        drdy = 1'b0;

    // The three commands whose identity is read outside R_PAY's byte dispatch,
    // decoded once when the command byte lands and not again. An 8-bit compare
    // is three LUT levels on this device, and cmd[] sits 5 columns from the
    // logic that reads it - `cmd == CMD_RUN` in R_EXEC put an 8-bit equal in
    // front of the R_TX branch tree and made cmd[6] -> 5 LUTs -> tsh[4]|CE the
    // critical path at 16.6 ns. The header gives 17 idle clocks between the
    // command byte and R_EXEC; spending one flop each to use them is free.
    //
    // Nothing rewrites cmd between the decode and the read: the next header
    // byte cannot arrive until R_HUNT, which is past the end of the response.
    reg        is_run   = 1'b0;
    reg        is_drain = 1'b0;
    reg        is_wgt   = 1'b0;

    assign dout_ready = drdy;

    always @(posedge clk) begin
        dv_d <= dout_valid;
    end

    // ------------------------------------------------------------------------
    // Control.
    // ------------------------------------------------------------------------
    always @(posedge clk) begin
        act_we  <= 1'b0;
        wgt_we  <= 1'b0;
        wgt_rst <= 1'b0;
        run     <= 1'b0;
        drain   <= 1'b0;
        drdy    <= 1'b0;

        hunt_sr <= hunt_nx;     // overridden by the clears in R_HDR and T_TXC
        rxcrc   <= rxcrc_nx;
        if (tx_en) txcrc <= txcrc_nx;

        // Prefetch runs independently of the transmit phase so word 0 is already
        // in hand by the time the 9 header bytes have gone out.
        if (dv_rise) begin
            wcur <= dout;
            have <= 1'b1;
        end

        case (state)
        // --------------------------------------------------------------------
        R_HUNT: begin
            if (sync_any) begin
                hcnt  <= 2'd0;
                state <= R_HDR;
            end
        end

        // --------------------------------------------------------------------
        R_HDR: if (rx_go) begin
            hcnt <= hcnt + 2'd1;
            case (hcnt)
            2'd0: begin
                cmd       <= rx_nx;
                is_run    <= (rx_nx == CMD_RUN);
                is_drain  <= (rx_nx == CMD_DRAIN);
                is_wgt    <= (rx_nx == CMD_WGT);
                cmd_known <= (rx_nx == CMD_CFG) || (rx_nx == CMD_ACT) ||
                             (rx_nx == CMD_WGT) || (rx_nx == CMD_RUN) ||
                             (rx_nx == CMD_DRAIN) || (rx_nx == CMD_NOP) ||
                             (rx_nx == CMD_LED) || (rx_nx == CMD_RQP);
                case (rx_nx)
                CMD_ACT: begin len_lim <= ACTMAX[15:0]; len_exact <= 1'b0; end
                CMD_WGT: begin len_lim <= WGTMAX[15:0]; len_exact <= 1'b0; end
                CMD_CFG: begin len_lim <= CFGB[15:0];   len_exact <= 1'b1; end
                CMD_RUN: begin len_lim <= 16'd1;        len_exact <= 1'b1; end
                CMD_LED: begin len_lim <= 16'd2;        len_exact <= 1'b1; end
                // <=, not ==: a block with Q < 32 sends only the entries it
                // has. The tile reads the table by channel index, so a short
                // table is well defined and a long one would run off 1984.
                CMD_RQP: begin len_lim <= RQMAXB[15:0]; len_exact <= 1'b0; end
                // DRAIN and NOP carry nothing, and so does anything
                // cmd_known has already rejected.
                default: begin len_lim <= 16'd0;        len_exact <= 1'b1; end
                endcase
            end
            2'd1: begin
                len_lo <= rx_nx;
                lo_eq  <= (rx_nx == len_lim[7:0]);
                lo_le  <= (rx_nx <= len_lim[7:0]);
            end
            default: begin
                pcnt     <= 16'd0;
                rem      <= len_nx;
                pay_last <= (len_nx == 16'd1);
                has_pay  <= (len_nx != 16'd0);
                rxcrc    <= 32'hffff_ffff;
                frame_ok <= cmd_known && len_ok;
                state    <= R_CHK;
            end
            endcase
        end

        // --------------------------------------------------------------------
        // One clock, spent so that the length verdict is a register rather than
        // a combinational cone reaching the state vector and a 16-bit shift
        // register's synchronous clear. There are 8 clocks between bytes and
        // this borrows one of them; R_PAY's first byte is still 8 clocks away.
        R_CHK: begin
            if (!frame_ok) begin
                // Cannot skip a payload of unknown length, so drop the whole
                // frame and resynchronise. The host sees no response, then
                // reads this flag back with NOP.
                bad_frame <= 1'b1;
                hunt_sr   <= {HUNTW{1'b0}};
                state     <= R_HUNT;
            end else begin
                if (is_wgt) wgt_rst <= 1'b1;
                // cfg_sr is NOT cleared anywhere. CFGW is exactly 8*CFGB and a
                // CFG frame is length-checked to CFGB bytes, so twenty shifts
                // replace every bit of it; a clear would only ever write zeroes
                // that were about to be overwritten. It cost more than it looks:
                // the clear's enable carried frame_ok onto a net with 161 sinks,
                // and that was the critical path.
                state <= has_pay ? R_PAY : R_EXEC;
            end
        end

        // --------------------------------------------------------------------
        // One dispatch per byte. ACT addresses the strip by payload index, so a
        // block always loads from zero - which is what split-K does anyway, and
        // what firmware/gen_gemm_vec.c's vectors assume.
        // `pay_last` is precomputed a byte ahead rather than tested as
        // `pcnt == len - 1` here. That test cost 5 levels of logic between a
        // 16-bit register and a 33-fanout clock-enable net, and at 13.333 ns it
        // was the critical path of the whole design - ahead of the MAC array,
        // by 6.1 ns. There are 8 clocks between byte boundaries; spending one
        // of them on the comparison is free.
        R_PAY: if (rx_go) begin
            pcnt     <= pcnt + 16'd1;
            rem      <= rem - 16'd1;
            pay_last <= (rem == 16'd2);

            // Unconditional, outside the case: every payload byte of every
            // command shifts cfg_sr. An ACT frame therefore fills it with
            // activations, which is harmless - the cfg_* outputs are latched at
            // a CFG frame's R_EXEC and never read from cfg_sr again, and the
            // next CFG refills all twenty bytes. What this buys is a clock
            // enable of `state == R_PAY && rx_bc == 7` on the widest register in
            // the design, instead of that AND an 8-bit compare against CMD_CFG.
            cfg_sr <= {rx_nx, cfg_sr[CFGW-1:8]};

            case (cmd)
            CMD_ACT: begin
                act_we   <= 1'b1;
                act_addr <= pcnt[AW-1:0];
                act_data <= rx_nx;
            end
            // Same write port, same byte, a different top three address bits.
            // RQBASE is a power-of-two boundary and the length check above caps
            // pcnt at 191, so this is wiring and not arithmetic.
            CMD_RQP: begin
                act_we   <= 1'b1;
                act_addr <= {RQBASE[AW-1:8], pcnt[7:0]};
                act_data <= rx_nx;
            end
            CMD_WGT: begin
                wgt_we   <= 1'b1;
                wgt_data <= rx_nx;
            end
            CMD_RUN: run_init <= rx_nx[0];
            // Byte 0 is red, byte 1 is green, and `pcnt` is still the index of
            // the byte in hand - the increment above is non-blocking. The
            // length check has already guaranteed exactly two, so `led_own`
            // rises only after a complete pair.
            //
            // Latched here and not at R_EXEC, which would be the tidier place:
            // R_EXEC would have to read the payload back out of cfg_sr, and
            // cfg_sr is the widest register in the design - the comment at
            // R_CHK records that touching its enable net was once the critical
            // path. This is one more branch on a decoder that already exists.
            CMD_LED: begin
                if (pcnt[0]) begin
                    led_g_duty <= rx_nx;
                    led_own    <= 1'b1;
                end else begin
                    led_r_duty <= rx_nx;
                end
            end
            default: ;
            endcase

            if (pay_last) state <= R_EXEC;
        end

        // --------------------------------------------------------------------
        // rxout is finalised here rather than on the last payload byte. rxcrc
        // only advances while state == R_PAY, so by the time this runs it
        // already holds the last bit - and a zero-length payload leaves it at
        // the seed, whose complement is 0, which is what zlib.crc32(b"")
        // returns. One assignment replaces a special case and a 32-bit enable.
        R_EXEC: begin
            rxout <= ~rxcrc;

            case (cmd)
            CMD_CFG: begin
                cfg_H           <= cfg_sr[  0 +: CW];
                cfg_W           <= cfg_sr[ 16 +: CW];
                cfg_OW          <= cfg_sr[ 32 +: CW];
                cfg_strip_rw    <= cfg_sr[ 48 +: AW];
                cfg_strip_ch    <= cfg_sr[ 64 +: AW];
                cfg_oy0         <= cfg_sr[ 80 +: CW];
                cfg_ox0         <= cfg_sr[ 96 +: CW];
                cfg_K           <= cfg_sr[112 +: KW];
                cfg_P           <= cfg_sr[128 +: PW];
                cfg_QG          <= cfg_sr[136 +: GW];
                cfg_stride2     <= cfg_sr[144];
                cfg_unsigned_in <= cfg_sr[145];
                cfg_w4          <= cfg_sr[146];
                // M15. Bit 147 was the last spare in p[18], which is why the
                // mode is a cfg bit and not a new command: CFGB stays 20 and
                // test_gemm_plan.c's M6c framing anchors are undisturbed.
                cfg_rq          <= cfg_sr[147];
            end
            CMD_NOP: begin
                underrun  <= 1'b0;
                bad_frame <= 1'b0;
            end
            default: ;
            endcase

            stat_r <= status_now;

            if (is_run) begin
                run       <= 1'b1;
                seen_busy <= 1'b0;
                state     <= R_WAIT;
            end else begin
                if (is_drain) begin
                    drain <= 1'b1;
                    have  <= 1'b0;   // no stale word from an aborted readout
                end
                tx_en <= 1'b1;
                tx_bc <= 3'd0;
                tph   <= T_PRE;
                tidx  <= 2'd0;
                tx_sr <= PREAMBLE[7:0];
                tsh   <= PREAMBLE >> 8;
                txcrc <= 32'hffff_ffff;
                state <= R_TX;
            end
        end

        // --------------------------------------------------------------------
        // RUN. busy lags the pulse by a cycle, so wait to see it rise before
        // believing it has fallen.
        R_WAIT: begin
            if (busy) begin
                seen_busy <= 1'b1;
            end else if (seen_busy) begin
                tx_en <= 1'b1;
                tx_bc <= 3'd0;
                tph   <= T_PRE;
                tidx  <= 2'd0;
                tx_sr <= PREAMBLE[7:0];
                tsh   <= PREAMBLE >> 8;
                txcrc <= 32'hffff_ffff;
                state <= R_TX;
            end
        end

        // --------------------------------------------------------------------
        // Every branch below loads tx_sr on the cycle the last bit of the
        // previous byte is presented, so the bit stream never gaps. A gap would
        // repeat a bit and be caught by txcrc, but only after the fact.
        R_TX: begin
            if (tx_last) begin
                tx_bc <= 3'd0;
                case (tph)
                T_PRE:
                    if (tidx == 2'd3) begin
                        tph   <= T_STAT;
                        tx_sr <= stat_r;
                    end else begin
                        tidx  <= tidx + 2'd1;
                        tx_sr <= tsh[7:0];
                        tsh   <= tsh >> 8;
                    end

                T_STAT: begin
                    tph   <= T_RXC;
                    tidx  <= 2'd0;
                    tx_sr <= rxout[7:0];
                    tsh   <= rxout >> 8;
                end

                T_RXC:
                    if (tidx == 2'd3) begin
                        if (is_drain) begin
                            tph  <= T_DATA;
                            tidx <= 2'd0;
                            if (have) begin
                                tx_sr <= wcur[7:0];
                                tsh   <= wcur >> 8;
                                // Not `1'b0`: the prefetch above may have landed
                                // a fresh word into wcur on this very cycle, and
                                // clearing unconditionally would drop it. The
                                // tile is never that slow in practice, but the
                                // failure mode is a short block, not an error.
                                have  <= dv_rise;
                                drdy  <= 1'b1;
                            end else begin
                                // The tile had 72 link clocks to produce word 0
                                // and needed 4. Reaching here means the drain
                                // pulse never landed.
                                underrun <= 1'b1;
                                tph      <= T_TXC;
                                tx_sr    <= txfin[7:0];
                                tsh      <= txfin >> 8;
                            end
                        end else begin
                            tph   <= T_TXC;
                            tidx  <= 2'd0;
                            tx_sr <= txfin[7:0];
                            tsh   <= txfin >> 8;
                        end
                    end else begin
                        tidx  <= tidx + 2'd1;
                        tx_sr <= tsh[7:0];
                        tsh   <= tsh >> 8;
                    end

                // Length is not counted here. The tile stops presenting words
                // when the block is drained, and `busy` distinguishes "done"
                // from "late" - so no multiplier is needed to form P*Q, and all
                // eight belong to the MAC array.
                //
                // M15: at cfg_rq the tile puts the code in dout[7:0] and the
                // other three bytes are zero, so the serializer refetches on
                // every byte rather than every fourth. That is the entire wire
                // format change - `tsh` is still loaded and still shifted, it
                // just never gets read. Writing it as a term on the existing
                // test rather than as a second arm keeps the two modes on one
                // decoder, and keeps the underrun path shared: falling behind
                // is now four times easier and the sticky bit that reports it
                // must not be in the mode-specific half.
                T_DATA:
                    if (cfg_rq || (tidx == 2'd3)) begin
                        if (have) begin
                            tidx  <= 2'd0;
                            tx_sr <= wcur[7:0];
                            tsh   <= wcur >> 8;
                            have  <= dv_rise;      // see T_RXC
                            drdy  <= 1'b1;
                        end else begin
                            if (busy) underrun <= 1'b1;
                            tph   <= T_TXC;
                            tidx  <= 2'd0;
                            tx_sr <= txfin[7:0];
                            tsh   <= txfin >> 8;
                        end
                    end else begin
                        tidx  <= tidx + 2'd1;
                        tx_sr <= tsh[7:0];
                        tsh   <= tsh >> 8;
                    end

                T_TXC:
                    if (tidx == 2'd3) begin
                        tx_en   <= 1'b0;
                        hunt_sr <= {HUNTW{1'b0}};
                        state   <= R_HUNT;
                    end else begin
                        tidx  <= tidx + 2'd1;
                        tx_sr <= tsh[7:0];
                        tsh   <= tsh >> 8;
                    end

                default: begin
                    tx_en <= 1'b0;
                    state <= R_HUNT;
                end
                endcase
            end else if (tx_en) begin
                tx_sr <= {1'b0, tx_sr[7:1]};
                tx_bc <= tx_bc + 3'd1;
            end
        end

        default: state <= R_HUNT;
        endcase
    end

endmodule

`default_nettype wire
