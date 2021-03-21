'''
monitor prices of the selected cryptocurrencies and send telegram notifications
if the price changes by the specified margin
'''


from kraken_wsclient_py import kraken_wsclient_py
import telegram
import requests
import json


class CryptoNoot:

    def __init__(self):
        self.assets = ['ETH/USD', 'BTC/USD']
        self.margin = .005
        self.contact = 'REDACTED_CHAT_ID'
        self.bot_token = 'REDACTED_TOKEN'
        self.price_log = './prices.log'
        self.initialize_prices()

    def initialize_prices(self):
        try:
            self.log_price = json.load(open(self.price_log, 'r'))
            assert all(ticker in self.log_price for ticker in self.assets)
        except:
            self.log_price = {}
            url = 'https://api.kraken.com/0/public/Ticker?pair='
            for ticker in self.assets:
                price = requests.get(url + ticker).json()['result'][ticker]['c'][0]
                self.log_price[ticker] = float(price)
            json.dump(self.log_price, open(self.price_log, 'w+'))

    def start_websocket(self):
        client = kraken_wsclient_py.WssClient()
        client.start()
        client.subscribe_public(subscription={'name':'ticker'},
            pair=self.assets, callback=self.process_event)

    def process_event(self, event):
        try:
            ticker = event[3]
            price = float(event[1]['c'][0])
            self.check_price(ticker, price)
        except:
            pass

    def check_price(self, ticker, price):
        change = abs(price / self.log_price[ticker] - 1)
        if change > self.margin:
            direction = 'up' if price > self.log_price[ticker] else 'down'
            self.update_price(ticker, price)
            self.send_nootification(f'{ticker} is {direction} {100*change:.2f}% to {price:.2f}')

    def update_price(self, ticker, price):
        self.log_price[ticker] = price
        json.dump(self.log_price, open(self.price_log, 'w+'))

    def send_nootification(self, message):
        telegram.Bot(self.bot_token).send_message(self.contact, str(message))


if __name__ == '__main__':
    CryptoNoot().start_websocket()

