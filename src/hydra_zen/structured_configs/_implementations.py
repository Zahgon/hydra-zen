# Copyright (c) 2026 Massachusetts Institute of Technology
# SPDX-License-Identifier: MIT
import functools
import inspect
import pathlib
import sys
import warnings
from collections import Counter, defaultdict, deque
from collections.abc import Collection, Mapping, Sequence
from dataclasses import (  # use this for runtime checks
    MISSING,
    Field as _Field,
    InitVar,
    dataclass,
    field,
    fields,
    is_dataclass,
    make_dataclass,
)
from datetime import timedelta
from enum import Enum
from functools import partial
from itertools import chain
from pathlib import Path, PosixPath, WindowsPath
from typing import (
    Annotated,
    Any,
    Callable,
    Dict,
    Final,
    Generic,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from omegaconf import DictConfig, ListConfig, _utils as _omegaconf_utils
from typing_extensions import (
    Concatenate,
    Literal,
    ParamSpec,
    ParamSpecArgs,
    ParamSpecKwargs,
    Protocol,
    Self,
    TypeAlias,
    Unpack,
    _AnnotatedAlias,
    dataclass_transform,
)

from hydra_zen._compatibility import (
    HYDRA_SUPPORTED_PRIMITIVE_TYPES,
    HYDRA_SUPPORTED_PRIMITIVES,
    ZEN_SUPPORTED_PRIMITIVES,
)
from hydra_zen.errors import (
    HydraZenDeprecationWarning,
    HydraZenUnsupportedPrimitiveError,
    HydraZenValidationError,
)
from hydra_zen.funcs import as_default_dict, get_obj
from hydra_zen.structured_configs import _utils
from hydra_zen.structured_configs._type_guards import safe_getattr
from hydra_zen.typing import (
    Builds,
    DataclassOptions,
    PartialBuilds,
    SupportedPrimitive,
    ZenConvert,
    ZenWrappers,
)
from hydra_zen.typing._implementations import (
    AllConvert,
    AnyBuilds,
    Builds,
    BuildsWithSig,
    DataClass,
    DataClass_,
    DataclassOptions,
    DefaultsList,
    Field,
    HasTarget,
    HasTargetInst,
    HydraSupportedType,
    InstOrType,
    Just as JustT,
    Partial,
    ZenConvert,
    _HydraPrimitive,
    _SupportedViaBuilds,
)

from ._globals import (
    CONVERT_FIELD_NAME,
    DEFAULTS_LIST_FIELD_NAME,
    HYDRA_FIELD_NAMES,
    JUST_FIELD_NAME,
    META_FIELD_NAME,
    PARTIAL_FIELD_NAME,
    POS_ARG_FIELD_NAME,
    RECURSIVE_FIELD_NAME,
    TARGET_FIELD_NAME,
    ZEN_PARTIAL_FIELD_NAME,
    ZEN_PROCESSING_LOCATION,
    ZEN_TARGET_FIELD_NAME,
    ZEN_WRAPPERS_FIELD_NAME,
)
from ._type_guards import (
    is_builds,
    is_generic_type,
    is_just,
    is_old_partial_builds,
    safe_getattr,
    uses_zen_processing,
)
from ._utils import merge_settings

T = TypeVar("T")
_T = TypeVar("_T")
P = ParamSpec("P")
R = TypeVar("R")

TD = TypeVar("TD", bound=DataClass_)
TC = TypeVar("TC", bound=Callable[..., Any])
TP = TypeVar("TP", bound=_HydraPrimitive)
TB = TypeVar("TB", bound=Union[_SupportedViaBuilds, frozenset[Any]])

Importable = TypeVar("Importable", bound=Callable[..., Any])
Field_Entry: TypeAlias = tuple[str, type, Field[Any]]

_JUST_CONVERT_SETTINGS = AllConvert(dataclass=True, flat_target=False)

# default zen_convert settings for `builds` and `hydrated_dataclass`
_BUILDS_CONVERT_SETTINGS = AllConvert(dataclass=True, flat_target=True)


# stores type -> value-conversion-fn
# for types with specialized support from hydra-zen
class _ConversionFn(Protocol):
    def __call__(self, __x: Any, CBuildsFn: "Type[BuildsFn[Any]]") -> Any: ...


ZEN_VALUE_CONVERSION: dict[type, _ConversionFn] = {}

# signature param-types
_POSITIONAL_ONLY: Final = inspect.Parameter.POSITIONAL_ONLY
_POSITIONAL_OR_KEYWORD: Final = inspect.Parameter.POSITIONAL_OR_KEYWORD
_VAR_POSITIONAL: Final = inspect.Parameter.VAR_POSITIONAL
_KEYWORD_ONLY: Final = inspect.Parameter.KEYWORD_ONLY
_VAR_KEYWORD: Final = inspect.Parameter.VAR_KEYWORD


NoneType = type(None)
_supported_types = HYDRA_SUPPORTED_PRIMITIVE_TYPES | {
    list,
    dict,
    tuple,
    List,
    Tuple,
    Dict,
}


_builtin_function_or_method_type = type(len)
# fmt: off
_lru_cache_type = type(functools.lru_cache(maxsize=128)(lambda: None))  # pragma: no branch
# fmt: on

_BUILTIN_TYPES: Final = (_builtin_function_or_method_type, _lru_cache_type)

del _lru_cache_type
del _builtin_function_or_method_type


# In python 3.13 the definitions for pathlib.Path, et al. were moved to the
# pathlib._locals, changing the tag for yaml serialization. Thus we monkey-patch
# omegaconf's yaml loader to handle these new strings.
_original_yaml_loader = _omegaconf_utils.get_yaml_loader


def _patched_yaml_loader(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover
    pass


_omegaconf_utils.get_yaml_loader = _patched_yaml_loader


def _retain_type_info(type_: type, value: Any, hydra_recursive: Optional[bool]):
    # OmegaConf's type-checking occurs before instantiation occurs.
    # This means that, e.g., passing `Builds[int]` to a field `x: int`
    # will fail Hydra's type-checking upon instantiation, even though
    # the recursive instantiation will appropriately produce `int` for
    # that field. This will not be addressed by hydra/omegaconf:
    #    https://github.com/facebookresearch/hydra/issues/1759
    # Thus we will auto-broaden the annotation when we see that a field
    # is set with a structured config as a default value - assuming that
    # the field isn't annotated with a structured config type.

    # Each condition is included separately to ensure that our tests
    # cover all scenarios
    pass


@dataclass_transform()
def hydrated_dataclass(
    target: Callable[..., Any],
    *pos_args: SupportedPrimitive,
    zen_partial: Optional[bool] = None,
    zen_wrappers: ZenWrappers[Callable[..., Any]] = tuple(),
    zen_meta: Optional[Mapping[str, Any]] = None,
    populate_full_signature: bool = False,
    hydra_recursive: Optional[bool] = None,
    hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = None,
    zen_convert: Optional[ZenConvert] = None,
    init: bool = True,
    repr: bool = True,
    eq: bool = True,
    order: bool = False,
    unsafe_hash: bool = True,
    frozen: bool = False,
    match_args: bool = True,
    kw_only: bool = False,
    slots: bool = False,
    weakref_slot: bool = False,
) -> Callable[[type[_T]], type[_T]]:
    """A decorator that uses `builds` to create a dataclass with the appropriate
    Hydra-specific fields for specifying a targeted config [1]_.

    This provides similar functionality to `builds`, but enables a user to define
    a config explicitly using the :func:`dataclasses.dataclass` syntax, which can
    enable enhanced static analysis of the resulting config.

    Parameters
    ----------
    hydra_target : T (Callable)
        The target-object to be configured. This is a required, positional-only argument.

    *pos_args : SupportedPrimitive
        Positional arguments passed as ``hydra_target(*pos_args, ...)`` upon instantiation.

        Arguments specified positionally are not included in the dataclass' signature
        and are stored as a tuple bound to in the ``_args_`` field.

    zen_partial : Optional[bool]
        If ``True``, then the resulting config will instantiate as
        ``functools.partial(hydra_target, *pos_args, **kwargs_for_target)`` rather than
        ``hydra_target(*pos_args, **kwargs_for_target)``. Thus this enables the
        partial-configuration of objects.

        Specifying ``zen_partial=True`` and ``populate_full_signature=True`` together
        will populate the config's signature only with parameters that: are explicitly
        specified by the user, or that have default values specified in the target's
        signature. I.e. it is presumed that un-specified parameters that have no
        default values are to be excluded from the config.

    zen_wrappers : None | Callable | Builds | InterpStr | Sequence[None | Callable | Builds | InterpStr]
        One or more wrappers, which will wrap ``hydra_target`` prior to instantiation.
        E.g. specifying the wrappers ``[f1, f2, f3]`` will instantiate as::

            f3(f2(f1(hydra_target)))(*args, **kwargs)

        Wrappers can also be specified as interpolated strings [2]_ or targeted
        configs.

    zen_meta : Optional[Mapping[str, SupportedPrimitive]]
        Specifies field-names and corresponding values that will be included in the
        resulting config, but that will *not* be used to builds ``<hydra_target>``
        via instantiation. These are called "meta" fields.

    populate_full_signature : bool, optional (default=False)
        If ``True``, then the resulting config's signature and fields will be populated
        according to the signature of ``hydra_target``; values also specified in
        ``**kwargs_for_target`` take precedent.

        This option is not available for objects with inaccessible signatures, such as
        NumPy's various ufuncs.

    zen_convert : Optional[ZenConvert]
        A dictionary that modifies hydra-zen's value and type conversion behavior.
        Consists of the following optional key-value pairs (:ref:`zen-convert`):

        - `dataclass` : `bool` (default=True):
            If `True` any dataclass type/instance without a
            `_target_` field is automatically converted to a targeted config
            that will instantiate to that type/instance. Otherwise the dataclass
            type/instance will be passed through as-is.

    hydra_recursive : Optional[bool], optional (default=True)
        If ``True``, then Hydra will recursively instantiate all other
        hydra-config objects nested within this config [3]_.

        If ``None``, the ``_recursive_`` attribute is not set on the resulting config.

        - ``"none"``: No conversion occurs; omegaconf containers are passed through (Default)
        - ``"partial"``: ``DictConfig`` and ``ListConfig`` objects converted to ``dict`` and
          ``list``, respectively. Structured configs and their fields are passed without conversion.
        - ``"all"``: All passed objects are converted to dicts, lists, and primitives, without a trace of OmegaConf containers.

        If ``None``, the ``_convert_`` attribute is not set on the resulting config.

    hydra_convert : Optional[Literal["none", "partial", "all", "object"]], optional (default="none")
        Determines how Hydra treats the non-primitive, omegaconf-specific objects
        during instantiateion [3]_.

        - ``"none"``: No conversion occurs; omegaconf containers are passed through (Default)
        - ``"partial"``: ``DictConfig`` and ``ListConfig`` objects converted to ``dict`` and
          ``list``, respectively. Structured configs and their fields are passed without conversion.
        - ``"all"``: All passed objects are converted to dicts, lists, and primitives, without
          a trace of OmegaConf containers.
        - ``"object"``: Passed objects are converted to dict and list. Structured Configs are converted to instances of the backing dataclass / attr class.

        If ``None``, the ``_convert_`` attribute is not set on the resulting config.

    init : bool, optional (default=True)
        If true (the default), a __init__() method will be generated. If the class
        already defines __init__(), this parameter is ignored.

    repr : bool, optional (default=True)
        If true (the default), a `__repr__()` method will be generated. The generated
        repr string will have the class name and the name and repr of each field, in
        the order they are defined in the class. Fields that are marked as being
        excluded from the repr are not included. For example:
        `InventoryItem(name='widget', unit_price=3.0, quantity_on_hand=10)`.

    eq : bool, optional (default=True)
        If true (the default), an __eq__() method will be generated. This method
        compares the class as if it were a tuple of its fields, in order. Both
        instances in the comparison must be of the identical type.

    order : bool, optional (default=False)
        If true (the default is `False`), `__lt__()`, `__le__()`, `__gt__()`, and
        `__ge__()` methods will be generated. These compare the class as if it were a
        tuple of its fields, in order. Both instances in the comparison must be of the
        identical type. If order is true and eq is false, a ValueError is raised.

        If the class already defines any of `__lt__()`, `__le__()`, `__gt__()`, or
        `__ge__()`, then `TypeError` is raised.

    unsafe_hash : bool, optional (default=False)
        If `False` (the default), a `__hash__()` method is generated according to how
        `eq` and `frozen` are set.

        If `eq` and `frozen` are both true, by default `dataclass()` will generate a
        `__hash__()` method for you. If `eq` is true and `frozen` is false, `__hash__()
        ` will be set to `None`, marking it unhashable. If `eq` is false, `__hash__()`
        will be left untouched meaning the `__hash__()` method of the superclass will
        be used (if the superclass is object, this means it will fall back to id-based
        hashing).

    frozen : bool, optional (default=False)
        If true (the default is `False`), assigning to fields will generate an
        exception. This emulates read-only frozen instances.

    match_args : bool, optional (default=True)
        (*New in version 3.10*) If true (the default is `True`), the `__match_args__`
        tuple will be created from the list of parameters to the generated `__init__()`
        method (even if `__init__()` is not generated, see above). If false, or if
        `__match_args__` is already defined in the class, then `__match_args__` will
        not be generated.

    kw_only : bool, optional (default=False)
        (*New in version 3.10*) If true (the default value is `False`), then all fields
        will be marked as keyword-only.

    slots : bool, optional (default=False)
        (*New in version 3.10*) If true (the default is `False`), `__slots__` attribute
        will be generated and new class will be returned instead of the original one.
        If `__slots__` is already defined in the class, then `TypeError` is raised.

    weakref_slot : bool, optional (default=False)
        (*New in version 3.11*) If true (the default is `False`), add a slot named
        â€œ__weakref__â€�, which is required to make an instance weakref-able. It is an
        error to specify `weakref_slot=True` without also specifying `slots=True`.


    See Also
    --------
    builds : Create a targeted structured config designed to "build" a particular object.

    Raises
    ------
    hydra_zen.errors.HydraZenUnsupportedPrimitiveError
        The provided configured value cannot be serialized by Hydra, nor does hydra-zen
        provide specialized support for it. See :ref:`valid-types` for more details.

    Notes
    -----
    Unlike `builds`, `hydrated_dataclass` enables config fields to be set explicitly
    with custom type annotations. Additionally, the resulting config' attributes
    can be analyzed by static tooling, which can help to warn about errors prior
    to running one's code.

    For details of the annotation `SupportedPrimitive`, see :ref:`valid-types`.

    References
    ----------
    .. [1] https://hydra.cc/docs/tutorials/structured_config/intro/
    .. [2] https://omegaconf.readthedocs.io/en/2.1_branch/usage.html#variable-interpolation
    .. [3] https://hydra.cc/docs/advanced/instantiate_objects/overview/#recursive-instantiation
    .. [4] https://hydra.cc/docs/advanced/instantiate_objects/overview/#parameter-conversion-strategies

    Examples
    --------
    **Basic usage**

    >>> from hydra_zen import hydrated_dataclass, instantiate

    Here, we specify a config that is designed to "build" a dictionary
    upon instantiation

    >>> @hydrated_dataclass(target=dict, frozen=True)
    ... class DictConf:
    ...     x: int = 2
    ...     y: str = 'hello'

    >>> instantiate(DictConf(x=10))  # override default `x`
    {'x': 10, 'y': 'hello'}

    >>> d = DictConf()
    >>> # Static type checker marks the following as
    >>> # an error because `d` is frozen.
    >>> d.x = 3  # type: ignore
    FrozenInstanceError: cannot assign to field 'x'

    For more detailed examples, refer to `builds`.
    """
    pass


@dataclass(unsafe_hash=True)
class Just:
    """Just[T] is a config that returns T when instantiated."""

    path: str
    _target_: str = field(default="hydra_zen.funcs.get_obj", init=False, repr=False)


def _is_ufunc(value: Any) -> bool:
    # checks without importing numpy
    pass


def _is_jax_ufunc(value: Any) -> bool:  # pragma: no cover
    # checks without importing numpy
    pass


def _is_numpy_array_func_dispatcher(value: Any) -> bool:
    pass


def _check_instance(*target_types: str, value: "Any", module: str):  # pragma: no cover
    """Checks if value is an instance of any of the target types imported
    from the specified module.

    Returns `False` if module/target type doesn't exists (e.g. not installed).
    This is useful for gracefully handling specialized logic for optional dependencies.
    """
    pass


_is_jax_compiled_func = functools.partial(
    _check_instance, "CompiledFunction", "PjitFunction", module="jaxlib.xla_extension"
)

_is_jax_compiled_func2 = functools.partial(
    _check_instance, "CompiledFunction", "PjitFunction", module="jaxlib._jax"
)
_is_jax_unspecified = functools.partial(
    _check_instance, "UnspecifiedValue", module="jax._src.interpreters.pxla"
)

_is_torch_optim_required = functools.partial(
    _check_instance, "required", module="torch.optim.optimizer"
)

_is_pydantic_BaseModel = functools.partial(
    _check_instance, "BaseModel", module="pydantic"
)


def _check_for_dynamically_defined_dataclass_type(target_path: str, value: Any) -> None:
    pass


class NOTHING:
    def __init__(self) -> None:  # pragma: no cover
        raise TypeError("`NOTHING` cannot be instantiated")


@dataclass(unsafe_hash=True)
class ZenField:
    """
    ZenField(hint=Any, default=<class 'NOTHING'>, name=<class 'NOTHING'>)

    Specifies a field's name and/or type-annotation and/or default value.
    Designed to specify fields in `make_config`.

    See the Examples section of the docstring for `make_config` for examples of using
    `ZenField`.

    Parameters
    ----------
    hint : type, optional (default=Any)
    default : Any, optional
    name : str, optional

    Notes
    -----
    ``default`` will be returned as an instance of :class:`dataclasses.Field`.
    Mutable values (e.g. lists or dictionaries) passed to ``default`` will automatically
    be "packaged" in a default-factory function [1]_.

    A type passed to ``hint`` will automatically be "broadened" such that the resulting
    type is compatible with Hydra's set of supported type annotations [2]_.

    References
    ----------
    .. [1] https://docs.python.org/3/library/dataclasses.html#default-factory-functions
    .. [2] https://hydra.cc/docs/next/tutorials/structured_config/intro/#structured-configs-supports

    See Also
    --------
    make_config: create a config with customized field names, default values, and annotations.
    """

    hint: Any = Any
    default: Union[Any, Field[Any]] = _utils.field(default=NOTHING)
    name: Union[str, type[NOTHING]] = NOTHING
    zen_convert: InitVar[Optional[ZenConvert]] = None
    _builds_fn: "Union[BuildsFn[Any], Type[BuildsFn[Any]]]" = _utils.field(
        default_factory=lambda: DefaultBuilds
    )  # type: ignore

    def __post_init__(
        self,
        zen_convert: Optional[ZenConvert],
    ) -> None:
        if not isinstance(self.name, str):
            if self.name is not NOTHING:
                raise TypeError(f"`ZenField.name` expects a string, got: {self.name}")
        convert_settings = _utils.merge_settings(zen_convert, _BUILDS_CONVERT_SETTINGS)
        del zen_convert

        self.hint = self._builds_fn._sanitized_type(self.hint)

        if self.default is not NOTHING:
            self.default = self._builds_fn._sanitized_field(
                self.default,
                convert_dataclass=convert_settings["dataclass"],
            )


_MAKE_CONFIG_SETTINGS = AllConvert(dataclass=False, flat_target=False)


class BuildsFn(Generic[T]):
    """A class that can be modified to customize the behavior of `builds`, `just`, `kwargs_of`, and `make_config`.

    These functions are exposed as class methods of `BuildsFn`.

    - To customize type-refinement support, override `_sanitized_type`.
    - To customize auto-config support, override `_make_hydra_compatible`.
    - To customize the ability to resolve import paths, override `_get_obj_path`.

    Notes
    -----
    Adding type-checking support for a custom type:

    To parameterize `BuildsFn` with, e.g., support for the custom types `MyTypeA` and `MyTypeB`, use::

       from typing import Union
       from hydra_zen.typing import CustomConfigType

       class MyTypeA: ...
       class MyTypeB: ...

       class MyBuilds(BuildsFn[CustomConfigType[Union[MyTypeA, MyTypeB]]]):
           ...

    Examples
    --------
    Suppose you wrote the following type::

       class Quaternion:
           def __init__(
               self,
               real: float = 0.0,
               i: float = 0.0,
               j: float = 0.0,
               k: float = 0.0,
           ):
               self._data = (real, i, j, k)

           def __repr__(self):
               return "Q" + repr(self._data)

    and you want hydra-zen's config-creation functions to be able to automatically
    know how to make configs from instances of this type. You do so by creating your
    own subclass of :class:`~hydra_zen.BuildsFn`::

       from typing import Any
       from hydra_zen import BuildsFn
       from hydra_zen.typing import CustomConfigType, HydraSupportedType

       class CustomBuilds(BuildsFn[CustomConfigType[Quaternion]]):
           @classmethod
           def _make_hydra_compatible(cls, value: Any, **k) -> HydraSupportedType:
               if isinstance(value, Quaternion):
                   real, i, j, k = value._data
                   return cls.builds(Quaternion, real=real, i=i, j=j, k=k)
               return super()._make_hydra_compatible(value, **k)

    Now you use the config-creation functions that are provided by `CustomBuilds` instead of those provided by `hydra_zen`::

       builds = CustomBuilds.builds
       just = CustomBuilds.just
       kwargs_of = CustomBuilds.kwargs_of
       make_config = CustomBuilds.make_config

    E.g.

    >>> from hydra_zen import to_yaml
    >>> Config = just([Quaternion(1.0), Quaternion(0.0, -12.0)])
    >>> print(to_yaml(Config))
    - _target_: __main__.Quaternion
      real: 1.0
      i: 0.0
      j: 0.0
      k: 0.0
    - _target_: __main__.Quaternion
      real: 0.0
      i: -12.0
      j: 0.0
      k: 0.0
    """

    __slots__ = ()

    _default_dataclass_options_for_kwargs_of: Optional[DataclassOptions] = None
    """Specifies the default options for `cls.kwargs_of(..., zen_dataclass)"""

    @classmethod
    def _sanitized_type(
        cls,
        type_: Any,
        *,
        primitive_only: bool = False,
        wrap_optional: bool = False,
        nested: bool = False,
    ) -> Any:  # is really type[Any]
        """Broadens a type annotation until it is compatible with Hydra.

        Override this to change how `builds` refines the type annotations
        of the configs that it produces.

        Parameters
        ----------
        type_ : Any
            The type being sanitized.

        primitive_only: bool, optional (default=False)
            If true, only `bool` | `None` | `int` | `float` | str`
            is permitted.

        wrap_optional: bool, optional (default=False)
            `True` indicates that the resulting type should be
            wrapped in `Optional`.

        nested: bool, optional (default=False)
            `True` indicates that this function is processing a type within
            a container type.

        Examples
        --------
        >>> sanitized_type(int)
        <class 'int'>

        >>> sanitized_type(frozenset)  # not supported by hydra
        typing.Any

        >>> sanitized_type(int, wrap_optional=True)
        typing.Union[int, NoneType]

        >>> sanitized_type(List[int])
        List[int]

        >>> sanitized_type(List[int], primitive_only=True)
        Any

        >>> sanitized_type(Dict[str, frozenset])
        Dict[str, Any]
        """
        pass

    @classmethod
    def _get_obj_path(cls, target: Any) -> str:
        """Used to get the `_target_` value for the resulting config.

        Override this to control how `builds` determines the _target_ field
        in the configs that it produces."""
        pass

    @classmethod
    def _just(cls, obj: Any) -> Just:
        pass

    @classmethod
    def _mutable_value(cls, x: _T, *, zen_convert: Optional[ZenConvert] = None) -> _T:
        """Used to set a mutable object as a default value for a field
        in a dataclass.

        This is an alias for ``field(default_factory=lambda: type(x)(x))``

        Note that ``type(x)(...)`` serves to make a copy

        Examples
        --------
        >>> from hydra_zen import mutable_value
        >>> from dataclasses import dataclass

        See https://docs.python.org/3/library/dataclasses.html#mutable-default-values

        >>> @dataclass  # doctest: +SKIP
        ... class HasMutableDefault:
        ...     a_list: list  = [1, 2, 3]  # error: mutable default

        Using `mutable_value` to specify the default list:

        >>> @dataclass
        ... class HasMutableDefault:
        ...     a_list: list  = mutable_value([1, 2, 3])  # ok

        >>> x = HasMutableDefault()
        >>> x.a_list.append(-1)  # does not append to `HasMutableDefault.a_list`
        >>> x
        HasMutableDefault(a_list=[1, 2, 3, -1])
        >>> HasMutableDefault()
        HasMutableDefault(a_list=[1, 2, 3])"""
        pass

    @classmethod
    def _make_hydra_compatible(
        cls,
        value: object,
        *,
        allow_zen_conversion: bool = True,
        error_prefix: str = "",
        field_name: str = "",
        structured_conf_permitted: bool = True,
        convert_dataclass: bool,
        hydra_recursive: Optional[bool] = None,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = None,
        zen_dataclass: Optional[DataclassOptions] = None,
    ) -> HydraSupportedType:
        """Converts `value` to Hydra-supported type or to a config whose fields are recursively supported by `_make_hydra_compatible`. Otherwise
        raises `HydraZenUnsupportedPrimitiveError`.

        Override this method to add support for adding auto-config support to custom
        types.

        Notes
        -----
        Hydra supports the following types:
            `bool`, `None`, `int`, `float`, `str`, `ByteString`, `pathlib.Path`,
            `dataclasses.MISSING`. As well as lists, tuples, dicts, and omegaconf
            containers containing the above.
        """
        pass

    @classmethod
    def _sanitize_collection(
        cls,
        x: _T,
        *,
        convert_dataclass: bool,
        hydra_recursive: Optional[bool] = None,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = None,
    ) -> _T:
        """Pass contents of lists, tuples, or dicts through sanitized_default_values"""
        pass

    @classmethod
    def _sanitized_field(
        cls,
        value: Any,
        init: bool = True,
        allow_zen_conversion: bool = True,
        *,
        error_prefix: str = "",
        field_name: str = "",
        convert_dataclass: bool,
    ) -> Field[Any]:
        pass

    @classmethod
    def _get_sig_obj(cls, target: Any) -> Any:
        """Return the signature object for `target`.

        `inspect.signature` has inconsistent/buggy behaviors across
        versions, so we implement our own."""
        pass

    # partial=False, pop-sig=True; no *args, **kwargs, nor builds_bases
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: type[BuildsWithSig[type[R], P]],
        *,
        zen_partial: Literal[False, None] = ...,
        populate_full_signature: Literal[True],
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[()] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
    ) -> type[BuildsWithSig[type[R], P]]: ...

    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: Callable[P, R],
        *,
        zen_partial: Literal[False, None] = ...,
        populate_full_signature: Literal[True],
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[()] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
    ) -> type[BuildsWithSig[type[R], P]]: ...

    # partial=False, pop-sig=bool
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: type[AnyBuilds[Importable]],
        *pos_args: T,
        zen_partial: Literal[False, None] = ...,
        populate_full_signature: bool = ...,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[type[DataClass_], ...] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
        **kwargs_for_target: T,
    ) -> type[Builds[Importable]]: ...

    # partial=False, pop-sig=bool
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: Importable,
        *pos_args: T,
        zen_partial: Literal[False, None] = ...,
        populate_full_signature: bool = ...,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[type[DataClass_], ...] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
        **kwargs_for_target: T,
    ) -> type[Builds[Importable]]: ...

    # partial=True, pop-sig=bool
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: type[AnyBuilds[Importable]],
        *pos_args: T,
        zen_partial: Literal[True],
        populate_full_signature: bool = ...,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[type[DataClass_], ...] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
        **kwargs_for_target: T,
    ) -> type[PartialBuilds[Importable]]: ...

    # partial=True, pop-sig=bool
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: Importable,
        *pos_args: T,
        zen_partial: Literal[True],
        populate_full_signature: bool = ...,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[type[DataClass_], ...] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
        **kwargs_for_target: T,
    ) -> type[PartialBuilds[Importable]]: ...

    # partial=bool, pop-sig=False
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: type[AnyBuilds[Importable]],
        *pos_args: T,
        zen_partial: Optional[bool] = ...,
        populate_full_signature: Literal[False] = ...,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[type[DataClass_], ...] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
        **kwargs_for_target: T,
    ) -> Union[type[Builds[Importable]], type[PartialBuilds[Importable]]]: ...

    # partial=bool, pop-sig=False
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: Importable,
        *pos_args: T,
        zen_partial: Optional[bool] = ...,
        populate_full_signature: Literal[False] = ...,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[type[DataClass_], ...] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
        **kwargs_for_target: T,
    ) -> Union[type[Builds[Importable]], type[PartialBuilds[Importable]]]: ...

    # partial=bool, pop-sig=bool
    @overload
    @classmethod
    def builds(
        cls: type[Self],
        __hydra_target: Union[Callable[P, R], type[Builds[Importable]], Importable],
        *pos_args: T,
        zen_partial: Optional[bool],
        populate_full_signature: bool = ...,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = ...,
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        hydra_defaults: Optional[DefaultsList] = ...,
        dataclass_name: Optional[str] = ...,
        builds_bases: tuple[type[DataClass_], ...] = ...,
        zen_dataclass: Optional[DataclassOptions] = None,
        frozen: bool = ...,
        zen_convert: Optional[ZenConvert] = ...,
        **kwargs_for_target: T,
    ) -> Union[
        type[Builds[Importable]],
        type[PartialBuilds[Importable]],
        type[BuildsWithSig[type[R], P]],
    ]: ...

    @classmethod
    def builds(
        cls: type[Self],
        *pos_args: Union[
            Importable,
            Callable[P, R],
            type[AnyBuilds[Importable]],
            type[BuildsWithSig[type[R], P]],
            Any,
        ],
        zen_partial: Optional[bool] = None,
        zen_wrappers: ZenWrappers[Callable[..., Any]] = tuple(),
        zen_meta: Optional[Mapping[str, SupportedPrimitive]] = None,
        populate_full_signature: bool = False,
        zen_convert: Optional[ZenConvert] = None,
        hydra_recursive: Optional[bool] = None,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = None,
        hydra_defaults: Optional[DefaultsList] = None,
        builds_bases: Union[tuple[type[DataClass_], ...], tuple[()]] = (),
        zen_dataclass: Optional[DataclassOptions] = None,
        **kwargs_for_target: Any,
    ) -> Union[
        type[Builds[Importable]],
        type[PartialBuilds[Importable]],
        type[BuildsWithSig[type[R], P]],
    ]:
        """builds(hydra_target, /, *pos_args, zen_partial=None, zen_wrappers=(), zen_meta=None, populate_full_signature=False, zen_exclude=(), hydra_recursive=None, hydra_convert=None, hydra_defaults=None, builds_bases=(),
        zen_dataclass=None, **kwargs_for_target)

        `builds(target, *args, **kw)` returns a Hydra-compatible config that, when
        instantiated, returns `target(*args, **kw)`.

        I.e., `instantiate(builds(target, *args, **kw)) == target(*args, **kw)`

        Consult the Notes section for more details, and the Examples section to see
        the various features of `builds` in action.

        Parameters
        ----------
        hydra_target : T (Callable)
            The target object to be configured. This is a required, **positional-only**
            argument.

        *pos_args : SupportedPrimitive
            Positional arguments passed as ``<hydra_target>(*pos_args, ...)`` upon
            instantiation.

            Arguments specified positionally are not included in the config's signature
            and are stored as a tuple bound to in the ``_args_`` field.

        **kwargs_for_target : SupportedPrimitive
            The keyword arguments passed as ``<hydra_target>(..., **kwargs_for_target)``
            upon instantiation.

            The arguments specified here determine the signature of the resulting
            config, unless ``populate_full_signature=True`` is specified (see below).

            Named parameters of the forms that have the prefixes ``hydra_``, ``zen_`` or
            ``_zen_`` are reserved to ensure future-compatibility, and thus cannot be
            specified by the user.

        zen_partial : Optional[bool]
            If ``True``, then the resulting config will instantiate as
            ``functools.partial(<hydra_target>, *pos_args, **kwargs_for_target)``. Thus
            this enables the partial-configuration of objects.

            Specifying ``zen_partial=True`` and ``populate_full_signature=True`` together
            will populate the config's signature only with parameters that: are explicitly
            specified by the user, or that have default values specified in the target's
            signature. I.e. it is presumed that un-specified parameters that have no
            default values are to be excluded from the config.

        zen_wrappers : None | Callable | Builds | InterpStr | Sequence[None | Callable | Builds | InterpStr]
            One or more wrappers, which will wrap ``hydra_target`` prior to instantiation.
            E.g. specifying the wrappers ``[f1, f2, f3]`` will instantiate as::

                f3(f2(f1(<hydra_target>)))(*args, **kwargs)

            Wrappers can also be specified as interpolated strings [2]_ or targeted
            configs.

        zen_meta : Optional[Mapping[str, SupportedPrimitive]]
            Specifies field-names and corresponding values that will be included in the
            resulting config, but that will *not* be used to instantiate
            ``<hydra_target>``. These are called "meta" fields.

        populate_full_signature : bool, optional (default=False)
            If ``True``, then the resulting config's signature and fields will be
            populated according to the signature of ``<hydra_target>``; values also
            specified in ``**kwargs_for_target`` take precedent.

            This option is not available for objects with inaccessible signatures, such
            as NumPy's various ufuncs.

        zen_exclude : Collection[str | int] | Callable[[str], bool], optional (default=[])
            Specifies parameter names and/or indices, or a function for checking names,
            to exclude those parameters from the config-creation process.

        Note that inherited fields cannot be excluded.
        zen_convert : Optional[ZenConvert]
            A dictionary that modifies hydra-zen's value and type conversion behavior.
            Consists of the following optional key-value pairs (:ref:`zen-convert`):

            - `dataclass` : `bool` (default=True):
                If `True` any dataclass type/instance without a
                `_target_` field is automatically converted to a targeted config
                that will instantiate to that type/instance. Otherwise the dataclass
                type/instance will be passed through as-is.

            - `flat_target`: `bool` (default=True)
                If `True` (default), `builds(builds(f))` is equivalent to `builds(f)`. I.e. the second `builds` call will use the `_target_` field of its input, if it exists.

        builds_bases : Tuple[Type[DataClass], ...]
            Specifies a tuple of parent classes that the resulting config inherits from.

        hydra_recursive : Optional[bool], optional (default=True)
            If ``True``, then Hydra will recursively instantiate all other
            hydra-config objects nested within this config [3]_.

            If ``None``, the ``_recursive_`` attribute is not set on the resulting config.

        hydra_convert : Optional[Literal["none", "partial", "all", "object"]], optional (default="none")
            Determines how Hydra handles the non-primitive, omegaconf-specific objects passed to
            ``<hydra_target>`` [4]_.

            - ``"none"``: No conversion occurs; omegaconf containers are passed through (Default)
            - ``"partial"``: ``DictConfig`` and ``ListConfig`` objects converted to ``dict`` and
            ``list``, respectively. Structured configs and their fields are passed without conversion.
            - ``"all"``: All passed objects are converted to dicts, lists, and primitives, without
            a trace of OmegaConf containers.
            - ``"object"``: Passed objects are converted to dict and list. Structured Configs are converted to instances of the backing dataclass / attr class.

            If ``None``, the ``_convert_`` attribute is not set on the resulting config.

        hydra_defaults : None | list[str | dict[str, str | list[str] | None ]], optional (default = None)
            A list in an input config that instructs Hydra how to build the output config
            [6]_ [7]_. Each input config can have a Defaults List as a top level element. The
            Defaults List itself is not a part of output config.

        zen_dataclass : Optional[DataclassOptions]
            A dictionary that can specify any option that is supported by
            :py:func:`dataclasses.make_dataclass` other than `fields`.
            The default value for `unsafe_hash` is `True`.

            `target` can be specified as a string to override the `_target_` field
            set on the dataclass type returned by `builds`.

            The `module` field can be specified to enable pickle
            compatibility. See `hydra_zen.typing.DataclassOptions` for details.

        frozen : bool, optional (default=False)
            .. deprecated:: 0.9.0
                `frozen` will be removed in hydra-zen 0.10.0. It is replaced by
                `zen_dataclass={'frozen': <bool>}`.

            If ``True``, the resulting config will create frozen (i.e. immutable) instances.
            I.e. setting/deleting an attribute of an instance will raise
            :py:class:`dataclasses.FrozenInstanceError` at runtime.

        dataclass_name : Optional[str]
            .. deprecated:: 0.9.0
                `dataclass_name` will be removed in hydra-zen 0.10.0. It is replaced by
                `zen_dataclass={'cls_name': <str>}`.

            If specified, determines the name of the returned class object.

        Returns
        -------
        Config : Type[Builds[Type[T]]] | Type[PartialBuilds[Type[T]]]
            A dynamically-generated structured config (i.e. a dataclass type) that
            describes how to build ``hydra_target``.

        Raises
        ------
        hydra_zen.errors.HydraZenUnsupportedPrimitiveError
            The provided configured value cannot be serialized by Hydra, nor does
            hydra-zen provide specialized support for it. See :ref:`valid-types` for
            more details.

        Notes
        -----
        The following pseudo code conveys the core functionality of `builds`:

        .. code-block:: python

            from dataclasses import make_dataclass

            def builds(self,target, populate_full_signature=False, **kw):
                # Dynamically defines a Hydra-compatible dataclass type.
                # Akin to doing:
                #
                # @dataclass
                # class Builds_thing:
                #     _target_: str = get_import_path(target)
                #     # etc.

                _target_ = get_import_path(target)

                if populate_full_signature:
                    sig = get_signature(target)
                    kw = {**sig, **kw}  # merge w/ preference for kw

                type_annots = [get_hints(target)[k] for k in kw]

                fields = [("_target_", str, _target_)]
                fields += [
                    (
                        field_name,
                        hydra_compat_type_annot(hint),
                        hydra_compat_val(v),
                    )
                    for hint, (field_name, v) in zip(type_annots, kw.items())
                ]

                Config = make_dataclass(f"Builds_{target}", fields)
                return Config

        The resulting "config" is a dynamically-generated dataclass type [5]_ with
        Hydra-specific attributes attached to it [1]_. It possesses a `_target_`
        attribute that indicates the import path to the configured target as a string.

        Using any of the ``zen_xx`` features will result in a config that depends
        explicitly on hydra-zen. I.e. hydra-zen must be installed in order to
        instantiate the resulting config, including its yaml version.

        For details of the annotation `SupportedPrimitive`, see :ref:`valid-types`.

        Type annotations are inferred from the target's signature and are only
        retained if they are compatible with Hydra's limited set of supported
        annotations; otherwise an annotation is automatically 'broadened' until
        it is made compatible with Hydra.

        `builds` provides runtime validation of user-specified arguments against
        the target's signature. E.g. specifying mis-named arguments or too many
        arguments will cause `builds` to raise.

        References
        ----------
        .. [1] https://hydra.cc/docs/tutorials/structured_config/intro/
        .. [2] https://omegaconf.readthedocs.io/en/2.1_branch/usage.html#variable-interpolation
        .. [3] https://hydra.cc/docs/advanced/instantiate_objects/overview/#recursive-instantiation
        .. [4] https://hydra.cc/docs/advanced/instantiate_objects/overview/#parameter-conversion-strategies
        .. [5] https://docs.python.org/3/library/dataclasses.html
        .. [6] https://hydra.cc/docs/tutorials/structured_config/defaults/
        .. [7] https://hydra.cc/docs/advanced/defaults_list/

        See Also
        --------
        instantiate: Instantiates a configuration created by `builds`, returning the instantiated target.
        make_custom_builds_fn: Returns a new `builds` function with customized default values.
        make_config: Creates a general config with customized field names, default values, and annotations.
        get_target: Returns the target-object from a targeted structured config.
        just: Produces a config that, when instantiated by Hydra, "just" returns the un-instantiated target-object.
        to_yaml: Serialize a config as a yaml-formatted string.

        Examples
        --------
        These examples describe:

        - Basic usage
        - Creating a partial config
        - Auto-populating parameters
        - Composing configs via inheritance
        - Runtime validation performed by builds
        - Using meta-fields
        - Using zen-wrappers
        - Creating a pickle-compatible config
        - Creating a frozen config
        - Support for partial'd targets

        A helpful utility for printing examples

        >>> from hydra_zen import builds, instantiate, to_yaml
        >>> def pyaml(x):
        ...     # for pretty printing configs
        ...     print(to_yaml(x))

        **Basic Usage**

        Lets create a basic config that describes how to 'build' a particular dictionary.

        >>> Conf = builds(dict, a=1, b='x')

        The resulting config is a dataclass with the following signature and attributes:

        >>> Conf  # signature: Conf(a: Any = 1, b: Any = 'x')
        <class 'types.Builds_dict'>

        >>> pyaml(Conf)
        _target_: builtins.dict
        a: 1
        b: x

        The `instantiate` function is used to enact this build â€“ to create the dictionary.

        >>> instantiate(Conf)  # calls: `dict(a=1, b='x')`
        {'a': 1, 'b': 'x'}

        The default parameters that we provided can be overridden.

        >>> new_conf = Conf(a=10, b="hi")  # an instance of our dataclass
        >>> instantiate(new_conf)  # calls: `dict(a=10, b='hi')`
        {'a': 10, 'b': 'hi'}

        Positional arguments are supported.

        >>> Conf = builds(len, [1, 2, 3])
        >>> Conf._args_  # type: ignore
        [1, 2, 3]
        >>> instantiate(Conf)
        3

        **Creating a Partial Config**

        `builds` can be used to partially-configure a target. Let's
        create a config for the following function

        >>> def a_two_tuple(x: int, y: float): return x, y

        such that we only configure the parameter ``x``.

        >>> PartialConf = builds(a_two_tuple, x=1, zen_partial=True)  # configures only `x`
        >>> pyaml(PartialConf)
        _target_: __main__.a_two_tuple
        _partial_: true
        x: 1

        Instantiating this config will return ``functools.partial(a_two_tuple, x=1)``.

        >>> partial_func = instantiate(PartialConf)
        >>> partial_func
        functools.partial(<function a_two_tuple at 0x00000220A7820EE0>, x=1)

        And thus the remaining parameter can be provided post-instantiation.

        >>> partial_func(y=22.0)  # providing the remaining parameter
        (1, 22.0)

        **Auto-populating parameters**

        The configurable parameters of a target can be auto-populated in our config.
        Suppose we want to configure the following function.

        >>> def bar(x: bool, y: str = 'foo'): return x, y

        The following config will have a signature that matches ``bar``; the
        annotations and default values of the parameters of ``bar`` are explicitly
        incorporated into the config.

        >>> # signature: `Builds_bar(x: bool, y: str = 'foo')`
        >>> Conf = builds(bar, populate_full_signature=True)
        >>> pyaml(Conf)
        _target_: __main__.bar
        x: ???
        'y': foo

        `zen_exclude` can be used to name parameter to be excluded from the
        auto-population process:

        >>> Conf2 = builds(bar, populate_full_signature=True, zen_exclude=["y"])
        >>> pyaml(Conf2)
        _target_: __main__.bar
        x: ???

        to exclude parameters by index:

        >>> Conf2 = builds(bar, populate_full_signature=True, zen_exclude=[-1])
        >>> pyaml(Conf2)
        _target_: __main__.bar
        x: ???

        or to specify a pattern - via a function - for excluding parameters:

        >>> Conf3 = builds(bar, populate_full_signature=True,
        ...                zen_exclude=lambda name: name.startswith("x"))
        >>> pyaml(Conf3)
        _target_: __main__.bar
        'y': foo

        Annotations will be used by Hydra to provide limited runtime type-checking
        during instantiation. Here, we'll pass a float for ``x``, which expects a
        boolean value.

        >>> instantiate(Conf(x=10.0))  # type: ignore
        ValidationError: Value '10.0' is not a valid bool (type float)
            full_key: x
            object_type=Builds_func

        **Composing configs via inheritance**

        Because a config produced via `builds` is simply a class-object, we can
        compose configs via class inheritance.

        >>> ParentConf = builds(dict, a=1, b=2)
        >>> ChildConf = builds(dict, b=-2, c=-3, builds_bases=(ParentConf,))
        >>> instantiate(ChildConf)
        {'a': 1, 'b': -2, 'c': -3}
        >>> issubclass(ChildConf, ParentConf)  # type: ignore
        True

        .. _builds-validation:

        **Runtime validation performed by builds**

        Misspelled parameter names and other invalid configurations for the targetâ€™s
        signature will be caught by `builds` so that such errors are caught prior to
        instantiation.

        >>> def func(a_number: int): pass

        >>> builds(func, a_nmbr=2)  # misspelled parameter name
        TypeError: Building: func ..

        >>> builds(func, 1, 2)  # too many arguments
        TypeError: Building: func ..

        >>> BaseConf = builds(func, a_number=2)
        >>> builds(func, 1, builds_bases=(BaseConf,))  # too many args (via inheritance)
        TypeError: Building: func ..

        >>> # value type not supported by Hydra
        >>> builds(int, (i for i in range(10)))  # type: ignore
        hydra_zen.errors.HydraZenUnsupportedPrimitiveError: Building: int ..


        .. _meta-field:

        **Using meta-fields**

        Meta-fields are fields that are included in a config but are excluded by the
        instantiation process. Thus arbitrary metadata can be attached to a config.

        Let's create a config whose fields reference a meta-field via
        relative-interpolation [2]_.

        >>> Conf = builds(dict, a="${.s}", b="${.s}", zen_meta=dict(s=-10))
        >>> instantiate(Conf)
        {'a': -10, 'b': -10}
        >>> instantiate(Conf, s=2)
        {'a': 2, 'b': 2}

        .. _zen-wrapper:

        **Using zen-wrappers**

        Zen-wrappers enables us to make arbitrary changes to ``<hydra_target>``, its inputs,
        and/or its outputs during the instantiation process.

        Let's use a wrapper to add a unit-conversion step to a config. We'll modify a
        config that builds a function, which converts a temperature in Fahrenheit to
        Celsius, and add a wrapper to it so that it will convert from Fahrenheit to
        Kelvin instead.

        >>> def faren_to_celsius(temp_f):  # our target
        ...     return ((temp_f - 32) * 5) / 9

        >>> def change_celcius_to_kelvin(celc_func):  # our wrapper
        ...     def wraps(*args, **kwargs):
        ...         return 273.15 + celc_func(*args, **kwargs)
        ...     return wraps

        >>> AsCelcius = builds(faren_to_celsius)
        >>> AsKelvin = builds(faren_to_celsius, zen_wrappers=change_celcius_to_kelvin)
        >>> instantiate(AsCelcius, temp_f=32)
        0.0
        >>> instantiate(AsKelvin, temp_f=32)
        273.15

        **Creating a pickle-compatible config**

        The dynamically-generated classes created by `builds` can be made pickle-compatible
        by specifying the name of the symbol that it is assigned to and the module in which
        it was defined.

        .. code-block:: python

           # contents of mylib/foo.py
           from pickle import dumps, loads

           DictConf = builds(dict,
                               zen_dataclass={'module': 'mylib.foo',
                                               'cls_name': 'DictConf'})

           assert DictConf is loads(dumps(DictConf))


        **Creating a frozen config**

        Let's create a config object whose instances will by "frozen" (i.e., immutable).

        >>> RouterConfig = builds(dict, ip_address=None, zen_dataclass={'frozen': True})
        >>> my_router = RouterConfig(ip_address="192.168.56.1")  # an immutable instance

        Attempting to overwrite the attributes of ``my_router`` will raise.

        >>> my_router.ip_address = "148.109.37.2"
        FrozenInstanceError: cannot assign to field 'ip_address'

        **Support for partial'd targets**

        Specifying ``builds(functools.partial(<target>, ...), ...)`` is supported; `builds`
        will automatically "unpack" a partial'd object that is passed as its target.

        >>> import functools
        >>> partiald_dict = functools.partial(dict, a=1, b=2)
        >>> Conf = builds(partiald_dict)  # signature: (a = 1, b = 2)
        >>> instantiate(Conf)  # equivalent to calling: `partiald_dict()`
        {'a': 1, 'b': 2}
        >>> instantiate(Conf(a=-4))  # equivalent to calling: `partiald_dict(a=-4)`
        {'a': -4, 'b': 2}
        """
        pass

    @overload
    @classmethod
    def just(
        cls,
        obj: TP,
        *,
        zen_convert: Optional[ZenConvert] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        zen_dataclass: Optional[DataclassOptions] = ...,
    ) -> TP: ...

    @overload
    @classmethod
    def just(
        cls,
        obj: complex,
        *,
        zen_convert: Optional[ZenConvert] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        zen_dataclass: Optional[DataclassOptions] = ...,
    ) -> "ConfigComplex": ...

    @overload
    @classmethod
    def just(
        cls,
        obj: TC,
        *,
        zen_convert: Optional[ZenConvert] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        zen_dataclass: Optional[DataclassOptions] = ...,
    ) -> JustT[TC]: ...

    @overload
    @classmethod
    def just(
        cls,
        obj: TB,
        *,
        zen_convert: Optional[ZenConvert] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        zen_dataclass: Optional[DataclassOptions] = ...,
    ) -> Builds[type[TB]]: ...

    @overload
    @classmethod
    def just(
        cls,
        obj: TD,
        *,
        zen_convert: Literal[None] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        zen_dataclass: Optional[DataclassOptions] = ...,
    ) -> type[Builds[type[TD]]]: ...

    @overload
    @classmethod
    def just(
        cls,
        obj: DataClass_,
        *,
        zen_convert: ZenConvert,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        zen_dataclass: Optional[DataclassOptions] = ...,
    ) -> Any: ...

    @overload
    @classmethod
    def just(
        cls,
        obj: Any,
        *,
        zen_convert: Optional[ZenConvert] = ...,
        hydra_recursive: Optional[bool] = ...,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = ...,
        zen_dataclass: Optional[DataclassOptions] = ...,
    ) -> Any: ...

    @classmethod
    def just(
        cls,
        obj: Any,
        *,
        zen_convert: Optional[ZenConvert] = None,
        hydra_recursive: Optional[bool] = None,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = None,
        zen_dataclass: Optional[DataclassOptions] = None,
    ) -> Any:
        """`just(obj)` returns a config that, when instantiated, just returns `obj`.

        I.e., `instantiate(just(obj)) == obj`

        `just` is designed to be idempotent. I.e., `just(obj) == just(just(obj))`

        See the docstring for `hydra_zen.just`
        """
        pass

    @classmethod
    def make_config(
        cls,
        *fields_as_args: Union[str, ZenField],
        hydra_recursive: Optional[bool] = None,
        hydra_convert: Optional[Literal["none", "partial", "all", "object"]] = None,
        hydra_defaults: Optional[DefaultsList] = None,
        zen_dataclass: Optional[DataclassOptions] = None,
        bases: tuple[type[DataClass_], ...] = (),
        zen_convert: Optional[ZenConvert] = None,
        **fields_as_kwargs: Union[T, ZenField],
    ) -> type[DataClass]:
        """
        Returns a config with user-defined field names and, optionally,
        associated default values and/or type annotations.

        See the docstring for hydra_zen.make_config
        """
        pass

    # cover zen_exclude=() -> (1, 2, 3)
    @overload
    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Callable[P, Any],
        *,
        zen_dataclass: Optional[DataclassOptions] = ...,
        zen_exclude: tuple[()],
    ) -> type[BuildsWithSig[type[dict[str, Any]], P]]: ...

    @overload
    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Callable[Concatenate[Any, P], Any],
        *,
        zen_dataclass: Optional[DataclassOptions] = ...,
        zen_exclude: tuple[Literal[0]],
    ) -> type[BuildsWithSig[type[dict[str, Any]], P]]: ...

    @overload
    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Callable[Concatenate[Any, Any, P], Any],
        *,
        zen_dataclass: Optional[DataclassOptions] = ...,
        zen_exclude: tuple[Literal[0], Literal[1]],
    ) -> type[BuildsWithSig[type[dict[str, Any]], P]]: ...

    @overload
    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Callable[Concatenate[Any, Any, Any, P], Any],
        *,
        zen_dataclass: Optional[DataclassOptions] = ...,
        zen_exclude: tuple[Literal[0], Literal[1], Literal[2]],
    ) -> type[BuildsWithSig[type[dict[str, Any]], P]]: ...

    # no zen-exclude

    @overload
    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Callable[P, Any],
        *,
        zen_dataclass: Optional[DataclassOptions] = ...,
        zen_exclude: Literal[None] = ...,
    ) -> type[BuildsWithSig[type[dict[str, Any]], P]]: ...

    @overload
    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Callable[P, Any],
        *,
        zen_dataclass: Optional[DataclassOptions] = ...,
        zen_exclude: Union["Collection[Union[str, int]]", Callable[[str], bool]],
    ) -> type[Builds[type[dict[str, Any]]]]: ...

    @overload
    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Callable[P, Any],
        *,
        zen_dataclass: Optional[DataclassOptions] = ...,
        zen_exclude: Union[
            None, "Collection[Union[str, int]]", Callable[[str], bool]
        ] = ...,
        **kwarg_overrides: T,
    ) -> type[Builds[type[dict[str, Any]]]]: ...

    @classmethod
    def kwargs_of(
        cls: type[Self],
        __hydra_target: Union[
            Callable[P, Any],
            Callable[Concatenate[Any, P], Any],
            Callable[Concatenate[Any, Any, P], Any],
            Callable[Concatenate[Any, Any, Any, P], Any],
        ],
        *,
        zen_dataclass: Optional[DataclassOptions] = None,
        zen_exclude: Union[
            None, "Collection[Union[str, int]]", Callable[[str], bool]
        ] = None,
        **kwarg_overrides: T,
    ) -> Union[
        type[BuildsWithSig[type[dict[str, Any]], P]], type[Builds[type[dict[str, Any]]]]
    ]:
        """Returns a config whose signature matches that of the provided target.

        Instantiating the config returns a dictionary.

        Parameters
        ----------
        __hydra_target : Callable[P, Any]
            An object with an inspectable signature.

        zen_exclude : Collection[str | int] | Callable[[str], bool], optional (default=[])
            Specifies parameter names and/or indices, or a function for checking names,
            to exclude those parameters from the config-creation process.

        **kwarg_overrides : T
            Named overrides for the parameters' default values.

        Returns
        -------
        type[Builds[type[dict[str, Any]]]]

        Examples
        --------
        >>> from inspect import signature
        >>> from hydra_zen import kwargs_of, instantiate

        >>> Config = kwargs_of(lambda x, y: None)
        >>> signature(Config)
        <Signature (x:Any, y: Any) -> None>
        >>> config = Config(x=1, y=2)
        >>> config
        kwargs_of_lambda(x=1, y=2)
        >>> instantiate(config)
        {'x': 1, 'y': 2}

        Excluding the first parameter from the target's signature:

        >>> Config = kwargs_of(lambda *, x, y: None, zen_exclude=(0,))
        >>> signature(Config)  # note: type checkers sees that x is removed as well
        <Signature (y: Any) -> None>
        >>> instantiate(Config(y=88))
        {'y': 88}


        Overwriting a default

        >>> Config = kwargs_of(lambda *, x, y: None, y=22)
        >>> signature(Config)
        <Signature (x: Any, y: Any = 22) -> None>
        """
        pass


