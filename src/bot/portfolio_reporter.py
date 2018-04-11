import pandas as pd

from src.exceptions import BotError, BadMathError
from src.exchange.exchange_adaptor import ExchangeAdaptor
from src.utils.conversion_utils import calculate_cost_average, get_usd_rate
from src.utils.utils import calculate_base_value, get_past_date
from src.data_structures.historical_prices import HistoricalRates
from src.db.psql import PostgresConnection
from src.exchange.exchange_adaptor import ExchangeAdaptor
from src.utils.logger import Logger
log = Logger(__name__)


class PortfolioReporter(ExchangeAdaptor):
    def __init__(self, exchanges):
        ExchangeAdaptor.__init__(self)
        self.pg = PostgresConnection()
        self.p_report = {}
        self.prev_daily_report, self.prev_daily_assets = self.get_prev_daily()
        self.prev_weekly_report, self.prev_weekly_assets = self.get_prev_weekly()
        # self.initial_investments = self.get_initial_investments()
        """
            {'LTC': <DataFrame>, 'XLM': <DataFrame>, ...}
        """
        self.aggregate_portfolio = pd.DataFrame()
        """
            {'LTCBTC': 0.023, 'XLMETH': 0.0093, ...}
        """
        self.current_exchange_rates = pd.DataFrame
        self.historical_rates = HistoricalRates()
        self.ex = ExchangeAdaptor()
        self.exchanges = exchanges
        self.coin_rates = pd.DataFrame()

    def get_initial_investments(self):
        return self.pg.get_initial_investments()

    def generate_p_report(self):
        self.get_aggregate_exchange_balances()
        self.get_coin_rates()
        self.calculate_portfolio_totals()
        self.calculate_percentage_holdings()
        self.save_p_report()

    def get_prev_daily(self):
        return self.pg.get_full_report(get_past_date(1))

    def get_prev_weekly(self):
        return self.pg.get_full_report(get_past_date(7))

    def get_coin_rates(self):
        for ex in self.exchanges:
            self.coin_rates = self.coin_rates.append(pd.DataFrame(self.ex.get_current_tickers(ex, False)))
        # add row for 1:1 bitcoin
        self.coin_rates = self.coin_rates.append(pd.DataFrame([{'base_coin': 'BTC', 'mkt_coin': 'BTC', 'last': 1.0}]))

        self.coin_rates['base_coin'] = self.coin_rates['base_coin'].str.upper()
        self.coin_rates['mkt_coin'] = self.coin_rates['mkt_coin'].str.upper()
        self.coin_rates = self.coin_rates[['base_coin', 'mkt_coin', 'last']]
        self.coin_rates = self.coin_rates[self.coin_rates['base_coin'] == 'BTC']
        agg_funcs = {'last': ['mean'], 'base_coin': ['last']}
        self.coin_rates = self.coin_rates.groupby('mkt_coin').agg(agg_funcs)
        self.coin_rates.columns = self.coin_rates.columns.droplevel(1)

    def get_aggregate_exchange_balances(self):
        log.debug('{PORTFOLIO REPORTER} == agg exchange balances ==')
        self.aggregate_portfolio = pd.DataFrame()
        for ex in self.exchanges:
            self.aggregate_portfolio = self.aggregate_portfolio.append(pd.DataFrame(self.ex.get_exchange_balances(exchange=ex)))
        self.p_report['total_coins'] = len(self.aggregate_portfolio)
        self.aggregate_portfolio.drop(columns='address', inplace=True)

    def calculate_portfolio_totals(self):
        btc_usd_rate = self.get_btc_usd_rate()
        # eth_usd_rate = self.get_eth_usd_rate()

        agg_funcs = {'balance': ['sum']}
        self.aggregate_portfolio = self.aggregate_portfolio.groupby('coin').agg(agg_funcs)
        self.aggregate_portfolio.columns = self.aggregate_portfolio.columns.droplevel(1)

        self.aggregate_portfolio = pd.merge(self.coin_rates, self.aggregate_portfolio, how='outer', left_index=True, right_index=True)
        self.aggregate_portfolio['btc_balance'] = self.aggregate_portfolio['last'] * self.aggregate_portfolio['balance']
        self.aggregate_portfolio['usd_balance'] = self.aggregate_portfolio['btc_balance'] * btc_usd_rate
        est_btc = self.aggregate_portfolio['btc_balance'].sum()
        # est_eth = self.aggregate_portfolio['est_eth'].sum()

        est_usd_btc = calculate_base_value(est_btc, btc_usd_rate)
        # est_usd_eth = calculate_base_value(est_eth, eth_usd_rate)

        self.p_report['est_btc'] = est_btc
        # self.p_report['est_eth'] = est_eth
        self.p_report['est_usd'] = max(est_usd_btc, 0)

        if self.prev_daily_report is not None and not self.prev_daily_report.empty:
            self.p_report = self.calculate_daily_changes(self.p_report, self.prev_daily_report)
        if self.prev_weekly_report is not None and not self.prev_weekly_report.empty:
            self.p_report = self.calculate_weekly_changes(self.p_report, self.prev_weekly_report)
        self.calculate_rois()

    def pull_all_trade_data_from_exchanges(self):
        """
            gets all historical trade data from exchanges and saves them to local db
        :return: None
        """
        trade_data = self.get_all_historical_trade_data('binance')
        self.pg.save_historical_trade_data(trade_data)

    def run_full_trade_analysis(self):
        all_trade_data = self.pg.get_all_trade_data()
        # TODO: group dataframe by market_currency, loop thru groups and analyze_trades(currency_group)
        # TODO: update trade data in database

    def analyze_trades(self, trade_data):
        """
            run through trade data to calculate cost_averages
        :param asset_data: dict
        :param trade_data:  dataframe
        :return:
        """
        total_coins = 0
        cost_avg_btc = 0
        cost_avg_eth = 0
        cost_avg_usd = 0
        rate_btc = 0
        rate_eth = 0

        for idx, trade_row in trade_data.iterrows():
            if trade_row['analyzed']:
                cost_avg_btc = trade_row['cost_avg_btc']
                cost_avg_eth = trade_row['cost_avg_eth']
                cost_avg_usd = trade_row['cost_avg_usd']
                if trade_row['trade_direction'] == 'buy':
                    total_coins += trade_row['quantity']
                elif trade_row['trade_direction'] == 'sell':
                    total_coins -= trade_row['quantity']
                    if total_coins < 0:
                        raise BadMathError('calculate_cost_averages')
                continue

            else:
                num_new_coins = trade_row['quantity']
                # calculate cost averages
                if trade_row['trade_direction'] == 'buy':
                    cost_avg_btc = calculate_cost_average(total_coins, cost_avg_btc, num_new_coins, trade_row['rate_btc'])
                    cost_avg_eth = calculate_cost_average(total_coins, cost_avg_eth, num_new_coins, trade_row['rate_eth'])
                    cost_avg_usd = calculate_cost_average(total_coins, cost_avg_usd, num_new_coins, trade_row['rate_usd'])
                    total_coins += num_new_coins
                elif trade_row['trade_direction'] == 'sell':
                    total_coins -= num_new_coins
                else:
                    raise BotError('trade_direction should be either "buy" or "sell"')

                # calculate rates, needs to pull data from exchanges
                # TODO: refactor to pull all data before this function?
                base_currency_usd_rates = self.get_historical_usd_vs_btc_eth_rates(trade_row['trade_time'])
                coin_exchange_rates = self.get_historical_coin_vs_btc_eth_rates(trade_row['trade_time'], trade_row['exchange_id'], trade_row['market_currency'])
                if trade_row['base_currency'] == 'btc':
                    rate_btc = trade_row['rate']
                    rate_eth = coin_exchange_rates['eth']
                if trade_row['base_currency'] == 'eth':
                    rate_btc = coin_exchange_rates['btc']
                    rate_eth = trade_row['rate']
                rate_usd, rate_base_currency = get_usd_rate({'eth': rate_eth, 'btc': rate_btc}, base_currency_usd_rates)

                # set all calculated values
                trade_data.set_value(idx, 'cost_avg_btc', cost_avg_btc)
                trade_data.set_value(idx, 'cost_avg_eth', cost_avg_eth)
                trade_data.set_value(idx, 'cost_avg_usd', cost_avg_usd)
                trade_data.set_value(idx, 'rate_btc', rate_btc)
                trade_data.set_value(idx, 'rate_eth', rate_eth)
                trade_data.set_value(idx, 'rate_usd', rate_usd)
                trade_data.set_value(idx, 'analyzed', True)

        return trade_data

    def calculate_daily_changes(self, asset_data, asset_data_t_minus_1):
        return self.calculate_time_changes(asset_data, asset_data_t_minus_1)

    def calculate_weekly_changes(self, asset_data, asset_data_t_minus_7):
        return self.calculate_time_changes(asset_data, asset_data_t_minus_7)

    def calculate_portfolio_rois(self, portfolio_data, initial_investments):
        return self.calculate_time_changes(portfolio_data, initial_investments)

    @staticmethod
    def calculate_time_changes(data, data_t_minus):
        data['est_btc_change_daily'] = data['est_btc'] - data_t_minus['est_btc']
        data['est_btc_pct_change_daily'] = data['est_btc_change_daily'] / data_t_minus['est_btc']

        data['est_eth_change_daily'] = data['est_eth'] - data_t_minus['est_eth']
        data['est_eth_pct_change_daily'] = data['est_eth_change_daily'] / data_t_minus['est_eth']

        data['est_usd_change_daily'] = data['est_usd'] - data_t_minus['est_usd']
        data['est_usd_pct_change_daily'] = data['est_usd_change_daily'] / data_t_minus['est_usd']

        return data

    @staticmethod
    def calculate_asset_rois(data):
        data['current_roi_btc'] = data['est_btc'] - (data['total_holdings'] * data['cost_avg_btc'])
        data['current_roi_pct_btc'] = data['current_roi_btc'] / data['est_btc']

        data['current_roi_eth'] = data['est_eth'] - (data['total_holdings'] * data['cost_avg_eth'])
        data['current_roi_pct_eth'] = data['current_roi_eth'] / data['est_eth']

        data['current_roi_usd'] = data['est_usd'] - (data['total_holdings'] * data['cost_avg_usd'])
        data['current_roi_pct_usd'] = data['current_roi_usd'] / data['est_usd']

        return data

    def calculate_percentage_holdings(self):
        for idx, row in self.aggregate_portfolio.iterrows():
            {
                "symbol": "LTCBTC",
                "price": "4.00000200"
            },
            tickers = self.get_all_tickers(exchange)

    def save_p_report(self):
        self.pg.save_p_report(self.p_report)

