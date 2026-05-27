import re
from system.agent.views import AgentHistoryList, DetectedVariable
from system.dom.views import DOMInteractedElement

def detect_variables_in_history(history: AgentHistoryList) -> dict[str, DetectedVariable]:
    detected: dict[str, DetectedVariable] = {}
    detected_values: set[str] = set()
    for step_idx, history_item in enumerate(history.history):
        if not history_item.model_output:
            continue
        for action_idx, action in enumerate(history_item.model_output.action):
            if hasattr(action, 'model_dump'):
                action_dict = action.model_dump()
            elif isinstance(action, dict):
                action_dict = action
            else:
                action_dict = vars(action)
            element = None
            if history_item.state and history_item.state.interacted_element:
                if len(history_item.state.interacted_element) > action_idx:
                    element = history_item.state.interacted_element[action_idx]
            _detect_in_action(action_dict, element, detected, detected_values)
    return detected

def _detect_in_action(action_dict: dict, element: DOMInteractedElement | None, detected: dict[str, DetectedVariable], detected_values: set[str]) -> None:
    for action_type, params in action_dict.items():
        if not isinstance(params, dict):
            continue
        fields_to_check = ['text', 'query']
        for field in fields_to_check:
            if field not in params:
                continue
            value = params[field]
            if not isinstance(value, str) or not value.strip():
                continue
            if value in detected_values:
                continue
            var_info = _detect_variable_type(value, element)
            if not var_info:
                continue
            var_name, var_format = var_info
            var_name = _ensure_unique_name(var_name, detected)
            detected[var_name] = DetectedVariable(name=var_name, original_value=value, type='string', format=var_format)
            detected_values.add(value)

def _detect_variable_type(value: str, element: DOMInteractedElement | None=None) -> tuple[str, str | None] | None:
    if element and element.attributes:
        attr_detection = _detect_from_attributes(element.attributes)
        if attr_detection:
            return attr_detection
    return _detect_from_value_pattern(value)

def _detect_from_attributes(attributes: dict[str, str]) -> tuple[str, str | None] | None:
    input_type = attributes.get('type', '').lower()
    if input_type == 'email':
        return ('email', 'email')
    elif input_type == 'tel':
        return ('phone', 'phone')
    elif input_type == 'date':
        return ('date', 'date')
    elif input_type == 'number':
        return ('number', 'number')
    elif input_type == 'url':
        return ('url', 'url')
    semantic_attrs = [attributes.get('id', ''), attributes.get('name', ''), attributes.get('placeholder', ''), attributes.get('aria-label', '')]
    combined_text = ' '.join(semantic_attrs).lower()
    if any((keyword in combined_text for keyword in ['address', 'street', 'addr'])):
        if 'billing' in combined_text:
            return ('billing_address', None)
        elif 'shipping' in combined_text:
            return ('shipping_address', None)
        else:
            return ('address', None)
    if any((keyword in combined_text for keyword in ['comment', 'note', 'message', 'description'])):
        return ('comment', None)
    if 'email' in combined_text or 'e-mail' in combined_text:
        return ('email', 'email')
    if any((keyword in combined_text for keyword in ['phone', 'tel', 'mobile', 'cell'])):
        return ('phone', 'phone')
    if 'first' in combined_text and 'name' in combined_text:
        return ('first_name', None)
    elif 'last' in combined_text and 'name' in combined_text:
        return ('last_name', None)
    elif 'full' in combined_text and 'name' in combined_text:
        return ('full_name', None)
    elif 'name' in combined_text:
        return ('name', None)
    if any((keyword in combined_text for keyword in ['date', 'dob', 'birth'])):
        return ('date', 'date')
    if 'city' in combined_text:
        return ('city', None)
    if 'state' in combined_text or 'province' in combined_text:
        return ('state', None)
    if 'country' in combined_text:
        return ('country', None)
    if any((keyword in combined_text for keyword in ['zip', 'postal', 'postcode'])):
        return ('zip_code', 'postal_code')
    if 'company' in combined_text or 'organization' in combined_text:
        return ('company', None)
    return None

def _detect_from_value_pattern(value: str) -> tuple[str, str | None] | None:
    if '@' in value and '.' in value:
        if re.match('^[\\w\\.-]+@[\\w\\.-]+\\.\\w+$', value):
            return ('email', 'email')
    if re.match('^[\\d\\s\\-\\(\\)\\+]+$', value):
        digits_only = re.sub('[\\s\\-\\(\\)\\+]', '', value)
        if len(digits_only) >= 10:
            return ('phone', 'phone')
    if re.match('^\\d{4}-\\d{2}-\\d{2}$', value):
        return ('date', 'date')
    if value and value[0].isupper() and value.replace(' ', '').replace('-', '').isalpha() and (2 <= len(value) <= 30):
        words = value.split()
        if len(words) == 1:
            return ('first_name', None)
        elif len(words) == 2:
            return ('full_name', None)
        else:
            return ('name', None)
    if value.isdigit() and 1 <= len(value) <= 9:
        return ('number', 'number')
    return None

def _ensure_unique_name(base_name: str, existing: dict[str, DetectedVariable]) -> str:
    if base_name not in existing:
        return base_name
    counter = 2
    while f'{base_name}_{counter}' in existing:
        counter += 1
    return f'{base_name}_{counter}'