class DefaultBuilds(BuildsFn[SupportedPrimitive]):
    _default_dataclass_options_for_kwargs_of = {}


builds: Final = DefaultBuilds.builds
kwargs_of: Final = DefaultBuilds.kwargs_of


@dataclass(unsafe_hash=True)
class ConfigComplex:
    real: Any
    imag: Any
    _target_: str = field(default=BuildsFn._get_obj_path(complex), init=False)
    CBuildsFn: InitVar[type[BuildsFn[Any]]]

    def __post_init__(self, CBuildsFn: type[BuildsFn[Any]]) -> None:
        del CBuildsFn


@dataclass(unsafe_hash=True)
class ConfigPath:
    _args_: tuple[str]
    _target_: str = field(default=BuildsFn._get_obj_path(Path), init=False)
    CBuildsFn: InitVar[type[BuildsFn[Any]]]

    def __post_init__(self, CBuildsFn: type[BuildsFn[Any]]) -> None:  # pragma: no cover
        del CBuildsFn


def get_target_path(obj: Union[HasTarget, HasTargetInst]) -> Any:
    """
    Returns the import-path from a targeted config.

    Parameters
    ----------
    obj : HasTarget
        An object with a ``_target_`` attribute.

    Returns
    -------
    target_str : str
        The import path stored on the config object.

    Raises
    ------
    TypeError: ``obj`` does not have a ``_target_`` attribute.
    """
    pass


