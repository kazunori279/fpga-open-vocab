// M5c: U1 is a healthy APS1604M. Find out why its reply is out of frame.
//
// The story so far, because each revision overturned the last and the wrong
// conclusion is cheap to re-reach.
//
// U1 is an APS1604M, physically on the board (photographed, underside,
// 2026-07-30). GPIO0 is wired to its CS, SD0..3 and SCLK land on the QMI, and
// the SDK is configured with PICO_PSRAM_CS_PIN 0 and PICO_AUTO_DETECT_PSRAM_SIZE.
// Yet psram_detect_size() returns 0, which it does for exactly one reason: the
// byte it read as KGD was not 0x5D. It then throws all eight bytes away.
//
// Rev 1 printed those bytes and declared U1 silent. That verdict was wrong - the
// classifier only recognized an exact 0D 5D pair and counted anything else as
// absence. The device was answering `00 00 00 00 5e 0c 03 57`, identically on
// every read, starting to drive at exactly rx[4].
//
// Rev 2 swept the sampling point: CLKDIV 6..100 (25 MHz down to 1.5 MHz SCK) x
// RXDELAY 0..3, twenty combinations, with a CS0 control row at every divisor.
// Every CS1 row came back byte-identical. That kills the timing hypothesis
// outright - no sampling point exists that would have worked - and a quad-lane
// 0xF5 plus a quad READ ID killed the QPI hypothesis alongside it.
//
// But rev 2 read twelve bytes instead of eight, and the four extra bytes are
// what cracked it. Searching the reply at *bit* granularity rather than byte:
//
//     00 00 00 00 5e 0c 03 57 46 f6 9c 06
//     -> 0D 5D at bit 50, then EID 1B DA 70
//     -> MFID 0x0D = AP Memory, KGD 0x5D = known good die,
//        EID[0] >> 5 = 0 = 2 MiB.
//
// Which is precisely an APS1604M. **The chip is healthy, it parsed the command,
// and it returned the correct ID.** Nothing is wrong with U1 and nothing is
// wrong with our sampling. The reply is simply 18 bit-times later than the
// datasheet says: 0x9F plus a 24-bit address should put MFID at bit 32.
//
// 18 is the whole remaining mystery, and it is a strange number - neither byte
// nor nibble aligned. It is also stable across a 16x change in SCK, which rules
// out the statistical faults; ringing and marginal edges do not reproduce
// bit-for-bit. And bits 32..47 are 5E 0C, a near-miss copy of 0D 5D sitting
// immediately before the clean one, as if the reply were emitted twice.
//
// This revision stops guessing at mechanism and measures four things instead.
// Each is chosen so that its result constrains the next one, and all four fit
// in one boot because every attempt costs a PRG strap.

#include <stdio.h>
#include <string.h>

#include "pico/stdlib.h"
#include "pico/bootrom.h"
#include "hardware/clocks.h"
#include "hardware/flash.h"
#include "hardware/gpio.h"
#include "hardware/psram.h"
#include "hardware/sync.h"
#include "hardware/structs/pads_bank0.h"
#include "hardware/structs/qmi.h"

#define PSRAM_CS 0

#define CMD_READ_ID  0x9Fu

#define MAXBYTES 48

// DIRECT_TX is a command word, not a byte: the low 16 bits are data and the
// rest select lane width and whether the pads drive. Mixing widths inside one
// CS assertion is legal and test D depends on it - single-lane command, then a
// quad-width read phase so all four lanes are captured at once.
#define TX_S(b)     ((uint32_t)(uint8_t)(b))
#define TX_Q_IN(b)  ((uint32_t)(uint8_t)(b) \
                     | (QMI_DIRECT_TX_IWIDTH_VALUE_Q << QMI_DIRECT_TX_IWIDTH_LSB))

// --- the transfer ----------------------------------------------------------

