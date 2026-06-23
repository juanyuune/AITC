import os
import re
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

load_dotenv()

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8080/v1")
VLLM_MODEL    = os.getenv("VLLM_MODEL", "/home/user/models/breeze2-8b")

chat_model = ChatOpenAI(
    model=VLLM_MODEL,
    base_url=VLLM_BASE_URL,
    api_key="dummy",
    temperature=float(os.getenv("VLLM_TEMPERATURE", "0.1")),
    max_tokens=int(os.getenv("VLLM_MAX_TOKENS", "2048")),
)

def get_message_text(message: BaseMessage) -> str:
    text = message.content.strip()
    # Strip any thinking blocks if present
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    return text