@overload
def get_target(obj: InstOrType[Builds[_T]]) -> _T: ...


@overload
def get_target(obj: HasTargetInst) -> Any: ...


@overload
def get_target(obj: HasTarget) -> Any: ...


def get_target(obj: Union[HasTarget, HasTargetInst]) -> Any:
    """
    Returns the target-object from a targeted config.

    Parameters
    ----------
    obj : HasTarget
        An object with a ``_target_`` attribute.

    Returns
    -------
    target : Any
        The target object of the config.

        Note that this will import the object using the import
        path specified by the config.

    Raises
    ------
    TypeError: ``obj`` does not have a ``_target_`` attribute.

    Examples
    --------
    **Basic usage**

    `get_target` works on all variety of configs produced by `builds` and
    `just`.

    >>> from hydra_zen import builds, just, get_target
    >>> get_target(builds(int))
    <class 'int'>
    >>> get_target(builds(int, zen_partial=True))
    <class 'int'>
    >>> get_target(just(str))
    <class 'str'>

    It works for manually-defined configs:

    >>> from dataclasses import dataclass
    >>> @dataclass
    ... class A:
    ...     _target_: str = "builtins.dict"

    >>> get_target(A())
    <class 'dict'>

    and for configs loaded from yamls.

    >>> from hydra_zen import load_from_yaml, save_as_yaml

    >>> class B: pass
    >>> Conf = builds(B)

    >>> save_as_yaml(Conf, "config.yaml")
    >>> loaded_conf = load_from_yaml("config.yaml")

    Note that the target of ``loaded_conf`` can be accessed without
    instantiating the config.

    >>> get_target(loaded_conf)  # type: ignore
    __main__.B
    """
    pass


