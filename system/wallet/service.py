import json
import os
from pathlib import Path

class InfoWallet:
    """
    A simple JSON-based Info Wallet that persists key-value pairs 
    tied to a specific user ID to the MIRA/wallets/ directory.
    """
    def __init__(self, user_id: str):
        self.user_id = user_id
        # MIRA root is two directories up from this file, assuming MIRA/system/wallet/service.py
        self.wallet_dir = Path(__file__).parent.parent.parent / "wallets"
        self.wallet_dir.mkdir(parents=True, exist_ok=True)
        self.wallet_file = self.wallet_dir / f"{user_id}.json"
        
        self.data: dict[str, str] = {}
        self._load()

    def _load(self):
        if self.wallet_file.exists():
            try:
                with open(self.wallet_file, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"Failed to load wallet for {self.user_id}: {e}")
                self.data = {}
        else:
            self.data = {}

    def _save(self):
        try:
            with open(self.wallet_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=4)
        except Exception as e:
            print(f"Failed to save wallet for {self.user_id}: {e}")

    def get_all(self) -> dict[str, str]:
        """Returns the full dictionary of stored data."""
        return self.data.copy()

    def set(self, key: str, value: str):
        """Updates a key and automatically writes back to the JSON file."""
        self.data[key] = value
        self._save()
        
    def format_for_prompt(self) -> str:
        """Helper to format the wallet into a string block for the agent prompt."""
        if not self.data:
            return "Wallet is currently empty."
        
        lines = []
        for key, value in self.data.items():
            lines.append(f"- {key}: {value}")
        return "\n".join(lines)
