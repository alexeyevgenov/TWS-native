from threading import Thread
import logging
import time
import os
import portalocker

from ibapi.client import EClient
from ibapi.wrapper import EWrapper, iswrapper
from ibapi.contract import Contract, ContractDetails
from ibapi.order import Order
from ibapi.common import TickerId, TickAttrib
from ibapi.ticktype import TickType, TickTypeEnum

logging.basicConfig(format='%(asctime)s  %(levelname)s: %(message)s')


class DataRequests:
    def __init__(self, req_id: int):
        self.req_id = req_id
        self.data_ready = False
        self.data = None


class TWS(EClient, EWrapper):
    def __init__(self, ip_address, port, client_id):
        EWrapper.__init__(self)
        EClient.__init__(self, wrapper=self)

        self._logger = logging.getLogger(__class__.__name__)
        self._logger.setLevel(logging.DEBUG)

        self._connected = False
        self._connecting = True
        self._thread = None

        self._request_id = 1000
        self._requests = {}

        self._logger.info(f'Connecting to TWS {ip_address}:{port} ({client_id})')
        self.connect(ip_address, port, client_id)
        self._thread = Thread(target=self.run, daemon=True)
        self._thread.start()

        while self._connecting:
            time.sleep(0.05)

    @property
    def connected(self):
        return self._connected

    def stop(self):
        if self._thread is not None:
            self.disconnect()
            self._thread.join()
            self._logger.info("TWS disconnected")

    @iswrapper
    def error(self, ticker_id: int, error_code: int, error_string: str):
        self._logger.info(f'TWS message. Id: {ticker_id} Code: {error_code}, Msg: {error_string}')

        if error_code in [502, 504]:
            self._connected = False
            self._connecting = False

    @iswrapper
    def connectAck(self):
        self._logger.info('TWS Connected!')

    @iswrapper
    def nextValidId(self, order_id: int):
        self.get_order_id(order_id)
        self._connected = True
        self._connecting = False

    @iswrapper
    def contractDetails(self, req_id: int, contract_details: ContractDetails):
        if req_id in self._requests:
            self._requests[req_id].data = contract_details

    @iswrapper
    def contractDetailsEnd(self, req_id: int):
        if req_id in self._requests:
            self._requests[req_id].data_ready = True
            self._requests.pop(req_id, None)

    def get_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def create_request(self):
        request = DataRequests(self.get_request_id())
        self._requests[request.req_id] = request
        return request

    def get_contract(self, symbol: str) -> ContractDetails:
        contract = Contract()
        contract.secType = 'FUT'
        contract.currency = 'USD'
        contract.exchange = 'GLOBEX'  # todo: it works for MES, but other futures could have another exchange
        contract.localSymbol = symbol

        self._logger.info(f'Request contract details for {contract.localSymbol}{contract.symbol}')
        request = self.create_request()
        self.reqContractDetails(request.req_id, contract)
        while not request.data_ready:
            time.sleep(0.05)

        return request.data

    def get_order_id(self, id=None) -> int:
        path = '.order_id'
        timeout = 10
        last_id = 0
        if os.path.exists(path):
            with portalocker.Lock(path, 'r+', timeout=timeout) as file:
                try:
                    last_id = int(file.read()) + 1
                except Exception:
                    pass
                if id is not None and last_id < id:
                    last_id = id
                file.truncate(0)
                file.seek(0)
                file.write(str(last_id))
                file.flush()
                os.fsync(file.fileno())
        else:
            if id is not None and last_id < id:
                last_id = id
            with portalocker.Lock(path, 'w+', timeout=timeout) as lock_file:
                lock_file.write(str(last_id))
                lock_file.flush()
                os.fsync(lock_file.fileno())
        return last_id

    def place_order(self, contract: Contract, action: str, order_type: str, quantity: int, price: float, stop: float):
        action = action.upper()
        order_type = order_type.upper()

        parent_order_id = self.get_order_id()
        entry_order = Order()
        entry_order.action = action
        entry_order.orderType = order_type
        if 'LMT' in order_type:
            entry_order.lmtPrice = price
        if 'STP' in order_type:
            entry_order.auxPrice = price
        entry_order.totalQuantity = quantity
        entry_order.transmit = False

        stop_order = Order()
        stop_order.parentId = parent_order_id
        stop_order.action = 'SELL' if action == 'BUY' else 'BUY'
        stop_order.orderType = "STP"
        stop_order.totalQuantity = quantity
        stop_order.auxPrice = stop
        stop_order.transmit = True

        self.placeOrder(parent_order_id, contract, entry_order)
        self.placeOrder(self.get_order_id(), contract, stop_order)

    def await_price(self, contract: Contract, above_price: float, below_price: float):
        self._logger.info(f'Subscribe market data for {contract.localSymbol}')
        request = self.create_request()
        request.data = None
        self.reqMktData(request.req_id, contract, "", False, False, [])

        if above_price is not None and below_price is not None:
            self._logger.info(f'Awaiting price above {above_price} or below {below_price}')
        elif above_price is not None:
            self._logger.info(f'Awaiting price above {above_price}')
        else:
            self._logger.info(f'Awaiting price below {below_price}')

        while True:
            if (request.data is not None and
                    ((above_price is not None and request.data >= above_price) or
                     (below_price is not None and request.data <= below_price))):
                break
            time.sleep(0.05)
        self._logger.info(f'price condition is ok, current market price {request.data}')

        self.cancelMktData(request.req_id)

    @iswrapper
    def tickPrice(self, req_id: TickerId, tick_type: TickType, price: float, attrib: TickAttrib):
        if tick_type == TickTypeEnum.LAST and req_id in self._requests:
            self._requests[req_id].data = price
