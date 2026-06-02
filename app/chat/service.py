from app.core.llm import agent
from langchain_core.prompts import ChatPromptTemplate
TASK_CREATION_PROMPT_TEMPLATE = """
original task: {task}
analyze the original task, and split it into serveral sub-tasks.
++output the sub-tasks in the following format**:
{{
  "main_task": "main_task",
  "deadline": "deadline",
  "task_list": [
    {{
      "sub_task": "sub_task",
      "desc": "desc",
      "assignee": "assignee",
      "priority": "priority",
      "pre_task": "pre_task"
    }}
  ]
}}
"""
async def chat_llm(message: str):
    prompt = TASK_CREATION_PROMPT_TEMPLATE.format(task=message)
    # template = ChatPromptTemplate.from_messages([{"user", TASK_CREATION_PROMPT_TEMPLATE}])
    # msg =template.invoke({
    #     "task": message
    # })
    result = await agent.ainvoke(
            {"messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]}
        )
    
    print("生成的文件：", result["files"])
    return result["messages"][-1].content