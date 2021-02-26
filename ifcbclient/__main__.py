import argparse
import time

from .ifcbclient import IFCBClient


parser = argparse.ArgumentParser()
parser.add_argument('address')
parser.add_argument('id')
args = parser.parse_args()


client = IFCBClient(args.address, args.id)
client.on(('valuechanged',), lambda _, key, *values: print(f'{key} => {values}'))
client.connect()


while True:
    time.sleep(1)
