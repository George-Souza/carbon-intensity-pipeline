import logging

# Attributes that exist on every LogRecord. Anything else passed via `extra=`
# is appended to the line as key=value.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class KeyValueFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        if not extras:
            return base
        pairs = " ".join(f"{k}={v}" for k, v in extras.items())
        return f"{base} {pairs}"


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(KeyValueFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # httpx logs every request at INFO; the runner already logs one line per day.
    logging.getLogger("httpx").setLevel(logging.WARNING)
