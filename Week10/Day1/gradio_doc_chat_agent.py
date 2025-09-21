import gradio as gr
import os
import json
import sqlite3
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime
from chatbot_models import (
    init_db, insert_agent_thread, insert_agent_message, get_agent_messages, 
    get_agent_thread, list_agent_threads, insert_agent_tool_call, 
    update_agent_tool_call_status, get_pending_agent_tool_calls, 
    get_agent_tool_calls_for_message
)
from chatbot_openrouter import chat_openrouter
from gradio_mcp_client import mcp_client

# System prompt for Gradio documentation assistance
GRADIO_DOC_SYSTEM_PROMPT = """You are a helpful Gradio documentation assistant that can access and search through Gradio's official documentation. You are an autonomous agent that uses tools to provide accurate, up-to-date information about Gradio.

Your capabilities:
- Load the complete Gradio documentation summary
- Search through Gradio docs, guides, and demos for specific information
- Provide detailed explanations with code examples
- Help users understand Gradio concepts, components, and best practices

Always use your tools to get the most current information from the official Gradio documentation before answering questions. Be thorough and provide helpful examples when appropriate."""

# Initialize database
init_db()

# Tool functions for Gradio Docs MCP Server (Proxy functions)
def load_gradio_docs():
    """Load the complete Gradio documentation summary - proxy to gradio_docs_mcp_load_gradio_docs"""
    try:
        # Call the MCP client (which acts as proxy to the actual MCP server)
        import asyncio
        result = asyncio.run(mcp_client.load_gradio_docs())
        return result
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error loading Gradio docs: {str(e)}"
        }

def search_gradio_docs(query: str):
    """Search through Gradio documentation for specific information - proxy to gradio_docs_mcp_search_gradio_docs"""
    try:
        # Call the MCP client (which acts as proxy to the actual MCP server)
        import asyncio
        result = asyncio.run(mcp_client.search_gradio_docs(query))
        return result
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error searching Gradio docs: {str(e)}"
        }

# Tool registry - using the exact same names as the actual MCP server tools
TOOLS = {
    "gradio_docs_mcp_load_gradio_docs": load_gradio_docs,
    "gradio_docs_mcp_search_gradio_docs": search_gradio_docs
}

# Tool definitions for OpenAI API
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "gradio_docs_mcp_load_gradio_docs",
            "description": "Load the complete Gradio documentation summary in /llms.txt style format",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "gradio_docs_mcp_search_gradio_docs",
            "description": "Search through Gradio's docs, guides, and demos using embedding search to return the most relevant context",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant Gradio documentation"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

def execute_tool_call(tool_name: str, arguments: dict):
    """Execute a tool call and return the result"""
    if tool_name not in TOOLS:
        return {
            "status": "error",
            "message": f"Unknown tool: {tool_name}"
        }
    
    try:
        tool_func = TOOLS[tool_name]
        if arguments:
            result = tool_func(**arguments)
        else:
            result = tool_func()
        return result
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error executing {tool_name}: {str(e)}"
        }

def process_tool_calls(thread_id: int, message_id: int, tool_calls: List[dict]) -> bool:
    """Process tool calls from an assistant message and return True when all are complete"""
    
    for tool_call in tool_calls:
        tool_call_id = tool_call.get("id")
        tool_name = tool_call.get("function", {}).get("name")
        arguments_str = tool_call.get("function", {}).get("arguments", "{}")
        
        try:
            arguments = json.loads(arguments_str)
        except json.JSONDecodeError:
            arguments = {}
        
        # Check if this tool call already exists in database
        existing_calls = get_agent_tool_calls_for_message(thread_id, message_id)
        already_exists = any(call["tool_call_id"] == tool_call_id for call in existing_calls)
        
        if not already_exists:
            # Store tool call in database BEFORE execution
            insert_agent_tool_call(
                thread_id=thread_id,
                message_id=message_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments_str,
                status="pending"
            )
    
    # Now execute all pending tool calls for this message
    pending_calls = get_pending_agent_tool_calls(thread_id, message_id)
    
    for pending_call in pending_calls:
        tool_call_id = pending_call["tool_call_id"]
        tool_name = pending_call["tool_name"]
        arguments_str = pending_call["arguments"]
        
        try:
            arguments = json.loads(arguments_str)
        except json.JSONDecodeError:
            arguments = {}
        
        # Execute the tool call
        result = execute_tool_call(tool_name, arguments)
        
        # Update tool call status
        status = "completed" if result.get("status") == "success" else "failed"
        update_agent_tool_call_status(
            tool_call_id=tool_call_id,
            status=status,
            result=json.dumps(result)
        )
        
        # Check if we already have a tool message for this tool_call_id
        existing_messages = get_agent_messages(thread_id)
        tool_message_exists = False
        for msg_row in existing_messages:
            msg = dict(msg_row)  # Convert sqlite3.Row to dict
            if msg["role"] == "tool" and msg.get("extra"):
                try:
                    extra = json.loads(msg["extra"])
                    if extra.get("tool_call_id") == tool_call_id:
                        tool_message_exists = True
                        break
                except:
                    pass
        
        # Only add tool message if one doesn't already exist for this tool_call_id
        if not tool_message_exists:
            # Format tool content according to OpenAI standards
            if result.get("status") == "success":
                if "content" in result:
                    tool_content = result["content"]
                else:
                    tool_content = result.get("message", "Operation completed successfully")
            else:
                tool_content = result.get("message", "Operation failed")
            
            insert_agent_message(
                thread_id=thread_id,
                role="tool",
                content=tool_content,
                extra=json.dumps({
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "full_result": result
                })
            )
    
    # Check if all tool calls for this message are completed
    remaining_pending = get_pending_agent_tool_calls(thread_id, message_id)
    return len(remaining_pending) == 0

