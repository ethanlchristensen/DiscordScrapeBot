import logging
import os
from dataclasses import dataclass, field, fields
from typing import Literal, TypeVar, get_args, get_origin

import yaml

logger = logging.getLogger("bot.config")

T = TypeVar("T")

@dataclass
class Config:
    discordToken: str = ""
    mongoUri: str = ""
    adminId: int = ""


class ConfigService:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = config_path
        self.config: Config | None = None

    def load(self) -> Config:
        """Load and validate configuration from YAML file."""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}. Please copy config.sample.yaml to config.yaml and configure it.")

        with open(self.config_path) as file:
            raw_config = yaml.safe_load(file)

        self.config = self._parse_dataclass(Config, raw_config)
        self._validate_config(self.config)
        logger.info(f"Configuration loaded successfully from {self.config_path}")
        return self.config

    def _parse_dataclass(self, cls: type[T], data: dict | None) -> T:
        """Recursively parse a dictionary into a dataclass instance."""
        if data is None:
            return cls()

        kwargs = {}
        for field_info in fields(cls):
            field_name = field_info.name
            field_type = field_info.type
            field_value = data.get(field_name)

            # Skip if value is not provided and field has a default
            if field_value is None:
                continue

            # Handle nested dataclasses
            origin = get_origin(field_type)

            # Handle Optional types (Union with None)
            if origin is type(None) or (origin is type(field_type) and type(None) in get_args(field_type)):
                args = get_args(field_type)
                if args:
                    # Get the non-None type
                    inner_type = next((arg for arg in args if arg is not type(None)), None)
                    if inner_type and hasattr(inner_type, "__dataclass_fields__"):
                        kwargs[field_name] = self._parse_dataclass(inner_type, field_value)
                    else:
                        kwargs[field_name] = field_value
                else:
                    kwargs[field_name] = field_value
            elif hasattr(field_type, "__dataclass_fields__"):
                # Direct dataclass field
                kwargs[field_name] = self._parse_dataclass(field_type, field_value)
            else:
                # Primitive types
                kwargs[field_name] = field_value

        return cls(**kwargs)

    def _validate_config(self, config: Config):
        """Validate the loaded configuration."""
        if not config.discordToken:
            raise ValueError("discordToken is missing or empty in the configuration.")

        if not config.mongoUri:
            raise ValueError("mongoUri is missing in the configuration.")

        if not config.adminId:
            raise ValueError("adminId is missing in the configuration.")

    def get_config(self) -> Config:
        """Get the loaded configuration."""
        if self.config is None:
            raise RuntimeError("Configuration not loaded. Call load() first.")
        return self.config


# Singleton instance
_config_service: ConfigService | None = None


def get_config_service(config_path: str = "config.yaml") -> ConfigService:
    """Get or create the ConfigService singleton."""
    global _config_service
    if _config_service is None:
        _config_service = ConfigService(config_path)
    return _config_service