def mutable_value(
    x: _T,
    *,
    zen_convert: Optional[ZenConvert] = None,
    BuildsFunction: type[BuildsFn[Any]] = BuildsFn[Any],
) -> _T:
    """Used to set a mutable object as a default value for a field
    in a dataclass.

    This is an alias for ``field(default_factory=lambda: type(x)(x))``

    Note that ``type(x)(...)`` serves to make a copy

    Examples
    --------
    >>> from hydra_zen import mutable_value
    >>> from dataclasses import dataclass

    See https://docs.python.org/3/library/dataclasses.html#mutable-default-values

    >>> @dataclass  # doctest: +SKIP
    ... class HasMutableDefault:
    ...     a_list: list  = [1, 2, 3]  # error: mutable default

    Using `mutable_value` to specify the default list:

    >>> @dataclass
    ... class HasMutableDefault:
    ...     a_list: list  = mutable_value([1, 2, 3])  # ok

    >>> x = HasMutableDefault()
    >>> x.a_list.append(-1)  # does not append to `HasMutableDefault.a_list`
    >>> x
    HasMutableDefault(a_list=[1, 2, 3, -1])
    >>> HasMutableDefault()
    HasMutableDefault(a_list=[1, 2, 3])"""
    pass


def convert_complex(
    value: complex, CBuildsFn: type[BuildsFn[Any]]
) -> Builds[type[complex]]:
    pass


