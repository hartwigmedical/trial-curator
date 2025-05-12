import logging
import sys

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)-5s [%(module)s] - %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.INFO)
