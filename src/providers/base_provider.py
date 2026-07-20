from abc import ABC, abstractmethod
import pandas as pd
from typing import List, Optional

class BaseProvider(ABC):
    @abstractmethod
    def fetch_historical_data(self, symbol: str, start_date: Optional[str] = None) -> pd.DataFrame:
        """
        Fetches historical daily data for a given symbol.
        If start_date is provided (format 'YYYY-MM-DD'), fetches from that date to today.
        Otherwise, fetches all available history (from listing).
        """
        pass

    @abstractmethod
    def fetch_symbols(self, market: str) -> List[str]:
        """
        Fetches or returns a list of symbols for the given market.
        """
        pass
