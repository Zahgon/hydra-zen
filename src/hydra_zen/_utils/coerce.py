# Copyright (c) 2026 Massachusetts Institute of Technology
# SPDX-License-Identifier: MIT

import inspect
from collections import deque
from collections.abc import Sequence
from functools import wraps
from typing import (
    Callable,
    NamedTuple,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from omegaconf import ListConfig
from typing_extensions import TypeGuard

_T = TypeVar("_T", bound=Callable)

__all__ = ["coerce_sequences"]


def _is_namedtuple_type(x) -> TypeGuard[type[NamedTuple]]:  # pragma: no cover
    pass


_NoneType = type(None)

_POSITIONAL_ONLY = inspect.Parameter.POSITIONAL_ONLY
_VAR_POSITIONAL = inspect.Parameter.VAR_POSITIONAL
_KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY
_VAR_KEYWORD = inspect.Parameter.VAR_KEYWORD


def coerce_sequences(obj: _T) -> _T:
    """
    Hydra is only able to read non-string sequences as lists (or ListConfig).
    This wrapper will cast these lists to their desired type, based
    on the annotated-type associated with that sequence.

    This is a no-op for objects that don't have any annotated non-string
    sequence types.

    This is strictly an experimental utility. Use at your own risk.

    Parameters
    ----------
    obj : Callable

    Returns
    -------
    wrapped_obj

    Notes
    -----
    The supported non-string sequence types are:
    - tuples
    - dequeues
    - lists
    - named-tuples

    The only Union-based types that are supported are of the form
    ``Optional[<sequence-type>]``.

    Examples
    --------
    >>> from hydra_zen import builds, instantiate
    >>> def f(x: tuple): return x

    Without wrapping:

    >>> conf_no_wrap = builds(f, x=(1, 2))
    >>> instantiate(conf_no_wrap)
    [1, 2]

    With wrapping:

    >>> conf_wrapped = builds(f, x=(1, 2), zen_wrappers=convert_sequences)
    >>> instantiate(conf_wrapped)
    (1, 2)
    """
    pass
