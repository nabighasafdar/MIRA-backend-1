from typing import overload
from openai.types.responses.easy_input_message_param import EasyInputMessageParam
from openai.types.responses.response_input_image_param import ResponseInputImageParam
from openai.types.responses.response_input_message_content_list_param import ResponseInputMessageContentListParam
from openai.types.responses.response_input_text_param import ResponseInputTextParam
from system.llm.messages import AssistantMessage, BaseMessage, ContentPartImageParam, ContentPartRefusalParam, ContentPartTextParam, SystemMessage, UserMessage

class ResponsesAPIMessageSerializer:

    @staticmethod
    def _serialize_content_part_text(part: ContentPartTextParam) -> ResponseInputTextParam:
        return ResponseInputTextParam(text=part.text, type='input_text')

    @staticmethod
    def _serialize_content_part_image(part: ContentPartImageParam) -> ResponseInputImageParam:
        return ResponseInputImageParam(image_url=part.image_url.url, detail=part.image_url.detail, type='input_image')

    @staticmethod
    def _serialize_user_content(content: str | list[ContentPartTextParam | ContentPartImageParam]) -> str | ResponseInputMessageContentListParam:
        if isinstance(content, str):
            return content
        serialized_parts: ResponseInputMessageContentListParam = []
        for part in content:
            if part.type == 'text':
                serialized_parts.append(ResponsesAPIMessageSerializer._serialize_content_part_text(part))
            elif part.type == 'image_url':
                serialized_parts.append(ResponsesAPIMessageSerializer._serialize_content_part_image(part))
        return serialized_parts

    @staticmethod
    def _serialize_system_content(content: str | list[ContentPartTextParam]) -> str | ResponseInputMessageContentListParam:
        if isinstance(content, str):
            return content
        serialized_parts: ResponseInputMessageContentListParam = []
        for part in content:
            if part.type == 'text':
                serialized_parts.append(ResponsesAPIMessageSerializer._serialize_content_part_text(part))
        return serialized_parts

    @staticmethod
    def _serialize_assistant_content(content: str | list[ContentPartTextParam | ContentPartRefusalParam] | None) -> str | ResponseInputMessageContentListParam | None:
        if content is None:
            return None
        if isinstance(content, str):
            return content
        serialized_parts: ResponseInputMessageContentListParam = []
        for part in content:
            if part.type == 'text':
                serialized_parts.append(ResponsesAPIMessageSerializer._serialize_content_part_text(part))
            elif part.type == 'refusal':
                serialized_parts.append(ResponseInputTextParam(text=f'[Refusal: {part.refusal}]', type='input_text'))
        return serialized_parts

    @overload
    @staticmethod
    def serialize(message: UserMessage) -> EasyInputMessageParam:
        ...

    @overload
    @staticmethod
    def serialize(message: SystemMessage) -> EasyInputMessageParam:
        ...

    @overload
    @staticmethod
    def serialize(message: AssistantMessage) -> EasyInputMessageParam:
        ...

    @staticmethod
    def serialize(message: BaseMessage) -> EasyInputMessageParam:
        if isinstance(message, UserMessage):
            return EasyInputMessageParam(role='user', content=ResponsesAPIMessageSerializer._serialize_user_content(message.content))
        elif isinstance(message, SystemMessage):
            return EasyInputMessageParam(role='system', content=ResponsesAPIMessageSerializer._serialize_system_content(message.content))
        elif isinstance(message, AssistantMessage):
            content = ResponsesAPIMessageSerializer._serialize_assistant_content(message.content)
            if content is None:
                if message.tool_calls:
                    tool_call_text = '\n'.join((f'[Tool call: {tc.function.name}({tc.function.arguments})]' for tc in message.tool_calls))
                    content = tool_call_text
                else:
                    content = ''
            return EasyInputMessageParam(role='assistant', content=content)
        else:
            raise ValueError(f'Unknown message type: {type(message)}')

    @staticmethod
    def serialize_messages(messages: list[BaseMessage]) -> list[EasyInputMessageParam]:
        return [ResponsesAPIMessageSerializer.serialize(m) for m in messages]