from dataclasses import dataclass
from system.llm.openai.chat import ChatOpenAI

@dataclass
class ChatOpenAILike(ChatOpenAI):
    model: str