import asyncio
import json
import os
import time
import anyio
import pyperclip
import tiktoken
from system.agent.prompts import AgentMessagePrompt
from system.browser import BrowserProfile, BrowserSession
from system.browser.events import ClickElementEvent, TypeTextEvent
from system.browser.profile import ViewportSize
from system.dom.service import DomService
from system.dom.views import DEFAULT_INCLUDE_ATTRIBUTES
from system.filesystem.file_system import FileSystem
TIMEOUT = 60

async def test_focus_vs_all_elements():
    browser_session = BrowserSession(browser_profile=BrowserProfile(window_size=ViewportSize(width=1100, height=1000), disable_security=False, wait_for_network_idle_page_load_time=1, headless=False, args=['--incognito'], paint_order_filtering=True))
    sample_websites = ['https://browser-use.github.io/stress-tests/challenges/iframe-inception-level2.html', 'https://www.google.com/travel/flights', 'https://v0-simple-ui-test-site.vercel.app', 'https://browser-use.github.io/stress-tests/challenges/iframe-inception-level1.html', 'https://browser-use.github.io/stress-tests/challenges/angular-form.html', 'https://www.google.com/travel/flights', 'https://www.amazon.com/s?k=laptop', 'https://github.com/trending', 'https://www.reddit.com', 'https://www.ycombinator.com/companies', 'https://www.kayak.com/flights', 'https://www.booking.com', 'https://www.airbnb.com', 'https://www.linkedin.com/jobs', 'https://stackoverflow.com/questions']
    difficult_websites = ['https://www.w3schools.com/html/tryit.asp?filename=tryhtml_iframe', 'https://semantic-ui.com/modules/dropdown.html', 'https://www.dezlearn.com/nested-iframes-example/', 'https://codepen.io/towc/pen/mJzOWJ', 'https://jqueryui.com/accordion/', 'https://v0-simple-landing-page-seven-xi.vercel.app/', 'https://www.unesco.org/en']
    difficult_descriptions = {'https://www.w3schools.com/html/tryit.asp?filename=tryhtml_iframe': '🔸 NESTED IFRAMES: Multiple iframe layers', 'https://semantic-ui.com/modules/dropdown.html': '🔸 COMPLEX DROPDOWNS: Custom dropdown components', 'https://www.dezlearn.com/nested-iframes-example/': '🔸 CROSS-ORIGIN IFRAMES: Different domain iframes', 'https://codepen.io/towc/pen/mJzOWJ': '🔸 CANVAS ELEMENTS: Interactive canvas graphics', 'https://jqueryui.com/accordion/': '🔸 ACCORDION WIDGETS: Collapsible content sections'}
    websites = sample_websites + difficult_websites
    current_website_index = 0

    def get_website_list_for_prompt() -> str:
        lines = []
        lines.append('📋 Websites:')
        for i, site in enumerate(sample_websites, 1):
            current_marker = ' ←' if i - 1 == current_website_index else ''
            domain = site.replace('https://', '').split('/')[0]
            lines.append(f'  {i:2d}.{domain[:15]:<15}{current_marker}')
        for i, site in enumerate(difficult_websites, len(sample_websites) + 1):
            current_marker = ' ←' if i - 1 == current_website_index else ''
            domain = site.replace('https://', '').split('/')[0]
            desc = difficult_descriptions.get(site, '')
            challenge = desc.split(': ')[1][:15] if ': ' in desc else ''
            lines.append(f'  {i:2d}.{domain[:15]:<15} ({challenge}){current_marker}')
        return '\n'.join(lines)
    await browser_session.start()
    print('\n🌐 BROWSER-USE DOM EXTRACTION TESTER')
    print(f'📊 {len(websites)} websites total: {len(sample_websites)} standard + {len(difficult_websites)} complex')
    print('🔧 Controls: Type 1-15 to jump | Enter to re-run | "n" next | "q" quit')
    print('💾 Outputs: tmp/user_message.txt & tmp/element_tree.json\n')
    dom_service = DomService(browser_session)
    while True:
        if current_website_index >= len(websites):
            current_website_index = 0
            print('Cycled back to first website!')
        website = websites[current_website_index]
        await browser_session._cdp_navigate(website)
        await asyncio.sleep(1)
        last_clicked_index = None
        while True:
            try:
                website_type = 'DIFFICULT' if website in difficult_websites else 'SAMPLE'
                print(f"\n{'=' * 60}")
                print(f'[{current_website_index + 1}/{len(websites)}] [{website_type}] Testing: {website}')
                if website in difficult_descriptions:
                    print(f'{difficult_descriptions[website]}')
                print(f"{'=' * 60}")
                print('\nGetting page state...')
                start_time = time.time()
                all_elements_state = await browser_session.get_browser_state_summary(True)
                end_time = time.time()
                get_state_time = end_time - start_time
                print(f'get_state_summary took {get_state_time:.2f} seconds')
                print('\nGetting detailed DOM timing...')
                serialized_state, _, timing_info = await dom_service.get_serialized_dom_tree()
                all_timing = {'get_state_summary_total': get_state_time, **timing_info}
                selector_map = all_elements_state.dom_state.selector_map
                total_elements = len(selector_map.keys())
                print(f'Total number of elements: {total_elements}')
                prompt = AgentMessagePrompt(browser_state_summary=all_elements_state, file_system=FileSystem(base_dir='./tmp'), include_attributes=DEFAULT_INCLUDE_ATTRIBUTES, step_info=None)
                user_message = prompt.get_user_message(use_vision=False).text
                text_to_save = user_message
                os.makedirs('./tmp', exist_ok=True)
                async with await anyio.open_file('./tmp/user_message.txt', 'w', encoding='utf-8') as f:
                    await f.write(text_to_save)
                if all_elements_state.dom_state._root:
                    async with await anyio.open_file('./tmp/simplified_element_tree.json', 'w', encoding='utf-8') as f:
                        await f.write(json.dumps(all_elements_state.dom_state._root.__json__(), indent=2))
                    async with await anyio.open_file('./tmp/original_element_tree.json', 'w', encoding='utf-8') as f:
                        await f.write(json.dumps(all_elements_state.dom_state._root.original_node.__json__(), indent=2))
                encoding = tiktoken.encoding_for_model('gpt-4.1-mini')
                token_count = len(encoding.encode(text_to_save))
                print(f'Token count: {token_count}')
                print('User message written to ./tmp/user_message.txt')
                print('Element tree written to ./tmp/simplified_element_tree.json')
                print('Original element tree written to ./tmp/original_element_tree.json')
                timing_text = '🔍 DOM EXTRACTION PERFORMANCE ANALYSIS\n'
                timing_text += f"{'=' * 50}\n\n"
                timing_text += f'📄 Website: {website}\n'
                timing_text += f'📊 Total Elements: {total_elements}\n'
                timing_text += f'🎯 Token Count: {token_count}\n\n'
                timing_text += '⏱️  TIMING BREAKDOWN:\n'
                timing_text += f"{'─' * 30}\n"
                for key, value in all_timing.items():
                    timing_text += f'{key:<35}: {value * 1000:>8.2f} ms\n'
                total_time = all_timing.get('get_state_summary_total', 0)
                if total_time > 0 and total_elements > 0:
                    timing_text += '\n📈 PERCENTAGE BREAKDOWN:\n'
                    timing_text += f"{'─' * 30}\n"
                    for key, value in all_timing.items():
                        if key != 'get_state_summary_total':
                            percentage = value / total_time * 100
                            timing_text += f'{key:<35}: {percentage:>7.1f}%\n'
                timing_text += '\n🎯 CLICKABLE DETECTION ANALYSIS:\n'
                timing_text += f"{'─' * 35}\n"
                clickable_time = all_timing.get('clickable_detection_time', 0)
                if clickable_time > 0 and total_elements > 0:
                    avg_per_element = clickable_time / total_elements * 1000000
                    timing_text += f'Total clickable detection time: {clickable_time * 1000:.2f} ms\n'
                    timing_text += f'Average per element: {avg_per_element:.2f} μs\n'
                    timing_text += f'Clickable detection calls: ~{total_elements} (approx)\n'
                async with await anyio.open_file('./tmp/timing_analysis.txt', 'w', encoding='utf-8') as f:
                    await f.write(timing_text)
                print('Timing analysis written to ./tmp/timing_analysis.txt')
                website_list = get_website_list_for_prompt()
                answer = input("🎮 Enter: element index | 'index' click (clickable) | 'index,text' input | 'c,index' copy | Enter re-run | 'n' next | 'q' quit: ")
                if answer.lower() == 'q':
                    return
                elif answer.lower() == 'n':
                    print('Moving to next website...')
                    current_website_index += 1
                    break
                elif answer.strip() == '':
                    print('Re-running extraction on current page state...')
                    continue
                elif answer.strip().isdigit():
                    try:
                        clicked_index = int(answer)
                        if clicked_index in selector_map:
                            element_node = selector_map[clicked_index]
                            print(f'Clicking element {clicked_index}: {element_node.tag_name}')
                            event = browser_session.event_bus.dispatch(ClickElementEvent(node=element_node))
                            await event
                            print('Click successful.')
                    except ValueError:
                        print(f"Invalid input: '{answer}'. Enter an index, 'index,text', 'c,index', or 'q'.")
                    continue
                try:
                    if answer.lower().startswith('c,'):
                        parts = answer.split(',', 1)
                        if len(parts) == 2:
                            try:
                                target_index = int(parts[1].strip())
                                if target_index in selector_map:
                                    element_node = selector_map[target_index]
                                    element_json = json.dumps(element_node.__json__(), indent=2, default=str)
                                    pyperclip.copy(element_json)
                                    print(f'Copied element {target_index} JSON to clipboard: {element_node.tag_name}')
                                else:
                                    print(f'Invalid index: {target_index}')
                            except ValueError:
                                print(f'Invalid index format: {parts[1]}')
                        else:
                            print("Invalid input format. Use 'c,index'.")
                    elif ',' in answer:
                        parts = answer.split(',', 1)
                        if len(parts) == 2:
                            try:
                                target_index = int(parts[0].strip())
                                text_to_input = parts[1]
                                if target_index in selector_map:
                                    element_node = selector_map[target_index]
                                    print(f"Inputting text '{text_to_input}' into element {target_index}: {element_node.tag_name}")
                                    event = await browser_session.event_bus.dispatch(TypeTextEvent(node=element_node, text=text_to_input))
                                    print('Input successful.')
                                else:
                                    print(f'Invalid index: {target_index}')
                            except ValueError:
                                print(f'Invalid index format: {parts[0]}')
                        else:
                            print("Invalid input format. Use 'index,text'.")
                except Exception as action_e:
                    print(f'Action failed: {action_e}')
            except Exception as e:
                print(f'Error in loop: {e}')
                await asyncio.sleep(1)
if __name__ == '__main__':
    asyncio.run(test_focus_vs_all_elements())