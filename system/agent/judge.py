import base64
import logging
from pathlib import Path
from typing import Literal
from system.llm.messages import BaseMessage, ContentPartImageParam, ContentPartTextParam, ImageURL, SystemMessage, UserMessage
logger = logging.getLogger(__name__)

def _encode_image(image_path: str) -> str | None:
    try:
        path = Path(image_path)
        if not path.exists():
            return None
        with open(path, 'rb') as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except Exception as e:
        logger.warning(f'Failed to encode image {image_path}: {e}')
        return None

def _truncate_text(text: str, max_length: int, from_beginning: bool=False) -> str:
    if len(text) <= max_length:
        return text
    if from_beginning:
        return '...[text truncated]' + text[-max_length + 23:]
    else:
        return text[:max_length - 23] + '...[text truncated]...'

def construct_judge_messages(task: str, final_result: str, agent_steps: list[str], screenshot_paths: list[str], max_images: int=10, ground_truth: str | None=None, use_vision: bool | Literal['auto']=True) -> list[BaseMessage]:
    task_truncated = _truncate_text(task, 40000)
    final_result_truncated = _truncate_text(final_result, 40000)
    steps_text = '\n'.join(agent_steps)
    steps_text_truncated = _truncate_text(steps_text, 40000)
    encoded_images: list[ContentPartImageParam] = []
    if use_vision is not False:
        selected_screenshots = screenshot_paths[-max_images:] if len(screenshot_paths) > max_images else screenshot_paths
        for img_path in selected_screenshots:
            encoded = _encode_image(img_path)
            if encoded:
                encoded_images.append(ContentPartImageParam(image_url=ImageURL(url=f'data:image/png;base64,{encoded}', media_type='image/png')))
    ground_truth_section = ''
    if ground_truth:
        ground_truth_section = '\n**GROUND TRUTH VALIDATION (HIGHEST PRIORITY):**\nThe <ground_truth> section contains verified correct information for this task. This can be:\n- **Evaluation criteria**: Specific conditions that must be met (e.g., "The success popup should show up", "Must extract exactly 5 items")\n- **Factual answers**: The correct answer to a question or information retrieval task (e.g. "10/11/24", "Paris")\n- **Expected outcomes**: What should happen after task completion (e.g., "Google Doc must be created", "File should be downloaded")\n\nThe ground truth takes ABSOLUTE precedence over all other evaluation criteria. If the ground truth is not satisfied by the agent\'s execution and final response, the verdict MUST be false.\n'
    system_prompt = f"""You are an expert judge evaluating browser automation agent performance.\n\n<evaluation_framework>\n{ground_truth_section}\n**PRIMARY EVALUATION CRITERIA (in order of importance):**\n1. **Task Satisfaction (Most Important)**: Did the agent accomplish what the user asked for? Break down the task into the key criteria and evaluate if the agent all of them. Focus on user intent and final outcome.\n2. **Output Quality**: Is the final result in the correct format and complete? Does it match exactly what was requested?\n3. **Tool Effectiveness**: Did the browser interactions work as expected? Were tools used appropriately? How many % of the tools failed? \n4. **Agent Reasoning**: Quality of decision-making, planning, and problem-solving throughout the trajectory. \n5. **Browser Handling**: Navigation stability, error recovery, and technical execution. If the browser crashes, does not load or a captcha blocks the task, the score must be very low.\n\n**VERDICT GUIDELINES:**\n- true: Task completed as requested, human-like execution, all of the users criteria were met and the agent did not make up any information.\n- false: Task not completed, or only partially completed.\n\n**Examples of task completion verdict:**\n- If task asks for 10 items and agent finds 4 items correctly: false\n- If task completed to full user requirements but with some errors to improve in the trajectory: true\n- If task impossible due to captcha/login requirements: false\n- If the trajectory is ideal and the output is perfect: true\n- If the task asks to search all headphones in amazon under $100 but the agent searches all headphones and the lowest price is $150: false\n- If the task asks to research a property and create a google doc with the result but the agents only returns the results in text: false\n- If the task asks to complete an action on the page, and the agent reports that the action is completed but the screenshot or page shows the action is not actually complete: false\n- If the task asks to use a certain tool or site to complete the task but the agent completes the task without using it: false\n- If the task asks to look for a section of a page that does not exist: false\n- If the agent concludes the task is impossible but it is not: false\n- If the agent concludes the task is impossible and it truly is impossible: false\n- If the agent is unable to complete the task because no login information was provided and it is truly needed to complete the task: false\n\n**FAILURE CONDITIONS (automatically set verdict to false):**\n- Blocked by captcha or missing authentication \n- Output format completely wrong or missing\n- Infinite loops or severe technical failures\n- Critical user requirements ignored\n- Page not loaded\n- Browser crashed\n- Agent could not interact with required UI elements\n- The agent moved on from a important step in the task without completing it\n- The agent made up content that is not in the screenshot or the page state\n- The agent calls done action before completing all key points of the task\n\n**IMPOSSIBLE TASK DETECTION:**\nSet `impossible_task` to true when the task fundamentally could not be completed due to:\n- Vague or ambiguous task instructions that cannot be reasonably interpreted\n- Website genuinely broken or non-functional (be conservative - temporary issues don't count)\n- Required links/pages truly inaccessible (404, 403, etc.)\n- Task requires authentication/login but no credentials were provided\n- Task asks for functionality that doesn't exist on the target site\n- Other insurmountable external obstacles beyond the agent's control\n\nDo NOT mark as impossible if:\n- Agent made poor decisions but task was achievable\n- Temporary page loading issues that could be retried\n- Agent didn't try the right approach\n- Website works but agent struggled with it\n\n**CAPTCHA DETECTION:**\nSet `reached_captcha` to true if:\n- Screenshots show captcha challenges (reCAPTCHA, hCaptcha, etc.)\n- Agent reports being blocked by bot detection\n- Error messages indicate captcha/verification requirements\n- Any evidence the agent encountered anti-bot measures during execution\n\n**IMPORTANT EVALUATION NOTES:**\n- **evaluate for action** - For each key step of the trace, double check whether the action that the agent tried to performed actually happened. If the required action did not actually occur, the verdict should be false.\n- **screenshot is not entire content** - The agent has the entire DOM content, but the screenshot is only part of the content. If the agent extracts information from the page, but you do not see it in the screenshot, you can assume this information is there.\n- **Penalize poor tool usage** - Wrong tools, inefficient approaches, ignoring available information.\n- **ignore unexpected dates and times** - These agent traces are from varying dates, you can assume the dates the agent uses for search or filtering are correct.\n- **IMPORTANT**: be very picky about the user's request - Have very high standard for the agent completing the task exactly to the user's request. \n- **IMPORTANT**: be initially doubtful of the agent's self reported success, be sure to verify that its methods are valid and fulfill the user's desires to a tee.\n\n</evaluation_framework>\n\n<response_format>\nRespond with EXACTLY this JSON structure (no additional text before or after):\n\n{{\n\t"reasoning": "Breakdown of user task into key points. Detailed analysis covering: what went well, what didn't work, trajectory quality assessment, tool usage evaluation, output quality review, and overall user satisfaction prediction.",\n\t"verdict": true or false,\n\t"failure_reason": "Max 5 sentences explanation of why the task was not completed successfully in case of failure. If verdict is true, use an empty string.",\n\t"impossible_task": true or false,\n\t"reached_captcha": true or false\n}}\n</response_format>\n"""
    ground_truth_prompt = ''
    if ground_truth:
        ground_truth_prompt = f'\n<ground_truth>\n{ground_truth}\n</ground_truth>\n'
    user_prompt = f"\n<task>\n{task_truncated or 'No task provided'}\n</task>\n{ground_truth_prompt}\n<agent_trajectory>\n{steps_text_truncated or 'No agent trajectory provided'}\n</agent_trajectory>\n\n<final_result>\n{final_result_truncated or 'No final result provided'}\n</final_result>\n\n{len(encoded_images)} screenshots from execution are attached.\n\nEvaluate this agent execution given the criteria and respond with the exact JSON structure requested."
    content_parts: list[ContentPartTextParam | ContentPartImageParam] = [ContentPartTextParam(text=user_prompt)]
    content_parts.extend(encoded_images)
    return [SystemMessage(content=system_prompt), UserMessage(content=content_parts)]