def agent_conversation_loop(thread_id: int, model: str = "openai/gpt-4o", temperature: float = 1.0, max_iterations: int = 10):
    """Run the agent conversation loop until no more tool calls are needed"""
    iterations = 0
    
    while iterations < max_iterations:
        iterations += 1
        
        # Get all messages for the thread
        msgs = get_agent_messages(thread_id)
        messages = []
        
        for m_row in msgs:
            m = dict(m_row)
            msg = {"role": m["role"], "content": m["content"]}
            
            # Add tool_call_id for tool messages
            if m["role"] == "tool" and m.get("extra"):
                try:
                    extra = json.loads(m["extra"])
                    if "tool_call_id" in extra:
                        msg["tool_call_id"] = extra["tool_call_id"]
                except:
                    pass
            
            # Add tool_calls for assistant messages
            elif m["role"] == "assistant" and m.get("extra"):
                try:
                    extra = json.loads(m["extra"])
                    # Check if this is the full API response data
                    if "choices" in extra:
                        assistant_message = extra.get("choices", [{}])[0].get("message", {})
                        tool_calls = assistant_message.get("tool_calls", [])
                        if tool_calls:
                            msg["tool_calls"] = tool_calls
                except:
                    pass
            
            messages.append(msg)
        
        # Make API call with tools
        assistant_content, data = chat_openrouter(
            messages=messages,
            model=model,
            temperature=temperature,
            tools=TOOL_DEFINITIONS
        )
        
        if data and "error" in data:
            # Add error message and break
            insert_agent_message(thread_id, "assistant", f"Error: {data['error']}")
            break
        
        # Extract assistant message and tool calls
        assistant_message = data.get("choices", [{}])[0].get("message", {})
        tool_calls = assistant_message.get("tool_calls", [])
        
        # Add assistant message (even if content is empty but has tool calls)
        message_id = insert_agent_message(
            thread_id=thread_id,
            role="assistant",
            content=assistant_content or "",
            extra=json.dumps(data) if data else None
        )
        
        # If no tool calls, we're done
        if not tool_calls:
            break
        
        # Process tool calls and wait for ALL to complete
        all_completed = process_tool_calls(thread_id, message_id, tool_calls)
        
        # Only continue if all tool calls are completed
        if not all_completed:
            print(f"Warning: Not all tool calls completed in iteration {iterations}")
            break
    
    return iterations

def new_agent_conversation(model: str = "openai/gpt-4o", temperature: float = 1.0) -> int:
    """Start a new agent conversation"""
    thread_id = insert_agent_thread(agent_type='gradio_docs')
    insert_agent_message(thread_id, "system", GRADIO_DOC_SYSTEM_PROMPT, model=model, temperature=temperature)
    return thread_id

def add_user_message_and_run_agent(thread_id: int, content: str, model: str = "openai/gpt-4o", temperature: float = 1.0):
    """Add user message and run the agent conversation loop"""
    # Add user message
    insert_agent_message(thread_id, "user", content, model=model, temperature=temperature)
    
    # Run agent loop
    iterations = agent_conversation_loop(thread_id, model, temperature)
    
    return iterations

