import os
import datetime
import json
import urllib
from datetime import timedelta
from time import sleep
import pandas as pd
from bs4 import BeautifulSoup
from src.db.psql import PostgresConnection
from src.utils.utils import is_valid_market, normalize_inf_rows_dicts, add_saved_timestamp, normalize_index, calculate_base_currency_volume, is_valid_pair
from src.utils.logger import Logger
from src.exceptions import LargeLossError, TradeFailureError, InsufficientFundsError, MixedTradeError, MissingTickError
from src.utils.reporter import Reporter
from src.utils.plotter import Plotter
from src.exchange.exchange_adaptor import ExchangeAdaptor
from src.exchange.coinmarketcap.core import Market
from src.exchange.coinmarketcap import coinmarketcap_usd_history

MAX_CURRENCY_PER_BUY = {
    'BTC': .2,
    'ETH': 2
}

log = Logger(__name__)

MAJOR_TICK_SIZE = int(os.getenv('MAJOR_TICK_SIZE', 5))
EXECUTE_TRADES = os.getenv('EXECUTE_TRADES', 'FALSE') == 'TRUE'
BACKTESTING = os.getenv('BACKTESTING', 'FALSE') == 'TRUE'
ORDER_BOOK_DEPTH = 20
REQUIRE_STRAT_CONSENSUS = os.getenv('REQUIRE_STRAT_CONSENSUS', 'FALSE') == 'TRUE'
SEND_REPORTS = os.getenv('SEND_REPORTS', 'FALSE') == 'TRUE'
TARGET_PAIR_TICKERS = os.getenv('TARGET_PAIR_TICKERS', 'FALSE') == 'TRUE'


