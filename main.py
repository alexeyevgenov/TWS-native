import argparse
import hashlib
import random
from tws import TWS


def create_id_from_params(args):
    value = args.action + args.symbol + str(args.quantity) + str(args.price) + str(args.type)
    s = (random.randint(1, 1000000) + int(hashlib.sha1(value.encode('utf-8')).hexdigest(), 16)) % (10 ** 8)
    return s


def parse_args():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description='TWS place order tool')

    parser.add_argument('action', type=str, default=None, choices=['buy', 'sell'],
                        help='Order action')
    parser.add_argument('symbol', type=str, default=None, help='futures symbol code like MESM0')
    parser.add_argument('-p', '--price', required=True, type=float, help='Entry order price')
    parser.add_argument('-s', '--stop', required=True, type=float, help='Stop order price')
    parser.add_argument('-q', '--quantity', required=False, type=int, default=1, help='order quantity')
    parser.add_argument('-t', '--type', required=False, type=str, default='LMT',
                        choices=['LMT', 'STP', 'STP LMT', 'MKT'], help='order type')
    parser.add_argument('-a', '--above', required=False, type=float, help='Place order if price above specified level')
    parser.add_argument('-b', '--below', required=False, type=float, help='Place order if price below specified level')

    return parser.parse_args()


def main():
    args = parse_args()
    tws = TWS("127.0.0.1", 7497, create_id_from_params(args))
    if tws.connected:
        contract_details = tws.get_contract(args.symbol)
        if contract_details is not None:
            if args.above is not None or args.below is not None:
                tws.await_price(contract_details.contract, args.above, args.below)

            tws.place_order(contract_details.contract, args.action, args.type, args.quantity, args.price, args.stop)

        tws.stop()


if __name__ == '__main__':
    main()
