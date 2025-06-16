import asyncio
import gradio as gr
from datetime import datetime

from semantic_kernel import Kernel
from semantic_kernel.agents import(
    ChatCompletionAgent, 
    GroupChatOrchestration, 
    RoundRobinGroupChatManager
)
from semantic_kernel.agents.runtime import InProcessRuntime
from semantic_kernel.connectors.ai.open_ai import OpenAIChatPromptExecutionSettings, OpenAIChatCompletion
from semantic_kernel.contents import ChatMessageContent, FunctionCallContent, FunctionResultContent
from semantic_kernel.functions import KernelArguments

from jaws.jaws_config import *
from jaws.jaws_utils import (
    dbms_connection,
    render_error_panel,
    render_info_panel,
    render_success_panel,
    render_input_panel,
    render_assistant_panel,
    render_response_panel
    #render_activity_panel
)
from jaws.sk_tools import *

driver = dbms_connection(DATABASE)
kernel = Kernel()
settings = OpenAIChatPromptExecutionSettings()
reasoning_service = OpenAIChatCompletion(ai_model_id=OPENAI_REASONING_MODEL, api_key=OPENAI_API_KEY)
kernel.add_service(reasoning_service)
lang_service = OpenAIChatCompletion(ai_model_id=OPENAI_MODEL, api_key=OPENAI_API_KEY)
kernel.add_service(lang_service)


async def agent_callback(message: ChatMessageContent) -> None:
    for item in message.items or []:
        if isinstance(item, FunctionCallContent):
            CONSOLE.print(render_assistant_panel(item.name, item.arguments, CONSOLE))
        elif isinstance(item, FunctionResultContent):
            CONSOLE.print(render_assistant_panel(item.name, item.result, CONSOLE))
        else:
            CONSOLE.print(render_assistant_panel(message.name, message.content, CONSOLE))


operator = ChatCompletionAgent(
    service=lang_service,
    name="Operator",
    description="The eyes of the network. Tasked with capturing small snapshots of network traffic data, enriching the data, and analyzing the data looking for patterns and anomalies, or 'red flags'.",
    instructions=OPERATOR_PROMPT,
    plugins=[ListInterfaces(), CapturePackets(), DocumentOrganizations(), ComputeEmbeddings()],
    arguments=KernelArguments(settings)
)

network_analyst = ChatCompletionAgent(
    service=reasoning_service,
    name="LeadAnalyst",
    description="An expert IT Professional, Sysadmin, and Senior Analyst. Tasked with reviewing the enriched network traffic data to further identify additional patterns and anomalies. Responsible for reporting to High Command.",
    instructions=MANAGER_PROMPT,
    plugins=[AnomalyDetection(), FetchData(), DropDatabase(), SendEmail()],
    arguments=KernelArguments(settings)
)


max_rounds = 2
group_members=[operator, network_analyst]
group_config = GroupChatOrchestration(
    members=group_members,
    manager=RoundRobinGroupChatManager(max_rounds=max_rounds),
    agent_response_callback=agent_callback
)


async def orchestration(input: str) -> str:
    runtime = InProcessRuntime()
    runtime.start()

    CONSOLE.print(render_input_panel("INPUT", input, CONSOLE))
    
    try:
        config = group_config
        message = f"GROUP CHAT | ROUND ROBIN({max_rounds}) | MEMBERS({len(group_members)}): {operator.name}, {network_analyst.name}"
        CONSOLE.print(render_info_panel("ORCHESTRATION", message, CONSOLE))

        result = await config.invoke(
            task=input,
            runtime=runtime
        )
        
        response = await result.get()
        response_text = response.content
        CONSOLE.print(render_response_panel("RESPONSE", response_text, CONSOLE))

        return response_text
       
    except Exception as e:
        CONSOLE.print(render_error_panel("ERROR", e, CONSOLE))
        return f"[ERROR] | {e}"
        
    finally:
        await runtime.stop_when_idle()


def main():
    with gr.Blocks(title="Network Traffic Monitoring") as INTERFACE:
        groupchat_history = gr.State(value=[{
            "role": "assistant", 
            "content": "A collaborative group of analysts tasked with capturing 30-60 second snapshots of network traffic data, enchring the data, and returning a comprehensive report to the command center.",
            "metadata": {"title": "🔎 Traffic Analysis"}
        }])
        
        with gr.Row(equal_height=True):
            with gr.Column():
                groupchat_chatbot = gr.Chatbot(
                    value=groupchat_history.value,
                    type="messages",
                    show_label=True,
                    label=f"Group Chat | Round Robin({max_rounds}) | Members({len(group_members)})",
                    autoscroll=True,
                    resizable=True,
                    show_copy_button=True,
                    height=600
                )
                groupchat_request_button = gr.Button("💬 Report in, team", variant="huggingface")
        

        async def group_orchestration(history: str) -> str:
            response = await orchestration("Please perform your tasks and return a report to the command center.")
            timestamp = datetime.now()
            formatted_response = {"role": "assistant", "content": response, "metadata": {"title": f"📋 Situation Report | {timestamp.strftime('%Y-%m-%d %H:%M:%S')}"}}
            chat_history = (history + [formatted_response])[-3:]
            return chat_history, chat_history


        groupchat_request_button.click(
            fn=group_orchestration,
            inputs=[groupchat_history],
            outputs=[groupchat_chatbot, groupchat_history]
        )

    INTERFACE.launch(server_name="0.0.0.0", server_port=7860)

if __name__ == "__main__":
    main()