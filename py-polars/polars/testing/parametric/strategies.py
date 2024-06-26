from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal as PyDecimal
from itertools import chain
from random import choice, randint, shuffle
from string import ascii_uppercase
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Iterator,
    Mapping,
    MutableMapping,
    Sequence,
)

import hypothesis.strategies as st
from hypothesis.strategies import (
    SearchStrategy,
    binary,
    booleans,
    characters,
    composite,
    dates,
    datetimes,
    floats,
    from_type,
    integers,
    lists,
    sampled_from,
    sets,
    text,
    timedeltas,
    times,
)

from polars.datatypes import (
    Array,
    Binary,
    Boolean,
    Categorical,
    Date,
    Datetime,
    Decimal,
    Duration,
    Float32,
    Float64,
    Int8,
    Int16,
    Int32,
    Int64,
    List,
    String,
    Time,
    UInt8,
    UInt16,
    UInt32,
    UInt64,
)
from polars.type_aliases import PolarsDataType

if TYPE_CHECKING:
    import sys

    from hypothesis.strategies import DrawFn

    if sys.version_info >= (3, 11):
        from typing import Self
    else:
        from typing_extensions import Self


@composite
def dtype_strategies(draw: DrawFn, dtype: PolarsDataType) -> SearchStrategy[Any]:
    """Returns a strategy which generates valid values for the given data type."""
    if (strategy := all_strategies.get(dtype)) is not None:
        return strategy
    elif (strategy_base := all_strategies.get(dtype.base_type())) is not None:
        return strategy_base

    if dtype == Decimal:
        return draw(
            decimal_strategies(
                precision=getattr(dtype, "precision", None),
                scale=getattr(dtype, "scale", None),
            )
        )
    else:
        msg = f"unsupported data type: {dtype}"
        raise TypeError(msg)


def between(draw: DrawFn, type_: type, min_: Any, max_: Any) -> Any:
    """Draw a value in a given range from a type-inferred strategy."""
    strategy_init = from_type(type_).function  # type: ignore[attr-defined]
    return draw(strategy_init(min_, max_))


# scalar dtype strategies are largely straightforward, mapping directly
# onto the associated hypothesis strategy, with dtype-defined limits
strategy_bool = booleans()
strategy_f32 = floats(width=32)
strategy_f64 = floats(width=64)
strategy_i8 = integers(min_value=-(2**7), max_value=(2**7) - 1)
strategy_i16 = integers(min_value=-(2**15), max_value=(2**15) - 1)
strategy_i32 = integers(min_value=-(2**31), max_value=(2**31) - 1)
strategy_i64 = integers(min_value=-(2**63), max_value=(2**63) - 1)
strategy_u8 = integers(min_value=0, max_value=(2**8) - 1)
strategy_u16 = integers(min_value=0, max_value=(2**16) - 1)
strategy_u32 = integers(min_value=0, max_value=(2**32) - 1)
strategy_u64 = integers(min_value=0, max_value=(2**64) - 1)

strategy_categorical = text(max_size=2, alphabet=ascii_uppercase)
strategy_string = text(
    alphabet=characters(max_codepoint=1000, exclude_categories=["Cs", "Cc"]),
    max_size=8,
)
strategy_binary = binary()
strategy_datetime_ns = datetimes(
    min_value=datetime(1677, 9, 22, 0, 12, 43, 145225),
    max_value=datetime(2262, 4, 11, 23, 47, 16, 854775),
)
strategy_datetime_us = strategy_datetime_ms = datetimes(
    min_value=datetime(1, 1, 1),
    max_value=datetime(9999, 12, 31, 23, 59, 59, 999000),
)
strategy_time = times()
strategy_date = dates()
strategy_duration = timedeltas(
    min_value=timedelta(microseconds=-(2**46)),
    max_value=timedelta(microseconds=(2**46) - 1),
)
strategy_closed = sampled_from(["left", "right", "both", "none"])
strategy_time_unit = sampled_from(["ns", "us", "ms"])


@composite
def decimal_strategies(
    draw: DrawFn, precision: int | None = None, scale: int | None = None
) -> SearchStrategy[PyDecimal]:
    """Returns a strategy which generates instances of Python `Decimal`."""
    if precision is None:
        precision = draw(integers(min_value=scale or 1, max_value=38))
    if scale is None:
        scale = draw(integers(min_value=0, max_value=precision))

    exclusive_limit = PyDecimal(f"1E+{precision - scale}")
    epsilon = PyDecimal(f"1E-{scale}")
    limit = exclusive_limit - epsilon
    if limit == exclusive_limit:  # Limit cannot be set exactly due to precision issues
        multiplier = PyDecimal("1") - PyDecimal("1E-20")  # 0.999...
        limit = limit * multiplier

    return st.decimals(
        allow_nan=False,
        allow_infinity=False,
        min_value=-limit,
        max_value=limit,
        places=scale,
    )


