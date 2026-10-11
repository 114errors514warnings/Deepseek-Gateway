"""多 provider 抽象（V2）。

V1 只支持 DeepSeek。V2 把上游抽成 Provider 对象，任何 OpenAI 兼容后端都能接入，
调用方通过请求头 `X-Gateway-Provider`（或请求体 `provider` 字段）选择，未指定则用默认 provider。
"""
from __future__ import annotations

from dataclasses import dataclass

from .settings import Settings


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    api_key: str
    default_model: str
    responses_path: str = "/responses"
    chat_path: str = "/chat/completions"

    def responses_url(self) -> str:
        return f"{self.base_url}{self.responses_path}"

    def chat_url(self) -> str:
        return f"{self.base_url}{self.chat_path}"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url)


def load_providers(settings: Settings) -> dict[str, Provider]:
    registry: dict[str, Provider] = {}
    for name, cfg in settings.providers().items():
        registry[name] = Provider(
            name=name,
            base_url=str(cfg.get("base_url", "")).rstrip("/"),
            api_key=str(cfg.get("api_key", "")),
            default_model=str(cfg.get("default_model", settings.default_model)),
            responses_path=str(cfg.get("responses_path", "/responses")),
            chat_path=str(cfg.get("chat_path", "/chat/completions")),
        )
    if settings.default_provider not in registry:
        # 默认 provider 未配置时回退 deepseek，避免启动即崩
        registry.setdefault("deepseek", registry.get("deepseek"))
    return {k: v for k, v in registry.items() if v is not None}


def resolve_provider(registry: dict[str, Provider], name: str | None) -> Provider | None:
    if name:
        return registry.get(name)
    return registry.get("deepseek") or next(iter(registry.values()), None)
