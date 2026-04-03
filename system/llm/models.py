import os
from typing import TYPE_CHECKING
from system.llm.google.chat import ChatGoogle
from system.llm.openai.chat import ChatOpenAI
if TYPE_CHECKING:
    from system.llm.base import BaseChatModel
openai_gpt_4o: 'BaseChatModel'
openai_gpt_4o_mini: 'BaseChatModel'
openai_gpt_4_1_mini: 'BaseChatModel'
openai_o1: 'BaseChatModel'
openai_o1_mini: 'BaseChatModel'
openai_o1_pro: 'BaseChatModel'
openai_o3: 'BaseChatModel'
openai_o3_mini: 'BaseChatModel'
openai_o3_pro: 'BaseChatModel'
openai_o4_mini: 'BaseChatModel'
openai_gpt_5: 'BaseChatModel'
openai_gpt_5_mini: 'BaseChatModel'
openai_gpt_5_nano: 'BaseChatModel'
google_gemini_2_0_flash: 'BaseChatModel'
google_gemini_2_0_pro: 'BaseChatModel'
google_gemini_2_5_pro: 'BaseChatModel'
google_gemini_2_5_flash: 'BaseChatModel'
google_gemini_2_5_flash_lite: 'BaseChatModel'

def get_llm_by_name(model_name: str):
    if not model_name:
        raise ValueError('Model name cannot be empty')
    parts = model_name.split('_', 1)
    if len(parts) < 2:
        raise ValueError(f"Invalid model name format: '{model_name}'. Expected format: 'provider_model_name'")
    provider = parts[0]
    model_part = parts[1]
    if 'gpt_4_1_mini' in model_part:
        model = model_part.replace('gpt_4_1_mini', 'gpt-4.1-mini')
    elif 'gpt_4o_mini' in model_part:
        model = model_part.replace('gpt_4o_mini', 'gpt-4o-mini')
    elif 'gpt_4o' in model_part:
        model = model_part.replace('gpt_4o', 'gpt-4o')
    elif 'gemini_2_0' in model_part:
        model = model_part.replace('gemini_2_0', 'gemini-2.0').replace('_', '-')
    elif 'gemini_2_5' in model_part:
        model = model_part.replace('gemini_2_5', 'gemini-2.5').replace('_', '-')
    else:
        model = model_part.replace('_', '-')
    if provider == 'openai':
        api_key = os.getenv('OPENAI_API_KEY')
        return ChatOpenAI(model=model, api_key=api_key)
    elif provider == 'google':
        api_key = os.getenv('GOOGLE_API_KEY')
        return ChatGoogle(model=model, api_key=api_key)
    else:
        available_providers = ['openai', 'google']
        raise ValueError(f"Unknown provider: '{provider}'. Available providers: {', '.join(available_providers)}")

def __getattr__(name: str) -> 'BaseChatModel':
    if name == 'ChatOpenAI':
        return ChatOpenAI
    elif name == 'ChatGoogle':
        return ChatGoogle
    try:
        return get_llm_by_name(name)
    except ValueError:
        raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
__all__ = ['ChatOpenAI', 'ChatGoogle', 'get_llm_by_name', 'openai_gpt_4o', 'openai_gpt_4o_mini', 'openai_gpt_4_1_mini', 'openai_o1', 'openai_o1_mini', 'openai_o1_pro', 'openai_o3', 'openai_o3_mini', 'openai_o3_pro', 'openai_o4_mini', 'openai_gpt_5', 'openai_gpt_5_mini', 'openai_gpt_5_nano', 'google_gemini_2_0_flash', 'google_gemini_2_0_pro', 'google_gemini_2_5_pro', 'google_gemini_2_5_flash', 'google_gemini_2_5_flash_lite']