class CryptoBot:
    def __init__(self, strats, exchange):
        log.info('Initializing bot...')
        self.psql = PostgresConnection()
        self.ex = ExchangeAdaptor()
        self.strats = strats['v1_strats']
        self.index_strats = strats['index_strats']
        self.btrx = exchange
        self.trade_functions = {'buy': self.buy_limit, 'sell': self.sell_limit}
        self.base_coins = ['btc']
        # self.tradeable_markets = self.init_tradeable_markets()
        self.active_currencies = {}
        self.tradeable_currencies = dict((m[4:], True) for m in os.getenv('TRADEABLE_MARKETS', 'BTC-LTC').split(','))
        self.volume_thresholds = {'BTC': 5000, 'ETH': 50000}
        self.completed_trades = {}
        # self.rate_limit = datetime.timedelta(0, 60, 0)
        self.rate_limit = datetime.timedelta(minutes=5)
        self.api_tick = datetime.datetime.now()
        self.currencies = []
        self.compressed_tickers = {}
        self.tickers = {}
        # self.init_markets()
        self.pairs_to_watch = []
        self.accounts = []
        self.tick = 0
        self.major_tick = 0
        self.reporter = Reporter()
        self.plotter = Plotter()
        self.exchange = 'gemini'
        self.exchange_pairs = {}
        self.balances = self.get_exchange_balances()
        self.valid_mkt_coins = None
        self.valid_base_coins = None
        self.init_pairs()
        self.cmc = Market()
        self.cmc_data = pd.DataFrame()
        self.cmc_api_tick = datetime.datetime.now()
        self.cmc_rate_limit = datetime.timedelta(minutes=5)
        self.hist_cmc_data = {}
        self.nonce = 0
        log.info('...bot successfully initialized')

    def init_pairs(self):
        self.exchange_pairs[self.exchange] = self.get_exchange_pairs()
        self.init_valid_base_coins()
        self.init_valid_mkt_coins()
        for p, pair in self.exchange_pairs[self.exchange].items():
            if self.is_valid_pair(pair):
                self.compressed_tickers[p] = pd.DataFrame()
                self.tickers[p] = pd.DataFrame()
        for strat in self.strats:
            strat.init_market_positions(self.exchange_pairs[self.exchange])

    @staticmethod
    def init_valid_coins(coin_type):
        env_valid_coins = os.getenv(coin_type, 'ALL')
        if env_valid_coins == 'ALL':
            return env_valid_coins
        else:
            return dict((m, True) for m in env_valid_coins.split(','))

    def init_valid_base_coins(self):
        self.valid_base_coins = self.init_valid_coins('VALID_BASE_COINS')

    def init_valid_mkt_coins(self):
        self.valid_mkt_coins = self.init_valid_coins('VALID_MKT_COINS')

    def is_valid_pair(self, pair):
        return (self.valid_base_coins == 'ALL' or self.valid_base_coins[pair['base_coin']]) and \
               (self.valid_mkt_coins == 'ALL' or self.valid_mkt_coins[pair['mkt_coin']])

    def run(self):
        if BACKTESTING:
            self.run_test()
        else:
            self.run_prod()

    def run_prod(self):
        log.info('* * * ! * * * BEGIN PRODUCTION RUN * * * ! * * *')
        self.timer_reset()
        while (True):
            # self.rate_limiter_limit()
            self.tick_step()

    def run_test(self):
        log.info('* * * ! * * * BEGIN TEST RUN * * * ! * * *')
        while self.tick < 1970:
            # if self.tick == 10:
            #     self.enable_volume_threshold()
            self.tick_step()

        self.cash_out()
        self.analyze_performance()

    def run_collect_cmc(self):
        self.rate_limiter_reset()
        while True:
            if self.rate_limiter_limit():
                self.collect_cmc_data()
                self.rate_limiter_reset()

    def run_test_cmc(self):
        BACKTESTING = True
        while True:
            self.get_cmc_tickers()
            self.generate_cmc_index()

    def kill(self):
        log.warning('* * * ! * * * SHUTTING DOWN BOT * * * ! * * *')
        raise Exception

    def send_report(self, subj, body):
        if SEND_REPORTS:
            self.reporter.send_report(subj, body)

    # # QUANT # #

    def tick_step(self):
        self.minor_tick_step()
        if self.check_major_tick():
            log.info('MAJOR TICK')
            self.major_tick_step()
            self.execute_trades()

    def minor_tick_step(self):
        # start = datetime.datetime.now()

        self.increment_minor_tick()
        if TARGET_PAIR_TICKERS:
            # get the ticker for all the markets
            for p, pair in self.exchange_pairs[self.exchange].items():
                ticker = self.get_current_pair_ticker(pair)
                self.tickers[pair['pair']] = self.tickers[pair['pair']].append(ticker, ignore_index=True)
        else:
            tickers = self.get_current_tickers()
            for ticker in tickers:
                self.tickers[ticker['pair']] = self.tickers[ticker['pair']].append(ticker, ignore_index=True)

        # end = datetime.datetime.now()
        # log.info('MINOR TICK STEP ' + str(self.tick) + ' runtime :: ' + str(end - start))

    def major_tick_step(self):
        # start = datetime.datetime.now()

        # pickup cmc tickers
        if self.cmc_timer_check():
            self.get_cmc_tickers()
            self.generate_cmc_index()
            self.save_cmc_data()

        self.increment_major_tick()
        self.compress_tickers()
        for strat in self.strats:
            log.info(strat.name + ' :: handle_data')
            for mkt_name, mkt_data in self.compressed_tickers.items():
                self.compressed_tickers[mkt_name] = strat.handle_data(mkt_data, mkt_name)
        self.generate_report()

        # end = datetime.datetime.now()
        # log.info('MAJOR TICK STEP runtime :: ' + str(end - start))

    def generate_cmc_index(self):
        self.index_strats[0].handle_data(self.cmc_data)

    def save_cmc_data(self):
        add_columns = []
        for strat in self.index_strats:
            add_columns += strat.add_columns
        self.psql.save_cmc_tickers(self.cmc_data, [])
        self.nonce += 1

    def compress_tickers(self):
        # start = datetime.datetime.now()

        for mkt_name, mkt_data in self.tickers.items():
            # mkt_data = mkt_data.drop(mkt_data.index[-MAJOR_TICK_SIZE:])
            agg_funcs = {
                'open': ['first'],
                'high': ['max'],
                'low': ['min'],
                'close': ['last'],
                'bid': ['last'],
                'last': ['last'],
                'ask': ['last'],
                'pair': ['last'],
                'vol_base': ['sum'],
                'vol_mkt': ['sum']
            }
            if BACKTESTING:
                agg_funcs['saved_timestamp'] = ['last']
            mkt_data = mkt_data.groupby('pair').agg(agg_funcs)
            mkt_data.columns = mkt_data.columns.droplevel(1)
            self.compressed_tickers[mkt_name] = self.compressed_tickers[mkt_name].append(mkt_data, ignore_index=True)
            self.tickers[mkt_name] = pd.DataFrame()

        # end = datetime.datetime.now()
        # log.info('COMPRESS TICKERS runtime :: ' + str(end - start))

    def enable_volume_threshold(self):
        log.info('* * * ! * * * VOLUME THRESHOLD ENABLED * * * ! * * *')
        for mkt_name, mkt_data in self.compressed_tickers.items():
            currency = mkt_name.split('-')[1]
            if not self.check_volume_threshold(mkt_data, mkt_name) and currency in self.active_currencies:
                del self.active_currencies[currency]
        log.info('REMAINING CURRENCIES ::\n')
        for mkt_name in self.active_currencies:
            log.info(mkt_name + "\n")

    # # RATE LIMITER # #

    def rate_limiter_reset(self):
        self.api_tick = datetime.datetime.now()

    def rate_limiter_check(self):
        current_tick = datetime.datetime.now()
        return (current_tick - self.api_tick) < self.rate_limit

    def rate_limiter_limit(self):
        if not BACKTESTING:
            current_tick = datetime.datetime.now()
            if self.rate_limiter_check():
                sleep_for = self.rate_limit - (current_tick - self.api_tick)
                log.warning('Rate Limit :: sleeping for ' + str(sleep_for) + ' seconds')
                sleep(sleep_for.seconds)
                self.rate_limiter_reset()

    def cmc_timer_reset(self):
        self.cmc_api_tick = datetime.datetime.now()

    def cmc_timer_check(self):
        log.info('CHECKING TIMER')
        current_tick = datetime.datetime.now()
        return (current_tick - self.cmc_api_tick) < self.cmc_rate_limit

    # # TICKER # #

    def increment_minor_tick(self):
        self.tick += 1

    def increment_major_tick(self):
        self.major_tick += 1

    def check_major_tick(self):
        return (self.tick % MAJOR_TICK_SIZE) == 0

    # # MARKET # #

    def get_exchange_pairs(self):
        log.debug('{BOT} == GET exchange pairs ==')
        return self.ex.get_exchange_pairs(self.exchange)

    def get_current_tickers(self):
        log.debug('{BOT} == GET current pair ticker ==')
        return self.ex.get_current_tickers(self.exchange)

    def get_current_pair_ticker(self, pair):
        log.debug('{BOT} == GET current pair ticker ==')
        return self.ex.get_current_pair_ticker(self.exchange, pair)

    def get_order_book(self, pair, side):
        log.debug('{BOT} == GET order book ==')
        return self.ex.get_order_book(self.exchange, pair=pair, side=side)

    def cancel_order(self, order_id, pair):
        log.debug('{BOT} == CANCEL order ==')
        return self.ex.cancel_order(self.exchange, order_id=order_id, pair=pair)

    def get_current_timestamp(self):
        log.debug('{BOT} == GET current timestamp ==')
        if BACKTESTING:
            return self.ex.get_current_timestamp(self.exchange)
        else:
            return datetime.datetime.now()

    def get_cmc_tickers(self):
        log.debug('{BOT} == GET cmc tickers ==')
        try:
            if BACKTESTING:
                self.cmc_data = self.psql.pull_cmc_tickers(self.nonce)
            else:
                self.cmc_data = self.cmc_data.append(pd.DataFrame(self.cmc.ticker(limit=250, convert='USD')))
                self.cmc_data.set_value('nonce', self.nonce)
                self.cmc_timer_reset()
        except Exception as e:
            log.error(str(e))

    # # ORDERS # #

    def calculate_num_coins(self, pair, order_type, quantity):
        """Calculates the QUANTITY for a trade
            -   if the order_type is 'buy', input parameter quantity is an amount of the base_currency to spend
            -   if the order_type is 'sell', input parameter quantity is a pct of the market_currency to sell
            return: num_mkt_coin, num_base_coin

            an InsufficientFundsError will be raised in the event that there is not enough of the desired
            base currency for a 'buy' order
        """
        idx = len(self.compressed_tickers[pair['pair']].index)
        rate = self.compressed_tickers[pair['pair']].loc[idx, 'last']
        if order_type == 'buy':
            base_coin = pair['base_coin']
            balance = self.get_balance(base_coin)
            if balance['balance'] >= quantity:
                num_mkt_coin = round(quantity / rate, 8)
                return num_mkt_coin, quantity
            else:
                msg = 'Not enough ' + base_coin + ' to complete this trade'
                raise InsufficientFundsError(balance, pair['base_coin'], quantity, rate, msg)
        else:
            mkt_coin = pair['mkt_coin']
            balance = self.get_balance(mkt_coin)
            num_mkt_coin = round(balance['balance'] * quantity, 8)
            num_base_coin = num_mkt_coin * rate
            return num_mkt_coin, num_base_coin

    def calculate_order_rate(self, pair, order_type, quantity, order_book_depth=20):
        """Calculates the RATE for a trade

            gets the order_book for the desired market and adds up the available coins within
            the desired price range. the returned rate is the rate of the deepest level of the order book
            for the integrated quantity of open orders in the specified book.
        """
        if order_type == 'buy':
            book_type = 'asks'
        elif order_type == 'sell':
            book_type = 'bids'
        else:
            book_type = 'both'
        order_book = self.get_order_book(pair, book_type)
        current_total = 0
        rate = 0
        # calculate an instant price
        for order in order_book[book_type]:
            current_total += order['amount']
            rate = order['price']
            if current_total >= quantity:
                break
        return rate

    def buy_limit(self, amount, price, pair):
        return self.ex.buy_limit(self.exchange, amount=amount, price=price, pair=pair)

    def sell_limit(self, amount, price, pair):
        return self.ex.sell_limit(self.exchange, amount=amount, price=price, pair=pair)

    def buy_instant(self, pair, amount):
        log.debug('{BOT} == BUY instant ==')
        self.trade_instant('buy', pair, amount)

    def sell_instant(self, pair, amount):
        log.debug('{BOT} == SELL instant ==')
        self.trade_instant('sell', pair, amount)
        self.complete_sell(pair)

    def trade_instant(self, order_type, pair, amount):
        try:
            # first calculate the number of coins involved in the trade
            num_mkt_coin, num_base_coin = self.calculate_num_coins(pair, order_type, amount)

            # next calculate the real rate to be paid based on the number of coins
            rate = self.calculate_order_rate(pair, order_type, num_mkt_coin, ORDER_BOOK_DEPTH)

            if self.can_buy(pair, num_base_coin):
                trade_resp = self.trade_functions[order_type](num_mkt_coin, rate, pair)
                if trade_resp and not isinstance(trade_resp, str):
                    self.trade_success(order_type, pair, num_mkt_coin, rate, trade_resp['order_id'])
                    return trade_resp
                else:
                    log.info(trade_resp)
                    return None
            else:
                log.info('not enough ' + pair['base_coin'] + "")
        except TradeFailureError:
            return None

    def trade_cancel(self, order_id):
        log.debug('{BOT} == CANCEL bid ==')
        try:
            trade_resp = self.cancel_order(order_id)
            self.psql.save_trade('CANCEL', 'market', 0, 0, trade_resp['order_id'])
            return trade_resp
        except Exception as e:
            log.error("*** !!! TRADE FAILED !!! ***")
            log.error(e)
            return None

    def trade_success(self, order_type, market, quantity, rate, order_id):
        timestamp = datetime.datetime.now()
        if BACKTESTING:
            timestamp = self.get_current_timestamp()
        trade_data = self.psql.save_trade(order_type, market, quantity, rate, order_id, timestamp)
        if market in self.completed_trades:
            self.completed_trades[market] = self.completed_trades[market].append(pd.Series(trade_data), ignore_index=True)
        else:
            self.completed_trades[market] = pd.DataFrame([trade_data])
        log.info('*** ' + order_type.upper() + ' Successful! ***')
        log.info("""
            market: """ + market + """
            quantity: """ + str(quantity) + """
            rate: """ + str(rate) + """
            trade id: """ + str(order_id))

    def should_buy(self, mkt_name, require_strat_consensus):
        if require_strat_consensus:
            for strat in self.strats:
                if not strat.should_buy(mkt_name):
                    return False
            return True
        else:
            for strat in self.strats:
                if strat.should_buy(mkt_name):
                    return True
            return False

    def should_sell(self, mkt_name, require_strat_consensus):
        if require_strat_consensus:
            for strat in self.strats:
                if not strat.should_sell(mkt_name):
                    return False
            return True
        else:
            for strat in self.strats:
                if strat.should_sell(mkt_name):
                    return True
            return False

    def execute_trades(self):
        for p, pair in self.exchange_pairs[self.exchange].items():
            if self.should_buy(pair, REQUIRE_STRAT_CONSENSUS) and self.can_spend(pair):
                self.buy_instant(pair, MAX_CURRENCY_PER_BUY[pair['base_coin']])
            elif self.should_sell(pair, REQUIRE_STRAT_CONSENSUS) and self.can_sell(pair):
                self.sell_instant(pair, 1)

    def complete_sell(self, market):
        currencies = market.split('-')
        base_currency = currencies[0]
        market_currency = currencies[1]
        mkt_trade_data = self.completed_trades[market]
        tail = mkt_trade_data.tail(2).reset_index(drop=True)
        net_gain, net_gain_pct, hold_time = self.calculate_net(tail, base_currency, market_currency)
        log_details = {
            'base_currency': base_currency,
            'market_currency': market_currency,
            'net_gain': net_gain,
            'net_gain_pct': net_gain_pct,
            'hold_time': hold_time}
        log.info(""""
            *** SELL details :\n\t
            Net Gain: {net_gain} {base_currency}, {net_gain_pct}%\n\t
            Hold Time: {hold_time}
            """.format(**log_details))
        if net_gain_pct <= -25:
            msg = """"{market_currency} Net Loss: {net_gain} {base_currency}, {net_gain_pct}%\n""".format(**log_details)
            raise LargeLossError(log_details, msg)

    def calculate_net(self, trade_data, sell_base_currency, market_currency):
        # TODO handle different buy and sell currencies
        try:
            buy_base_currency = trade_data.loc[0, 'base_currency']
            if sell_base_currency != buy_base_currency:
                raise MixedTradeError(buy_base_currency, sell_base_currency, market_currency)
            coin_in = trade_data.loc[0, 'quantity'] * trade_data.loc[0, 'rate']
            # KeyError: 'the label [1] is not in the [index]'
            coin_out = trade_data.loc[1, 'quantity'] * trade_data.loc[1, 'rate']
            net_gain = coin_out - coin_in
            net_gain_pct = 100 * net_gain / coin_in
            hold_time = trade_data.loc[1, 'timestamp'] - trade_data.loc[0, 'timestamp']
            return net_gain, net_gain_pct, hold_time
        except Exception as e:
            log.error(e)
            return 0, 0, 0

    # # ACCOUNT # #

    def can_sell(self, pair):
        balance = self.get_balance(pair['mkt_coin'])
        print("CAN SELL current " + pair['pair'] + " balance : " + str(balance['balance']))
        return balance['balance'] > 0

    def can_buy(self, pair, amt):
        """
            calculates if <base_coin> balance is high enough to spend <amt>
        :param pair:
        :param amt:
        :return:
        """
        balance = self.get_balance(pair['base_coin'])
        print("CAN BUY current " + pair['pair'] + " balance : " + str(balance['balance']))
        return balance['balance'] >= amt

    def can_spend(self, pair):
        balance = self.get_balance(pair['base_coin'])
        print("CAN BUY current " + pair['pair'] + " balance : " + str(balance['balance']))
        return balance['balance'] > 0

    def get_exchange_balances(self):
        log.debug('{BOT} == GET exchange balances ==')
        return self.ex.get_exchange_balances(exchange=self.exchange)

    def get_balance(self, coin):
        log.debug('{BOT} == GET balance ==')
        return self.ex.get_coin_balance(exchange=self.exchange, coin=coin)

    def get_historical_trades(self, pair):
        log.debug('{BOT} == GET order history ==')
        history = self.ex.get_historical_trades(self.exchange, pair=pair)
        return history


    # # MARKET DATA COLLECTOR # #

    def collect_order_books(self):
        order_books = {}
        for ex, pair in self.exchange_pairs.items():
            order_books[pair['pair']] = []
        self.rate_limiter_reset()
        while True:
            for p, pair in self.exchange_pairs[self.exchange].items():
                market_name = pair['mkt_coin']
                log.info('Collecting order book for: ' + market_name)
                order_book = self.get_order_book(market_name, 'both', 50)
                order_books[market_name].append(order_book)
            self.rate_limiter_limit()

    def collect_summaries(self):
        self.rate_limiter_reset()
        while True:
            try:
                self.increment_minor_tick()
                summaries = self.get_current_tickers()
                summaries = add_saved_timestamp(summaries, self.tick)
                self.psql.save_summaries(summaries)
                self.rate_limiter_limit()
            except Exception as e:
                log.error(e)
                self.reporter.send_report('Collect OrderBooks Failure', type(e).__name__ + ' :: ' + e.message)

    def collect_markets(self):
        markets = self.get_exchange_pairs()
        self.psql.save_markets(markets)

    def collect_currencies(self):
        currencies = self.get_exchange_pairs()
        results = []
        for exchange, pair in currencies.items():
            currency_data = normalize_index(pd.Series(pair))
            results.append(currency_data)
        self.psql.save_currencies(results)

    def collect_cmc_data(self):
        log.info("GATHERING CMC DATA")
        self.get_cmc_tickers()
        self.save_cmc_data()
        self.cmc_data = pd.DataFrame()

    def collect_historical_cmc_data(self):
        self.get_cmc_tickers()
        coins = self.cmc_data['id'].values
        for coin in coins:
            hist_data = coinmarketcap_usd_history.main([coin, '2017-01-01', '2018-03-30', '--dataframe'])
            self.psql.save_cmc_historical_data(hist_data)

    # # BACKTESTING TOOLS # #

    # TODO refactor backtesting to work w/ generally
    #       save all normalized exchange data to database, use for backtesting

    # def analyze_performance(self):
    #     self.plot_market_data()
    #     starting_balances = self.btrx.get_starting_balances()
    #     current_balances = self.get_exchange_balances()
    #     log.info('*** PERFORMANCE RESULTS ***')
    #     for currency in starting_balances:
    #         if currency not in self.tradeable_currencies and currency not in self.base_coins:
    #             continue
    #         start = starting_balances[currency]['balance']
    #         end = current_balances[currency]['balance']
    #         log_statement = currency + ' :: ' + 'Start = ' + str(start) + ' , End = ' + str(end)
    #         if currency in self.base_coins:
    #             log_statement += '% diff   :: ' + str((end - start) / start)
    #         log.info(log_statement)

    def cash_out(self):
        log.info('*** CASHING OUT ***')
        current_balances = self.get_exchange_balances()
        for p, pair in self.exchange_pairs[self.exchange].items():
            cur_balance = current_balances[pair['mkt_coin']]['balance']
            # TODO add logic to optimize and get the best return (eth vs btc)
            if cur_balance > 0:
                self.trade_instant('sell', pair, 1)

    def plot_market_data(self):
        for market, trades in self.completed_trades.items():
            self.plotter.plot_market(market, self.compressed_tickers[market], trades, self.strats)

    def generate_report(self):
        if SEND_REPORTS and self.major_tick >= 50:
            self.reporter.generate_report(self.strats, self.exchange_pairs, self.compressed_tickers)

    def check_volume_threshold(self, mkt_data, mkt_name):
        base_currency = mkt_name.split('-')[0]
        vol_threshold = self.volume_thresholds[base_currency]
        recent_volume = mkt_data.loc[len(mkt_data) - 1, 'volume']
        recent_rate = mkt_data.loc[len(mkt_data) - 1, 'last']
        recent_base_currency_trade_volume = calculate_base_currency_volume(recent_volume, recent_rate)
        return recent_base_currency_trade_volume >= vol_threshold
