"""Base class for all factors."""

from abc import ABC, abstractmethod

import pandas as pd


class FactorBase(ABC):
    """Abstract base class for factors.

    All factors MUST inherit this class and implement the `calculate` method.
    """

    # Subclasses should override these
    factor_name: str = ""
    description: str = ""
    description_cn: str = ""
    category: str = "其他"

    @abstractmethod
    def calculate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate factor values and add as a new column.

        Args:
            df: DataFrame with at least [trade_date, ts_code, close] columns.

        Returns:
            Input DataFrame with a new column named after self.factor_name.
        """
        ...

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.factor_name}>"


# Factor registry
_FACTOR_REGISTRY: dict[str, type[FactorBase]] = {}


def register_factor(cls: type[FactorBase]) -> type[FactorBase]:
    """Decorator to register a factor class."""
    _FACTOR_REGISTRY[cls.factor_name] = cls
    return cls


def get_registered_factors() -> dict[str, type[FactorBase]]:
    """Return all registered factor classes."""
    return _FACTOR_REGISTRY.copy()


def get_factor(name: str) -> FactorBase:
    """Instantiate a registered factor by name."""
    if name not in _FACTOR_REGISTRY:
        raise ValueError(f"Factor '{name}' not registered. Available: {list(_FACTOR_REGISTRY.keys())}")
    return _FACTOR_REGISTRY[name]()