@composite
def strategy_datetime_format(draw: DrawFn) -> str:
    """Draw a random datetime format string."""
    fmt = draw(
        sets(
            sampled_from(
                [
                    "%m",
                    "%b",
                    "%B",
                    "%d",
                    "%j",
                    "%a",
                    "%A",
                    "%w",
                    "%H",
                    "%I",
                    "%p",
                    "%M",
                    "%S",
                    "%U",
                    "%W",
                    "%%",
                ]
            ),
        )
    )

    # Make sure year is always present
    fmt.add("%Y")

    return " ".join(fmt)


class StrategyLookup(MutableMapping[PolarsDataType, SearchStrategy[Any]]):
    """
    Mapping from polars DataTypes to hypothesis Strategies.

    We customise this so that retrieval of nested strategies respects the inner dtype
    of List/Struct types; nested strategies are stored as callables that create the
    given strategy on demand (there are infinitely many possible nested dtypes).
    """

    _items: dict[
        PolarsDataType, SearchStrategy[Any] | Callable[..., SearchStrategy[Any]]
    ]

    def __init__(
        self,
        items: (
            Mapping[
                PolarsDataType, SearchStrategy[Any] | Callable[..., SearchStrategy[Any]]
            ]
            | None
        ) = None,
    ):
        """
        Initialise lookup with the given dtype/strategy items.

        Parameters
        ----------
        items
            A dtype to strategy dict/mapping.
        """
        self._items = {}
        if items is not None:
            self._items.update(items)

    def __setitem__(
        self,
        item: PolarsDataType,
        value: SearchStrategy[Any] | Callable[..., SearchStrategy[Any]],
    ) -> None:
        """Add a dtype and its associated strategy to the lookup."""
        self._items[item] = value

    def __delitem__(self, item: PolarsDataType) -> None:
        """Remove the given dtype from the lookup."""
        del self._items[item]

    def __getitem__(self, item: PolarsDataType) -> SearchStrategy[Any]:
        """Retrieve a hypothesis strategy for the given dtype."""
        strat = self._items[item]

        # if the item is a scalar strategy, return it directly
        if isinstance(strat, SearchStrategy):
            return strat

        # instantiate nested strategies on demand, using the inner dtype.
        # if no inner dtype, a randomly selected dtype is assigned.
        return strat(inner_dtype=getattr(item, "inner", None))

    def __len__(self) -> int:
        """Return the number of items in the lookup."""
        return len(self._items)

    def __iter__(self) -> Iterator[PolarsDataType]:
        """Iterate over the lookup's dtype keys."""
        yield from self._items

    def __or__(self, other: StrategyLookup) -> StrategyLookup:
        """Create a new StrategyLookup from the union of this lookup and another."""
        return StrategyLookup().update(self).update(other)

    def update(self, items: StrategyLookup) -> Self:  # type: ignore[override]
        """Add new strategy items to the lookup."""
        self._items.update(items)
        return self


scalar_strategies: StrategyLookup = StrategyLookup(
    {
        Boolean: strategy_bool,
        Float32: strategy_f32,
        Float64: strategy_f64,
        Int8: strategy_i8,
        Int16: strategy_i16,
        Int32: strategy_i32,
        Int64: strategy_i64,
        UInt8: strategy_u8,
        UInt16: strategy_u16,
        UInt32: strategy_u32,
        UInt64: strategy_u64,
        Time: strategy_time,
        Date: strategy_date,
        Datetime("ns"): strategy_datetime_ns,
        Datetime("us"): strategy_datetime_us,
        Datetime("ms"): strategy_datetime_ms,
        # Datetime("ns", "*"): strategy_datetime_ns_tz,
        # Datetime("us", "*"): strategy_datetime_us_tz,
        # Datetime("ms", "*"): strategy_datetime_ms_tz,
        Datetime: strategy_datetime_us,
        Duration("ns"): strategy_duration,
        Duration("us"): strategy_duration,
        Duration("ms"): strategy_duration,
        Duration: strategy_duration,
        Categorical: strategy_categorical,
        String: strategy_string,
        Binary: strategy_binary,
    }
)
nested_strategies: StrategyLookup = StrategyLookup()


def _get_strategy_dtypes() -> list[PolarsDataType]:
    """Get a list of all the dtypes for which we have a strategy."""
    strategy_dtypes = list(chain(scalar_strategies.keys(), nested_strategies.keys()))
    return [tp.base_type() for tp in strategy_dtypes]


def _flexhash(elem: Any) -> int:
    """Hashing that also handles lists/dicts (for 'unique' check)."""
    if isinstance(elem, list):
        return hash(tuple(_flexhash(e) for e in elem))
    elif isinstance(elem, dict):
        return hash((_flexhash(k), _flexhash(v)) for k, v in elem.items())
    return hash(elem)