// A direct-mode transfer with the sampling point under our control.
// flash_do_cmd_cs() cannot do this - it hands DIRECT_CSR to the bootrom and
// then only sets EN - so the register sequence is open-coded. Everything this
// touches while XIP is off must live in RAM; the caller owns interrupts.
static void __no_inline_not_in_flash_func(raw_xfer)(
        const uint32_t *tx, uint8_t *rx, size_t n,
        uint cs, uint clkdiv, uint rxdelay)
{
    rom_connect_internal_flash();
    rom_flash_exit_xip();

    qmi_hw->direct_csr = (clkdiv  << QMI_DIRECT_CSR_CLKDIV_LSB)
                       | (rxdelay << QMI_DIRECT_CSR_RXDELAY_LSB)
                       | QMI_DIRECT_CSR_EN_BITS;
    while (qmi_hw->direct_csr & QMI_DIRECT_CSR_BUSY_BITS)
        tight_loop_contents();

    // A leftover byte in the RX FIFO would shift every byte of the reply and
    // look exactly like the bug being chased.
    while (!(qmi_hw->direct_csr & QMI_DIRECT_CSR_RXEMPTY_BITS))
        (void)qmi_hw->direct_rx;

    const uint32_t cs_bit = cs ? QMI_DIRECT_CSR_ASSERT_CS1N_BITS
                               : QMI_DIRECT_CSR_ASSERT_CS0N_BITS;
    hw_set_bits(&qmi_hw->direct_csr, cs_bit);

    size_t tx_left = n, rx_left = n;
    while (tx_left || rx_left) {
        uint32_t f = qmi_hw->direct_csr;
        if (tx_left && !(f & QMI_DIRECT_CSR_TXFULL_BITS)) {
            qmi_hw->direct_tx = *tx++;
            tx_left--;
        }
        if (rx_left && !(f & QMI_DIRECT_CSR_RXEMPTY_BITS)) {
            *rx++ = (uint8_t)qmi_hw->direct_rx;
            rx_left--;
        }
    }

    hw_clear_bits(&qmi_hw->direct_csr, cs_bit);
    qmi_hw->direct_csr = 0;

    // Getting back to XIP: the only public path is the tail of
    // flash_do_cmd_cs(), which re-runs boot2 and restores the M0 window, so a
    // throwaway READ ID on the stacked flash is how this returns to code that
    // lives in flash. Both functions are RAM-resident, so calling it with XIP
    // down is fine - what is not fine is calling raw_xfer before
    // flash_do_cmd_cs() has run once, because its boot2 copyout reads flash
    // through XIP. main() does a CS0 transfer first for that reason.
    const uint8_t nop[1] = { CMD_READ_ID };
    uint8_t sink[1];
    flash_do_cmd_cs(nop, sink, 1, 0);
}

// Interrupts must be off across any transfer that takes XIP down: every handler
// in this build lives in flash, TinyUSB's included, and the host polling the CDC
// endpoint means one is always about to fire. Rev 1 learned this the hard way.
static void xfer(const uint32_t *tx, uint8_t *rx, size_t n,
                 uint cs, uint clkdiv, uint rxdelay)
{
    uint32_t irq = save_and_disable_interrupts();
    raw_xfer(tx, rx, n, cs, clkdiv, rxdelay);
    restore_interrupts(irq);
}

// --- bit-level decoding ----------------------------------------------------
//
// Byte-aligned searching is what hid the answer for two revisions. Everything
// below works in bits.

static uint32_t bits_at(const uint8_t *buf, size_t off, int n)
{
    uint32_t v = 0;
    for (int k = 0; k < n; k++) {
        size_t b = off + (size_t)k;
        v = (v << 1) | ((buf[b >> 3] >> (7 - (b & 7))) & 1u);
    }
    return v;
}

// Bit offset of a 16-bit signature, searching from `from`, or -1.
static int find_sig(const uint8_t *buf, size_t nbits, size_t from, uint32_t sig)
{
    for (size_t off = from; off + 16 <= nbits; off++)
        if (bits_at(buf, off, 16) == sig)
            return (int)off;
    return -1;
}

// Bit offset of the AP Memory MFID+KGD pair, searching from `from`, or -1.
static int find_id(const uint8_t *buf, size_t nbits, size_t from)
{
    return find_sig(buf, nbits, from, 0x0D5Du);
}

static void print_hex(const uint8_t *b, size_t n)
{
    for (size_t i = 0; i < n; i++)
        printf("%02x%s", b[i], (i % 16 == 15 && i + 1 < n) ? "\n                 " : " ");
}