def markdown_to_html(text: str) -> str:
    """Convert basic markdown to HTML for better display in Gradio"""
    import re
    
    # Convert code blocks with proper formatting
    text = re.sub(r'```python\n(.*?)\n```', r'<div style="background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 4px; padding: 12px; margin: 8px 0; font-family: \'Courier New\', monospace; font-size: 14px; overflow-x: auto;"><code style="color: #d63384;">\1</code></div>', text, flags=re.DOTALL)
    text = re.sub(r'```\n(.*?)\n```', r'<div style="background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 4px; padding: 12px; margin: 8px 0; font-family: \'Courier New\', monospace; font-size: 14px; overflow-x: auto;"><code>\1</code></div>', text, flags=re.DOTALL)
    
    # Convert inline code
    text = re.sub(r'`([^`]+)`', r'<code style="background-color: #f1f3f4; padding: 2px 4px; border-radius: 3px; font-family: \'Courier New\', monospace; font-size: 13px;">\1</code>', text)
    
    # Convert bold text
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong style="font-weight: bold;">\1</strong>', text)
    
    # Convert headers
    text = re.sub(r'^### (.*?)$', r'<h3 style="color: #333; margin: 16px 0 8px 0; font-size: 18px;">\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.*?)$', r'<h2 style="color: #333; margin: 20px 0 10px 0; font-size: 20px;">\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.*?)$', r'<h1 style="color: #333; margin: 24px 0 12px 0; font-size: 24px;">\1</h1>', text, flags=re.MULTILINE)
    
    # Convert bullet points
    text = re.sub(r'^- (.*?)$', r'<li style="margin: 4px 0;">\1</li>', text, flags=re.MULTILINE)
    text = re.sub(r'(<li style="margin: 4px 0;">.*</li>)', r'<ul style="margin: 8px 0; padding-left: 20px;">\1</ul>', text, flags=re.DOTALL)
    
    # Convert line breaks
    text = text.replace('\n', '<br>')
    
    return text

def format_response_as_markdown(text: str) -> str:
    """Format plain text response as proper markdown using AI"""
    if not text or not text.strip():
        return text
    
    try:
        import requests
        import os
        from dotenv import load_dotenv
        
        load_dotenv()
        
        # Get API key (support both OpenAI and OpenRouter)
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        api_key = openai_api_key or openrouter_api_key
        
        if not api_key:
            raise ValueError("No API key found. Please set OPENAI_API_KEY or OPENROUTER_API_KEY environment variable.")
        
        # Use OpenRouter API
        api_url = "https://openrouter.ai/api/v1/chat/completions"
        
        # System prompt for markdown formatting
        system_prompt = """Format a given plain text input as proper markdown by identifying and converting elements (such as command, code, header titles, lists, numbered lists, etc.) into their correct markdown syntax. 

Carefully analyze the input, determine which parts should be formatted with relevant markdown styling, and rewrite the text accordingly. 
Continue reviewing and formatting until all appropriate markdown conventions are applied throughout the input before finalizing your response.
Use chain-of-thought reasoning internally before producing your answer to ensure every relevant part of the text receives appropriate markdown formatting.

**Detailed Instructions:**
- Analyze the plain text thoroughly and decide, for each section, the most suitable markdown formatting.
    - Apply headers (e.g. #, ##, ###) to any titles or headings.
    - Convert code commands or code snippets into inline code or fenced code blocks, as relevant.
    - Change bullet point lists or number lists into the equivalent markdown list format using -, *, or numbers.
    - Apply bold or italics for emphasis where appropriate (e.g. for important words or phrases clearly meant to be emphasized).
    - Handle sub-lists and nested lists as nested markdown lists.
    - Identify and convert any links, blockquotes, or other markdown features as needed.
- Rewrite and output the fully formatted markdown version, replacing the original plain text.

**Output Format:**
Return a single markdown-formatted version of the entire input text, ready to be rendered directly. Do not include explanations, comments, or code blocks—only the properly formatted markdown output.

**Reminder:**  
- Your objective is to understand a plain text input and return a markdown-formatted version, converting all applicable elements.
- Be thorough in applying markdown syntax for every relevant element (headers, lists, code, etc.).
- Output only the markdown-formatted version, no extra commentary."""
        
        # Prepare request
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": "openai/gpt-5-nano",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            "temperature": 0.1,
            "max_tokens": 4000
        }
        
        # Make API call
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        formatted_text = result["choices"][0]["message"]["content"].strip()
        return formatted_text
        
    except Exception as e:
        print(f"Error formatting markdown: {e}")
        # Fallback to original text if formatting fails
        return text

