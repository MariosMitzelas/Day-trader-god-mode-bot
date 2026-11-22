"""Multi-account routing."""
import json, logging, os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

log = logging.getLogger("accounts")

@dataclass(frozen=True)
class Account:
    name: str
    symbols: tuple
    telegram_chat_id: str = None
    slack_webhook: str = None
    discord_webhook: str = None
    extra: dict = field(default_factory=dict)

    def matches(self, symbol):
        return any(fnmatch(symbol, p) for p in self.symbols)

class AccountRegistry:
    def __init__(self, accounts):
        if not accounts:
            raise ValueError("At least one account required.")
        self.accounts = accounts
        self._default = next((a for a in accounts if "*" in a.symbols), None)

    def resolve(self, symbol):
        upper = symbol.upper()
        for a in self.accounts:
            if a.name != "default" and a.matches(upper):
                return a
        return self._default or self.accounts[0]

    def all(self):
        return list(self.accounts)

def send_telegram_to(chat_id, message):
    from bot import send_telegram
    old = os.environ.get("TELEGRAM_CHAT_ID")
    os.environ["TELEGRAM_CHAT_ID"] = chat_id
    try:
        send_telegram(message); return True
    except Exception as e:
        log.error("Telegram failed: %s", e); return False
    finally:
        if old is None: os.environ.pop("TELEGRAM_CHAT_ID", None)
        else: os.environ["TELEGRAM_CHAT_ID"] = old

def send_slack(url, message):
    from urllib.request import Request, urlopen
    import json as _j
    try:
        req = Request(url, _j.dumps({"text": message}).encode(),
                      {"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as r: return r.status == 200
    except Exception as e:
        log.error("Slack failed: %s", e); return False

def send_discord(url, message):
    from urllib.request import Request, urlopen
    import json as _j
    try:
        req = Request(url, _j.dumps({"content": message[:1990]}).encode(),
                      {"Content-Type": "application/json"})
        with urlopen(req, timeout=10) as r: return 200 <= r.status < 300
    except Exception as e:
        log.error("Discord failed: %s", e); return False

def deliver_to_account(account, message):
    status = {}
    if account.telegram_chat_id:
        status["telegram"] = send_telegram_to(account.telegram_chat_id, message)
    if account.slack_webhook:
        status["slack"] = send_slack(account.slack_webhook, message)
    if account.discord_webhook:
        status["discord"] = send_discord(account.discord_webhook, message)
    return status
