import os
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage
from langchain_ollama import ChatOllama

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://192.168.1.102:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "gemma4:31b")

chat_model = ChatOllama(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_BASE_URL,
    temperature=float(os.getenv("OLLAMA_TEMPERATURE", "0.1")),
    num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "16384")),
    top_k=int(os.getenv("OLLAMA_TOP_K", "20")),
    top_p=float(os.getenv("OLLAMA_TOP_P", "0.9")),
    repeat_penalty=float(os.getenv("OLLAMA_REPEAT_PENALTY", "1.1")),
)

def get_message_text(message: BaseMessage) -> str:
    return message.content.strip()

#---
#import os
#from dotenv import load_dotenv
#from langchain_core.messages import BaseMessage
#from langchain_openai import ChatOpenAI

#load_dotenv()

#chat_model = ChatOpenAI(
#    model=os.getenv("OPENAI_MODEL_NAME", "gpt-5.5"),
#    api_key=os.getenv("OPENAI_API_KEY"),
#    temperature=0.1,
#)

#def get_message_text(message: BaseMessage) -> str:
#    return message.content.strip()
#-----