import datetime
from time import mktime

import pandas as pd
import psycopg2
from psycopg2.extensions import AsIs

from src.utils.logger import Logger

log = Logger(__name__)


class PostgresConnection:
    def __init__(self):
        self.conn = None
        self.cur = None

    def _exec_query(self, query, params):
        ###
        # EXECUTES A QUERY ON THE DATABASE, RETURNS NO DATA
        ###
        self.conn = psycopg2.connect("dbname=cryptobot user=patrickmckelvy")
        self.cur = self.conn.cursor()
        try:
            self.cur.execute(query, params)
        except Exception as e:
            log.error('*** POSTGRES ERROR ***')
            log.error(e)

        self.conn.commit()
        self.cur.close()
        self.conn.close()

    def _fetch_query(self, query, params):
        ###
        # EXECUTES A FETCHING QUERY ON THE DATABASE, RETURNS A DATAFRAME
        ###
        self.conn = psycopg2.connect("dbname=cryptobot user=patrickmckelvy")
        self.cur = self.conn.cursor()
        result = None
        try:
            self.cur.execute(query, params)
            column_names = [desc[0] for desc in self.cur.description]
            result = pd.DataFrame(self.cur.fetchall(), columns=column_names)
        except Exception as e:
            log.error('*** POSTGRES ERROR ***')
            log.error(e)

        self.conn.commit()
        self.cur.close()
        self.conn.close()
        return result

    def save_trade(self, order_type, market, quantity, rate, uuid):
        log.info('== SAVE trade ==')
        fmt_str = "('{order_type}','{market}',{quantity},{rate},'{uuid}','{base_currency}','{market_currency}','{timestamp}')"
        columns = "(order_type, market, quantity, rate, uuid, base_currency, market_currency, timestamp)"
        timestamp = datetime.datetime.now()
        market_currencies = market.split('-')
        values = {
            "order_type": order_type,
            "market": market,
            "quantity": quantity,
            "rate": rate,
            "uuid": uuid,
            "base_currency": market_currencies[0],
            "market_currency": market_currencies[1],
            "timestamp": timestamp
        }
        params = {
            "columns": AsIs(columns),
            "values": fmt_str.format(**values)
        }
        query = """ INSERT INTO orders (%(columns)s) VALUES %(values)s; """
        self._exec_query(query, params)

    def save_summaries(self, summaries):
        log.info('== SAVE market summaries ==')
        fmt_str = "({prevday},{volume},{last},{opensellorders},'{timestamp}',{bid},'{created}',{openbuyorders},{high},'{marketname}',{low},{ask},{basevolume},{saved_timestamp})"
        columns = "prevday,volume,last,opensellorders,timestamp,bid,created,openbuyorders,high,marketname,low,ask,basevolume,saved_timestamp,"
        values = AsIs(','.join(fmt_str.format(**summary.loc[0]) for summary in summaries))
        params = {
            "columns": AsIs(columns),
            "values": values
        }
        query = """ INSERT INTO summaries (%(columns)s) VALUES %(values)s; """
        self._exec_query(query, params)

    def save_historical_data(self, data):
        log.info('== SAVE historical data ==')
        fmt_str = '(%s,%s,%s,%s,%s,%s,%s,%s)'
        columns = 'timestamp,open,high,low,close,volume_btc,volume_usd,weighted_price'
        values = AsIs(','.join(fmt_str % tuple(row) for row in data))
        params = {
            "columns": AsIs(columns),
            "values": values
        }
        query = """ INSERT INTO btc_historical (%(columns)s) VALUES %(values)s ; """
        self._exec_query(query, params)

    def get_historical_data(self, start_date, end_date):
        log.info('== GET historical data ==')
        params = {
            "start_date": mktime(start_date.timetuple()),
            "end_date": mktime(end_date.timetuple())
        }
        query = """
            SELECT open, high, low, close, volume_btc, volume_usd, timestamp FROM btc_historical
            WHERE timestamp >= %(start_date)s AND timestamp < %(end_date)s
            ORDER BY timestamp ASC ;
        """
        return self._fetch_query(query, params)

    def get_market_summaries_by_timestamp(self, target_timestamp):
        log.info('== GET market summaries ==')
        params = {
            'target_timestamp': target_timestamp
        }
        query = """
            SELECT marketname, last, bid, ask, saved_timestamp FROM summaries
            WHERE saved_timestamp = {target_timestamp} ;
        """
        return self._fetch_query(query, params)