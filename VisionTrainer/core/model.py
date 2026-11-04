import torch
from torch import nn
from torch.nn import functional as F
from torchvision.models import mobilenet_v2

LABELS = ["1000_front_up", "1000_front_down", "1001_front", "back"]
WIDTH, HEIGHT, STRIDE = 512, 288, 4


class ResistorNet(nn.Module):
    def __init__(self, states=len(LABELS)):
        super().__init__()
        self.backbone = mobilenet_v2(weights=None).features[:14]
        self.head = nn.Sequential(
            nn.Conv2d(152, 48, 1),
            nn.ReLU(),
            nn.Conv2d(48, 48, 5, padding=2, groups=48),
            nn.ReLU(),
            nn.Conv2d(48, 64, 1),
            nn.ReLU(),
            nn.Conv2d(64, 64, 3, padding=1, groups=64),
            nn.ReLU(),
            nn.Conv2d(64, 5 + states, 1),
        )
        with torch.no_grad():
            self.head[-1].bias.zero_()
            self.head[-1].bias[0] = -3
            self.head[-1].bias[1:3] = -2

    def encode(self, x):
        fine = mid = None
        for i, layer in enumerate(self.backbone):
            x = layer(x)
            if i == 3:
                fine = x
            if i == 6:
                mid = x
        return torch.cat(
            [
                fine,
                F.interpolate(mid, scale_factor=2, mode="nearest"),
                F.interpolate(x, scale_factor=4, mode="nearest"),
            ],
            1,
        )

    def forward(self, x):
        z = self.head(self.encode(x))
        return z[:, :5].sigmoid(), z[:, 5:]