ZEN_VALUE_CONVERSION[complex] = convert_complex


if Path in ZEN_SUPPORTED_PRIMITIVES:  # pragma: no cover

    def convert_path(value: Path, CBuildsFn: type[BuildsFn[Any]]) -> Builds[type[Path]]:
        pass

    ZEN_VALUE_CONVERSION[Path] = convert_path
    ZEN_VALUE_CONVERSION[PosixPath] = convert_path
    ZEN_VALUE_CONVERSION[WindowsPath] = convert_path


def _unpack_partial(
    value: Partial[_T], CBuildsFn: type[BuildsFn[Any]]
) -> PartialBuilds[type[_T]]:
    pass


@dataclass(unsafe_hash=True)
class ConfigFromTuple:
    _args_: tuple[Any, ...]
    _target_: str
    CBuildsFn: InitVar[type[BuildsFn[Any]]]

    def __post_init__(self, CBuildsFn: type[BuildsFn[Any]]) -> None:
        self._args_ = (
            CBuildsFn._make_hydra_compatible(
                tuple(self._args_),
                convert_dataclass=True,
                allow_zen_conversion=True,
                structured_conf_permitted=True,
            ),
        )


@dataclass(unsafe_hash=True)
class ConfigFromDict:
    _args_: Any
    _target_: str
    CBuildsFn: InitVar[type[BuildsFn[Any]]]

    def __post_init__(self, CBuildsFn: type[BuildsFn[Any]]) -> None:
        self._args_ = (
            CBuildsFn._make_hydra_compatible(
                dict(self._args_),
                convert_dataclass=True,
                allow_zen_conversion=True,
                structured_conf_permitted=True,
            ),
        )


