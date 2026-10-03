"""DeepSeek 单次同步适配器；秘密仅进入固定地址的认证头。"""

import hashlib
import json
from collections.abc import Callable
from http.client import HTTPException
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import TypeAdapter, ValidationError

from ..contracts.analysis import TokenUsage
from ..contracts.common import Identifier
from .types import (
    ModelCallRequest,
    ModelCallResponse,
    ModelCapabilities,
    ModelGatewayError,
    ModelRuntimeIdentity,
)

_IDENTIFIER = TypeAdapter(Identifier)


class _NoRedirect(HTTPRedirectHandler):
    # 禁止认证头随供应商重定向进入另一地址。
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


def _transport(request: Request, timeout: int) -> bytes:
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        return bytes(response.read())


class DeepSeekModelGateway:
    """固定 deepseek-flash 身份，不猜测或伪造供应商模型修订。"""

    def __init__(
        self,
        *,
        api_key: str,
        max_input_tokens_per_call: int,
        transport: Callable[[Request, int], bytes] | None = None,
    ) -> None:
        if (
            not isinstance(api_key, str)
            or not api_key
            or any(not 33 <= ord(character) <= 126 for character in api_key)
            or type(max_input_tokens_per_call) is not int
            or max_input_tokens_per_call <= 0
        ):
            raise ModelGatewayError("invalid_configuration", retryable=False) from None
        self._api_key = api_key
        self._max_input_tokens_per_call = max_input_tokens_per_call
        self._transport = transport if transport is not None else _transport

    @property
    def identity(self) -> ModelRuntimeIdentity:
        return ModelRuntimeIdentity(model_name="deepseek-flash", model_revision=None)

    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities(
            supports_text=True,
            supports_structured_output=True,
            supports_images=False,
            max_input_tokens_per_call=self._max_input_tokens_per_call,
        )

    def invoke(self, request: ModelCallRequest) -> ModelCallResponse:
        if (
            request.model_name != "deepseek-flash"
            or request.model_revision is not None
            or request.scenario_id is not None
            or not 0 < request.max_output_tokens <= 393216
        ):
            raise ModelGatewayError("invalid_configuration", retryable=False) from None
        # system 仅由受控块组成；外部数据与修复指令保持独立 user 消息。
        system = [block.content for block in request.content_blocks if block.kind == "system"]
        system.append("Return the requested result as a JSON object.")
        messages = [{"role": "system", "content": "\n\n".join(system)}]
        messages.extend(
            {"role": "user", "content": block.content}
            for block in request.content_blocks
            if block.kind != "system"
        )
        try:
            body = json.dumps(
                {
                    "model": "deepseek-flash",
                    "messages": messages,
                    "thinking": {"type": "disabled"},
                    "response_format": {"type": "json_object"},
                    "stream": False,
                    "max_tokens": request.max_output_tokens,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            wire_request = Request(
                "https://api.deepseek.com/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            raw = self._transport(wire_request, request.timeout_seconds)
        except HTTPError as error:
            if error.code in {401, 403}:
                raise ModelGatewayError("authentication_failed", retryable=False) from None
            if error.code == 429:
                raise ModelGatewayError("rate_limited", retryable=True) from None
            if error.code in {408, 504}:
                raise ModelGatewayError("timeout", retryable=True) from None
            if 500 <= error.code <= 599:
                raise ModelGatewayError("transport_error", retryable=True) from None
            raise ModelGatewayError("invalid_configuration", retryable=False) from None
        except TimeoutError:
            raise ModelGatewayError("timeout", retryable=True) from None
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise ModelGatewayError("timeout", retryable=True) from None
            raise ModelGatewayError("transport_error", retryable=True) from None
        except (OSError, HTTPException):
            raise ModelGatewayError("transport_error", retryable=True) from None
        except (ValueError, TypeError, UnicodeError):
            raise ModelGatewayError("invalid_configuration", retryable=False) from None

        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            name = _IDENTIFIER.validate_python(data["model"])
            choices = data["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError
            choice = choices[0]
            if not isinstance(choice, dict):
                raise ValueError
            message = choice["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError
            content = message["content"]
            reason = choice["finish_reason"]
            if not isinstance(content, str) or not isinstance(reason, str):
                raise ValueError
            normalized_reason: Literal["stop", "length", "content_filter", "unknown"]
            if reason == "stop":
                normalized_reason = "stop"
            elif reason == "length":
                normalized_reason = "length"
            elif reason == "content_filter":
                normalized_reason = "content_filter"
            else:
                normalized_reason = "unknown"
            fingerprint = data.get("system_fingerprint")
            if fingerprint is not None:
                fingerprint = _IDENTIFIER.validate_python(fingerprint)
            usage_data = data.get("usage")
            usage = None
            if usage_data is not None:
                if not isinstance(usage_data, dict):
                    raise ValueError
                usage = TokenUsage(
                    input_tokens=usage_data["prompt_tokens"],
                    output_tokens=usage_data["completion_tokens"],
                    total_tokens=usage_data["total_tokens"],
                )
            return ModelCallResponse(
                payload=content,
                model_name=name,
                model_revision=None,
                finish_reason=normalized_reason,
                usage=usage,
                response_fingerprint=hashlib.sha256(raw).hexdigest(),
                provider_system_fingerprint=fingerprint,
            )
        except (ValueError, TypeError, KeyError, UnicodeError, ValidationError, RecursionError):
            raise ModelGatewayError("invalid_configuration", retryable=False) from None
