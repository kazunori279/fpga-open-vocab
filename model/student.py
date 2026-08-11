# /// script
# requires-python = ">=3.10"
# dependencies = ["torch", "numpy"]
# ///
"""The student: a ~1.4M-param CNN mapping 128x128x3 into the teacher's space.

    uv run model/student.py            # print the params/MACs budget table

Two constraints shape this network, and both come from downstream milestones
rather than from accuracy:

**Dense 3x3 convolutions, deliberately not depthwise separable.** Depthwise is
the standard way to hit a small parameter count, and it is exactly wrong here.
M6 maps convolutions onto the T8's GEMM tile via im2col, and depthwise convs
have terrible arithmetic intensity - the worst possible shape for an accelerator
fed over a 26.8 MB/s link with a 1-bit return path. A student that passes M4 but
cannot be built on the FPGA is worth nothing.

**ReLU, no conv bias, BatchNorm only during training.** ReLU is a comparison in
RTL and survives int8 well. BN folds into the preceding conv's weights and a
per-channel bias at export (see quantize.py), so it costs nothing at inference.

The 512-d output is *not* L2-normalized here. Normalization is the MCU's job in
float, after the last layer, so the exported int8 graph stops at the linear.
"""

import sys

import torch
from torch import nn

INPUT_SIZE = 128
EMBED_DIM = 512

# (out_channels, stride). Counted out to land near 1.4M params / 160 MMACs -
# under both budgets, leaving headroom to widen if the M4 gate comes in marginal.
STAGES = [
    (32, 2),   # 128 -> 64
    (64, 2),   # 64 -> 32
    (64, 1),
    (128, 2),  # 32 -> 16
    (128, 1),
    (192, 2),  # 16 -> 8
    (192, 1),
    (256, 2),  # 8 -> 4
]

PARAM_BUDGET = 1_500_000   # int8 bytes, must fit beside activations in 2 MB PSRAM
MAC_BUDGET = 250_000_000   # ~70 ms at the T8's estimated 3-4 GMAC/s


class ConvBNReLU(nn.Sequential):
    def __init__(self, cin: int, cout: int, stride: int):
        super().__init__(
            nn.Conv2d(cin, cout, 3, stride=stride, padding=1, bias=False),
            nn.BatchNorm2d(cout),
            nn.ReLU(inplace=True),
        )


class Student(nn.Module):
    def __init__(self, stages=STAGES, embed_dim: int = EMBED_DIM, width: float = 1.0):
        super().__init__()
        cin = 3
        blocks = []
        for cout, stride in stages:
            cout = max(8, int(round(cout * width / 8)) * 8)
            blocks.append(ConvBNReLU(cin, cout, stride))
            cin = cout
        self.features = nn.Sequential(*blocks)
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.head = nn.Linear(cin, embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.features(x)
        x = self.pool(x).flatten(1)
        return self.head(x)


def budget(model: nn.Module, input_size: int = INPUT_SIZE) -> tuple[list[tuple], int, int]:
    """Count parameters and multiply-accumulates per forward pass.

    Output shapes are captured with hooks rather than recomputed from strides,
    so the count follows whatever the module tree actually does - including any
    padding or rounding a hand-derived formula would get subtly wrong.
    """
    rows: list[tuple] = []

    def hook(module, _inputs, output):
        if isinstance(module, nn.Conv2d):
            oh, ow = output.shape[-2:]
            macs = oh * ow * module.out_channels * module.in_channels * module.kernel_size[0] * module.kernel_size[1]
            shape = f"{oh}x{ow}x{module.out_channels}"
        elif isinstance(module, nn.Linear):
            macs = module.in_features * module.out_features
            shape = f"{module.out_features}"
        else:
            return
        params = sum(p.numel() for p in module.parameters())
        rows.append((module.__class__.__name__, shape, params, macs))

    handles = [m.register_forward_hook(hook) for m in model.modules()]
    was_training = model.training
    model.eval()
    # Follow the model rather than assuming CPU - callers count the budget after
    # moving to MPS.
    device = next(model.parameters()).device
    with torch.no_grad():
        model(torch.zeros(1, 3, input_size, input_size, device=device))
    model.train(was_training)
    for h in handles:
        h.remove()

    # BatchNorm parameters fold away at export, but they are real during
    # training, so the total counts every parameter in the module tree and the
    # per-row conv/linear counts are what survives quantization.
    total_params = sum(p.numel() for p in model.parameters())
    total_macs = sum(r[3] for r in rows)
    return rows, total_params, total_macs


def report(model: nn.Module, input_size: int = INPUT_SIZE) -> bool:
    rows, params, macs = budget(model, input_size)
    print(f"{'layer':<10} {'output':<14} {'params':>10} {'MACs':>12}")
    for kind, shape, p, m in rows:
        print(f"{kind:<10} {shape:<14} {p:>10,} {m:>12,}")
    print(f"{'total':<10} {'':<14} {params:>10,} {macs:>12,}")
    print()
    print(f"int8 weights : {params / 2**20:.2f} MiB of the 2 MB PSRAM")
    print(f"params       : {params:,} / {PARAM_BUDGET:,} budget")
    print(f"MACs         : {macs / 1e6:.0f}M / {MAC_BUDGET / 1e6:.0f}M budget")
    return params <= PARAM_BUDGET and macs <= MAC_BUDGET


def main() -> int:
    model = Student()
    ok = report(model)
    print("\nRESULT : " + ("PASS - within budget" if ok else "FAIL - over budget"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
