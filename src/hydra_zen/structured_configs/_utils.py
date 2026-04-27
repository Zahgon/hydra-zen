# Copyright (c) 2026 Massachusetts Institute of Technology
# SPDX-License-Identifier: MIT
import inspect
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import MISSING, field as _field, is_dataclass
from keyword import iskeyword
from typing import Any, Callable, Final, Optional, TypeVar, Union, cast, overload

from omegaconf import II, DictConfig, ListConfig
from typing_extensions import Literal, TypeGuard

from hydra_zen._compatibility import HYDRA_SUPPORTS_OBJECT_CONVERT
from hydra_zen.errors import HydraZenValidationError
from hydra_zen.typing import DataclassOptions, ZenConvert
from hydra_zen.typing._implementations import (
    DEFAULT_DATACLASS_OPTIONS,
    UNSUPPORTED_DATACLASS_OPTIONS,
    AllConvert,
    Field,
    InterpStr,
    StrictDataclassOptions,
    convert_types,
)

COMMON_MODULES_WITH_OBFUSCATED_IMPORTS: tuple[str, ...] = (
    "random",
    "numpy",
    "numpy.random",
    "jax.numpy",
    "jax",
    "torch",
)
UNKNOWN_NAME: Final[str] = "<unknown>"
KNOWN_MUTABLE_TYPES: set[
    Union[type[list[Any]], type[dict[Any, Any]], type[set[Any]]]
] = {list, dict, set}

T = TypeVar("T")


# The typeshed definition of `field` has an inaccurate annotation:
#  https://github.com/python/typeshed/blob/b9e1d7d522fe90b98e07d43a764bbe60216bc2c4/stdlib/dataclasses.pyi#L109
# This makes it impossible for `make_dataclass` to by type-correct in the eyes of
# static checkers. See https://github.com/microsoft/pyright/issues/1680 for discussion.
#
# We happen to make rather heavy use of `make_dataclass`, thus we..*sigh*.. we provide
# our own overloads for `field`.
@overload  # `default` and `default_factory` are optional and mutually exclusive.
def field(
    *,
    default: Any,
    init: bool = ...,
    repr: bool = ...,
    hash: Optional[bool] = ...,
    compare: bool = ...,
    metadata: Optional[Mapping[Any, Any]] = ...,
) -> Field[Any]:  # pragma: no cover
    ...


@overload
def field(
    *,
    default_factory: Callable[[], Any],
    init: bool = ...,
    repr: bool = ...,
    hash: Optional[bool] = ...,
    compare: bool = ...,
    metadata: Optional[Mapping[Any, Any]] = ...,
) -> Field[Any]:  # pragma: no cover
    ...


def field(
    *,
    default: Any = MISSING,
    default_factory: Union[Callable[[], Any], Any] = MISSING,
    init: bool = True,
    repr: bool = True,
    hash: Optional[bool] = None,
    compare: bool = True,
    metadata: Optional[Mapping[Any, Any]] = None,
) -> Field[Any]:
    if default is MISSING:
        return cast(
            Field[Any],
            _field(
                default_factory=default_factory,
                init=init,
                repr=repr,
                hash=hash,
                compare=compare,
                metadata=metadata,
            ),
        )
    else:
        return cast(
            Field[Any],
            _field(
                default=default,
                init=init,
                repr=repr,
                hash=hash,
                compare=compare,
                metadata=metadata,
            ),
        )


def safe_name(obj: Any, repr_allowed: bool = True) -> str:
    """Tries to get a descriptive name for an object. Returns '<unknown>`
    instead of raising - useful for writing descriptive/dafe error messages."""
    pass


def is_classmethod(obj: Any) -> bool:
    """
    https://stackoverflow.com/a/19228282/6592114

    Credit to: Martijn Pieters
    License: CC BY-SA 4.0 (free to copy/redistribute/remix/transform)"""
    pass


def building_error_prefix(target: Any) -> str:
    pass


NoneType = type(None)


def is_interpolated_string(x: Any) -> TypeGuard[InterpStr]:
    # This is only a necessary check â€“ not a sufficient one â€“ that `x`
    # is a valid interpolated string. We do not verify that it rigorously
    # satisfies omegaconf's grammar
    pass


def check_suspicious_interpolations(
    validated_wrappers: Sequence[Any], zen_meta: Mapping[str, Any], target: Any
):
    """Looks for patterns among zen_meta fields and interpolated fields in
    wrappers. Relative interpolations pointing to the wrong level will produce
    a warning"""
    pass


def valid_defaults_list(hydra_defaults: Any) -> bool:
    """
    Raises
    ------
    HydraZenValidationError: Duplicate _self_ entries"""
    pass


def merge_settings(
    user_settings: Optional[ZenConvert], default_settings: AllConvert
) -> AllConvert:
    """Merges settings as `default_settings.update(user_settings)`"""
    pass


_DATACLASS_OPTION_KEYS: frozenset[str] = (
    DataclassOptions.__required_keys__ | DataclassOptions.__optional_keys__
)

_STRICT_DATACLASS_OPTION_KEYS: frozenset[str] = (
    StrictDataclassOptions.__required_keys__ | StrictDataclassOptions.__optional_keys__
)
_STRICT_DATACLASS_OPTION_KEYS.copy()


def parse_dataclass_options(
    options: Mapping[str, Any], include_module: bool = True
) -> DataclassOptions:
    """
    Ensures `options` adheres to `DataclassOptions` and merges hydra-zen defaults
    for missing options.

    All valid `@dataclass`/`make_dataclass` options are supported, even for features
    introduced in later versions of Python. This function will remove valid options
    that are not supported for by the current Python version.

    Parameters
    ----------
    options : Mapping[str, Any]
        User-specified options for `zen_dataclass` to be validated.

    Returns
    -------
    DataclassOptions

    Examples
    --------
    >>> parse_dataclass_options({})
    {'unsafe_hash': True}

    >>> parse_dataclass_options({"unsafe_hash": False, "cls_name": "Foo"})
    {'unsafe_hash': False, 'cls_name': 'Foo'}

    >>> parse_dataclass_options({"moo": 1})
    ValueError: moo is not a valid dataclass option.

    Options that are supported by `make_dataclass` for later versions of
    Python are ignored/removed automatically by this function. E.g. the following
    Python 3.10+ option has the following behavior in Python 3.9:

    >>> parse_dataclass_options({"slots": False})
    {'unsafe_hash': True}
    """
    pass


def parse_strict_dataclass_options(
    options: Mapping[str, Any],
) -> TypeGuard[StrictDataclassOptions]:
    pass


_HYDRA_CONVERT_OPTIONS = (
    {"none", "partial", "all", "object"}
    if HYDRA_SUPPORTS_OBJECT_CONVERT
    else {"none", "partial", "all"}
)


def validate_hydra_options(
    hydra_recursive: Optional[bool] = None,
    hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = None,
) -> None:
    pass
