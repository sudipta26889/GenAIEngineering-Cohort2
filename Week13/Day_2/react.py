import os
from dotenv import load_dotenv
from langchain import hub
from langchain.agents import AgentExecutor, create_react_agent
from langchain.tools import Tool
from langchain_openai import ChatOpenAI

load_dotenv()

os.environ["OPENAI_API_KEY"] = os.getenv("OPEN_ROUTER_KEY")
os.environ['OPENAI_API_BASE'] = 'https://openrouter.ai/api/v1'
os.environ['OPENAI_BASE_URL'] = 'https://openrouter.ai/api/v1'

# Define a tool the agent can use
tools = [
    Tool(
        name="Calculator",
        func=lambda x: str(eval(x)),
        description="Useful for doing math calculations"
    )
]

prompt_input = "What is 23 * 17?"

# Pull the standard ReAct prompt template
prompt = hub.pull("hwchase17/react")

# Create a chat model
llm = ChatOpenAI(
            model="openai/gpt-4o", 
            temperature=0,
            openai_api_base="https://openrouter.ai/api/v1"
        )

# Create a ReAct agent
agent = create_react_agent(llm, tools, prompt)

# Wrap in an executor to run
executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

# Run agent
result = executor.invoke({"input": prompt_input})
print(result["output"])
