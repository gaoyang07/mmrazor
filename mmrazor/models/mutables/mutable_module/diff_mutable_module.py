# Copyright (c) OpenMMLab. All rights reserved.
from abc import abstractmethod
from functools import partial
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from mmrazor.registry import MODELS
from ..base_mutable import CHOICE_TYPE, CHOSEN_TYPE
from .mutable_module import MutableModule

PartialType = Callable[[Any, Optional[nn.Parameter]], Any]


class DiffMutableModule(MutableModule[CHOICE_TYPE, CHOSEN_TYPE]):
    """Base class for differentiable mutables.

    Args:
        module_kwargs (dict[str, dict], optional): Module initialization named
            arguments. Defaults to None.
        alias (str, optional): alias of the `MUTABLE`.
        init_cfg (dict, optional): initialization configuration dict for
            ``BaseModule``. OpenMMLab has implement 5 initializer including
            `Constant`, `Xavier`, `Normal`, `Uniform`, `Kaiming`,
            and `Pretrained`.

    Note:
        :meth:`forward_all` is called when calculating FLOPs.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def forward(self,
                x: Any,
                arch_param: Optional[nn.Parameter] = None) -> Any:
        """Calls either :func:`forward_fixed` or :func:`forward_arch_param`
        depending on whether :func:`is_fixed` is ``True`` and whether
        :func:`arch_param` is None.

        To reduce the coupling between `Mutable` and `Mutator`, the
        `arch_param` is generated by the `Mutator` and is passed to the
        forward function as an argument.

        Note:
            :meth:`forward_fixed` is called when in `fixed` mode.
            :meth:`forward_arch_param` is called when in `unfixed` mode.

        Args:
            x (Any): input data for forward computation.
            arch_param (nn.Parameter, optional): the architecture parameters
                for ``DiffMutableModule``.

        Returns:
            Any: the result of forward
        """
        if self.is_fixed:
            return self.forward_fixed(x)
        else:
            return self.forward_arch_param(x, arch_param=arch_param)

    def compute_arch_probs(self, arch_param: nn.Parameter) -> Tensor:
        """compute chosen probs according to architecture params."""
        return F.softmax(arch_param, -1)

    @abstractmethod
    def forward_fixed(self, x: Any) -> Any:
        """Forward when the mutable is fixed.

        All subclasses must implement this method.
        """

    @abstractmethod
    def forward_all(self, x: Any) -> Any:
        """Forward all choices."""

    @abstractmethod
    def forward_arch_param(self,
                           x: Any,
                           arch_param: Optional[nn.Parameter] = None) -> Any:
        """Forward when the mutable is not fixed.

        All subclasses must implement this method.
        """

    def set_forward_args(self, arch_param: nn.Parameter) -> None:
        """Interface for modifying the arch_param using partial."""
        forward_with_default_args: PartialType = \
            partial(self.forward, arch_param=arch_param)
        setattr(self, 'forward', forward_with_default_args)


@MODELS.register_module()
class DiffMutableOP(DiffMutableModule[str, str]):
    """A type of ``MUTABLES`` for differentiable architecture search, such as
    DARTS. Search the best module by learnable parameters `arch_param`.

    Args:
        candidates (dict[str, dict]): the configs for the candidate
            operations.
        module_kwargs (dict[str, dict], optional): Module initialization named
            arguments. Defaults to None.
        alias (str, optional): alias of the `MUTABLE`.
        init_cfg (dict, optional): initialization configuration dict for
            ``BaseModule``. OpenMMLab has implement 5 initializer including
            `Constant`, `Xavier`, `Normal`, `Uniform`, `Kaiming`,
            and `Pretrained`.
    """

    def __init__(
        self,
        candidates: Dict[str, Dict],
        module_kwargs: Optional[Dict[str, Dict]] = None,
        alias: Optional[str] = None,
        init_cfg: Optional[Dict] = None,
    ) -> None:
        super().__init__(
            module_kwargs=module_kwargs, alias=alias, init_cfg=init_cfg)
        assert len(candidates) >= 1, \
            f'Number of candidate op must greater than or equal to 1, ' \
            f'but got: {len(candidates)}'

        self._is_fixed = False
        self._candidates = self._build_ops(candidates, self.module_kwargs)

    @staticmethod
    def _build_ops(candidates: Dict[str, Dict],
                   module_kwargs: Optional[Dict[str, Dict]]) -> nn.ModuleDict:
        """Build candidate operations based on candidates configures.

        Args:
            candidates (dict[str, dict]): the configs for the candidate
                operations.
            module_kwargs (dict[str, dict], optional): Module initialization
                named arguments.

        Returns:
            ModuleDict (dict[str, Any], optional):  the key of ``ops`` is
                the name of each choice in configs and the value of ``ops``
                is the corresponding candidate operation.
        """
        ops = nn.ModuleDict()
        for name, op_cfg in candidates.items():
            assert name not in ops
            if module_kwargs is not None:
                op_cfg.update(module_kwargs)
            ops[name] = MODELS.build(op_cfg)
        return ops

    def forward_fixed(self, x: Any) -> Tensor:
        """Forward when the mutable is in `fixed` mode.

        Args:
            x (Any): x could be a Torch.tensor or a tuple of
                Torch.tensor, containing input data for forward computation.

        Returns:
            Tensor: the result of forward the fixed operation.
        """
        return sum(self._candidates[choice](x) for choice in self._chosen)

    def forward_arch_param(self,
                           x: Any,
                           arch_param: Optional[nn.Parameter] = None
                           ) -> Tensor:
        """Forward with architecture parameters.

        Args:
            x (Any): x could be a Torch.tensor or a tuple of
                Torch.tensor, containing input data for forward computation.
            arch_param (str, optional): architecture parameters for
                `DiffMutableModule`


        Returns:
            Tensor: the result of forward with ``arch_param``.
        """
        if arch_param is None:
            return self.forward_all(x)
        else:
            # compute the probs of choice
            probs = self.compute_arch_probs(arch_param=arch_param)

            # forward based on probs
            outputs = list()
            for prob, module in zip(probs, self._candidates.values()):
                if prob > 0.:
                    outputs.append(prob * module(x))

            return sum(outputs)

    def forward_all(self, x: Any) -> Tensor:
        """Forward all choices. Used to calculate FLOPs.

        Args:
            x (Any): x could be a Torch.tensor or a tuple of
                Torch.tensor, containing input data for forward computation.

        Returns:
            Tensor: the result of forward all of the ``choice`` operation.
        """
        outputs = list()
        for op in self._candidates.values():
            outputs.append(op(x))
        return sum(outputs)

    def fix_chosen(self, chosen: Union[str, List[str]]) -> None:
        """Fix mutable with `choice`. This operation would convert `unfixed`
        mode to `fixed` mode. The :attr:`is_fixed` will be set to True and only
        the selected operations can be retained.

        Args:
            chosen (str): the chosen key in ``MUTABLE``.
                Defaults to None.
        """
        if self.is_fixed:
            raise AttributeError(
                'The mode of current MUTABLE is `fixed`. '
                'Please do not call `fix_chosen` function again.')

        if isinstance(chosen, str):
            chosen = [chosen]

        for c in self.choices:
            if c not in chosen:
                self._candidates.pop(c)

        self._chosen = chosen
        self.is_fixed = True

    def sample_choice(self, arch_param):
        """Sample choice based on arch_parameters."""
        return self.choices[torch.argmax(arch_param).item()]

    def dump_chosen(self):
        """Dump current choice."""
        assert self.current_choice is not None
        return self.current_choice

    @property
    def choices(self) -> List[str]:
        """list: all choices. """
        return list(self._candidates.keys())


@MODELS.register_module()
class OneHotMutableOP(DiffMutableOP):
    """A type of ``MUTABLES`` for one-hot sample based architecture search,
    such as DSNAS. Search the best module by learnable parameters `arch_param`.

    Args:
        candidates (dict[str, dict]): the configs for the candidate
            operations.
        module_kwargs (dict[str, dict], optional): Module initialization named
            arguments. Defaults to None.
        alias (str, optional): alias of the `MUTABLE`.
        init_cfg (dict, optional): initialization configuration dict for
            ``BaseModule``. OpenMMLab has implement 5 initializer including
            `Constant`, `Xavier`, `Normal`, `Uniform`, `Kaiming`,
            and `Pretrained`.
    """

    def sample_weights(self,
                       arch_param: nn.Parameter,
                       probs: torch.Tensor,
                       random_sample: bool = False) -> Tensor:
        """Use one-hot distributions to sample the arch weights based on the
        arch params.

        Args:
            arch_param (nn.Parameter): architecture parameters for
                `DiffMutableModule`.
            probs (Tensor): the probs of choice.
            random_sample (bool): Whether to random sample arch weights or not
                Defaults to False.

        Returns:
            Tensor: Sampled one-hot arch weights.
        """
        import torch.distributions as D
        if random_sample:
            uni = torch.ones_like(arch_param)
            m = D.one_hot_categorical.OneHotCategorical(uni)
        else:
            m = D.one_hot_categorical.OneHotCategorical(probs=probs)
        return m.sample()

    def forward_arch_param(self,
                           x: Any,
                           arch_param: Optional[nn.Parameter] = None
                           ) -> Tensor:
        """Forward with architecture parameters.

        Args:
            x (Any): x could be a Torch.tensor or a tuple of
                Torch.tensor, containing input data for forward computation.
            arch_param (str, optional): architecture parameters for
                `DiffMutableModule`.

        Returns:
            Tensor: the result of forward with ``arch_param``.
        """
        if arch_param is None:
            return self.forward_all(x)
        else:
            # compute the probs of choice
            probs = self.compute_arch_probs(arch_param=arch_param)
            self.arch_weights = self.sample_weights(arch_param, probs)
            self.arch_weights.requires_grad_()

            # forward based on self.arch_weights
            outputs = list()
            for prob, module in zip(self.arch_weights,
                                    self._candidates.values()):
                if prob > 0.:
                    outputs.append(prob * module(x))

            return sum(outputs)


@MODELS.register_module()
class DiffChoiceRoute(DiffMutableModule[str, List[str]]):
    """A type of ``MUTABLES`` for Neural Architecture Search, which can select
    inputs from different edges in a differentiable or non-differentiable way.
    It is commonly used in DARTS.

    Args:
        edges (nn.ModuleDict): the key of `edges` is the name of different
            edges. The value of `edges` can be :class:`nn.Module` or
            :class:`DiffMutableModule`.
        with_arch_param (bool): whether forward with arch_param. When set to
            `True`, a differentiable way is adopted. When set to `False`,
            a non-differentiable way is adopted.
        alias (str, optional): alias of the `DiffChoiceRoute`.
        init_cfg (dict, optional): initialization configuration dict for
            ``BaseModule``. OpenMMLab has implement 6 initializers including
            `Constant`, `Xavier`, `Normal`, `Uniform`, `Kaiming`,
            and `Pretrained`.

    Examples:
        >>> import torch
        >>> import torch.nn as nn
        >>> edges_dict=nn.ModuleDict()
        >>> edges_dict.add_module('first_edge', nn.Conv2d(32, 32, 3, 1, 1))
        >>> edges_dict.add_module('second_edge', nn.Conv2d(32, 32, 5, 1, 2))
        >>> edges_dict.add_module('third_edge', nn.MaxPool2d(3, 1, 1))
        >>> edges_dict.add_module('fourth_edge', nn.MaxPool2d(5, 1, 2))
        >>> edges_dict.add_module('fifth_edge', nn.MaxPool2d(7, 1, 3))
        >>> diff_choice_route_cfg = dict(
        ...     type="DiffChoiceRoute",
        ...     edges=edges_dict,
        ...     with_arch_param=True,
        ... )
        >>> arch_param
        Parameter containing:
        tensor([-6.1426e-04,  2.3596e-04,  1.4427e-03,  7.1668e-05,
            -8.9739e-04], requires_grad=True)
        >>> x = [torch.randn(4, 32, 64, 64) for _ in range(5)]
        >>> output=diffchoiceroute.forward_arch_param(x, arch_param)
        >>> output.shape
        torch.Size([4, 32, 64, 64])
    """

    def __init__(
        self,
        edges: nn.ModuleDict,
        num_chsoen: int = 2,
        with_arch_param: bool = False,
        alias: Optional[str] = None,
        init_cfg: Optional[Dict] = None,
    ) -> None:
        super().__init__(alias=alias, init_cfg=init_cfg)
        assert len(edges) >= 1, \
            f'Number of edges must greater than or equal to 1, ' \
            f'but got: {len(edges)}'

        self._with_arch_param = with_arch_param
        self._is_fixed = False
        self._candidates: nn.ModuleDict = edges
        self.num_chosen = num_chsoen

    def forward_fixed(self, inputs: Union[List, Tuple]) -> Tensor:
        """Forward when the mutable is in `fixed` mode.

        Args:
            inputs (Union[List[Any], Tuple[Any]]): inputs could be a list or
                a tuple of Torch.tensor, containing input data for
                forward computation.

        Returns:
            Tensor: the result of forward the fixed operation.
        """
        assert self._chosen is not None, \
            'Please call fix_chosen before calling `forward_fixed`.'

        outputs = list()
        for choice, x in zip(self._unfixed_choices, inputs):
            if choice in self._chosen:
                outputs.append(self._candidates[choice](x))
        return sum(outputs)

    def forward_arch_param(
            self,
            x: Union[List[Any], Tuple[Any]],
            arch_param: Optional[nn.Parameter] = None) -> Tensor:
        """Forward with architecture parameters.

        Args:
            x (list[Any] | tuple[Any]]): x could be a list or a tuple
                of Torch.tensor, containing input data for forward selection.
            arch_param (nn.Parameter): architecture parameters for
                for ``DiffMutableModule``.

        Returns:
            Tensor: the result of forward with ``arch_param``.
        """
        assert len(x) == len(self._candidates), \
            f'Length of `edges` {len(self._candidates)} should be ' \
            f'same as the length of inputs {len(x)}.'

        if self._with_arch_param:
            probs = self.compute_arch_probs(arch_param=arch_param)

            outputs = list()
            for prob, module, input in zip(probs, self._candidates.values(),
                                           x):
                if prob > 0:
                    # prob may equal to 0 in gumbel softmax.
                    outputs.append(prob * module(input))

            return sum(outputs)
        else:
            return self.forward_all(x)

    def forward_all(self, x: Any) -> Tensor:
        """Forward all choices.

        Args:
            x (Any): x could be a Torch.tensor or a tuple of
                Torch.tensor, containing input data for forward computation.

        Returns:
            Tensor: the result of forward all of the ``choice`` operation.
        """
        assert len(x) == len(self._candidates), \
            f'Lenght of edges {len(self._candidates)} should be same as ' \
            f'the length of inputs {len(x)}.'

        outputs = list()
        for op, input in zip(self._candidates.values(), x):
            outputs.append(op(input))

        return sum(outputs)

    def fix_chosen(self, chosen: List[str]) -> None:
        """Fix mutable with `choice`. This operation would convert to `fixed`
        mode. The :attr:`is_fixed` will be set to True and only the selected
        operations can be retained.

        Args:
            chosen (list(str)): the chosen key in ``MUTABLE``.
        """
        self._unfixed_choices = self.choices

        if self.is_fixed:
            raise AttributeError(
                'The mode of current MUTABLE is `fixed`. '
                'Please do not call `fix_chosen` function again.')

        for c in self.choices:
            if c not in chosen:
                self._candidates.pop(c)

        self._chosen = chosen
        self.is_fixed = True

    @property
    def choices(self) -> List[CHOSEN_TYPE]:
        """list: all choices. """
        return list(self._candidates.keys())

    def dump_chosen(self):
        """dump current choice."""
        assert self.current_choice is not None
        return self.current_choice

    def sample_choice(self, arch_param):
        """sample choice based on `arch_param`."""
        sort_idx = torch.argsort(-arch_param).cpu().numpy().tolist()
        choice_idx = sort_idx[:self.num_chosen]
        choice = [self.choices[i] for i in choice_idx]
        return choice


@MODELS.register_module()
class GumbelChoiceRoute(DiffChoiceRoute):
    """A type of ``MUTABLES`` for Neural Architecture Search using Gumbel-Max
    trick, which can select inputs from different edges in a differentiable or
    non-differentiable way. It is commonly used in DARTS.

    Args:
        edges (nn.ModuleDict): the key of `edges` is the name of different
            edges. The value of `edges` can be :class:`nn.Module` or
            :class:`DiffMutableModule`.
        tau (float): non-negative scalar temperature in gumbel softmax.
        hard (bool): if `True`, the returned samples will be discretized as
            one-hot vectors, but will be differentiated as if it is the soft
            sample in autograd. Defaults to `True`.
        with_arch_param (bool): whether forward with arch_param. When set to
            `True`, a differentiable way is adopted. When set to `False`,
            a non-differentiable way is adopted.
        init_cfg (dict, optional): initialization configuration dict for
            ``BaseModule``. OpenMMLab has implement 6 initializers including
            `Constant`, `Xavier`, `Normal`, `Uniform`, `Kaiming`,
            and `Pretrained`.
    """

    def __init__(
        self,
        edges: nn.ModuleDict,
        tau: float = 1.0,
        hard: bool = True,
        with_arch_param: bool = False,
        alias: Optional[str] = None,
        init_cfg: Optional[Dict] = None,
    ) -> None:
        super().__init__(
            edges=edges,
            with_arch_param=with_arch_param,
            alias=alias,
            init_cfg=init_cfg)
        self.tau = tau
        self.hard = hard

    def compute_arch_probs(self, arch_param: nn.Parameter) -> Tensor:
        """Compute chosen probs by Gumbel-Max trick."""
        return F.gumbel_softmax(
            arch_param, tau=self.tau, hard=self.hard, dim=-1)

    def set_temperature(self, tau: float) -> None:
        """Set temperature of gumbel softmax."""
        self.tau = tau
