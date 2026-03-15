import json
from pathlib import Path

from pydantic import BaseModel


class RuntimeCredentials(BaseModel):
    username: str
    password: str
    access_token: str
    refresh_token: str


class SecureRuntimeStorage:
    def __init__(self, runtime_home: Path):
        self.runtime_home = runtime_home
        self.path = runtime_home / "credentials.json"

    def save_credentials(
        self,
        *,
        username: str,
        password: str,
        access_token: str,
        refresh_token: str,
    ) -> None:
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "username": username,
                    "password": password,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                },
                indent=2,
            )
        )

    def load_credentials(self) -> RuntimeCredentials:
        return RuntimeCredentials.model_validate_json(self.path.read_text())
