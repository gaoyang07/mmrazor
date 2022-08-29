# Copyright (c) OpenMMLab. All rights reserved.
from .base import BaseAlgorithm
from .distill import (DAFLDataFreeDistillation, DataFreeDistillation,
                      FpnTeacherDistill, OverhaulFeatureDistillation,
                      SelfDistill, SingleTeacherDistill)
from .nas import SPOS, AutoSlim, AutoSlimDDP, Darts, DartsDDP, Dsnas, DsnasDDP
from .pruning import SlimmableNetwork, SlimmableNetworkDDP

__all__ = [
    'SingleTeacherDistill', 'BaseAlgorithm', 'FpnTeacherDistill', 'SPOS',
    'SlimmableNetwork', 'SlimmableNetworkDDP', 'AutoSlim', 'AutoSlimDDP',
    'Darts', 'DartsDDP', 'SelfDistill', 'DataFreeDistillation',
    'DAFLDataFreeDistillation', 'OverhaulFeatureDistillation',
    'Dsnas', 'DsnasDDP'
]
