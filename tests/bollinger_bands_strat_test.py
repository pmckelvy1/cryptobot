from src.strats.bollinger_bands_strat import BollingerBandsStrat
import pandas as pd
from pandas.util.testing import assert_series_equal
from fixtures.processed_summary_tickers_fixture import PROCESSED_SUMMARY_TICKERS_FIXTURE
import os
import datetime

os.environ['BACKTESTING'] = 'True'

bb_options = {
    'name': 'BollingerBands',
    'active': True,
    'plot_overlay': True,
    'num_standard_devs': 2,
    'sma_window': 5,
    'stat_key': 'last',
    'window': 5
}


class TestBBStrat:
    def setup_class(self):
        self.strat = BollingerBandsStrat(bb_options)

    def teardown_class(self):
        self.strat = None

    def test_calc_bollinger_bands(self):
        result = self.strat.calc_bollinger_bands(PROCESSED_SUMMARY_TICKERS_FIXTURE)
        expected_result = pd.Series(
            {'ask': 2.2, 'bid': 2.2, 'last': 2.2, 'marketname': 'BTC-LTC',
             'saved_timestamp': datetime.datetime(2017, 1, 1, 1, 20, 1), 'SMA': 2.0, 'STDDEV': 0.158114,
             'UPPER_BB': 2.316228, 'LOWER_BB': 1.683772},
            index=['LOWER_BB', 'SMA', 'STDDEV', 'UPPER_BB', 'ask', 'bid', 'last', 'marketname', 'saved_timestamp'],
            name=4
        )
        assert_series_equal(result.iloc[4], expected_result)
