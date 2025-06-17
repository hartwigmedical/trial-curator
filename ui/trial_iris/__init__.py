import sys
import os
import logging

logging.basicConfig(stream=sys.stdout,
                    format='%(asctime)s %(levelname)-5s [%(module)s] - %(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.INFO)

# Add parent directory (or wherever pydantic_curator is) to Python path
sys.path.append(os.path.join(os.path.dirname(__file__), '../..'))
# Or use absolute path: sys.path.append('/absolute/path/to/directory/containing/pydantic_curator')