def get_conversation_history(thread_id: int) -> List[dict]:
    """Get conversation history for display with enhanced tool call information"""
    msgs = get_agent_messages(thread_id)
    messages = []
    
    for m_row in msgs:
        m = dict(m_row)
        if m["role"] == "system":
            continue
        
        content = m["content"]
        
        # Enhanced handling for assistant messages
        if m["role"] == "assistant":
            # Get tool calls for this message
            tool_calls = get_agent_tool_calls_for_message(thread_id, m["id"])
            
            if tool_calls:
                # Show "Tool Calls" even if content is empty
                tool_call_summary = f"📚 **Gradio Docs Tools** ({len(tool_calls)}):\n"
                for tc in tool_calls:
                    status_emoji = "✅" if tc["status"] == "completed" else "❌" if tc["status"] == "failed" else "⏳"
                    tool_call_summary += f"- {status_emoji} `{tc['tool_name']}`\n"
                
                if content:
                    content = f"{content}\n\n---\n\n{tool_call_summary}"
                else:
                    content = tool_call_summary
            
            # Format the content as proper markdown using AI
            content = format_response_as_markdown(content)
        
        msg = {"role": m["role"], "content": content}
        messages.append(msg)
    
    return messages

def build_gradio_doc_agent_ui():
    """Build the Gradio UI for the Gradio Docs agent chat"""
    from chatbot_models import get_default_title
    
    def get_sidebar_conversations(offset=0, max_display=10):
        threads = list_agent_threads(agent_type='gradio_docs')
        threads = sorted(threads, key=lambda t: t["updated_at"], reverse=True)
        total = len(threads)
        threads = threads[offset:offset+max_display]
        
        options = []
        thread_ids = []
        for t in threads:
            title = t["title"] or get_default_title(t["created_at"])
            options.append([f"📚 {title}"])
            thread_ids.append(t["id"])
        
        more = (offset + max_display) < total
        return options, thread_ids, more, total
    
    def refresh_conversations():
        options, thread_ids, more, total = get_sidebar_conversations()
        return gr.update(value=options), thread_ids, gr.update(visible=more)
    
    def on_new_conversation(model, temperature):
        thread_id = new_agent_conversation(model, temperature)
        return thread_id, [], f"New Gradio Docs agent conversation started (ID: {thread_id})"
    
    def on_send_message(message, thread_id, model, temperature):
        if not thread_id:
            return [], "Please start a new conversation first"
        
        if not message.strip():
            return get_conversation_history(thread_id), ""
        
        # Add user message and run agent
        iterations = add_user_message_and_run_agent(
            int(thread_id), message, model, temperature
        )
        
        # Get updated history
        history = get_conversation_history(int(thread_id))
        
        return history, f"Gradio Docs agent completed in {iterations} iterations"
    
    def on_select_conversation(evt: gr.SelectData, thread_ids):
        if not thread_ids or evt is None or evt.index is None or evt.index[0] >= len(thread_ids):
            return [], None, "No conversation selected"
        
        idx = evt.index[0]
        thread_id = thread_ids[idx]
        
        history = get_conversation_history(int(thread_id))
        return history, thread_id, f"Loaded Gradio Docs conversation {thread_id}"
    
    def on_load_more(current_offset, current_thread_ids):
        new_offset = current_offset + 10
        options, thread_ids, more, total = get_sidebar_conversations(new_offset)
        return gr.update(value=options), thread_ids, gr.update(visible=more), new_offset
    
    with gr.Blocks(title="Gradio Docs Agent", css="""
        .gradio-container {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        .chatbot .message {
            white-space: pre-wrap;
        }
        .chatbot .message code {
            background-color: #f1f3f4;
            padding: 2px 4px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
            font-size: 13px;
        }
        .chatbot .message pre {
            background-color: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 4px;
            padding: 12px;
            margin: 8px 0;
            font-family: 'Courier New', monospace;
            font-size: 14px;
            overflow-x: auto;
            white-space: pre;
        }
        .chatbot .message pre code {
            background: none;
            padding: 0;
            border-radius: 0;
        }
        .chatbot .message strong {
            font-weight: bold;
        }
        .chatbot .message h1, .chatbot .message h2, .chatbot .message h3 {
            color: #333;
            margin: 16px 0 8px 0;
        }
        .chatbot .message ul {
            margin: 8px 0;
            padding-left: 20px;
        }
        .chatbot .message li {
            margin: 4px 0;
        }
        /* Enhanced styling for better markdown rendering */
        .chatbot .message {
            line-height: 1.6;
        }
        .chatbot .message p {
            margin: 8px 0;
        }
        .chatbot .message blockquote {
            border-left: 4px solid #ddd;
            margin: 8px 0;
            padding-left: 16px;
            color: #666;
        }
        .chatbot .message hr {
            border: none;
            border-top: 1px solid #eee;
            margin: 16px 0;
        }
    """) as demo:
        gr.Markdown("# 📚 Gradio Documentation Agent")
        gr.Markdown("This agent can access and search through Gradio's official documentation using the Gradio Docs MCP Server. Ask questions about Gradio components, features, and best practices!")
        
        with gr.Row():
            with gr.Column(scale=1):
                gr.Markdown("### Gradio Docs Conversations")
                
                new_conv_btn = gr.Button("🆕 New Gradio Docs Chat", variant="primary")
                
                conversation_list = gr.Dataframe(
                    headers=["Conversations"],
                    datatype=["str"],
                    interactive=True,
                    row_count=10,
                    col_count=1,
                    label="Load Gradio Docs Conversation"
                )
                
                load_more_btn = gr.Button("Load More", visible=False)
                refresh_btn = gr.Button("🔄 Refresh")
                
            with gr.Column(scale=3):
                gr.Markdown("### Gradio Documentation Assistant")
                
                chatbot = gr.Chatbot(
                    type="messages",
                    height=400,
                    show_label=False
                )
                
                with gr.Row():
                    msg_input = gr.Textbox(
                        placeholder="Ask about Gradio components, features, or best practices...",
                        scale=4,
                        show_label=False
                    )
                    send_btn = gr.Button("Send", variant="primary", scale=1)
                
                # Settings moved below message input
                with gr.Accordion("⚙️ Settings", open=False):
                    with gr.Row():
                        model_dropdown = gr.Dropdown(
                            label="Model",
                            choices=[
                                "openai/gpt-5-nano",
                                "openai/gpt-4o",
                                "openai/gpt-4.1",
                                "openai/gpt-4.1-mini",
                                "openai/gpt-4.1-nano",
                                "anthropic/claude-sonnet-4",
                                "anthropic/claude-3.5-sonnet"
                            ],
                            value="openai/gpt-5-nano",
                            scale=2
                        )
                        temperature_slider = gr.Slider(
                            label="Temperature",
                            minimum=0.0,
                            maximum=2.0,
                            value=1.0,
                            step=0.1,
                            scale=1
                        )
                
                status_text = gr.Textbox(
                    label="Status",
                    interactive=False,
                    max_lines=2
                )
        
        # State
        current_thread_id = gr.State(value=None)
        sidebar_thread_ids = gr.State(value=[])
        sidebar_offset = gr.State(value=0)
        
        # Event handlers
        new_conv_btn.click(
            fn=on_new_conversation,
            inputs=[model_dropdown, temperature_slider],
            outputs=[current_thread_id, chatbot, status_text]
        )
        
        send_btn.click(
            fn=on_send_message,
            inputs=[msg_input, current_thread_id, model_dropdown, temperature_slider],
            outputs=[chatbot, status_text]
        ).then(
            fn=lambda: "",
            outputs=[msg_input]
        )
        
        msg_input.submit(
            fn=on_send_message,
            inputs=[msg_input, current_thread_id, model_dropdown, temperature_slider],
            outputs=[chatbot, status_text]
        ).then(
            fn=lambda: "",
            outputs=[msg_input]
        )
        
        refresh_btn.click(
            fn=refresh_conversations,
            outputs=[conversation_list, sidebar_thread_ids, load_more_btn]
        )
        
        conversation_list.select(
            fn=on_select_conversation,
            inputs=[sidebar_thread_ids],
            outputs=[chatbot, current_thread_id, status_text]
        )
        
        load_more_btn.click(
            fn=on_load_more,
            inputs=[sidebar_offset, sidebar_thread_ids],
            outputs=[conversation_list, sidebar_thread_ids, load_more_btn, sidebar_offset]
        )
        
        # Initialize sidebar on load
        demo.load(
            fn=refresh_conversations,
            outputs=[conversation_list, sidebar_thread_ids, load_more_btn]
        )
    
    return demo

if __name__ == "__main__":
    demo = build_gradio_doc_agent_ui()
    demo.launch()