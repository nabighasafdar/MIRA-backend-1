import base64
from google.genai.types import Content, ContentListUnion, Part
from system.llm.messages import AssistantMessage, BaseMessage, SystemMessage, UserMessage

class GoogleMessageSerializer:

    @staticmethod
    def serialize_messages(messages: list[BaseMessage], include_system_in_user: bool=False) -> tuple[ContentListUnion, str | None]:
        messages = [m.model_copy(deep=True) for m in messages]
        formatted_messages: ContentListUnion = []
        system_message: str | None = None
        system_parts: list[str] = []
        for i, message in enumerate(messages):
            role = message.role if hasattr(message, 'role') else None
            if isinstance(message, SystemMessage) or role in ['system', 'developer']:
                if isinstance(message.content, str):
                    if include_system_in_user:
                        system_parts.append(message.content)
                    else:
                        system_message = message.content
                elif message.content is not None:
                    parts = []
                    for part in message.content:
                        if part.type == 'text':
                            parts.append(part.text)
                    combined_text = '\n'.join(parts)
                    if include_system_in_user:
                        system_parts.append(combined_text)
                    else:
                        system_message = combined_text
                continue
            if isinstance(message, UserMessage):
                role = 'user'
            elif isinstance(message, AssistantMessage):
                role = 'model'
            else:
                role = 'user'
            message_parts: list[Part] = []
            if include_system_in_user and system_parts and (role == 'user') and (not formatted_messages):
                system_text = '\n\n'.join(system_parts)
                if isinstance(message.content, str):
                    message_parts.append(Part.from_text(text=f'{system_text}\n\n{message.content}'))
                else:
                    message_parts.append(Part.from_text(text=system_text))
                system_parts = []
            elif isinstance(message.content, str):
                message_parts = [Part.from_text(text=message.content)]
            elif message.content is not None:
                for part in message.content:
                    if part.type == 'text':
                        message_parts.append(Part.from_text(text=part.text))
                    elif part.type == 'refusal':
                        message_parts.append(Part.from_text(text=f'[Refusal] {part.refusal}'))
                    elif part.type == 'image_url':
                        url = part.image_url.url
                        header, data = url.split(',', 1)
                        image_bytes = base64.b64decode(data)
                        mime_type = part.image_url.media_type
                        image_part = Part.from_bytes(data=image_bytes, mime_type=mime_type)
                        message_parts.append(image_part)
            if message_parts:
                final_message = Content(role=role, parts=message_parts)
                formatted_messages.append(final_message)
        return (formatted_messages, system_message)