def create_array_strategy(
    inner_dtype: PolarsDataType | None = None,
    width: int | None = None,
    *,
    select_from: Sequence[Any] | None = None,
    unique: bool = False,
) -> SearchStrategy[list[Any]]:
    """
    Hypothesis strategy for producing polars Array data.

    Parameters
    ----------
    inner_dtype : PolarsDataType
        type of the inner array elements (can also be another Array).
    width : int, optional
        generated arrays will have this length.
    select_from : list, optional
        randomly select the innermost values from this list (otherwise
        the default strategy associated with the innermost dtype is used).
    unique : bool, optional
        ensure that the generated lists contain unique values.

    Examples
    --------
    Create a strategy that generates arrays of i32 values:

    >>> arr = create_array_strategy(inner_dtype=pl.Int32, width=3)
    >>> arr.example()  # doctest: +SKIP
    [-11330, 24030, 116]

    Create a strategy that generates arrays of specific strings:

    >>> arr = create_array_strategy(inner_dtype=pl.String, width=2)
    >>> arr.example()  # doctest: +SKIP
    ['xx', 'yy']
    """
    if width is None:
        width = randint(a=1, b=8)

    if inner_dtype is None:
        strats = list(_get_strategy_dtypes())
        shuffle(strats)
        inner_dtype = choice(strats)

    strat = create_list_strategy(
        inner_dtype=inner_dtype,
        select_from=select_from,
        size=width,
        unique=unique,
    )
    strat._dtype = Array(inner_dtype, width=width)  # type: ignore[attr-defined]
    return strat


def create_list_strategy(
    inner_dtype: PolarsDataType | None = None,
    *,
    select_from: Sequence[Any] | None = None,
    size: int | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    unique: bool = False,
) -> SearchStrategy[list[Any]]:
    """
    Hypothesis strategy for producing polars List data.

    Parameters
    ----------
    inner_dtype : PolarsDataType
        type of the inner list elements (can also be another List).
    select_from : list, optional
        randomly select the innermost values from this list (otherwise
        the default strategy associated with the innermost dtype is used).
    size : int, optional
        if set, generated lists will be of exactly this size (and
        ignore the min_size/max_size params).
    min_size : int, optional
        set the minimum size of the generated lists (default: 0 if unset).
    max_size : int, optional
        set the maximum size of the generated lists (default: 3 if
        min_size is unset or zero, otherwise 2x min_size).
    unique : bool, optional
        ensure that the generated lists contain unique values.

    Examples
    --------
    Create a strategy that generates a list of i32 values:

    >>> lst = create_list_strategy(inner_dtype=pl.Int32)
    >>> lst.example()  # doctest: +SKIP
    [-11330, 24030, 116]

    Create a strategy that generates lists of lists of specific strings:

    >>> lst = create_list_strategy(
    ...     inner_dtype=pl.List(pl.String),
    ...     select_from=["xx", "yy", "zz"],
    ... )
    >>> lst.example()  # doctest: +SKIP
    [['yy', 'xx'], [], ['zz']]

    Create a UInt8 dtype strategy as a hypothesis composite that generates
    pairs of small int values where the first is always <= the second:

    >>> from hypothesis.strategies import composite
    >>>
    >>> @composite
    ... def uint8_pairs(draw, uints=create_list_strategy(pl.UInt8, size=2)):
    ...     pairs = list(zip(draw(uints), draw(uints)))
    ...     return [sorted(ints) for ints in pairs]
    >>> uint8_pairs().example()  # doctest: +SKIP
    [(12, 22), (15, 131)]
    >>> uint8_pairs().example()  # doctest: +SKIP
    [(59, 176), (149, 149)]
    """
    if select_from and inner_dtype is None:
        msg = "if specifying `select_from`, must also specify `inner_dtype`"
        raise ValueError(msg)

    if inner_dtype is None:
        strats = list(_get_strategy_dtypes())
        shuffle(strats)
        inner_dtype = choice(strats)
    if size:
        min_size = max_size = size
    else:
        min_size = min_size or 0
        if max_size is None:
            max_size = 3 if not min_size else (min_size * 2)

    if inner_dtype in (Array, List):
        if inner_dtype == Array:
            if (width := getattr(inner_dtype, "width", None)) is None:
                width = randint(a=1, b=8)
            st = create_array_strategy(
                inner_dtype=inner_dtype.inner,  # type: ignore[union-attr]
                select_from=select_from,
                width=width,
            )
        else:
            st = create_list_strategy(
                inner_dtype=inner_dtype.inner,  # type: ignore[union-attr]
                select_from=select_from,
                min_size=min_size,
                max_size=max_size,
            )

        if inner_dtype.inner is None and hasattr(st, "_dtype"):  # type: ignore[union-attr]
            inner_dtype = st._dtype
    else:
        st = (
            sampled_from(list(select_from))
            if select_from
            else scalar_strategies[inner_dtype]
        )

    ls = lists(
        elements=st,
        min_size=min_size,
        max_size=max_size,
        unique_by=(_flexhash if unique else None),
    )
    ls._dtype = List(inner_dtype)  # type: ignore[attr-defined, arg-type]
    return ls


# TODO: strategy for Struct dtype.
# def create_struct_strategy(


nested_strategies[Array] = create_array_strategy
nested_strategies[List] = create_list_strategy
# nested_strategies[Struct] = create_struct_strategy(inner_dtype=None)

all_strategies = scalar_strategies | nested_strategies