@dataclass(unsafe_hash=True)
class ConfigRange:
    start: InitVar[int]
    stop: InitVar[int]
    step: InitVar[int]
    _target_: str = field(default=BuildsFn._get_obj_path(range), init=False)
    _args_: tuple[int, ...] = field(default=(), init=False, repr=False)
    CBuildsFn: InitVar[type[BuildsFn[Any]]]

    def __post_init__(
        self, start: int, stop: int, step: int, CBuildsFn: type[BuildsFn[Any]]
    ) -> None:
        del CBuildsFn
        self._args_ = (start, stop, step)


@dataclass(unsafe_hash=True)
class ConfigTimeDelta:
    CBuildsFn: InitVar[type[BuildsFn[Any]]]
    days: float = 0.0
    seconds: float = 0.0
    microseconds: float = 0.0
    milliseconds: float = 0.0
    minutes: float = 0.0
    hours: float = 0.0
    weeks: float = 0.0
    _target_: str = field(default=BuildsFn._get_obj_path(timedelta), init=False)

    def __post_init__(self, CBuildsFn: type[BuildsFn[Any]]) -> None:
        del CBuildsFn


@dataclass(unsafe_hash=True)
class ConfigFromDefaultDict:
    dict_: dict[Any, Any]
    default_factory: Any = field(init=False)
    CBuildsFn: InitVar[type[BuildsFn[Any]]]
    _target_: str = BuildsFn._get_obj_path(as_default_dict)

    def __post_init__(self, CBuildsFn: type[BuildsFn[Any]]) -> None:
        assert isinstance(self.dict_, defaultdict)
        self.default_factory = CBuildsFn.just(self.dict_.default_factory)
        out = CBuildsFn._make_hydra_compatible(
            dict(self.dict_),
            convert_dataclass=True,
            allow_zen_conversion=True,
            structured_conf_permitted=True,
        )
        assert isinstance(out, dict)
        self.dict_ = out


