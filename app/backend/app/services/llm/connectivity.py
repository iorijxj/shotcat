"""临时 LLM 配置里的连通性测试（web/ 顶栏「LLM 配置」保存前手动核对用）。

设计取舍：
- 文本：真实发一条最短对话请求，成本可忽略。
- 图片：真实调用一次图片生成，有真实生成成本——用户点击测试即视为知情同意。
- 视频：只提交生成任务、不轮询等待完成——视频生成通常要几十秒到几分钟，
  同步测试按钮等不起；测试结果只代表"供应商已接受请求"，不保证最终生成成功。

不对 base_url 做 SSRF 校验：Provider 配置本就是登录用户自己维护的可信配置，
和 resolver.py/image_task_runner.py 等既有生成链路一致（都直接拿 base_url 发请求，
不设内网校验）；只有"供应商返回的动态 URL"才过 ssrf_guard（见 utils/files.py），
这里加了反而在企业代理/内网 DNS 改写场景下把正常域名解析结果误判成内网地址。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException

from app.core.contracts.image_generation import ImageGenerationInput
from app.core.contracts.provider import ProviderConfig
from app.core.contracts.video_generation import VideoGenerationInput
from app.core.integrations.openai.images import OpenAIImageApiAdapter
from app.core.integrations.openai.video import OpenAIVideoApiAdapter
from app.core.integrations.volcengine.images import VolcengineImageApiAdapter
from app.core.integrations.volcengine.video import VolcengineVideoApiAdapter
from app.models.llm import ModelCategoryKey
from app.services.llm.provider_registry import get_provider_spec

_TEST_TIMEOUT_S = 30.0
_TEST_PROMPT = "connectivity test, reply with a single word OK"

_IMAGE_ADAPTERS = {"openai": OpenAIImageApiAdapter(), "volcengine": VolcengineImageApiAdapter()}
_VIDEO_ADAPTERS = {"openai": OpenAIVideoApiAdapter(), "volcengine": VolcengineVideoApiAdapter()}


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    ok: bool
    message: str


def _short_error(e: Exception) -> str:
    text = str(e).strip() or e.__class__.__name__
    return text if len(text) <= 300 else text[:300] + "…"


async def test_provider_connection(
    *,
    provider_key: str,
    base_url: str,
    api_key: str,
    category: ModelCategoryKey,
    model_name: str,
) -> ConnectionTestResult:
    """按类别真实探测一次；任何供应商侧/网络异常都转成失败结果而非抛 500。"""
    spec = get_provider_spec(provider_key)
    if category not in spec.supported_categories:
        return ConnectionTestResult(ok=False, message=f"{spec.display_name} 不支持 {category.value} 类别")
    if spec.requires_api_key and not api_key.strip():
        return ConnectionTestResult(ok=False, message="缺少 API Key")
    if not model_name.strip():
        return ConnectionTestResult(ok=False, message="缺少模型名称")

    effective_base_url = (base_url or spec.default_base_url or "").strip()
    if not effective_base_url:
        return ConnectionTestResult(ok=False, message="缺少 Base URL")

    try:
        if category == ModelCategoryKey.text:
            return await _test_text(
                provider_key=provider_key, base_url=effective_base_url, api_key=api_key, model_name=model_name
            )
        if category == ModelCategoryKey.image:
            return await _test_image(
                provider_key=provider_key, base_url=effective_base_url, api_key=api_key, model_name=model_name
            )
        return await _test_video(
            provider_key=provider_key, base_url=effective_base_url, api_key=api_key, model_name=model_name
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - 供应商侧任意异常都要转成失败结果，不能让测试接口 500
        return ConnectionTestResult(ok=False, message=_short_error(e))


async def _test_text(*, provider_key: str, base_url: str, api_key: str, model_name: str) -> ConnectionTestResult:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise HTTPException(status_code=503, detail="Install langchain-openai to test text models") from e

    kwargs: dict = {
        "model": model_name,
        "api_key": api_key,
        "base_url": base_url,
        "request_timeout": _TEST_TIMEOUT_S,
        "max_tokens": 8,
    }
    if not (provider_key == "openai" and model_name.startswith("gpt-5")):
        kwargs["temperature"] = 0
    llm = ChatOpenAI(**kwargs)
    result = await llm.ainvoke(_TEST_PROMPT)
    reply = (getattr(result, "content", "") or "").strip()
    return ConnectionTestResult(ok=True, message=f"连通成功，模型回复：{reply[:80] or '(空)'}")


async def _test_image(*, provider_key: str, base_url: str, api_key: str, model_name: str) -> ConnectionTestResult:
    adapter = _IMAGE_ADAPTERS.get(provider_key)
    if adapter is None:
        return ConnectionTestResult(ok=False, message=f"{provider_key} 暂不支持图片连通性测试")
    cfg = ProviderConfig(provider=provider_key, api_key=api_key, base_url=base_url)
    inp = ImageGenerationInput(prompt=_TEST_PROMPT, model=model_name, n=1)
    result = await adapter.generate(cfg=cfg, inp=inp, timeout_s=_TEST_TIMEOUT_S)
    return ConnectionTestResult(ok=True, message=f"连通成功，已生成 {len(result.images)} 张测试图片")


async def _test_video(*, provider_key: str, base_url: str, api_key: str, model_name: str) -> ConnectionTestResult:
    adapter = _VIDEO_ADAPTERS.get(provider_key)
    if adapter is None:
        return ConnectionTestResult(ok=False, message=f"{provider_key} 暂不支持视频连通性测试")
    cfg = ProviderConfig(provider=provider_key, api_key=api_key, base_url=base_url)
    inp = VideoGenerationInput(prompt=_TEST_PROMPT, model=model_name, ratio="16:9")
    if provider_key == "openai":
        task_id = await adapter.create_video(cfg=cfg, input_=inp, timeout_s=_TEST_TIMEOUT_S)
    else:
        task_id = await adapter.create_contents_task(cfg=cfg, input_=inp, timeout_s=_TEST_TIMEOUT_S)
    return ConnectionTestResult(
        ok=True,
        message=f"已提交测试任务（task_id={task_id}），供应商已接受请求；视频生成耗时较长，未等待最终完成",
    )