// The SDK's ladder from psram_eid_to_size(), not an ad-hoc shift: rev 3 printed
// `1 << size_id` and so reported 1 MiB where the SDK says 2. It lived inline in
// two places, one got fixed and the other kept printing 1 MiB for another
// revision - hence the single copy here.
static unsigned eid_to_mib(uint32_t eid)
{
    unsigned sid = (unsigned)(eid >> 5);
    if (sid == 4)                                 return 16;
    if (eid == 0x26 || sid == 2 || sid == 3)      return 8;
    if (sid == 1)                                 return 4;
    return 2;
}

// Reports every occurrence, not just the first: if the reply repeats, the gap
// between hits is its period, and a period is a mechanism.
static int report_ids(const uint8_t *buf, size_t nbits, int expect_at)
{
    int n = 0, at = -1;
    size_t from = 0;
    while ((at = find_id(buf, nbits, from)) >= 0) {
        uint32_t eid = bits_at(buf, (size_t)at + 16, 8);
        printf("      0D 5D at bit %d", at);
        if (expect_at >= 0)
            printf(" (expected %d, off by %+d)", expect_at, at - expect_at);
        printf(", EID %02x -> size_id %u -> %u MiB\n",
               (unsigned)eid, (unsigned)(eid >> 5), eid_to_mib(eid));
        n++;
        from = (size_t)at + 1;
    }
    if (!n)
        printf("      no 0D 5D at any bit offset\n");
    return n;
}

// --- test A/B/C: single-lane frames ----------------------------------------

// One single-lane transfer: `opcode`, then `n_addr` address bytes, then payload
// bytes clocked as 0xFF. The device should start driving at bit 8 + 8*n_addr.
static int frame(const char *label, uint8_t opcode, int n_addr, size_t total,
                 uint clkdiv)
{
    uint32_t tx[MAXBYTES];
    uint8_t  rx[MAXBYTES] = { 0 };

    if (total > MAXBYTES) total = MAXBYTES;
    tx[0] = TX_S(opcode);
    for (size_t i = 1; i < total; i++)
        tx[i] = TX_S((int)i <= n_addr ? 0x00 : 0xFF);

    // Label and flush before the transfer: if a step takes the board down, the
    // last line on the wire has to name the step that did it.
    printf("  %-30s ", label);
    stdio_flush();

    xfer(tx, rx, total, 1, clkdiv, 0);

    print_hex(rx, total);
    printf("\n");
    return report_ids(rx, total * 8, 8 + 8 * n_addr);
}

