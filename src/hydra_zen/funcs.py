# Copyright (c) 2026 Massachusetts Institute of Technology
# SPDX-License-Identifier: MIT

"""
Simple helper functions used to implement `just` and `builds`. This module is designed specifically so
that these functions have a legible module-path when they appear in configuration files.
"""

import functools as _functools
import typing as _tp
from functools import partial
from typing import Any, Callable, TypeVar

from hydra._internal import utils as _hydra_internal_utils
from hydra.utils import log as _log
from typing_extensions import TypeAlias

from hydra_zen.structured_configs._utils import (
    is_interpolated_string as _is_interpolated_string,
)
from hydra_zen.typing import Partial as _Partial

__all__ = ["partial", "get_obj", "zen_processing"]

_T = _tp.TypeVar("_T")

_Wrapper = _tp.Callable[[_tp.Callable[..., _tp.Any]], _tp.Callable[..., _tp.Any]]
_WrapperConf = _tp.Union[str, _Wrapper]


T = TypeVar("T")
F = TypeVar("F", bound=Callable[..., Any])

Wrappers: TypeAlias = tuple[Callable[[Callable[..., Any]], Callable[..., Any]], ...]


class partial_with_wrapper(partial[T]):
    """`partial` where a wrapper's application is deferred until the call."""

    __slots__ = "wrappers"
    wrappers: Wrappers

    def __new__(
        cls,
        wrappers: Wrappers,
        func: Callable[..., T],
        *args: Any,
        **keywords: Any,
    ) -> "partial_with_wrapper[T]":
        if isinstance(func, partial_with_wrapper):
            # unwrap the partial_with_wrapper
            wrappers = func.wrappers + wrappers
            keywords = {**func.keywords, **keywords}
            args = func.args + args
            func = func.func

        self = super().__new__(cls, func, *args, **keywords)
        self.wrappers = wrappers
        return self

    def __call__(self, /, *args: Any, **keywords: Any) -> T:
        keywords = {**self.keywords, **keywords}
        func = self.func
        for wrapper in self.wrappers:
            func = wrapper(func)

        return func(*self.args, *args, **keywords)

    def __reduce__(self) -> _tp.Any:
        return (
            type(self),
            (
                self.wrappers,
                self.func,
            ),
            (
                self.wrappers,
                self.func,
                self.args,
                self.keywords or None,
                self.__dict__ or None,
            ),
        )

    def __setstate__(
        self,
        state: tuple[
            tuple[Callable[..., Any], ...],
            Callable[..., Any],
            tuple[Any, ...],
            dict[str, Any],
            dict[str, Any],
        ],
    ) -> None:
        if not isinstance(state, tuple):  # pragma: no cover
            raise TypeError("argument to __setstate__ must be a tuple")
        if len(state) != 5:  # pragma: no cover
            raise TypeError(f"expected 4 items in state, got {len(state)}")

        self.wrappers, *substate = state
        substate = tuple(substate)
        super().__setstate__(substate)  # type: ignore


def partial(
    *args: _tp.Any,
    _partial_target_: _tp.Callable[..., _T],
    **kwargs: _tp.Any,
) -> _Partial[_T]:
    """Equivalent to ``functools.partial`` but provides a named parameter for the callable."""
    return _tp.cast(_Partial[_T], _functools.partial(_partial_target_, *args, **kwargs))


def get_obj(*, path: str) -> _tp.Union[type, _tp.Callable[..., _tp.Any]]:
    """Imports an object given the specified path."""
    pass


def zen_processing(
    *args: _tp.Any,
    _zen_target: str,
    _zen_partial: bool = False,
    _zen_exclude: _tp.Sequence[str] = tuple(),
    _zen_wrappers: _tp.Union[_WrapperConf, _tp.Sequence[_WrapperConf]] = tuple(),
    _zen_target_wrapper: _tp.Union[None, _Wrapper] = None,
    **kwargs: _tp.Any,
) -> _tp.Any:
    pass


def as_default_dict(
    dict_: _tp.Dict[_tp.Any, _tp.Any], *, default_factory: _tp.Any
) -> _tp.DefaultDict[_tp.Any, _tp.Any]:
    pass
