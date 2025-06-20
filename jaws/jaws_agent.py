import asyncio
import gradio as gr
from datetime import datetime

from semantic_kernel import Kernel
from semantic_kernel.functions import KernelArguments
from semantic_kernel.agents.runtime import InProcessRuntime
from semantic_kernel.agents import ChatCompletionAgent, GroupChatOrchestration, RoundRobinGroupChatManager, StandardMagenticManager, MagenticOrchestration
from semantic_kernel.connectors.ai.open_ai import OpenAIChatPromptExecutionSettings, OpenAIChatCompletion
from semantic_kernel.contents import ChatMessageContent

from jaws.jaws_config import *
from jaws.jaws_utils import (
    dbms_connection,
    render_error_panel,
    render_input_panel,
    render_assistant_panel,
    render_response_panel
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
    CONSOLE.print(render_assistant_panel(message.name, message.content, CONSOLE))


operator = ChatCompletionAgent(
    service=lang_service,
    name="Operator",
    description="The eyes of the network. Tasked with capturing small snapshots of network traffic data for downstream analysis.",
    instructions=OPERATOR_PROMPT,
    plugins=[ListInterfaces(), CapturePackets()],
    arguments=KernelArguments(settings)
)

data_scientist = ChatCompletionAgent(
    service=lang_service,
    name="DataScientist",
    description="Tasked with enriching the network traffic data for downstream analysis.",
    instructions=DATA_SCIENTIST_PROMPT,
    plugins=[DocumentOrganizations(), ComputeEmbeddings()],
    arguments=KernelArguments(settings)
)

lead_analyst = ChatCompletionAgent(
    service=lang_service,
    name="LeadAnalyst",
    description="Tasked with reviewing the enriched network traffic data to further identify additional patterns and anomalies. Responsible for reporting to the command center and managing the data.",
    instructions=LEAD_ANALYST_PROMPT,
    plugins=[FetchData(), AnomalyDetection(), DropDatabase()],
    arguments=KernelArguments(settings)
)

project_manager = ChatCompletionAgent(
    service=lang_service,
    name="ProjectManager",
    description="Tasked with helping the team by managing their email communications and ensuring a copy of the email report is returned to the command center.",
    instructions=PROJECT_MANAGER_PROMPT,
    plugins=[SendEmail()],
    arguments=KernelArguments(settings)
)


async def orchestration(input: str) -> str:
    runtime = InProcessRuntime()
    runtime.start()
    CONSOLE.print(render_input_panel("INPUT", input, CONSOLE))
    try:
        # orchestration = GroupChatOrchestration(
        #    members=[operator, lead_analyst],
        #    manager=RoundRobinGroupChatManager(max_rounds=2), # Even number so lead_analyst gets the last word
        #    agent_response_callback=agent_callback,
        #)
        #message = f"GROUP CHAT | ROUND ROBIN | A collaborative conversation among agents..."
        orchestration = MagenticOrchestration(
            members=[operator, data_scientist, lead_analyst, project_manager],
            manager=StandardMagenticManager(chat_completion_service=reasoning_service),
            agent_response_callback=agent_callback,
        )
        message = "MAGENTIC | Flexible, general-purpose multi-agent pattern designed for complex, open-ended tasks that require dynamic collaboration."
        CONSOLE.print(render_assistant_panel("ORCHESTRATION", message, CONSOLE))
        result = await orchestration.invoke(task=input, runtime=runtime)
        response = await result.get()
        response_text = str(response.content)
        CONSOLE.print(render_response_panel("RESPONSE", response_text, CONSOLE))
        return response.content
    except Exception as e:
        CONSOLE.print(render_error_panel("ERROR", str(e), CONSOLE))
        response = None
    finally:
        await runtime.stop_when_idle()
       

def main():
    with gr.Blocks(title="JAWS | Agentic Network Traffic Monitoring") as INTERFACE:
        chat_history = gr.State(value=[{
            "role": "assistant", 
            "content": f"A collaborative group(4) of network analysts tasked with capturing 30-60 second snapshots of network traffic data, enriching the data with OSINT, and returning situation reports to the command center.\n\nMembers: {operator.name}, {data_scientist.name}, {lead_analyst.name}, {project_manager.name}\nTools: ListInterfaces, CapturePackets, DocumentOrganizations, ComputeEmbeddings, AnomalyDetection, FetchData, SendEmail, DropDatabase",
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
                input_text = gr.Textbox(show_label=False, container=False, placeholder="Ask the team questions about the current state of the network.")
                request_button = gr.Button("💬 Report in, team", variant="huggingface")
        
        async def group_orchestration(history, input_text=""):
            input = input_text if input_text else "Please perform a deep network traffic analysis and email the group a situation report, after which submit a report to the command center."
            response = await orchestration(input)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_turn = {"role": "assistant", "content": response, "metadata": {"title": f"📋 Situation Report | {timestamp}"}}
            updated_history = history + [new_turn]
            return updated_history, updated_history

        input_text.submit(
            fn=group_orchestration,
            inputs=[chat_history, input_text],
            outputs=[chatbot, chat_history]
        )

        request_button.click(
            fn=lambda history: asyncio.run(group_orchestration(history, "")),
            inputs=[chat_history],
            outputs=[chatbot, chat_history]
        )
    
    INTERFACE.launch(server_name="0.0.0.0", server_port=7860)

if __name__ == "__main__":
    main()