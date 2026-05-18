import os
import sys
import asyncio

import uvicorn

from fastapi import FastAPI, APIRouter
from dotenv import load_dotenv

load_dotenv()

from src.mappings.company_stock_code_array import CompanyStockCodeArray
from fastapi.middleware.cors import CORSMiddleware

# import type
from src.types.langgraph_state_types import OverallState

# import graph
from src.agent.graph import graph

# import api routers
from src.api.chatbot import chatbot_router

from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

app = FastAPI()
api_router = APIRouter()
api_router.include_router(chatbot_router)
app.include_router(api_router)

origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def terminal_chat():
    while True:
        user_input = input("You: ")
        if user_input.lower() in ("exit", "quit"):
            break
        try:
            graph_answer = graph.invoke(
                {
                    "messages": [HumanMessage(content=user_input)],
                    "user_input": user_input,
                },
                config={"configurable": {"thread_id": "1"}},
            )
        except Exception as err:
            print("Error:", err, file=sys.stderr)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=3001)