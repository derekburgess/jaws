import asyncio
from semantic_kernel import Kernel
from semantic_kernel.agents import(
    ChatCompletionAgent, 
    GroupChatOrchestration, 
    RoundRobinGroupChatManager, 
    OrchestrationHandoffs, 
    HandoffOrchestration, 
    ConcurrentOrchestration
)
from semantic_kernel.agents.runtime import InProcessRuntime
from semantic_kernel.connectors.ai.open_ai import OpenAIChatPromptExecutionSettings, OpenAIChatCompletion
from semantic_kernel.contents import AuthorRole, ChatMessageContent, FunctionCallContent, FunctionResultContent
from semantic_kernel.functions import KernelArguments

import gradio as gr
from datetime import datetime
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


def human_in_the_loop() -> ChatMessageContent:
    message = "Please provide a report of the situation."
    return ChatMessageContent(role=AuthorRole.USER, content=message)


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
    description="The eyes of the network. Tasked with capturing small snapshots of network traffic data, enriching the data, and analyzing the data looking for patterns and anomalies, or 'red flags'.",
    instructions=OPERATOR_PROMPT,
    plugins=[ListInterfaces(), CapturePackets(), DocumentOrganizations(), ComputeEmbeddings(), AnomalyDetection()],
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
    service=lang_service, 
    name="NetworkAnalyst",
    description="An expert IT Professional and Analyst. Tasked with capturing additional network traffic data and performing ETL(Extract, Transform, and Load) to enrich and prepare the data for analysis.",
    instructions=ANALYST_MANAGED_PROMPT,
    plugins=[ListInterfaces(), CapturePackets(), DocumentOrganizations(), ComputeEmbeddings()],
    arguments=KernelArguments(settings)
)

lead_network_analyst = ChatCompletionAgent(
    service=reasoning_service,
    name="LeadAnalyst",
    description="An expert IT Professional, Sysadmin, and Senior Analyst. Tasked with reviewing the enriched network traffic data to further identify additional patterns and anomalies. Responsible for reporting to High Command.",
    instructions=MANAGER_PROMPT,
    plugins=[AnomalyDetection(), FetchData(), DropDatabase(), SendEmail()],
    arguments=KernelArguments(settings)
)


handoffs = (
    OrchestrationHandoffs()
    .add_many(
        source_agent=lead_network_analyst.name,
        target_agents={
            operator_0.name: f"Transfer to {operator_0.name} to capture a quick snapshot of network traffic data, the agent will return a list of red flags. The red flags are an important part of the final report to High Command.",
            network_analyst.name: f"Transfer to {network_analyst.name} for longer snapshots of network traffic data, this agent will also enrich the data, which improves the quality and accuracy of the final report.",
        },
    )
    .add(
        source_agent=operator_0.name,
        target_agent=lead_network_analyst.name,
        description=f"Transfer back to the {lead_network_analyst.name} when the {operator_0.name} has completed their task so that the {lead_network_analyst.name} can review the data and provide a final report.",
    )
    .add(
        source_agent=network_analyst.name,
        target_agent=lead_network_analyst.name,
        description=f"Transfer back to the {lead_network_analyst.name} when the {network_analyst.name} has completed their task so that the {lead_network_analyst.name} can review the data and provide a final report.",
    )
)

concurrent_members=[operator_0, operator_1]
handoff_members=[lead_network_analyst, operator_0, network_analyst]
max_rounds = 2
group_members=[network_analyst, lead_network_analyst]


