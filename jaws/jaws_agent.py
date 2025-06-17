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


operator_0 = ChatCompletionAgent(
    service=lang_service,
    name="Operator0",
    description="An expert IT Professional and Analyst. Tasked with capturing network traffic data and performing ETL(Extract, Transform, and Load) to enrich and prepare the data for analysis.",
    instructions=ANALYST_MANAGED_PROMPT,
    plugins=[ListInterfaces(), CapturePackets(), DocumentOrganizations(), ComputeEmbeddings()],
    arguments=KernelArguments(settings)
)

operator_1 = ChatCompletionAgent(
    service=lang_service,
    name="Operator1",
    description="The eyes of the network. Tasked with capturing small snapshots of network traffic data, enriching the data, and analyzing the data looking for patterns and anomalies, or 'red flags'.",
    instructions=OPERATOR_PROMPT,
    plugins=[ListInterfaces(), CapturePackets(), DocumentOrganizations(), ComputeEmbeddings(), AnomalyDetection()],
    arguments=KernelArguments(settings)
)

network_analyst = ChatCompletionAgent(
    service=reasoning_service,
    name="LeadAnalyst",
    description="An expert IT Professional, Sysadmin, and Senior Analyst. Tasked with reviewing the enriched network traffic data to further identify additional patterns and anomalies. Responsible for reporting to the command center.",
    instructions=MANAGER_PROMPT,
    plugins=[AnomalyDetection(), FetchData(), DropDatabase(), SendEmail()],
    arguments=KernelArguments(settings)
)


group_members=[operator_0, operator_1, network_analyst]
async def orchestration(input: str, max_rounds: int = 3) -> str:
    runtime = InProcessRuntime()
    runtime.start()
    CONSOLE.print(render_input_panel("INPUT", input, CONSOLE))
    try:
        orchestration = GroupChatOrchestration(
            members=group_members,
            manager=RoundRobinGroupChatManager(max_rounds=max_rounds),
            agent_response_callback=agent_callback,
        )
        message = f"GROUP CHAT | ROUND ROBIN({max_rounds}) | MEMBERS({len(group_members)}): {operator_0.name}, {operator_1.name}, {network_analyst.name}"
        CONSOLE.print(render_info_panel("ORCHESTRATION", message, CONSOLE))
        result = await orchestration.invoke(task=input, runtime=runtime)
        response = await result.get()
        response_text = str(response.content)
        CONSOLE.print(render_response_panel("RESPONSE", response_text, CONSOLE))
        return response.content
    finally:
        await runtime.stop_when_idle()
       

def main():
    with gr.Blocks(title="Network Traffic Monitoring") as INTERFACE:
        chat_history = gr.State(value=[{
            "role": "assistant", 
            "content": f"A collaborative group of analysts tasked with capturing 30-60 second snapshots of network traffic data, enriching the data, and returning reports to the command center.\n\nMembers: {operator_0.name}, {operator_1.name}, {network_analyst.name}\nTools: ListInterfaces, CapturePackets, DocumentOrganizations, ComputeEmbeddings, AnomalyDetection, FetchData, DropDatabase, SendEmail",
            "metadata": {"title": "🛡️ Command Center"}
        }])
        
        with gr.Row(equal_height=True):
            with gr.Column():
                chatbot = gr.Chatbot(
                    value=chat_history.value,
                    type="messages",
                    show_label=False,
                    autoscroll=True,
                    resizable=True,
                    show_copy_button=True,
                    height=480
                )
                input_text = gr.Textbox(show_label=False, container=False, placeholder="Ask the team a question about the network traffic.")
                with gr.Row(equal_height=True):
                    rounds_slider = gr.Slider(
                        minimum=3,
                        maximum=12,
                        value=3,
                        step=3,
                        show_label=False,
                        container=False
                    )
                    request_button = gr.Button("💬 Report in, team", variant="huggingface")
        
        async def group_orchestration(history, input_text="", max_rounds=2):
            input = input_text if input_text else "Please perform a network traffic analysis and return a situation report to the command center."
            response = await orchestration(input, max_rounds)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_turn = {"role": "assistant", "content": response, "metadata": {"title": f"📋 Situation Report | {timestamp}"}}
            updated_history = history + [new_turn]
            return updated_history, updated_history

        input_text.submit(
            fn=group_orchestration,
            inputs=[chat_history, input_text, rounds_slider],
            outputs=[chatbot, chat_history]
        )

        request_button.click(
            fn=lambda history, max_rounds: asyncio.run(group_orchestration(history, "", max_rounds)),
            inputs=[chat_history, rounds_slider],
            outputs=[chatbot, chat_history]
        )
    
    INTERFACE.launch(server_name="0.0.0.0", server_port=7860)

if __name__ == "__main__":
    main()