int main(void)
{
    stdio_init_all();
    while (!stdio_usb_connected())
        sleep_ms(50);
    sleep_ms(200);

    printf("\n=== M5c rev4: is the 18-bit slip ours or U1's? ===\n\n");
    printf("clock     : %u MHz sys\n", (unsigned)(clock_get_hz(clk_sys) / 1000000));
    printf("sdk says  : psram_is_available=%d, size=%u\n",
           (int)psram_is_available(), (unsigned)psram_get_size());
    uint32_t pad = pads_bank0_hw->io[PSRAM_CS];
    printf("gpio%-6d: func=%u (want %u), iso=%u\n", PSRAM_CS,
           (unsigned)gpio_get_function(PSRAM_CS), (unsigned)GPIO_FUNC_XIP_CS1,
           (unsigned)((pad & PADS_BANK0_GPIO0_ISO_BITS) != 0));
    if (gpio_get_function(PSRAM_CS) != GPIO_FUNC_XIP_CS1)
        gpio_set_function(PSRAM_CS, GPIO_FUNC_XIP_CS1);

    flash_devinfo_set_cs_size(1, FLASH_DEVINFO_SIZE_8K);

    // Priming, and the reason raw_xfer can get back to XIP at all: this is the
    // call that populates the boot2 copyout. It doubles as the control - the
    // stacked flash is known good because it is running this program, so if
    // this row stops decoding, the harness is broken and nothing below it means
    // anything.
    {
        uint8_t tx[8], rx[8] = { 0 };
        memset(tx, 0xFF, sizeof tx);
        tx[0] = CMD_READ_ID;
        uint32_t irq = save_and_disable_interrupts();
        flash_do_cmd_cs(tx, rx, sizeof tx, 0);
        restore_interrupts(irq);
        printf("cs0 control: ");
        print_hex(rx, sizeof rx);
        printf(" -> JEDEC mfr %02x type %02x, %u KiB\n",
               rx[1], rx[2], (unsigned)(1u << rx[3]) / 1024u);
    }

    // --- 0: the control rev 1-3 never actually had -------------------------
    //
    // Every previous "control" compared flash-via-flash_do_cmd_cs() against
    // U1-via-raw_xfer(). Two variables at once, so a slip born in raw_xfer()
    // would have looked exactly like a slip born in U1. This fills the missing
    // cell: the same flash, the same known-good reply, down the same code path
    // the CS1 reads use.
    //
    // W25Q16 answers 0x9F with EF 40 15 and no address phase, so `EF 40` must
    // land at bit 8. Land it at bit 26 instead - 8 + the same 18 - and the
    // fault is ours and fixable in software. Land it at 8 and raw_xfer() is
    // exonerated and the 18 belongs to the U1 transaction.
    printf("\n0. path x chip matrix - where is the 18 bits born?\n");
    {
        uint32_t tx[16];
        uint8_t  rx[16];
        for (int i = 0; i < 16; i++) tx[i] = TX_S(i ? 0xFF : CMD_READ_ID);

        uint8_t ftx[16];
        memset(ftx, 0xFF, sizeof ftx);
        ftx[0] = CMD_READ_ID;

        struct { const char *label; bool raw; uint cs; uint32_t sig; int want; } row[] = {
            { "flash_do_cmd_cs + cs0 (flash)", false, 0, 0xEF40u,  8 },
            { "raw_xfer        + cs0 (flash)", true,  0, 0xEF40u,  8 },
            { "flash_do_cmd_cs + cs1 (U1)   ", false, 1, 0x0D5Du, 32 },
            { "raw_xfer        + cs1 (U1)   ", true,  1, 0x0D5Du, 32 },
        };

        for (unsigned r = 0; r < count_of(row); r++) {
            memset(rx, 0, sizeof rx);
            printf("  %s ", row[r].label);
            stdio_flush();
            if (row[r].raw) {
                xfer(tx, rx, sizeof rx, row[r].cs, 20, 0);
            } else {
                uint32_t irq = save_and_disable_interrupts();
                flash_do_cmd_cs(ftx, rx, sizeof rx, row[r].cs);
                restore_interrupts(irq);
            }
            print_hex(rx, sizeof rx);
            int at = find_sig(rx, sizeof rx * 8, 0, row[r].sig);
            if (at < 0)
                printf("\n      %04x not present\n", (unsigned)row[r].sig);
            else
                printf("\n      %04x at bit %d (want %d, off by %+d)\n",
                       (unsigned)row[r].sig, at, row[r].want, at - row[r].want);
        }
    }

    // --- A: does the reply repeat? -----------------------------------------
    //
    // Rev 2 read twelve bytes and found one ID. Forty-eight bytes is 384 bit
    // times: if the part loops its ID while CS stays low, the gap between hits
    // is the period, and the 18-bit offset is probably a phase within it. If
    // there is exactly one hit in 384 bits, the reply is a one-shot and the
    // offset is a real latency that has to be explained some other way.
    printf("\nA. long read - does the reply repeat, and with what period?\n");
    frame("0x9F + 3 addr, 48 bytes", CMD_READ_ID, 3, 48, 20);

    // --- B: is this a reply at all? ----------------------------------------
    //
    // The control this diagnostic has never had. Everything so far assumes the
    // bytes are U1 answering our command, and that assumption has survived only
    // because it was never tested. If 0D 5D still appears when the opcode is
    // 0x00 or 0xFF, nothing is parsing us and the pattern is coming from
    // somewhere else entirely - at which point every conclusion above is void.
    printf("\nB. opcode control - is 0D 5D actually a response to 0x9F?\n");
    frame("0x00 + 3 addr (should be dead)", 0x00, 3, 24, 20);
    frame("0xFF + 3 addr (should be dead)", 0xFF, 3, 24, 20);

    // --- C: where is the latency measured from? ----------------------------
    //
    // The device should answer at bit 8 + 8*n_addr, i.e. as soon as the address
    // phase ends. Vary the address length and watch where the ID lands:
    //   offset tracks n_addr   -> the part is answering our frame, just late,
    //                             and the late-by is a fixed dummy count;
    //   offset pinned to bit 50 -> the part answers at an absolute clock count
    //                             regardless of what we send, which means it is
    //                             counting clocks we are not issuing;
    //   offset moves some other way -> read it and see.
    // This is the measurement that distinguishes "device inserts dummy cycles"
    // from "device and host disagree about how many clocks have elapsed".
    printf("\nC. address-phase sweep - what is the offset measured from?\n");
    for (int na = 0; na <= 4; na++) {
        char label[40];
        snprintf(label, sizeof label, "0x9F + %d addr", na);
        frame(label, CMD_READ_ID, na, 24, 20);
    }

    // --- D: which lane is U1 actually driving? -----------------------------
    //
    // The hypothesis of last resort, and the one that would explain a stable
    // non-aligned offset when nothing else does: we may be reading the wrong
    // wire. If U1's SO lands somewhere other than the QMI's SD1, then what SD1
    // sees is capacitive crosstalk from the real data trace - a ghost that
    // carries the aggressor's transitions with skew, which is why it is
    // recognizable, corrupted, and *edge-rate* dependent rather than
    // frequency-dependent. That last property is exactly what made rev 2's
    // sweep come back invariant.
    //
    // Sending the command single-lane and then switching the read phase to quad
    // width captures all four lanes at once: each RX byte is two nibbles, one
    // clock each, and nibble bit k is lane k. Deinterleave and search each lane
    // separately. A clean 0D 5D on a lane that is not SD1 is the answer.
    printf("\nD. per-lane capture - which wire is U1 driving?\n");
    {
        const size_t NQ = 40;               // 40 quad bytes = 80 clocks of reply
        uint32_t tx[4 + 40];
        uint8_t  rx[4 + 40] = { 0 };

        tx[0] = TX_S(CMD_READ_ID);
        for (int i = 1; i < 4; i++) tx[i] = TX_S(0x00);
        for (size_t i = 0; i < NQ; i++) tx[4 + i] = TX_Q_IN(0xFF);

        printf("  %-30s ", "0x9F single, reply in quad");
        stdio_flush();
        xfer(tx, rx, 4 + NQ, 1, 20, 0);
        print_hex(rx, 4 + NQ);
        printf("\n");

        // Clock 0 of the quad phase is frame bit 32, so a lane's bit n here is
        // frame bit 32 + n. Reported that way so the offsets are comparable
        // with tests A-C.
        for (int lane = 0; lane < 4; lane++) {
            uint8_t bitbuf[(40 * 2 + 7) / 8] = { 0 };
            size_t nbits = NQ * 2;
            for (size_t i = 0; i < NQ; i++) {
                uint8_t nib[2] = { (uint8_t)(rx[4 + i] >> 4), (uint8_t)(rx[4 + i] & 0xF) };
                for (int h = 0; h < 2; h++) {
                    size_t c = i * 2 + (size_t)h;
                    if ((nib[h] >> lane) & 1u)
                        bitbuf[c >> 3] |= (uint8_t)(0x80u >> (c & 7));
                }
            }
            printf("    lane SD%d: ", lane);
            print_hex(bitbuf, (nbits + 7) / 8);
            printf("\n");
            int at = find_id(bitbuf, nbits, 0);
            if (at >= 0) {
                uint32_t eid = bits_at(bitbuf, (size_t)at + 16, 8);
                printf("      <== 0D 5D at frame bit %d, EID %02x -> %u MiB\n",
                       32 + at, (unsigned)eid, eid_to_mib(eid));
            }
        }
    }

    printf("\nRESULT : read the four blocks, not this line.\n");
    printf("  B is the one that can invalidate everything else: if 0D 5D shows\n");
    printf("  up under opcode 0x00, U1 is not what is talking and rev 1-3 are\n");
    printf("  all void. Assuming B is clean, C says where the 18 bits come from\n");
    printf("  and D says whether we are even reading the right wire.\n");

    while (true) tight_loop_contents();
}