async def orchestration(input: str, orchestration: str) -> str:
    runtime = InProcessRuntime()
    runtime.start()

    CONSOLE.print(render_input_panel("INPUT", input, CONSOLE))
    
    try:
        if orchestration == "concurrent":
            config = ConcurrentOrchestration(members=concurrent_members)
            message = f"CONCURRENT | MEMBERS({len(concurrent_members)}): {operator_0.name}, {operator_1.name}"
        elif orchestration == "handoff":
            config = HandoffOrchestration(
                members=handoff_members,
                handoffs=handoffs,
                agent_response_callback=agent_callback,
                human_response_function=human_in_the_loop
            )
            message = f"HANDOFF | MEMBERS({len(handoff_members)}): {lead_network_analyst.name}, {network_analyst.name}, {operator_0.name}"
        elif orchestration == "group":
            config = GroupChatOrchestration(
                members=group_members,
                manager=RoundRobinGroupChatManager(max_rounds=max_rounds),
                agent_response_callback=agent_callback
            )
            message = f"GROUP CHAT | ROUND ROBIN({max_rounds}) | MEMBERS({len(group_members)}): {lead_network_analyst.name}, {network_analyst.name}"
        else:
            raise ValueError(f"[CONCURRENT] | [HANDOFF] | [GROUP CHAT]")
        
        CONSOLE.print(render_info_panel("ORCHESTRATION", message, CONSOLE))

        result = await config.invoke(
            task=input,
            runtime=runtime
        )
        
        response = await result.get()
        
        if orchestration == "concurrent":
            response_collection = []
            for item in response:
                response_collection.append(f"{item.name}: {item.content}")
            response_text = str("\n".join(response_collection))
            CONSOLE.print(render_response_panel("RESPONSE", response_text, CONSOLE))
        else:
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

        concurrent_chat_history = gr.State(value=[{
            "role": "assistant", 
            "content": "A group of operators tasked with capturing short 10-30 second snapshots of network traffic and returning a list of potential red flags to the command center.",
            "metadata": {"title": "👁️ Traffic Overwatch"}
        }])
        
        handoff_chat_history = gr.State(value=[{
            "role": "assistant", 
            "content": "A managed group of network analysts tasked with capturing 30-60 second snapshots of network traffic data, enchring the data, and returning a moderately detailed situational report to high command.",
            "metadata": {"title": "🔎 Traffic Analysis"}
        }])

        groupchat_history = gr.State(value=[{
            "role": "assistant", 
            "content": "A collaborative group of analysts tasked with capturing 30-60 second snapshots of network traffic data, enchring the data, and returning a comprehensive report to the high command.",
            "metadata": {"title": "🪬 Command Center"}
        }])
        
        
        with gr.Row(equal_height=True):
            with gr.Column():
                concurrent_chatbot = gr.Chatbot(
                    value=concurrent_chat_history.value,
                    type="messages",
                    show_label=True,
                    label="Concurrent",
                    autoscroll=True,
                    resizable=True,
                    show_copy_button=True,
                    height=600
                )
                concurrent_request_button = gr.Button("💬 Report in, team", variant="huggingface")
        
            with gr.Column():
                handoff_chatbot = gr.Chatbot(
                    value=handoff_chat_history.value,
                    type="messages",
                    show_label=True,
                    label="Handoff",
                    autoscroll=True,
                    resizable=True,
                    show_copy_button=True,
                    height=600
                )
                handoff_request_button = gr.Button("💬 Report in, team", variant="huggingface")

            with gr.Column():
                groupchat_chatbot = gr.Chatbot(
                    value=groupchat_history.value,
                    type="messages",
                    show_label=True,
                    label="Group Chat",
                    autoscroll=True,
                    resizable=True,
                    show_copy_button=True,
                    height=600
                )
                groupchat_request_button = gr.Button("💬 Report in, team", variant="huggingface")


        async def concurrent_orchestration(history: str) -> str:
            input = "Perform a short 10-30 second network probe and report back to the command center ASAP."
            response = await orchestration(input, "concurrent")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_turn = {
                "role": "assistant",
                "content": response,
                "metadata": {"title": f"️📋 Overwatch Report | {timestamp}"}
            }

            updated_history = history + [new_turn]
            return updated_history, updated_history

        async def handoff_orchestration(history: str) -> str:
            input = "The Command Center is reporting weird traffic near your endpoint. Perform a complete network scan and return a moderately detailed situational report to High Command ASAP."
            response = await orchestration(input, "handoff")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_turn = {
                "role": "assistant",
                "content": response,
                "metadata": {"title": f"️📋 Situation Report | {timestamp}"}
            }

            updated_history = history + [new_turn]
            return updated_history, updated_history

        async def group_orchestration(history: str) -> str:
            input = "High command is requesting the comprehensive network traffic report ASAP."
            response = await orchestration(input, "group")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_turn = {
                "role": "assistant",
                "content": response,
                "metadata": {"title": f"🛡️ Briefing for High Command | {timestamp}"}
            }

            updated_history = history + [new_turn]
            return updated_history, updated_history


        concurrent_request_button.click(
            fn=concurrent_orchestration,
            inputs=[concurrent_chat_history],
            outputs=[concurrent_chatbot, concurrent_chat_history]
        )

        handoff_request_button.click(
            fn=handoff_orchestration,
            inputs=[handoff_chat_history],
            outputs=[handoff_chatbot, handoff_chat_history]
        )

        groupchat_request_button.click(
            fn=group_orchestration,
            inputs=[groupchat_history],
            outputs=[groupchat_chatbot, groupchat_history]
        )

    INTERFACE.launch(server_name="0.0.0.0", server_port=7860)

if __name__ == "__main__":
    main()