import logging
import sys

def setup_logging(stream=None, log_level=logging.INFO):
    logger = logging.getLogger('system')
    if logger.hasHandlers():
        return logger
    logger.handlers = []

    class MIRAFormatter(logging.Formatter):

        def format(self, record):
            if isinstance(record.name, str) and record.name.startswith('system.'):
                parts = record.name.split('.')
                if len(parts) >= 2:
                    record.name = parts[-1]
            return super().format(record)
    console = logging.StreamHandler(stream or sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(MIRAFormatter('%(levelname)-8s [%(name)s] %(message)s'))
    logger.addHandler(console)
    logger.setLevel(log_level)
    logger.propagate = False
    third_party_loggers = ['playwright', 'asyncio', 'openai', 'httpx', 'httpcore']
    for logger_name in third_party_loggers:
        third_party = logging.getLogger(logger_name)
        third_party.setLevel(logging.ERROR)
        third_party.propagate = False
    return logger