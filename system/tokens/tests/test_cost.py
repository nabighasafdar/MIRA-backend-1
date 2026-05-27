import asyncio
import logging
from system.llm import ChatGoogle, ChatOpenAI
from system.llm.messages import AssistantMessage, SystemMessage, UserMessage
from system.tokens.service import TokenCost
try:
    from examples.models.oci_models import meta_llm
    OCI_MODELS_AVAILABLE = True
except ImportError:
    meta_llm = None
    OCI_MODELS_AVAILABLE = False
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

def get_oci_model_if_available():
    if not OCI_MODELS_AVAILABLE:
        return None
    try:
        return meta_llm
    except Exception as e:
        logger.info(f'OCI model not available for testing: {e}')
        return None

async def test_iterative_country_generation():
    tc = TokenCost(include_cost=True)
    system_prompt = "You are a country name generator. When asked, you will provide exactly ONE country name and nothing else.\nEach time you're asked to continue, provide the next country name that hasn't been mentioned yet.\nKeep track of which countries you've already said and don't repeat them.\nOnly output the country name, no numbers, no punctuation, just the name."
    models = []
    models.append(ChatOpenAI(model='gpt-4.1'))
    models.append(ChatGoogle(model='gemini-2.0-flash-exp'))
    oci_model = get_oci_model_if_available()
    if oci_model:
        models.append(oci_model)
        print(f'✅ OCI model added to test: {oci_model.name}')
    else:
        print('ℹ️  OCI model not available (install with pip install browser-use[oci] and configure credentials)')
    print('\n🌍 Iterative Country Generation Test')
    print('=' * 80)
    for llm in models:
        print(f'\n📍 Testing {llm.model}')
        print('-' * 60)
        tc.register_llm(llm)
        messages = [SystemMessage(content=system_prompt), UserMessage(content='Give me a country name')]
        countries = []
        for i in range(10):
            result = await llm.ainvoke(messages)
            country = result.completion.strip()
            countries.append(country)
            messages.append(AssistantMessage(content=country))
            if i < 9:
                messages.append(UserMessage(content='Next country please'))
            print(f'  Country {i + 1}: {country}')
        print(f"\n  Generated countries: {', '.join(countries)}")
    print('\n💰 Cost Summary')
    print('=' * 80)
    summary = await tc.get_usage_summary()
    print(f'Total calls: {summary.entry_count}')
    print(f'Total tokens: {summary.total_tokens:,}')
    print(f'Total cost: ${summary.total_cost:.6f}')
    expected_cost = 0
    expected_invocations = 0
    print('\n📊 Cost breakdown by model:')
    for model, stats in summary.by_model.items():
        expected_cost += stats.cost
        expected_invocations += stats.invocations
        print(f'\n{model}:')
        print(f'  Calls: {stats.invocations}')
        print(f'  Prompt tokens: {stats.prompt_tokens:,}')
        print(f'  Completion tokens: {stats.completion_tokens:,}')
        print(f'  Total tokens: {stats.total_tokens:,}')
        print(f'  Cost: ${stats.cost:.6f}')
        print(f'  Average tokens per call: {stats.average_tokens_per_invocation:.1f}')
    assert summary.entry_count == expected_invocations, f'Expected {expected_invocations} invocations, got {summary.entry_count}'
    assert abs(summary.total_cost - expected_cost) < 1e-06, f'Expected total cost ${expected_cost:.6f}, got ${summary.total_cost:.6f}'
if __name__ == '__main__':
    asyncio.run(test_iterative_country_generation())