ZEN_VALUE_CONVERSION[defaultdict] = lambda dict_, CBuildsFn: ConfigFromDefaultDict(
    dict_, CBuildsFn
)

ZEN_VALUE_CONVERSION[set] = partial(
    ConfigFromTuple, _target_=BuildsFn._get_obj_path(set)
)
ZEN_VALUE_CONVERSION[frozenset] = partial(
    ConfigFromTuple, _target_=BuildsFn._get_obj_path(frozenset)
)
ZEN_VALUE_CONVERSION[deque] = partial(
    ConfigFromTuple, _target_=BuildsFn._get_obj_path(deque)
)

if bytes in ZEN_SUPPORTED_PRIMITIVES:  # pragma: no cover
    ZEN_VALUE_CONVERSION[bytes] = partial(
        ConfigFromTuple, _target_=BuildsFn._get_obj_path(bytes)
    )

ZEN_VALUE_CONVERSION[bytearray] = partial(
    ConfigFromTuple, _target_=BuildsFn._get_obj_path(bytearray)
)
ZEN_VALUE_CONVERSION[range] = lambda value, CBuildsFn: ConfigRange(
    value.start,
    value.stop,
    value.step,
    CBuildsFn=CBuildsFn,
)
ZEN_VALUE_CONVERSION[timedelta] = lambda value, CBuildsFn: ConfigTimeDelta(
    days=value.days,
    seconds=value.seconds,
    microseconds=value.microseconds,
    CBuildsFn=CBuildsFn,
)
ZEN_VALUE_CONVERSION[Counter] = partial(
    ConfigFromDict, _target_=BuildsFn._get_obj_path(Counter)
)
ZEN_VALUE_CONVERSION[functools.partial] = _unpack_partial
