from src.bot.crypto_bot import CryptoBot
from src.strats.bollinger_bands_strat import BollingerBandsStrat
from src.strats.stochastic_rsi_strat import StochasticRSIStrat
from src.exchange.exchange_factory import ExchangeFactory
import datetime
import os

BACKTESTING_START_DATE = datetime.datetime(2017, 1, 1)
BACKTESTING_END_DATE = datetime.datetime(2017, 8, 31)
BACKTESTING = os.getenv('BACKTESTING', 'FALSE')

if BACKTESTING == 'TRUE':
    btrx = ExchangeFactory().get_exchange()(BACKTESTING_START_DATE, BACKTESTING_END_DATE)
else:
    btrx = ExchangeFactory().get_exchange()()

MAJOR_TICK_SIZE = int(os.getenv('MAJOR_TICK_SIZE', 5))
SMA_WINDOW = 15
bb_options = {
    'active': True,
    'market_names': [],
    'num_standard_devs': 2,
    'sma_window': SMA_WINDOW,
    'sma_stat_key': 'last',
    'minor_tick': 1,
    'major_tick': MAJOR_TICK_SIZE
}
stoch_rsi_options = {
    'active': True,
    'market_names': [],
    'rsi_window': SMA_WINDOW,
    'sma_window': SMA_WINDOW,
    'stat_key': 'last',
    'minor_tick': 1,
    'major_tick': MAJOR_TICK_SIZE
}

strat1 = BollingerBandsStrat(bb_options)
strat2 = StochasticRSIStrat(stoch_rsi_options)

bot = CryptoBot([strat1, strat2], btrx)

# bot.get_balance('ETH')
# bot.get_balances()
# bot.get_order_history('ETH-BCC', 50)
# bot.BUY_instant('ETH-NEO', 0.01)
# bot.BUY_market('ETH-NEO', 0.01)
# bot.sell_market('ETH-NEO', 0.01)
# bot.collect_summaries()
# bot.rate_limiter_start()
# bot.rate_limiter_limit()

# bot.get_historical_data()
bot.run()
# bot.calculate_num_coins('buy', 'BTC-ETH', 1)
# bot.send_report('This is a test', 'TEST REPORT')
