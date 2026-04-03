import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import anyio
import httpx
from dotenv import load_dotenv
from system.llm.base import BaseChatModel
from system.llm.views import ChatInvokeUsage
from system.tokens.custom_pricing import CUSTOM_MODEL_PRICING
from system.tokens.mappings import MODEL_TO_LITELLM
from system.tokens.views import CachedPricingData, ModelPricing, ModelUsageStats, ModelUsageTokens, TokenCostCalculated, TokenUsageEntry, UsageSummary
from system.utils import create_task_with_error_handling
load_dotenv()
from system.config import CONFIG
logger = logging.getLogger(__name__)
cost_logger = logging.getLogger('cost')

def xdg_cache_home() -> Path:
    default = Path.home() / '.cache'
    if CONFIG.XDG_CACHE_HOME and (path := Path(CONFIG.XDG_CACHE_HOME)).is_absolute():
        return path
    return default

class TokenCost:
    CACHE_DIR_NAME = 'system/token_cost'
    CACHE_DURATION = timedelta(days=1)
    PRICING_URL = 'https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json'

    def __init__(self, include_cost: bool=False):
        self.include_cost = include_cost or os.getenv('BROWSER_USE_CALCULATE_COST', 'false').lower() == 'true'
        self.usage_history: list[TokenUsageEntry] = []
        self.registered_llms: dict[str, BaseChatModel] = {}
        self._pricing_data: dict[str, Any] | None = None
        self._initialized = False
        self._cache_dir = xdg_cache_home() / self.CACHE_DIR_NAME

    async def initialize(self) -> None:
        if not self._initialized:
            if self.include_cost:
                await self._load_pricing_data()
            self._initialized = True

    async def _load_pricing_data(self) -> None:
        cache_file = await self._find_valid_cache()
        if cache_file:
            await self._load_from_cache(cache_file)
        else:
            await self._fetch_and_cache_pricing_data()

    async def _find_valid_cache(self) -> Path | None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_files = list(self._cache_dir.glob('*.json'))
            if not cache_files:
                return None
            cache_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            for cache_file in cache_files:
                if await self._is_cache_valid(cache_file):
                    return cache_file
                else:
                    try:
                        os.remove(cache_file)
                    except Exception:
                        pass
            return None
        except Exception:
            return None

    async def _is_cache_valid(self, cache_file: Path) -> bool:
        try:
            if not cache_file.exists():
                return False
            cached = CachedPricingData.model_validate_json(await anyio.Path(cache_file).read_text())
            return datetime.now() - cached.timestamp < self.CACHE_DURATION
        except Exception:
            return False

    async def _load_from_cache(self, cache_file: Path) -> None:
        try:
            content = await anyio.Path(cache_file).read_text()
            cached = CachedPricingData.model_validate_json(content)
            self._pricing_data = cached.data
        except Exception as e:
            logger.debug(f'Error loading cached pricing data from {cache_file}: {e}')
            await self._fetch_and_cache_pricing_data()

    async def _fetch_and_cache_pricing_data(self) -> None:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(self.PRICING_URL, timeout=30)
                response.raise_for_status()
                self._pricing_data = response.json()
            cached = CachedPricingData(timestamp=datetime.now(), data=self._pricing_data or {})
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            cache_file = self._cache_dir / f'pricing_{timestamp_str}.json'
            await anyio.Path(cache_file).write_text(cached.model_dump_json(indent=2))
        except Exception as e:
            logger.debug(f'Error fetching pricing data: {e}')
            self._pricing_data = {}

    async def get_model_pricing(self, model_name: str) -> ModelPricing | None:
        if not self._initialized:
            await self.initialize()
        if model_name in CUSTOM_MODEL_PRICING:
            data = CUSTOM_MODEL_PRICING[model_name]
            return ModelPricing(model=model_name, input_cost_per_token=data.get('input_cost_per_token'), output_cost_per_token=data.get('output_cost_per_token'), max_tokens=data.get('max_tokens'), max_input_tokens=data.get('max_input_tokens'), max_output_tokens=data.get('max_output_tokens'), cache_read_input_token_cost=data.get('cache_read_input_token_cost'), cache_creation_input_token_cost=data.get('cache_creation_input_token_cost'))
        litellm_model_name = MODEL_TO_LITELLM.get(model_name, model_name)
        if not self._pricing_data or litellm_model_name not in self._pricing_data:
            return None
        data = self._pricing_data[litellm_model_name]
        return ModelPricing(model=model_name, input_cost_per_token=data.get('input_cost_per_token'), output_cost_per_token=data.get('output_cost_per_token'), max_tokens=data.get('max_tokens'), max_input_tokens=data.get('max_input_tokens'), max_output_tokens=data.get('max_output_tokens'), cache_read_input_token_cost=data.get('cache_read_input_token_cost'), cache_creation_input_token_cost=data.get('cache_creation_input_token_cost'))

    async def calculate_cost(self, model: str, usage: ChatInvokeUsage) -> TokenCostCalculated | None:
        if not self.include_cost:
            return None
        data = await self.get_model_pricing(model)
        if data is None:
            return None
        uncached_prompt_tokens = usage.prompt_tokens - (usage.prompt_cached_tokens or 0)
        return TokenCostCalculated(new_prompt_tokens=usage.prompt_tokens, new_prompt_cost=uncached_prompt_tokens * (data.input_cost_per_token or 0), prompt_read_cached_tokens=usage.prompt_cached_tokens, prompt_read_cached_cost=usage.prompt_cached_tokens * data.cache_read_input_token_cost if usage.prompt_cached_tokens and data.cache_read_input_token_cost else None, prompt_cached_creation_tokens=usage.prompt_cache_creation_tokens, prompt_cache_creation_cost=usage.prompt_cache_creation_tokens * data.cache_creation_input_token_cost if data.cache_creation_input_token_cost and usage.prompt_cache_creation_tokens else None, completion_tokens=usage.completion_tokens, completion_cost=usage.completion_tokens * float(data.output_cost_per_token or 0))

    def add_usage(self, model: str, usage: ChatInvokeUsage) -> TokenUsageEntry:
        entry = TokenUsageEntry(model=model, timestamp=datetime.now(), usage=usage)
        self.usage_history.append(entry)
        return entry

    async def _log_usage(self, model: str, usage: TokenUsageEntry) -> None:
        if not self._initialized:
            await self.initialize()
        C_CYAN = '\x1b[96m'
        C_YELLOW = '\x1b[93m'
        C_GREEN = '\x1b[92m'
        C_BLUE = '\x1b[94m'
        C_RESET = '\x1b[0m'
        cost = await self.calculate_cost(model, usage.usage)
        input_part = self._build_input_tokens_display(usage.usage, cost)
        completion_tokens_fmt = self._format_tokens(usage.usage.completion_tokens)
        if self.include_cost and cost and (cost.completion_cost > 0):
            output_part = f'📤 {C_GREEN}{completion_tokens_fmt} (${cost.completion_cost:.4f}){C_RESET}'
        else:
            output_part = f'📤 {C_GREEN}{completion_tokens_fmt}{C_RESET}'
        cost_logger.debug(f'🧠 {C_CYAN}{model}{C_RESET} | {input_part} | {output_part}')

    def _build_input_tokens_display(self, usage: ChatInvokeUsage, cost: TokenCostCalculated | None) -> str:
        C_YELLOW = '\x1b[93m'
        C_BLUE = '\x1b[94m'
        C_RESET = '\x1b[0m'
        parts = []
        if usage.prompt_cached_tokens or usage.prompt_cache_creation_tokens:
            new_tokens = usage.prompt_tokens - (usage.prompt_cached_tokens or 0)
            if new_tokens > 0:
                new_tokens_fmt = self._format_tokens(new_tokens)
                if self.include_cost and cost and (cost.new_prompt_cost > 0):
                    parts.append(f'🆕 {C_YELLOW}{new_tokens_fmt} (${cost.new_prompt_cost:.4f}){C_RESET}')
                else:
                    parts.append(f'🆕 {C_YELLOW}{new_tokens_fmt}{C_RESET}')
            if usage.prompt_cached_tokens:
                cached_tokens_fmt = self._format_tokens(usage.prompt_cached_tokens)
                if self.include_cost and cost and cost.prompt_read_cached_cost:
                    parts.append(f'💾 {C_BLUE}{cached_tokens_fmt} (${cost.prompt_read_cached_cost:.4f}){C_RESET}')
                else:
                    parts.append(f'💾 {C_BLUE}{cached_tokens_fmt}{C_RESET}')
            if usage.prompt_cache_creation_tokens:
                creation_tokens_fmt = self._format_tokens(usage.prompt_cache_creation_tokens)
                if self.include_cost and cost and cost.prompt_cache_creation_cost:
                    parts.append(f'🔧 {C_BLUE}{creation_tokens_fmt} (${cost.prompt_cache_creation_cost:.4f}){C_RESET}')
                else:
                    parts.append(f'🔧 {C_BLUE}{creation_tokens_fmt}{C_RESET}')
        if not parts:
            total_tokens_fmt = self._format_tokens(usage.prompt_tokens)
            if self.include_cost and cost and (cost.new_prompt_cost > 0):
                parts.append(f'📥 {C_YELLOW}{total_tokens_fmt} (${cost.new_prompt_cost:.4f}){C_RESET}')
            else:
                parts.append(f'📥 {C_YELLOW}{total_tokens_fmt}{C_RESET}')
        return ' + '.join(parts)

    def register_llm(self, llm: BaseChatModel) -> BaseChatModel:
        instance_id = str(id(llm))
        if instance_id in self.registered_llms:
            logger.debug(f'LLM instance {instance_id} ({llm.provider}_{llm.model}) is already registered')
            return llm
        self.registered_llms[instance_id] = llm
        original_ainvoke = llm.ainvoke
        token_cost_service = self

        async def tracked_ainvoke(messages, output_format=None, **kwargs):
            result = await original_ainvoke(messages, output_format, **kwargs)
            if result.usage:
                usage = token_cost_service.add_usage(llm.model, result.usage)
                logger.debug(f'Token cost service: {usage}')
                create_task_with_error_handling(token_cost_service._log_usage(llm.model, usage), name='log_token_usage', suppress_exceptions=True)
            return result
        setattr(llm, 'ainvoke', tracked_ainvoke)
        return llm

    def get_usage_tokens_for_model(self, model: str) -> ModelUsageTokens:
        filtered_usage = [u for u in self.usage_history if u.model == model]
        return ModelUsageTokens(model=model, prompt_tokens=sum((u.usage.prompt_tokens for u in filtered_usage)), prompt_cached_tokens=sum((u.usage.prompt_cached_tokens or 0 for u in filtered_usage)), completion_tokens=sum((u.usage.completion_tokens for u in filtered_usage)), total_tokens=sum((u.usage.prompt_tokens + u.usage.completion_tokens for u in filtered_usage)))

    async def get_usage_summary(self, model: str | None=None, since: datetime | None=None) -> UsageSummary:
        filtered_usage = self.usage_history
        if model:
            filtered_usage = [u for u in filtered_usage if u.model == model]
        if since:
            filtered_usage = [u for u in filtered_usage if u.timestamp >= since]
        if not filtered_usage:
            return UsageSummary(total_prompt_tokens=0, total_prompt_cost=0.0, total_prompt_cached_tokens=0, total_prompt_cached_cost=0.0, total_completion_tokens=0, total_completion_cost=0.0, total_tokens=0, total_cost=0.0, entry_count=0)
        total_prompt = sum((u.usage.prompt_tokens for u in filtered_usage))
        total_completion = sum((u.usage.completion_tokens for u in filtered_usage))
        total_tokens = total_prompt + total_completion
        total_prompt_cached = sum((u.usage.prompt_cached_tokens or 0 for u in filtered_usage))
        models = list({u.model for u in filtered_usage})
        model_stats: dict[str, ModelUsageStats] = {}
        total_prompt_cost = 0.0
        total_completion_cost = 0.0
        total_prompt_cached_cost = 0.0
        for entry in filtered_usage:
            if entry.model not in model_stats:
                model_stats[entry.model] = ModelUsageStats(model=entry.model)
            stats = model_stats[entry.model]
            stats.prompt_tokens += entry.usage.prompt_tokens
            stats.completion_tokens += entry.usage.completion_tokens
            stats.total_tokens += entry.usage.prompt_tokens + entry.usage.completion_tokens
            stats.invocations += 1
            if self.include_cost:
                cost = await self.calculate_cost(entry.model, entry.usage)
                if cost:
                    stats.cost += cost.total_cost
                    total_prompt_cost += cost.prompt_cost
                    total_completion_cost += cost.completion_cost
                    total_prompt_cached_cost += cost.prompt_read_cached_cost or 0
        for stats in model_stats.values():
            if stats.invocations > 0:
                stats.average_tokens_per_invocation = stats.total_tokens / stats.invocations
        return UsageSummary(total_prompt_tokens=total_prompt, total_prompt_cost=total_prompt_cost, total_prompt_cached_tokens=total_prompt_cached, total_prompt_cached_cost=total_prompt_cached_cost, total_completion_tokens=total_completion, total_completion_cost=total_completion_cost, total_tokens=total_tokens, total_cost=total_prompt_cost + total_completion_cost + total_prompt_cached_cost, entry_count=len(filtered_usage), by_model=model_stats)

    def _format_tokens(self, tokens: int) -> str:
        if tokens >= 1000000000:
            return f'{tokens / 1000000000:.1f}B'
        if tokens >= 1000000:
            return f'{tokens / 1000000:.1f}M'
        if tokens >= 1000:
            return f'{tokens / 1000:.1f}k'
        return str(tokens)

    async def log_usage_summary(self) -> None:
        if not self.usage_history:
            return
        summary = await self.get_usage_summary()
        if summary.entry_count == 0:
            return
        C_CYAN = '\x1b[96m'
        C_YELLOW = '\x1b[93m'
        C_GREEN = '\x1b[92m'
        C_BLUE = '\x1b[94m'
        C_MAGENTA = '\x1b[95m'
        C_RESET = '\x1b[0m'
        C_BOLD = '\x1b[1m'
        total_tokens_fmt = self._format_tokens(summary.total_tokens)
        prompt_tokens_fmt = self._format_tokens(summary.total_prompt_tokens)
        completion_tokens_fmt = self._format_tokens(summary.total_completion_tokens)
        if self.include_cost and summary.total_cost > 0:
            total_cost_part = f' (${C_MAGENTA}{summary.total_cost:.4f}{C_RESET})'
            prompt_cost_part = f' (${summary.total_prompt_cost:.4f})'
            completion_cost_part = f' (${summary.total_completion_cost:.4f})'
        else:
            total_cost_part = ''
            prompt_cost_part = ''
            completion_cost_part = ''
        if len(summary.by_model) > 1:
            cost_logger.debug(f'💲 {C_BOLD}Total Usage Summary{C_RESET}: {C_BLUE}{total_tokens_fmt} tokens{C_RESET}{total_cost_part} | ⬅️ {C_YELLOW}{prompt_tokens_fmt}{prompt_cost_part}{C_RESET} | ➡️ {C_GREEN}{completion_tokens_fmt}{completion_cost_part}{C_RESET}')
        for model, stats in summary.by_model.items():
            model_total_fmt = self._format_tokens(stats.total_tokens)
            model_prompt_fmt = self._format_tokens(stats.prompt_tokens)
            model_completion_fmt = self._format_tokens(stats.completion_tokens)
            avg_tokens_fmt = self._format_tokens(int(stats.average_tokens_per_invocation))
            if self.include_cost:
                total_model_cost = 0.0
                model_prompt_cost = 0.0
                model_completion_cost = 0.0
                for entry in self.usage_history:
                    if entry.model == model:
                        cost = await self.calculate_cost(entry.model, entry.usage)
                        if cost:
                            model_prompt_cost += cost.prompt_cost
                            model_completion_cost += cost.completion_cost
                total_model_cost = model_prompt_cost + model_completion_cost
                if total_model_cost > 0:
                    cost_part = f' (${C_MAGENTA}{total_model_cost:.4f}{C_RESET})'
                    prompt_part = f'{C_YELLOW}{model_prompt_fmt} (${model_prompt_cost:.4f}){C_RESET}'
                    completion_part = f'{C_GREEN}{model_completion_fmt} (${model_completion_cost:.4f}){C_RESET}'
                else:
                    cost_part = ''
                    prompt_part = f'{C_YELLOW}{model_prompt_fmt}{C_RESET}'
                    completion_part = f'{C_GREEN}{model_completion_fmt}{C_RESET}'
            else:
                cost_part = ''
                prompt_part = f'{C_YELLOW}{model_prompt_fmt}{C_RESET}'
                completion_part = f'{C_GREEN}{model_completion_fmt}{C_RESET}'
            cost_logger.debug(f'  🤖 {C_CYAN}{model}{C_RESET}: {C_BLUE}{model_total_fmt} tokens{C_RESET}{cost_part} | ⬅️ {prompt_part} | ➡️ {completion_part} | 📞 {stats.invocations} calls | 📈 {avg_tokens_fmt}/call')

    async def get_cost_by_model(self) -> dict[str, ModelUsageStats]:
        summary = await self.get_usage_summary()
        return summary.by_model

    def clear_history(self) -> None:
        self.usage_history = []

    async def refresh_pricing_data(self) -> None:
        if self.include_cost:
            await self._fetch_and_cache_pricing_data()

    async def clean_old_caches(self, keep_count: int=3) -> None:
        try:
            cache_files = list(self._cache_dir.glob('*.json'))
            if len(cache_files) <= keep_count:
                return
            cache_files.sort(key=lambda f: f.stat().st_mtime)
            for cache_file in cache_files[:-keep_count]:
                try:
                    os.remove(cache_file)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f'Error cleaning old cache files: {e}')

    async def ensure_pricing_loaded(self) -> None:
        if not self._initialized and self.include_cost:
            await self.initialize()